"""Tests for the job service layer -- the seam between HTTP and the downloader.

Nothing here starts an event loop, constructs an HTTP client, or imports the web
stack. That is not incidental: if this file needed `backend.api` to exercise
`backend.jobs`, the Principle III boundary would already have failed. T025 adds
the check that asserts this structurally, by walking both modules' imports.

No network and no real download. `submit()` takes a `download` seam whose only
purpose is this file: a plain function that returns a literal `DownloadOutcome`,
per Principle II's "plain fakes and stub objects only, if anything".
"""

import ast
import json
import re
import time
from pathlib import Path

import pytest

from backend import jobs
from backend.downloader import DownloadOutcome

POST_ID = "1234567890123456789"
BARE_URL = f"https://x.com/someone/status/{POST_ID}"


@pytest.fixture()
def service(tmp_path, monkeypatch):
    """A configured, empty service rooted in a temp directory.

    The registry is module state, so it is cleared explicitly -- otherwise a job
    from one test would satisfy another test's deduplication check and the
    failure would look like a logic bug rather than leakage between tests.
    """
    monkeypatch.setenv("XVD_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("XVD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("XVD_MAX_CONCURRENT", raising=False)
    jobs._registry.clear()
    jobs.init()
    yield jobs
    # wait=True, or a worker started by this test outlives it and persists its
    # record into the NEXT test's jobs directory, since _jobs_dir is module
    # state. That produced an intermittent failure in the "creates nothing"
    # assertions before it was tracked down.
    jobs.shutdown(wait=True)
    jobs._registry.clear()


def _outcome(*paths: Path, status: str = "downloaded", message: str = "ok") -> DownloadOutcome:
    return DownloadOutcome(status, tuple(paths), message)


def _stub(*paths: Path, status: str = "downloaded", message: str = "ok"):
    """A download that transfers nothing and reports what the test wants."""

    def download(url, output_dir, progress=None, on_warning=None):
        return _outcome(*paths, status=status, message=message)

    return download


def _finished(service, tmp_path, count: int = 1):
    """Run a job to completion with `count` real files on disk."""
    files = []
    for index in range(count):
        path = tmp_path / "out" / f"video-{index}.mp4"
        path.write_bytes(b"not really a video")
        files.append(path)
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub(*files))
    _await_terminal(job)
    return job, files


def _await_terminal(job, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while job.state not in jobs._TERMINAL_STATES and time.time() < deadline:
        time.sleep(0.01)
    assert job.state in jobs._TERMINAL_STATES, f"job stuck in {job.state}"


# --------------------------------------------------------------------------
# Handles (FR-027, SC-011)
# --------------------------------------------------------------------------


def test_handle_is_43_urlsafe_characters():
    """32 random bytes -> 256 bits, base64url, no padding.

    The length is asserted because it is the entropy: possession of the handle
    is the only authorization in the system (spec Q1), so a handle that quietly
    shrank would silently weaken every access decision.
    """
    handle = jobs._mint_handle()
    assert len(handle) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", handle)


def test_handles_are_unique():
    assert len({jobs._mint_handle() for _ in range(500)}) == 500


def test_handle_is_not_derived_from_anything(service):
    """Two jobs for different posts must share no structure (FR-027)."""
    first = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    second = service.submit("https://x.com/other/status/20", "203.0.113.7", download=_stub())
    assert first.handle != second.handle
    assert POST_ID not in first.handle


# --------------------------------------------------------------------------
# Submission and deduplication (FR-003, FR-016, FR-017)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        f"https://x.com/someone/status/{POST_ID}",
        f"https://mobile.twitter.com/anyone/status/{POST_ID}/",
        f"https://www.x.com/someone/status/{POST_ID}?s=20&t=abc",
        f"https://x.com/i/web/status/{POST_ID}",
    ],
)
def test_url_variants_deduplicate_to_one_job(service, variant):
    """Host, query string, trailing slash, and handle-less form are one post."""
    first = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    again = service.submit(variant, "198.51.100.4", download=_stub())
    assert again.handle == first.handle


def test_indexed_url_is_a_different_job(service):
    """/video/1 names one media item, not the post -- and yields a different file."""
    bare = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    indexed = service.submit(f"{BARE_URL}/video/1", "203.0.113.7", download=_stub())
    assert indexed.handle != bare.handle
    assert indexed.canonical_url.endswith("/video/1")


def test_finished_job_does_not_absorb_a_new_submission(service, tmp_path):
    """Deduplication covers waiting and running only.

    A finished job's file may have been deleted since, so a later submission
    must start its own job rather than be handed a stale one.
    """
    first, _ = _finished(service, tmp_path)
    again = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    assert again.handle != first.handle


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/someone/status/20",
        "https://x.com.evil.net/someone/status/20",
        "https://t.co/abcdef",
        "https://x.com/home",
        "ftp://x.com/someone/status/20",
        "",
        "   ",
    ],
)
def test_rejected_url_raises_and_creates_no_job(service, url):
    """FR-003: no job, no record, nothing reserved.

    An audit line IS written -- that is FR-031, and a run of rejections from one
    address is the pattern worth seeing. Nothing in the *jobs* directory.
    """
    with pytest.raises(ValueError):
        service.submit(url, "203.0.113.7", download=_stub())
    assert service._registry == {}
    assert list((service._jobs_dir).iterdir()) == []


def test_submission_persists_a_record(service):
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    assert (service._jobs_dir / f"{job.handle}.json").is_file()


# --------------------------------------------------------------------------
# The state machine (FR-006, data-model invariant 1)
# --------------------------------------------------------------------------


def test_job_reaches_finished_and_records_its_files(service, tmp_path):
    job, files = _finished(service, tmp_path, count=2)
    assert job.state == jobs.FINISHED
    assert job.files == tuple(files)
    assert job.failure_code is None
    assert job.started_at is not None and job.completed_at is not None


def test_skipped_is_a_success(service, tmp_path):
    """Feature 001 returns "skipped" when the file is already on disk (FR-016)."""
    path = tmp_path / "out" / "already-there.mp4"
    path.write_bytes(b"x")
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub(path, status="skipped"))
    _await_terminal(job)
    assert job.state == jobs.FINISHED


def test_failed_outcome_records_a_code_and_no_text(service):
    """The record must carry a code and must have nowhere to put the message."""
    job = service.submit(
        BARE_URL,
        "203.0.113.7",
        download=_stub(status="failed", message="this post has no video in it."),
    )
    _await_terminal(job)
    assert job.state == jobs.FAILED
    assert job.failure_code is not None
    assert not hasattr(job, "message")


def test_download_raising_does_not_wedge_the_job(service):
    """A ValueError from the frozen layer must still produce a terminal state."""

    def exploding(url, output_dir, progress=None, on_warning=None):
        raise ValueError("refusing to write outside the output directory: /etc/passwd")

    job = service.submit(BARE_URL, "203.0.113.7", download=exploding)
    _await_terminal(job)
    assert job.state == jobs.FAILED
    assert job.failure_code == jobs.UNCLASSIFIED


def test_terminal_state_is_never_left(service, tmp_path):
    """A late worker must not overwrite a verdict already recorded.

    Not hypothetical: a later phase adds a watchdog that fails an over-deadline
    job while its thread is still alive and may still return an outcome.
    """
    job, _ = _finished(service, tmp_path)
    with jobs._lock:
        applied = jobs._enter_terminal(job, jobs.FAILED, failure_code="time_limit")
    assert applied is False
    assert job.state == jobs.FINISHED
    assert job.failure_code is None


def test_progress_updates_memory_but_not_disk(service, tmp_path):
    """Progress must never trigger a write (research D3)."""
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    _await_terminal(job)
    hook = jobs._make_progress_hook(job)
    hook({"status": "downloading", "downloaded_bytes": 512, "total_bytes": 2048})
    assert job.downloaded_bytes == 512
    on_disk = json.loads((service._jobs_dir / f"{job.handle}.json").read_text())
    assert on_disk["downloaded_bytes"] is None


def test_progress_tolerates_a_missing_total(service):
    """HLS reports no total_bytes; FR-008 makes progress advisory."""
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    hook = jobs._make_progress_hook(job)
    hook({"status": "downloading", "downloaded_bytes": 100})
    assert job.total_bytes is None
    hook({"status": "downloading", "downloaded_bytes": 200, "total_bytes_estimate": 900})
    assert job.total_bytes == 900


# --------------------------------------------------------------------------
# File selection (FR-014, FR-035, FR-036)
# --------------------------------------------------------------------------


def test_single_file_needs_no_index(service, tmp_path):
    """The common case. An index is never required for a one-video post."""
    job, files = _finished(service, tmp_path, count=1)
    result = service.file_for(job)
    assert result.path == files[0]
    assert result.problem is None


def test_multi_file_without_index_refuses_and_names_the_count(service, tmp_path):
    job, _ = _finished(service, tmp_path, count=3)
    result = service.file_for(job)
    assert result.problem == "index_required"
    assert result.file_count == 3
    assert result.path is None


def test_index_selects_the_right_file(service, tmp_path):
    job, files = _finished(service, tmp_path, count=3)
    assert service.file_for(job, 1).path == files[0]
    assert service.file_for(job, 3).path == files[2]


@pytest.mark.parametrize("index", [0, -1, 4, 999])
def test_out_of_range_index_is_refused_not_clamped(service, tmp_path, index):
    """Serving file 1 to someone who asked for file 9 is the wrong-file bug."""
    job, _ = _finished(service, tmp_path, count=3)
    result = service.file_for(job, index)
    assert result.problem == "not_found"
    assert result.path is None


def test_unfinished_job_yields_no_file(service):
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    job.state = jobs.WAITING  # whatever the worker did, ask as if still queued
    result = service.file_for(job)
    assert result.problem == "not_ready"
    assert result.path is None


def test_failed_job_yields_no_file(service):
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub(status="failed"))
    _await_terminal(job)
    result = service.file_for(job)
    assert result.problem == jobs.FAILED
    assert result.path is None


def test_deleted_file_reports_expired_not_a_partial_body(service, tmp_path):
    """FR-014: never hand back a missing or empty file as if it were the video."""
    job, files = _finished(service, tmp_path, count=1)
    files[0].unlink()
    result = service.file_for(job)
    assert result.problem == jobs.EXPIRED
    assert result.path is None


# --------------------------------------------------------------------------
# Lookup (FR-028, research D3)
# --------------------------------------------------------------------------


def test_get_returns_none_for_unknown_handles(service):
    assert service.get("a" * 43) is None
    assert service.get("") is None
    assert service.get("../../etc/passwd") is None


# --------------------------------------------------------------------------
# The operator's audit trail (FR-031, FR-032)
# --------------------------------------------------------------------------


def _audit_lines(service):
    path = service._state_dir / "submissions.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_acceptance_is_audited(service):
    job = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    (line,) = _audit_lines(service)
    assert line["outcome"] == "accepted"
    assert line["client_address"] == "203.0.113.7"
    assert line["canonical_url"] == job.canonical_url
    assert line["handle"] == job.handle
    assert line["at"]


def test_deduplication_is_audited_separately(service):
    service.submit(BARE_URL, "203.0.113.7", download=_stub())
    service.submit(BARE_URL, "198.51.100.4", download=_stub())
    outcomes = [line["outcome"] for line in _audit_lines(service)]
    assert outcomes == ["accepted", "deduplicated"]
    assert _audit_lines(service)[1]["client_address"] == "198.51.100.4"


def test_rejection_is_audited_without_the_submitted_text(service):
    """FR-032: a string that failed validation is unvalidated caller free text.

    The operator gets the address and the time, which is what identifies abuse.
    They do not get an attacker-chosen string written into a file they will open
    in a terminal.
    """
    hostile = "https://evil.com/\x1b]0;pwned\x07/status/20"
    with pytest.raises(ValueError):
        service.submit(hostile, "203.0.113.7", download=_stub())

    (line,) = _audit_lines(service)
    assert line["outcome"] == "rejected_url"
    assert line["client_address"] == "203.0.113.7"
    assert line["canonical_url"] is None
    assert line["handle"] is None
    assert "evil.com" not in json.dumps(line)
    assert "pwned" not in json.dumps(line)


# --------------------------------------------------------------------------
# Classification drift (FR-010, FR-011, research D5)
#
# This section is the single guard against silent decay in the failure codes.
# The diagnosis exists only as prose in a PRIVATE table inside a module we are
# forbidden to modify, so our map duplicates knowledge we do not own. That
# coupling cannot be removed -- the clean fix is a `code` field on
# DownloadOutcome, and downloader.py is frozen -- so the whole mitigation is
# that an upstream edit must break the build instead of quietly turning every
# diagnosis into "unclassified".
#
# Importing a private name is acceptable HERE and nowhere else: this is the one
# place whose job is to notice when that private thing changes.
# --------------------------------------------------------------------------


def test_every_upstream_diagnosis_is_classified():
    """Every explanation in _ERROR_DIAGNOSES maps to exactly one code.

    If this fails, yt-dlp's error text or the frozen module's wording changed.
    Do NOT relax the assertion -- update FAILURE_PREFIXES to match, which is the
    whole point of being told.
    """
    from backend.downloader import _ERROR_DIAGNOSES

    for _needle, explanation in _ERROR_DIAGNOSES:
        matches = [code for prefix, code in jobs.FAILURE_PREFIXES if explanation.startswith(prefix)]
        assert matches, (
            f"no failure code covers the upstream diagnosis {explanation!r}. "
            "backend/downloader.py changed; update FAILURE_PREFIXES."
        )
        assert len(matches) == 1, (
            f"the diagnosis {explanation!r} matches {len(matches)} prefixes {matches}. "
            "Prefixes must be unambiguous."
        )


def test_upstream_diagnoses_do_not_all_collapse_to_one_code():
    """Coverage alone is not enough: FR-010 requires the codes to be distinct.

    A single catch-all prefix would satisfy the test above while destroying the
    thing the requirement exists for.
    """
    from backend.downloader import _ERROR_DIAGNOSES

    codes = {jobs._classify(explanation) for _needle, explanation in _ERROR_DIAGNOSES}
    assert len(codes) >= 4
    assert jobs.UNCLASSIFIED not in codes


def test_every_code_has_a_caller_safe_message():
    from_prefixes = {code for _prefix, code in jobs.FAILURE_PREFIXES}
    assert from_prefixes | jobs.SELF_ASSIGNED_CODES == set(jobs.FAILURE_MESSAGES)


def test_no_caller_message_can_leak_a_path():
    """FR-029 at the level of the catalog itself."""
    for code, message in jobs.FAILURE_MESSAGES.items():
        assert "/" not in message, code
        assert "\\" not in message, code
        assert ".." not in message, code
        assert "yt-dlp" not in message.lower(), code
        assert "ffmpeg" not in message.lower(), code


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("this post has no video in it.", "no_video"),
        ("this post contains media, but it is not a video.", "not_a_video"),
        (
            "this post belongs to a protected account and is not publicly accessible. "
            "This tool does not authenticate.",
            "protected_account",
        ),
        (
            "this post is age-restricted and is not publicly accessible. "
            "This tool does not authenticate.",
            "age_restricted",
        ),
        ("this post could not be found. It may have been deleted.", "post_unavailable"),
        (
            "ffmpeg was not found on PATH. It is required to combine video and audio "
            "into one file. Install it and try again.",
            "service_unavailable",
        ),
        ("Interrupted by the operator. Video 1 was not completed.", "interrupted"),
        ("could not extract video from this post. yt-dlp said: HTTP 429", "unclassified"),
        ("download failed for video 2: expected exactly one finished file in C:\\x", "unclassified"),
        ("something nobody has ever seen", "unclassified"),
    ],
)
def test_classification_of_each_known_message(message, expected):
    assert jobs._classify(message) == expected


def test_classification_survives_the_partial_failure_suffix():
    """_partial_failure appends to the reason (downloader.py:559).

    This is why startswith is exact rather than a guess -- and why the suffix
    must not change the code.
    """
    reason = "this post has no video in it."
    composed = f"{reason} Files already saved: someone-20-1.mp4"
    assert jobs._classify(composed) == jobs._classify(reason) == "no_video"


def test_get_never_touches_the_filesystem(service, monkeypatch):
    """A caller-supplied handle must not be able to reach the filesystem at all.

    Enforced by making `open` explode: if the lookup path opened anything, this
    fails. That is stronger than checking the traversal string is rejected,
    because it holds for inputs nobody thought to parametrize.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("get() touched the filesystem")

    monkeypatch.setattr("builtins.open", forbidden)
    assert service.get("../../../etc/passwd") is None


# --------------------------------------------------------------------------
# The Principle III boundary (T025)
#
# Feature 001 checked this with `grep -nE "argparse|sys\.exit|print\("` and the
# grep matched the module docstring, which described the constraint using the
# forbidden words. It therefore never passed as written and was signed off by
# eye (specs/001-post-video-download/tasks.md:147-151).
#
# These checks walk the syntax tree instead. An AST sees imports and calls and
# cannot see prose, so a docstring is free to state the rule honestly. They also
# live in the test suite rather than in a command someone has to remember, which
# is the other half of what went wrong last time.
# --------------------------------------------------------------------------

_BACKEND = Path(__file__).resolve().parent.parent / "backend"

# Importing any of these into the service layer would end its independence from
# the transport -- and with it the ability to write this very file.
_FRAMEWORK_ROOTS = frozenset({"fastapi", "starlette", "pydantic", "asyncio", "anyio", "uvicorn"})

# Markers of work that belongs one layer down, not in a request handler.
_LOWER_LAYER_ROOTS = frozenset({"os", "pathlib", "shutil", "tempfile", "subprocess", "yt_dlp"})


def _parse(name: str) -> ast.Module:
    return ast.parse((_BACKEND / name).read_text(encoding="utf-8"), filename=name)


def _imported(tree: ast.Module) -> set[str]:
    """Every module named by an import statement, dotted forms included."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _called_names(tree: ast.Module) -> set[str]:
    """Bare function names that are called, e.g. `open` in `open(path)`."""
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_service_layer_imports_no_framework():
    """backend/jobs.py must stay usable without a web stack or an event loop.

    This is the assertion the whole phase ordering exists to protect. If it
    fails, the service layer can no longer be exercised by plain function calls
    and every test above it becomes an integration test.
    """
    offenders = {
        module
        for module in _imported(_parse("jobs.py"))
        if module.split(".")[0] in _FRAMEWORK_ROOTS
    }
    assert not offenders, f"backend/jobs.py imports {sorted(offenders)}"


def test_ast_check_is_not_fooled_by_prose():
    """The T006 lesson, tested rather than trusted.

    A grep for these words would match this literal source; the AST walk finds
    no imports in it, because a docstring is not an import statement.
    """
    prose_only = ast.parse(
        '"""This module must never import fastapi, asyncio, or pydantic."""\n'
        "import json\n"
        "# fastapi is also mentioned here, in a comment\n"
    )
    assert _imported(prose_only) == {"json"}
    assert "fastapi" in ast.get_docstring(prose_only)


def test_transport_layer_calls_the_service_layer():
    assert "backend.jobs" in _imported(_parse("api.py"))


def test_transport_layer_does_not_reach_past_the_service_layer():
    """api.py parses, calls, and serialises. Filesystem work belongs below it."""
    offenders = {
        module
        for module in _imported(_parse("api.py"))
        if module.split(".")[0] in _LOWER_LAYER_ROOTS
    }
    assert not offenders, f"backend/api.py imports {sorted(offenders)}"
    assert "open" not in _called_names(_parse("api.py"))


def test_transport_layer_has_no_loops():
    """A loop in a handler is iteration over domain data, which is logic.

    Cheap and blunt on purpose: if formatting a response ever genuinely needs a
    loop, that is worth a second look rather than a silent allowance.
    """
    tree = _parse("api.py")
    loops = [node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))]
    assert not loops, f"backend/api.py contains {len(loops)} loop(s)"


def test_frozen_modules_are_not_imported_for_writing():
    """jobs.py consumes the frozen modules; it must not reach into their privates."""
    imported = _imported(_parse("jobs.py"))
    private = {name for name in imported if name.rsplit(".", 1)[-1].startswith("_")}
    assert not private, f"backend/jobs.py imports private names {sorted(private)}"
