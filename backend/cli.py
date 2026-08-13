"""Terminal entrypoint. Argument parsing, output formatting, exit codes.

No business logic lives here (Constitution Principle III). This module knows how
to talk to a terminal; downloader.py knows how to download.
"""

import argparse
import sys
from pathlib import Path

from backend.config import output_dir
from backend.downloader import download_post, list_formats
from backend.validation import parse_post_url

_EXIT_OK = 0
_EXIT_FAILED = 1

# Maps a DownloadOutcome.status to a process exit code (FR-018). Already having
# the file is success, per FR-016.
_EXIT_CODES = {
    "downloaded": _EXIT_OK,
    "skipped": _EXIT_OK,
    "failed": _EXIT_FAILED,
}


# Every progress rewrite is padded to this width. A carriage return moves the
# cursor without erasing, so a short line drawn over a longer one leaves the
# previous line's tail on screen ("done, combining streams...   iB/s").
_PROGRESS_WIDTH = 60


def _format_bytes(count: float | None) -> str:
    if not count:
        return "?"
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GiB"


def _make_progress_hook():
    """A progress line on stderr (FR-014).

    stdout carries output paths only, so `xvd <url> > paths.txt` yields a file
    with nothing but paths in it.
    """

    def hook(status: dict) -> None:
        state = status.get("status")
        if state == "downloading":
            done = status.get("downloaded_bytes") or 0
            # HLS formats report no total_bytes, so fall back to the estimate
            # and then to showing progress without a percentage. Never divide
            # by None (research D7).
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            speed = status.get("speed")
            speed_text = f"  {_format_bytes(speed)}/s" if speed else ""
            if total:
                percent = done / total * 100
                line = f"  {percent:5.1f}%  {_format_bytes(done)}/{_format_bytes(total)}{speed_text}"
            else:
                line = f"  {_format_bytes(done)}{speed_text}"
            print(f"\r{line:<{_PROGRESS_WIDTH}}", end="", file=sys.stderr, flush=True)
        elif state == "finished":
            done_line = "  done, combining streams..."
            print(f"\r{done_line:<{_PROGRESS_WIDTH}}", file=sys.stderr, flush=True)

    return hook


def _as_sentence(message: str) -> str:
    """Capitalise the first character, leaving the rest untouched.

    str.capitalize() would lowercase everything after it, destroying "X",
    "yt-dlp", and filenames inside the message.
    """
    return message[:1].upper() + message[1:] if message else message


def _make_warning_hook():
    """Warnings from the core, rendered for a terminal.

    downloader.py hands over the text and nothing else; that it goes to stderr
    behind a "Warning:" prefix is a presentation decision, and presentation
    lives here (Principle III) -- the same split as the progress hook above.

    stderr, not stdout: a leftover temp directory is a diagnostic, and stdout
    carries output paths only.
    """

    def hook(message: str) -> None:
        print(f"Warning: {message}", file=sys.stderr, flush=True)

    return hook


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xvd",
        description="Download the video from a single X (Twitter) post.",
    )
    parser.add_argument("url", help="an X or Twitter post URL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="where to write the video (default: $XVD_OUTPUT_DIR, else the current directory)",
    )
    # Listing and choosing are opposite ends of the same workflow; asking for
    # both in one invocation is a mistake, not a preference. argparse rejects it
    # with exit 2, which is the usage-error code the contract already reserves.
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--list-formats",
        action="store_true",
        help="list the available quality options and exit, downloading nothing",
    )
    selection.add_argument(
        "--format",
        dest="format_id",
        default=None,
        metavar="ID",
        help="download this specific format id (see --list-formats)",
    )
    return parser


def _run_listing(url: str) -> int:
    """--list-formats: print the options and write nothing at all (FR-006)."""
    print("Fetching metadata...", file=sys.stderr)
    listing = list_formats(url, progress=None)

    if listing.status == "failed":
        print(f"Error: {_as_sentence(listing.message)}", file=sys.stderr)
        return _EXIT_FAILED

    # stdout, deliberately. The rule is that stdout carries the tool's output and
    # nothing else (FR-018, SC-007); on this path the listing IS the output,
    # since no file is produced and there are no paths to print.
    print(f"{'ID':<18} {'RESOLUTION':<14} {'EXT':<6} {'SIZE':>10}  TBR")
    for option in listing.formats:
        size = _format_bytes(option.filesize_approx)
        tbr = f"{option.tbr:.0f}k" if option.tbr else "?"
        print(
            f"{option.format_id:<18} {option.resolution or '?':<14} "
            f"{option.ext or '?':<6} {size:>10}  {tbr}"
        )

    print(_as_sentence(listing.message), file=sys.stderr)
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        # Validate before announcing a fetch, so a rejected URL never prints
        # "Fetching metadata..." and imply a network request that FR-002
        # guarantees did not happen. download_post validates again internally;
        # that is the gate no caller can skip, and this call does not replace it.
        parse_post_url(args.url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return _EXIT_FAILED

    # Before output_dir(), which CREATES the directory as a side effect. Listing
    # must write nothing anywhere, including a stray empty directory, so this
    # path short-circuits first.
    if args.list_formats:
        try:
            return _run_listing(args.url)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return _EXIT_FAILED
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return _EXIT_FAILED

    try:
        destination = output_dir(args.output_dir)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return _EXIT_FAILED

    try:
        print("Fetching metadata...", file=sys.stderr)
        outcome = download_post(
            args.url,
            destination,
            progress=_make_progress_hook(),
            on_warning=_make_warning_hook(),
            format_id=args.format_id,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return _EXIT_FAILED
    except KeyboardInterrupt:
        print("\nInterrupted. No partial file was left behind.", file=sys.stderr)
        return _EXIT_FAILED

    # _diagnose returns lowercase-leading text on purpose, so _partial_failure
    # can embed it mid-sentence ("{reason} Files already saved: ..."). Capitalise
    # here instead: this is the only place the message becomes a standalone line.
    print(_as_sentence(outcome.message), file=sys.stderr)
    for path in outcome.paths:
        print(path, file=sys.stdout)

    return _EXIT_CODES[outcome.status]


if __name__ == "__main__":
    raise SystemExit(main())
