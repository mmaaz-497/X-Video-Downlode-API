"""Tests for the job service layer -- the seam between HTTP and the downloader.

Nothing here starts an event loop, constructs an HTTP client, or imports the web
stack. That is not incidental: if this file needed `backend.api` to exercise
`backend.jobs`, the Principle III boundary would already have failed. T025 adds
the check that asserts this structurally, by walking both modules' imports.

No network and no real download. `submit()` takes a `download` seam whose only
purpose is this file: a plain function that returns a literal `DownloadOutcome`,
per Principle II's "plain fakes and stub objects only, if anything".
"""

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
def test_rejected_url_raises_and_creates_nothing(service, url):
    """FR-003: no job, no record, nothing reserved."""
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
