"""Core download logic. Framework-free: no CLI parsing, no terminal output.

Communicates solely through its return value, its two optional callbacks
(progress and warnings), and raised ValueError. This is what a future HTTP layer
will call instead of re-implementing (Constitution Principle III).

The warning callback exists because temp-directory cleanup can fail inside a
finally block, after the outcome is already decided -- there is no return value
left for it to travel through. Handing the text to the caller keeps the choice
of where a warning goes (a terminal, a log, an HTTP response) outside this
module, which is the whole point of the boundary.
"""

import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from backend.validation import PostReference, build_target, parse_post_url

ProgressHook = Callable[[dict], None]
WarningHook = Callable[[str], None]


@dataclass(frozen=True)
class DownloadOutcome:
    """What happened. cli.py formats this and picks an exit code, nothing more."""

    status: str  # "downloaded" | "skipped" | "failed"
    paths: tuple[Path, ...]
    message: str


@dataclass(frozen=True)
class FormatOption:
    """One quality option, carried verbatim from yt-dlp (research D4).

    Nothing is derived, reformatted, or invented here: FR-007 requires the
    identifier to be passable straight back to --format, so format_id is echoed
    exactly. The remaining fields are what the operator needs to choose between
    options, and any of them may legitimately be absent.
    """

    format_id: str
    resolution: str | None
    ext: str | None
    filesize_approx: int | None
    tbr: float | None


@dataclass(frozen=True)
class FormatListing:
    """The result of asking what qualities exist. Mirrors DownloadOutcome."""

    status: str  # "listed" | "failed"
    formats: tuple[FormatOption, ...]
    message: str


# Ordered, case-insensitive substring match against yt-dlp's own error text
# (research D6). These strings live in a third-party library and are
# unversioned, so an unmatched message falls through to a generic explanation
# that quotes yt-dlp verbatim -- a string change degrades a specific diagnosis
# rather than crashing or lying.
_ERROR_DIAGNOSES: tuple[tuple[str, str], ...] = (
    ("no video could be found", "this post has no video in it."),
    ("is not a video", "this post contains media, but it is not a video."),
    (
        "not authorized to view this protected tweet",
        "this post belongs to a protected account and is not publicly accessible. "
        "This tool does not authenticate.",
    ),
    (
        "nsfw tweet requires authentication",
        "this post is age-restricted and is not publicly accessible. "
        "This tool does not authenticate.",
    ),
    ("twitter api says", "this post could not be found. It may have been deleted."),
    (
        "tweet is unavailable",
        "this post could not be found. It may have been deleted.",
    ),
)


# Temp-directory removal is retried because the obstruction is a file handle
# that is in the process of being released, not a permission the operator lacks.
# Five attempts spans ~0.8s, which is far longer than a process teardown needs
# and short enough that a genuinely stuck directory is reported promptly.
_CLEANUP_ATTEMPTS = 5
_CLEANUP_RETRY_SECONDS = 0.2


def _base_options(progress: ProgressHook | None) -> dict:
    """Options shared by every YoutubeDL construction in this module.

    allowed_extractors is SECURITY-CRITICAL, not tidiness. A post containing no
    video but carrying a link makes the extractor hand an author-chosen
    third-party URL back to yt-dlp for extraction -- after our allowlist gate
    has already passed. Restricting the loaded extractor set to exactly one is
    what closes that (research D8). It also blocks t.co and Spaces at the
    library level rather than by convention.
    """
    options = {
        "allowed_extractors": ["twitter"],
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if progress is not None:
        options["progress_hooks"] = [progress]
    return options


def _diagnose(error: DownloadError) -> str:
    """Map yt-dlp's error text to a plain explanation (FR-004).

    No custom exception hierarchy (Principle VI) -- this returns a string.
    """
    text = str(error).lower()
    for needle, explanation in _ERROR_DIAGNOSES:
        if needle in text:
            return explanation
    return f"could not extract video from this post. yt-dlp said: {error}"


def _diagnose_format(error: DownloadError, requested: str, info: dict) -> str:
    """Explain an unavailable format by naming both sides (FR-008).

    NOTE: T022 specified adding this to _ERROR_DIAGNOSES as one more ordered
    row. That is not possible -- every row in that table is a static string, and
    this message has to name the requested id and the ids actually on offer. It
    lives here instead, in front of the table rather than inside it, so the
    table and its generic fallback stay exactly as they were.

    The available list comes from the metadata pass already in hand. No second
    network call is made to build an error message.
    """
    if "requested format is not available" not in str(error).lower():
        return _diagnose(error)
    return _unavailable_format_message(requested, info)


def _unavailable_format_message(requested: str, info: dict) -> str:
    """The FR-008 message: name what was asked for and what is on offer.

    Shared by two callers that reach the same conclusion from opposite ends --
    yt-dlp raising after a download attempt, and _extension_for_format finding
    the id absent before one. Both must say the same thing.
    """
    available = ", ".join(option.format_id for option in _format_options_of(info))
    for entry in _entries_of(info):
        if available:
            break
        available = ", ".join(option.format_id for option in _format_options_of(entry))

    return (
        f"format {requested!r} is not available for this post. "
        + (f"Available formats: {available}." if available else "This post reports no formats.")
        + " Run with --list-formats to see them with sizes."
    )


def _entries_of(info: dict) -> list[dict]:
    """Normalise single-video and multi-video posts to a list.

    A multi-video post comes back as a playlist; a single-video post is a flat
    info dict (research D5).
    """
    if info.get("_type") == "playlist":
        return [entry for entry in (info.get("entries") or []) if entry]
    return [info]


def _filename_index(reference: PostReference, position: int, multiple: bool) -> int | None:
    """Which index, if any, goes in this file's name.

    An indexed URL resolves to a single entry, so `multiple` is false and the
    positional suffix would be dropped -- meaning /video/1 and /video/2 of the
    same post would both be written as <handle>-<post-id>.<ext>. FR-016 would
    then report the second as "Already downloaded" and hand the operator the
    wrong video, silently. The media index takes precedence for exactly that
    reason (FR-020, ADR-0001).
    """
    if reference.media_index is not None:
        return reference.media_index
    return position if multiple else None


def _handle_of(info: dict) -> str:
    """The author handle for the filename.

    Not taken from the URL: the i/web/status/<id> form carries no handle at all
    (research D3).
    """
    return info.get("uploader_id") or info.get("uploader") or "unknown"


def _extension_of(ydl: YoutubeDL, entry: dict) -> str:
    """The extension yt-dlp will actually write.

    Never hardcoded. merge_output_format applies only when a merge occurs, so a
    progressive rendition taken by the /best fallback keeps its native
    container -- webm, in practice. Hardcoding .mp4 would make the FR-016
    existence check test a path that never exists and re-download every run
    (research D3, corrected).

    prepare_filename substitutes the literal "NA" for a field the entry does not
    carry, so ".NA" means "no container reported" exactly as an empty suffix
    does. Both are refused rather than defaulted: an entry with no container is
    metadata we do not understand, and _promote renames whatever landed to the
    name built from this value -- so guessing .mp4 would not produce an mp4, it
    would produce a webm called .mp4. A filename that lies about its contents is
    worse than a failed download, and it is the same judgement _promote already
    makes when it refuses to pick between two files.

    Raises ValueError, which download_post lets through to the caller.
    """
    suffix = Path(ydl.prepare_filename(entry)).suffix
    if not suffix or suffix == ".NA":
        raise ValueError(
            f"yt-dlp reported no container format for video {entry.get('id') or '?'}. "
            "Refusing to guess an extension, because the resulting filename "
            "would not describe the file."
        )
    return suffix


def _extension_for_format(entry: dict, format_id: str) -> str | None:
    """The container the *requested* format will produce, not the default one.

    `_extension_of` reads the extension off the entry, which yt-dlp populated
    from the format its default selector picked. Under an explicit --format that
    is the wrong format: choosing a webm rendition would still yield ".mp4", and
    since _promote renames whatever landed to that name, the file would be a
    webm called .mp4. FR-016's existence check would then keep agreeing with the
    wrong name on every subsequent run. That is the lying-filename class
    ADR-0001 rejected, so the extension is taken from the chosen format's own
    entry in info["formats"].

    Returns None when the requested id is not among the entry's formats. That is
    the FR-008 unavailable-format case and the caller reports it as such -- there
    is deliberately no fallback guess, because a guess is the defect.

    Raises ValueError when the format exists but reports no container, matching
    _extension_of's refusal to invent one.
    """
    for candidate in entry.get("formats") or []:
        if str(candidate.get("format_id")) != format_id:
            continue
        extension = candidate.get("ext")
        if not extension or extension == "NA":
            raise ValueError(
                f"format {format_id!r} reports no container for video "
                f"{entry.get('id') or '?'}. Refusing to guess an extension, "
                "because the resulting filename would not describe the file."
            )
        return f".{extension}"
    return None


def _remove_temp_dir(temp_dir: Path, on_warning: WarningHook | None = None) -> None:
    """Delete the temp directory, retrying while the OS still holds handles.

    shutil.rmtree(..., ignore_errors=True) is not usable here. On Windows,
    deleting a file that any process still has open raises PermissionError
    (WinError 32), and ignore_errors swallows it -- leaving a .tmp-xvd-*
    directory in the operator's output directory with nothing said about it.
    That is the exact failure FR-015 exists to prevent.

    The handle is transient rather than permanent: when Ctrl+C unwinds us,
    yt-dlp has not necessarily finished releasing its own files. Its fragment
    pool shuts down with wait=False (downloader/fragment.py), and on Windows the
    console delivers Ctrl+C to ffmpeg as a process-group event concurrently with
    our unwinding rather than through us (noted in downloader/external.py). So
    the right response is to wait briefly and try again, not to give up.

    POSIX unlinks open files without complaint, so in practice only Windows ever
    consumes a retry. The warning path is reachable on both.

    Reports failure through on_warning rather than writing anywhere itself. When
    no callback is supplied the warning is dropped -- that is the caller's
    choice to make, and a caller that wants to ignore leftovers is entitled to.
    """
    for attempt in range(_CLEANUP_ATTEMPTS):
        try:
            shutil.rmtree(temp_dir)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == _CLEANUP_ATTEMPTS - 1:
                break
            time.sleep(_CLEANUP_RETRY_SECONDS)

    if on_warning is None:
        return

    # Visible leftover beats invisible leftover. Name the exact path so the
    # operator can delete it without hunting for it.
    on_warning(
        "could not remove the temporary directory. It may hold a "
        f"partial download; delete it manually: {temp_dir}"
    )


def _promote(temp_dir: Path, target: Path) -> None:
    """Move the finished file out of its temp directory, atomically.

    The temp directory is created empty and holds exactly one entry's download,
    so listing it identifies the file without assembling a path from
    assumptions. Zero or several files means an assumption broke: fail loudly
    rather than guess, because a silent wrong-file promotion is worse than an
    error.

    os.replace is atomic within a filesystem, and the temp directory is created
    inside the output directory so that is guaranteed. shutil.move is NOT used:
    it degrades to copy-then-delete across filesystems and can leave a partial
    file at the destination, violating FR-015 (research D2).
    """
    produced = [child for child in temp_dir.iterdir() if child.is_file()]
    if len(produced) != 1:
        found = ", ".join(sorted(child.name for child in produced)) or "nothing"
        raise RuntimeError(
            f"expected exactly one finished file in {temp_dir}, found {len(produced)}: {found}"
        )
    os.replace(produced[0], target)


def fetch_metadata(reference: PostReference, progress: ProgressHook | None = None) -> dict:
    """Resolve post metadata without transferring any video data.

    download=False is what lets FR-016 short-circuit before fetching content.
    """
    with YoutubeDL(_base_options(progress)) as ydl:
        return ydl.extract_info(reference.canonical_url, download=False)


def _format_options_of(info: dict) -> tuple[FormatOption, ...]:
    """Project info["formats"] onto the five fields FR-007 and research D4 name.

    A format with no format_id cannot be passed back, so it is not offered.
    Absent fields stay absent -- substituting 0 for a missing filesize would
    read as "empty file" rather than "not reported".
    """
    return tuple(
        FormatOption(
            format_id=str(entry["format_id"]),
            resolution=entry.get("resolution"),
            ext=entry.get("ext"),
            filesize_approx=entry.get("filesize") or entry.get("filesize_approx"),
            tbr=entry.get("tbr"),
        )
        for entry in (info.get("formats") or [])
        if entry and entry.get("format_id")
    )


def list_formats(url: str, progress: ProgressHook | None = None) -> FormatListing:
    """What qualities exist for this post, without downloading anything (FR-006).

    Transfers no video bytes, needs no ffmpeg, and needs no output directory --
    the caller must not create one on this path.

    Calls parse_post_url internally for the same reason download_post does: the
    Principle V gate must not be skippable. The US2 diagnoses apply here too, so
    a deleted or protected post gets an explanation rather than an empty list
    (US3 acceptance scenario 3).

    Raises ValueError for a rejected URL (FR-002).
    """
    reference = parse_post_url(url)

    try:
        info = fetch_metadata(reference, progress)
    except DownloadError as exc:
        return FormatListing("failed", (), _diagnose(exc))

    if not info:
        return FormatListing("failed", (), "this post has no video in it.")

    # A multi-video post is a playlist whose formats live on each entry, not on
    # the container -- the same branching download_post does (research D5).
    options: tuple[FormatOption, ...] = ()
    for entry in _entries_of(info):
        options += _format_options_of(entry)

    if not options:
        return FormatListing("failed", (), "this post has no video in it.")

    return FormatListing("listed", options, f"{len(options)} format(s) available.")


def download_post(
    url: str,
    output_dir: Path,
    progress: ProgressHook | None = None,
    on_warning: WarningHook | None = None,
    format_id: str | None = None,
) -> DownloadOutcome:
    """Download every video in one X post. The whole capability.

    Validation happens here, so no caller can skip the Principle V gate by
    forgetting to validate first. Writes nothing anywhere and never ends the
    process; both callbacks are the caller's to route.

    on_warning receives problems that are not failures -- currently only a temp
    directory that could not be removed. It fires from a finally block, so it
    still reports on the Ctrl+C path, which is the path it exists for.

    format_id, when given, replaces the default bestvideo+bestaudio/best. It is
    passed to yt-dlp verbatim and is never validated, normalised, or
    second-guessed here: yt-dlp owns that vocabulary (FR-008). When it is None,
    behaviour is exactly what it was before the option existed.

    Raises ValueError for a rejected URL (FR-002) or for metadata this module
    refuses to guess at -- the caller maps that to an exit status.
    """
    reference = parse_post_url(url)
    output_dir = Path(output_dir)

    try:
        info = fetch_metadata(reference, progress)
    except DownloadError as exc:
        return DownloadOutcome("failed", (), _diagnose(exc))

    if not info:
        return DownloadOutcome(
            "failed", (), "this post has no video in it."
        )

    entries = _entries_of(info)
    if not entries:
        return DownloadOutcome("failed", (), "this post has no video in it.")

    handle = _handle_of(info)
    multiple = len(entries) > 1

    completed: list[Path] = []
    skipped: list[Path] = []

    with YoutubeDL(_base_options(progress)) as probe:
        targets = []
        for position, entry in enumerate(entries, 1):
            if format_id:
                extension = _extension_for_format(entry, format_id)
                if extension is None:
                    # Caught here rather than after a download attempt: the
                    # target filename cannot be built without knowing the
                    # container, and the answer is already in hand (FR-008).
                    return _partial_failure(
                        completed, skipped, _unavailable_format_message(format_id, info)
                    )
            else:
                extension = _extension_of(probe, entry)
            targets.append(
                build_target(
                    output_dir,
                    _handle_of(entry) or handle,
                    reference.post_id,
                    extension,
                    _filename_index(reference, position, multiple),
                )
            )

    for position, target in enumerate(targets, 1):
        if target.exists:
            skipped.append(target.path)
            continue

        # ffmpeg is only needed when something will actually be downloaded, so
        # this check sits here rather than at the top: a rejected URL and an
        # idempotent skip must both work without it (research D9).
        if shutil.which("ffmpeg") is None:
            return _partial_failure(
                completed,
                skipped,
                "ffmpeg was not found on PATH. It is required to combine video and audio "
                "into one file. Install it and try again.",
            )

        # A fresh temp directory per entry. Sharing one across entries would
        # break the "exactly one finished file" invariant that _promote relies
        # on, and would mean a failure on entry 3 could disturb entries 1 and 2.
        temp_dir = Path(tempfile.mkdtemp(dir=output_dir, prefix=".tmp-xvd-"))
        try:
            options = _base_options(progress)
            options["paths"] = {"home": str(temp_dir)}
            options["outtmpl"] = {"default": "%(id)s.%(ext)s"}
            # allowed_extractors is untouched by this -- a format override must
            # not reopen research D8.
            if format_id:
                options["format"] = format_id
            # Select this one entry by position rather than re-processing the
            # already-processed info dict from the metadata pass. Re-running
            # format selection over a dict that already carries
            # requested_formats is not a documented use of the API; asking for
            # the entry by index is. Costs one extra metadata call per video.
            if multiple:
                options["playlist_items"] = str(position)
            with YoutubeDL(options) as ydl:
                retcode = ydl.download([reference.canonical_url])
            if retcode:
                raise RuntimeError(f"yt-dlp exited with code {retcode}")
            _promote(temp_dir, target.path)
        except DownloadError as exc:
            reason = (
                _diagnose_format(exc, format_id, info) if format_id else _diagnose(exc)
            )
            return _partial_failure(completed, skipped, reason)
        except KeyboardInterrupt:
            # Handled separately from a failure because it is not one, and
            # because str(KeyboardInterrupt()) is the empty string -- folding it
            # into the generic branch below printed a bare colon and named
            # nothing (FR-019).
            return _partial_failure(
                completed,
                skipped,
                f"Interrupted by the operator. Video {position} was not completed.",
            )
        except Exception as exc:  # noqa: BLE001
            # An exception carrying no text must still name what went wrong, so
            # fall back to its type rather than emitting an empty explanation.
            detail = str(exc).strip() or f"{type(exc).__name__} (no message)"
            return _partial_failure(
                completed, skipped, f"download failed for video {position}: {detail}"
            )
        finally:
            # Covers KeyboardInterrupt and every exception, so no partial file
            # survives an interruption (FR-015). The callback reaches the caller
            # from here too -- a return statement has already run in every
            # failure branch above, but finally still executes before it lands.
            _remove_temp_dir(temp_dir, on_warning)

        completed.append(target.path)

    return _success(completed, skipped, multiple)


def _partial_failure(
    completed: list[Path], skipped: list[Path], reason: str
) -> DownloadOutcome:
    """Report a failure without hiding files that did complete.

    The operator is never told "failed" while silently keeping files they do
    not know about (FR-015 per-file rule + FR-017).
    """
    kept = tuple(completed + skipped)
    if kept:
        names = ", ".join(path.name for path in kept)
        return DownloadOutcome("failed", kept, f"{reason} Files already saved: {names}")
    return DownloadOutcome("failed", (), reason)


def _success(
    completed: list[Path], skipped: list[Path], multiple: bool
) -> DownloadOutcome:
    """Everything asked for is now on disk, whether just now or previously."""
    paths = tuple(completed + skipped)
    count = f"{len(paths)} videos" if multiple else "1 video"

    if completed and skipped:
        message = (
            f"Post contains {count}. "
            f"Downloaded {len(completed)}, already had {len(skipped)}."
        )
    elif not completed:
        names = ", ".join(path.name for path in skipped)
        message = f"Already downloaded: {names}"
    elif multiple:
        message = f"Post contains {count}."
    else:
        message = f"Downloaded {paths[0].name}"

    status = "skipped" if not completed else "downloaded"
    return DownloadOutcome(status, paths, message)
