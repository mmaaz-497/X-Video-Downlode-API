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
import os
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


def _accept(service, url=BARE_URL, address="203.0.113.7", **kwargs):
    """Submit, assert the submission was accepted, and return the job.

    submit() returns a SubmitResult so that four different refusals can cross
    the service boundary without a custom exception hierarchy. Most tests here
    are about what happens *after* acceptance, and this helper says so at each
    call site -- which the bare `.job` never did.
    """
    kwargs.setdefault("download", _stub())
    result = service.submit(url, address, **kwargs)
    assert result.problem is None, f"submission refused: {result.problem}"
    assert result.job is not None
    return result.job


def _finished(service, tmp_path, count: int = 1):
    """Run a job to completion with `count` real files on disk."""
    files = []
    for index in range(count):
        path = tmp_path / "out" / f"video-{index}.mp4"
        path.write_bytes(b"not really a video")
        files.append(path)
    job = _accept(service, download=_stub(*files))
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
    first = _accept(service)
    second = _accept(service, "https://x.com/other/status/20")
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
    first = _accept(service)
    again = _accept(service, variant, "198.51.100.4")
    assert again.handle == first.handle


def test_indexed_url_is_a_different_job(service):
    """/video/1 names one media item, not the post -- and yields a different file."""
    bare = _accept(service)
    indexed = _accept(service, f"{BARE_URL}/video/1")
    assert indexed.handle != bare.handle
    assert indexed.canonical_url.endswith("/video/1")


def test_finished_job_does_not_absorb_a_new_submission(service, tmp_path):
    """Deduplication covers waiting and running only.

    A finished job's file may have been deleted since, so a later submission
    must start its own job rather than be handed a stale one.
    """
    first, _ = _finished(service, tmp_path)
    again = _accept(service)
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
def test_rejected_url_creates_no_job(service, url):
    """FR-003: no job, no record, nothing reserved.

    An audit line IS written -- that is FR-031, and a run of rejections from one
    address is the pattern worth seeing. Nothing in the *jobs* directory.
    """
    result = service.submit(url, "203.0.113.7", download=_stub())
    assert result.problem == jobs.INVALID_URL
    assert result.job is None
    assert service._registry == {}
    assert list((service._jobs_dir).iterdir()) == []


def test_submission_persists_a_record(service):
    job = _accept(service)
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
    job = _accept(service, download=_stub(path, status="skipped"))
    _await_terminal(job)
    assert job.state == jobs.FINISHED


def test_failed_outcome_records_a_code_and_no_text(service):
    """The record must carry a code and must have nowhere to put the message."""
    job = _accept(
        service,
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

    job = _accept(service, download=exploding)
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
    job = _accept(service)
    _await_terminal(job)
    hook = jobs._make_progress_hook(job)
    hook({"status": "downloading", "downloaded_bytes": 512, "total_bytes": 2048})
    assert job.downloaded_bytes == 512
    on_disk = json.loads((service._jobs_dir / f"{job.handle}.json").read_text())
    assert on_disk["downloaded_bytes"] is None


def test_progress_tolerates_a_missing_total(service):
    """HLS reports no total_bytes; FR-008 makes progress advisory."""
    job = _accept(service)
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
    job = _accept(service)
    job.state = jobs.WAITING  # whatever the worker did, ask as if still queued
    result = service.file_for(job)
    assert result.problem == "not_ready"
    assert result.path is None


def test_failed_job_yields_no_file(service):
    job = _accept(service, download=_stub(status="failed"))
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
    job = _accept(service)
    (line,) = _audit_lines(service)
    assert line["outcome"] == "accepted"
    assert line["client_address"] == "203.0.113.7"
    assert line["canonical_url"] == job.canonical_url
    assert line["handle"] == job.handle
    assert line["at"]


def test_deduplication_is_audited_separately(service):
    _accept(service)
    _accept(service, address="198.51.100.4")
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
    assert service.submit(hostile, "203.0.113.7", download=_stub()).problem == jobs.INVALID_URL

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
# Resource protection (US4: FR-015, FR-018, FR-019, FR-020)
#
# Not one time.sleep in this section. The clock and the free-space reading are
# both defaulted parameters on submit(), so an hour passes by handing in a
# different lambda. A suite that waited through a rate-limit window would take
# an hour to tell you the window works.
# --------------------------------------------------------------------------


def _clock(start: float = 1_000_000.0):
    """A clock a test can wind forward."""

    class Clock:
        now = start

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    return Clock()


def test_rate_limit_refuses_beyond_the_allowance(service, monkeypatch):
    monkeypatch.setenv("XVD_RATE_LIMIT", "3")
    service.init()
    clock = _clock()

    for index in range(3):
        result = service.submit(
            f"{BARE_URL}{index}", "203.0.113.7", download=_stub(), now=clock
        )
        assert result.problem is None

    refused = service.submit(f"{BARE_URL}9", "203.0.113.7", download=_stub(), now=clock)
    assert refused.problem == jobs.RATE_LIMITED
    assert refused.job is None


def test_rate_limit_reports_a_usable_retry_after(service, monkeypatch):
    """FR-019: a refusal must state when the caller may retry."""
    monkeypatch.setenv("XVD_RATE_LIMIT", "1")
    monkeypatch.setenv("XVD_RATE_WINDOW", "600")
    service.init()
    clock = _clock()

    _accept(service, now=clock)
    clock.advance(100)
    refused = service.submit(BARE_URL, "203.0.113.7", download=_stub(), now=clock)

    assert refused.problem == jobs.RATE_LIMITED
    # 600 - 100 elapsed. An integer, positive, and never larger than the window.
    assert refused.retry_after == 500
    assert isinstance(refused.retry_after, int)


def test_rate_limit_window_slides(service, monkeypatch):
    """Past the window, the allowance returns -- without any real time passing."""
    monkeypatch.setenv("XVD_RATE_LIMIT", "1")
    monkeypatch.setenv("XVD_RATE_WINDOW", "600")
    service.init()
    clock = _clock()

    _accept(service, now=clock)
    assert service.submit(BARE_URL, "203.0.113.7", download=_stub(), now=clock).problem

    clock.advance(601)
    assert _accept(service, now=clock)


def test_rate_limit_is_per_address(service, monkeypatch):
    """One caller exhausting their allowance must not refuse everyone else."""
    monkeypatch.setenv("XVD_RATE_LIMIT", "1")
    service.init()
    clock = _clock()

    _accept(service, now=clock)
    assert service.submit(
        f"{BARE_URL}1", "203.0.113.7", download=_stub(), now=clock
    ).problem == jobs.RATE_LIMITED
    assert _accept(service, f"{BARE_URL}2", "198.51.100.4", now=clock)


def test_rate_limit_counts_invalid_urls(service, monkeypatch):
    """A deliberate deviation from FR-019's literal "jobs created" wording.

    An uncounted invalid-URL path is the cheapest abuse route available: a
    validation pass and an audit append per request, free forever. This test
    exists so that inverting the decision is a failing test and a conversation,
    not a silent drift.
    """
    monkeypatch.setenv("XVD_RATE_LIMIT", "2")
    service.init()
    clock = _clock()

    assert service.submit(
        "https://evil.com/a/status/20", "203.0.113.7", download=_stub(), now=clock
    ).problem == jobs.INVALID_URL
    assert service.submit(
        "https://evil.com/b/status/20", "203.0.113.7", download=_stub(), now=clock
    ).problem == jobs.INVALID_URL

    # The allowance is spent, so a perfectly valid URL is now refused.
    assert service.submit(
        BARE_URL, "203.0.113.7", download=_stub(), now=clock
    ).problem == jobs.RATE_LIMITED


def test_rate_limit_counts_deduplicated_submissions(service, monkeypatch):
    """Same decision, the other case it changes."""
    monkeypatch.setenv("XVD_RATE_LIMIT", "2")
    service.init()
    clock = _clock()

    _accept(service, now=clock)
    _accept(service, now=clock)  # deduplicated onto the first, still counted

    assert service.submit(
        f"{BARE_URL}9", "203.0.113.7", download=_stub(), now=clock
    ).problem == jobs.RATE_LIMITED


def test_rate_limited_submission_is_audited_without_the_url(service, monkeypatch):
    """FR-032: the check runs before validation, so the URL is still caller text."""
    monkeypatch.setenv("XVD_RATE_LIMIT", "1")
    service.init()
    clock = _clock()

    _accept(service, now=clock)
    service.submit(BARE_URL, "203.0.113.7", download=_stub(), now=clock)

    line = _audit_lines(service)[-1]
    assert line["outcome"] == "rate_limited"
    assert line["client_address"] == "203.0.113.7"
    assert line["canonical_url"] is None
    assert line["handle"] is None


def test_rate_buckets_are_pruned_by_the_sweep(service):
    """Otherwise the address map is a slow leak on a public service."""
    clock = _clock()
    _accept(service, now=clock)
    assert "203.0.113.7" in service._rate_buckets

    clock.advance(service._rate_window + 1)
    service.sweep(now=clock)
    assert service._rate_buckets == {}


# --- The free-disk guard (FR-018) -----------------------------------------


def test_disk_below_threshold_refuses_and_creates_nothing(service):
    result = service.submit(
        BARE_URL, "203.0.113.7", download=_stub(), free_space=lambda: 1024
    )
    assert result.problem == jobs.DISK_LOW
    assert result.job is None
    assert service._registry == {}
    assert list(service._jobs_dir.iterdir()) == []


def test_disk_refusal_is_audited_with_the_url(service):
    """Unlike a rate-limited refusal: validation has passed, so there is a
    canonical form to record."""
    service.submit(BARE_URL, "203.0.113.7", download=_stub(), free_space=lambda: 1024)
    (line,) = _audit_lines(service)
    assert line["outcome"] == "disk_low"
    assert line["canonical_url"].endswith(POST_ID)
    assert line["handle"] is None


def test_disk_guard_is_disabled_by_a_zero_threshold(service, monkeypatch):
    monkeypatch.setenv("XVD_MIN_FREE_BYTES", "0")
    service.init()
    assert service._free_bytes() is None


def test_disk_measurement_failure_fails_open(service):
    """A monitoring problem must not become an outage.

    If the volume cannot be measured, the guard declines to decide rather than
    refusing every submission -- FR-018 guards against filling the disk, not
    against uncertainty about it.
    """

    def unmeasurable(_path):
        raise OSError("no such device")

    assert service._free_bytes(unmeasurable) is None
    assert _accept(service, free_space=lambda: service._free_bytes(unmeasurable))


# --- The pending-depth cap (amended FR-015) --------------------------------


def test_pending_cap_refuses_beyond_the_depth(service, monkeypatch):
    monkeypatch.setenv("XVD_MAX_PENDING", "2")
    service.init()

    # Jobs parked in `waiting` without a worker to pick them up.
    for index in range(2):
        job = jobs.Job(
            handle=jobs._mint_handle(),
            canonical_url=f"https://x.com/i/web/status/{index}",
            client_address="203.0.113.7",
        )
        service._registry[job.handle] = job

    result = service.submit(BARE_URL, "203.0.113.7", download=_stub())
    assert result.problem == jobs.AT_CAPACITY
    assert result.job is None


def test_deduplicated_submission_is_accepted_at_capacity(service, monkeypatch):
    """The cap protects against NEW work. A dedup hit creates none.

    Refusing it would punish a caller for work the service had already decided
    to do, and would hand them nothing when a usable handle already exists.
    """
    monkeypatch.setenv("XVD_MAX_PENDING", "1")
    service.init()

    existing = jobs.Job(
        handle=jobs._mint_handle(),
        canonical_url=f"https://x.com/i/web/status/{POST_ID}",
        client_address="203.0.113.7",
    )
    service._registry[existing.handle] = existing

    result = service.submit(BARE_URL, "198.51.100.4", download=_stub())
    assert result.problem is None
    assert result.job is existing


def test_capacity_refusal_is_audited(service, monkeypatch):
    monkeypatch.setenv("XVD_MAX_PENDING", "1")
    service.init()
    parked = jobs.Job(
        handle=jobs._mint_handle(),
        canonical_url="https://x.com/i/web/status/1",
        client_address="203.0.113.7",
    )
    service._registry[parked.handle] = parked

    service.submit(BARE_URL, "203.0.113.7", download=_stub())
    (line,) = _audit_lines(service)
    assert line["outcome"] == "at_capacity"
    assert line["canonical_url"] is not None


# --- The job time limit and the watchdog (FR-020) --------------------------


def test_deadline_raises_from_the_progress_hook(service, monkeypatch):
    """The download is aborted from inside, which is the only lever the frozen
    boundary offers (research D4)."""
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "60")
    service.init()
    clock = _clock()

    def slow(url, output_dir, progress=None, on_warning=None):
        progress({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
        clock.advance(61)
        progress({"status": "downloading", "downloaded_bytes": 20, "total_bytes": 100})
        raise AssertionError("the hook should have aborted this download")

    job = _accept(service, download=slow, now=clock)
    _await_terminal(job)
    assert job.state == jobs.FAILED
    assert job.failure_code == jobs.TIME_LIMIT


def test_deadline_uses_its_own_flag_not_the_message(service, monkeypatch):
    """download_post wraps text as "download failed for video N: ..." and no
    classifier should have to reverse-engineer that."""
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "60")
    service.init()
    clock = _clock()
    job = jobs.Job(handle="x", canonical_url=BARE_URL, client_address="203.0.113.7")
    job.started_at = clock.now
    hook = service._make_progress_hook(job, clock)

    hook({"status": "downloading", "downloaded_bytes": 1})
    assert job.timed_out is False

    clock.advance(61)
    with pytest.raises(RuntimeError):
        hook({"status": "downloading", "downloaded_bytes": 2})
    assert job.timed_out is True
    assert service._abort_code(job) == jobs.TIME_LIMIT


def test_deadline_error_is_not_an_oserror(service):
    """It must not subclass OSError or a network error, or yt-dlp's handler at
    YoutubeDL.py:3597 would swallow it instead of aborting."""
    job = jobs.Job(handle="x", canonical_url=BARE_URL, client_address="203.0.113.7")
    job.started_at = 0.0
    hook = service._make_progress_hook(job, lambda: 1e12)
    with pytest.raises(RuntimeError) as caught:
        hook({"status": "downloading"})
    assert not isinstance(caught.value, OSError)


def test_watchdog_fails_a_job_whose_worker_never_reports(service, monkeypatch):
    """The wedged-ffmpeg case: no hook fires, so nothing raises, and without the
    watchdog the caller polls "running" forever."""
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "60")
    service.init()
    clock = _clock()

    job = jobs.Job(handle=jobs._mint_handle(), canonical_url=BARE_URL, client_address="203.0.113.7")
    job.state = jobs.RUNNING
    job.started_at = clock.now
    service._registry[job.handle] = job

    clock.advance(61)
    service.sweep(now=clock)

    assert job.state == jobs.FAILED
    assert job.failure_code == jobs.TIME_LIMIT


def test_watchdog_leaves_a_job_inside_its_deadline_alone(service, monkeypatch):
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "600")
    service.init()
    clock = _clock()

    job = jobs.Job(handle=jobs._mint_handle(), canonical_url=BARE_URL, client_address="203.0.113.7")
    job.state = jobs.RUNNING
    job.started_at = clock.now
    service._registry[job.handle] = job

    clock.advance(60)
    service.sweep(now=clock)
    assert job.state == jobs.RUNNING


def test_a_late_worker_cannot_overwrite_the_watchdog(service, monkeypatch):
    """The race T003's terminal guard was built for stops being hypothetical here."""
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "60")
    service.init()
    clock = _clock()

    job = jobs.Job(handle=jobs._mint_handle(), canonical_url=BARE_URL, client_address="203.0.113.7")
    job.state = jobs.RUNNING
    job.started_at = clock.now
    service._registry[job.handle] = job

    clock.advance(61)
    service.sweep(now=clock)

    # The thread finally returns with a success it can no longer claim.
    service._record_outcome(job, _outcome(Path("late.mp4")))
    assert job.state == jobs.FAILED
    assert job.failure_code == jobs.TIME_LIMIT
    assert job.files == ()


def test_wedged_worker_is_counted_until_the_thread_returns(service, monkeypatch):
    """The accepted limitation of ADR-0002, made visible rather than mysterious."""
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "60")
    service.init()
    clock = _clock()

    job = jobs.Job(handle=jobs._mint_handle(), canonical_url=BARE_URL, client_address="203.0.113.7")
    job.state = jobs.RUNNING
    job.started_at = clock.now
    service._registry[job.handle] = job

    assert service.health()["wedged_workers"] == 0

    clock.advance(61)
    service.sweep(now=clock)
    assert service.health()["wedged_workers"] == 1
    assert service.health()["status"] == "degraded"

    service._clear_wedged(job.handle)
    assert service.health()["wedged_workers"] == 0
    assert service.health()["status"] == "ok"


def test_health_reports_counts_and_nothing_identifying(service):
    """Unauthenticated and reachable by anyone: totals only."""
    job = _accept(service)
    _await_terminal(job)
    body = service.health()

    assert set(body) == {"status", "running", "waiting", "wedged_workers"}
    serialised = json.dumps(body)
    assert job.handle not in serialised
    assert "x.com" not in serialised
    assert "203.0.113.7" not in serialised


# --------------------------------------------------------------------------
# Retention (US5: FR-021, FR-022, FR-023)
#
# Same rule as the section above: no time.sleep. A whole retention period passes
# by handing sweep() a different clock. The delete is a seam too, so the Windows
# file-handle failure can be exercised on any platform rather than only where it
# happens to occur.
# --------------------------------------------------------------------------


def test_finished_job_past_retention_is_expired_and_its_file_deleted(service, tmp_path):
    job, files = _finished(service, tmp_path)
    clock = _clock(job.completed_at)

    clock.advance(service._retention + 1)
    service.sweep(now=clock)

    assert job.state == jobs.EXPIRED
    assert not files[0].exists()
    assert service.file_for(job).problem == jobs.EXPIRED


def test_job_inside_the_retention_period_is_untouched(service, tmp_path):
    """US5 acceptance scenario 2: state, file, and record all unchanged."""
    job, files = _finished(service, tmp_path)
    clock = _clock(job.completed_at)

    clock.advance(service._retention - 1)
    service.sweep(now=clock)

    assert job.state == jobs.FINISHED
    assert files[0].exists()
    assert service.file_for(job).path == files[0]


def test_expired_is_distinguishable_from_failed(service, tmp_path):
    """FR-022. A caller who came back too late has not had a failure."""
    job, _ = _finished(service, tmp_path)
    clock = _clock(job.completed_at)
    clock.advance(service._retention + 1)
    service.sweep(now=clock)

    assert job.state == jobs.EXPIRED
    assert job.state != jobs.FAILED
    # Invariant 4: failure_code is set if and only if the state is failed.
    assert job.failure_code is None


def test_retention_is_measured_from_completion_not_file_age(service, tmp_path):
    """spec.md:213-216. A job that finished instantly by reusing a file an
    earlier CLI run left behind still gets a full period from ITS completion."""
    old_file = tmp_path / "out" / "left-over.mp4"
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_bytes(b"downloaded last week")
    os.utime(old_file, (0, 0))  # ancient on disk

    job = _accept(service, download=_stub(old_file, status="skipped"))
    _await_terminal(job)

    clock = _clock(job.completed_at)
    clock.advance(service._retention - 1)
    service.sweep(now=clock)

    assert job.state == jobs.FINISHED
    assert old_file.exists()


@pytest.mark.parametrize("state", ["waiting", "running", "failed"])
def test_only_finished_jobs_are_ever_expired(service, state):
    """However old they are. Expiry is about a file that exists to delete."""
    job = jobs.Job(
        handle=jobs._mint_handle(), canonical_url=BARE_URL, client_address="203.0.113.7"
    )
    job.state = state
    job.completed_at = 0.0  # as old as it gets
    job.started_at = 0.0
    service._registry[job.handle] = job

    service.sweep(now=lambda: service._retention * 100)

    # The claim is about expiry specifically. A `running` job IS legitimately
    # changed by this sweep -- the watchdog fails it -- so asserting the state
    # is unchanged would be either wrong or vacuous depending on the case. What
    # must hold for all three is that none of them became `expired`.
    assert job.state != jobs.EXPIRED
    if state != "running":
        assert job.state == state


def test_the_expired_record_survives_a_read(service, tmp_path):
    """Mark-before-delete has to be durable, not only in memory."""
    job, _ = _finished(service, tmp_path)
    clock = _clock(job.completed_at)
    clock.advance(service._retention + 1)
    service.sweep(now=clock)

    on_disk = json.loads((service._jobs_dir / f"{job.handle}.json").read_text())
    assert on_disk["state"] == "expired"


# --- Mark-before-delete, and the tolerated Windows delete (FR-023) ---------


def test_the_mark_happens_before_the_delete(service, tmp_path):
    """FR-023's ordering, observed rather than assumed.

    The unlink seam checks the job's state at the moment it is called. If the
    delete ran first, the state would still be `finished` here -- and a caller
    who asked for the file in that window would be handed a path to a file that
    was disappearing, which is precisely the truncated-response failure the
    requirement exists to prevent.
    """
    job, _ = _finished(service, tmp_path)
    observed = []

    def watching_unlink(path):
        observed.append(job.state)
        path.unlink()

    clock = _clock(job.completed_at)
    clock.advance(service._retention + 1)
    service.sweep(now=clock, unlink=watching_unlink)

    assert observed == [jobs.EXPIRED], "the file was deleted before the job was marked"


def test_a_retrieval_that_started_first_is_not_cut_off(service, tmp_path):
    """A reader holding the file must finish reading it.

    On POSIX, unlink removes the directory entry while an open handle goes on
    working, so a response already streaming completes intact. On Windows the
    unlink fails instead, which the sweep tolerates -- and the reader likewise
    finishes. Both platforms end with the reader whole; only the disk differs.
    """
    job, files = _finished(service, tmp_path)
    expected = files[0].read_bytes()

    clock = _clock(job.completed_at)
    clock.advance(service._retention + 1)

    # The retrieval resolves its path BEFORE the sweep runs -- the window
    # FR-023 is about.
    resolved = service.file_for(job).path
    assert resolved is not None

    with open(resolved, "rb") as reader:
        first = reader.read(4)

        def platform_unlink(path):
            try:
                path.unlink()
            except PermissionError:
                # Windows, with the handle above still open. Tolerated.
                pass

        service.sweep(now=clock, unlink=platform_unlink)

        # The reader carries on regardless of which platform this is.
        rest = reader.read()

    assert first + rest == expected
    assert job.state == jobs.EXPIRED


def test_a_delete_that_fails_is_tolerated_and_retried(service, tmp_path, caplog):
    """The Windows file-handle case, exercised on any platform.

    The sweep must not crash, the job must be expired anyway, and the failure
    must be VISIBLE -- a file that fails to delete on every pass forever is a
    real leak, and swallowing it silently would hide that.
    """
    job, files = _finished(service, tmp_path)
    clock = _clock(job.completed_at)
    clock.advance(service._retention + 1)

    def refusing_unlink(path):
        raise PermissionError(32, "The process cannot access the file")

    with caplog.at_level("WARNING", logger="xvd.jobs"):
        service.sweep(now=clock, unlink=refusing_unlink)

    assert job.state == jobs.EXPIRED
    assert files[0].exists()  # still there; the delete really did fail
    assert any("could not be deleted yet" in record.message for record in caplog.records)

    # The next pass retries, with no extra state needed to remember to.
    attempts = []
    service.sweep(now=clock, unlink=lambda path: attempts.append(path) or path.unlink())
    assert attempts == [files[0]]
    assert not files[0].exists()


def test_a_file_already_gone_is_not_reported_as_a_problem(service, tmp_path, caplog):
    """An operator deleted it, or a previous pass did. Nothing to say."""
    job, files = _finished(service, tmp_path)
    files[0].unlink()

    clock = _clock(job.completed_at)
    clock.advance(service._retention + 1)
    with caplog.at_level("WARNING", logger="xvd.jobs"):
        service.sweep(now=clock, unlink=_unlink_for_test)

    assert job.state == jobs.EXPIRED
    assert not [r for r in caplog.records if "could not be deleted" in r.message]


def _unlink_for_test(path):
    path.unlink()


def test_expire_refuses_any_transition_other_than_from_finished(service):
    """The one exception to invariant 1 must stay exactly one exception."""
    job = jobs.Job(
        handle=jobs._mint_handle(), canonical_url=BARE_URL, client_address="203.0.113.7"
    )

    for state in (jobs.WAITING, jobs.RUNNING, jobs.FAILED, jobs.EXPIRED):
        job.state = state
        assert service._expire(job) is False
        assert job.state == state

    job.state = jobs.FINISHED
    assert service._expire(job) is True
    assert job.state == jobs.EXPIRED


def test_enter_terminal_still_refuses_to_leave_a_terminal_state(service, tmp_path):
    """_expire is a second function precisely so this stays true.

    If expiry had been added as a flag on _enter_terminal, the guard the
    watchdog race depends on would have become one that can be talked into
    leaving a terminal state.
    """
    job, _ = _finished(service, tmp_path)
    with jobs._lock:
        assert jobs._enter_terminal(job, jobs.EXPIRED) is False
    assert job.state == jobs.FINISHED


# --------------------------------------------------------------------------
# Restart recovery (US6: FR-024 read side, FR-025, FR-026)
#
# No subprocess and no real restart. A restart IS "a fresh registry plus the
# state directory the last process left", so these tests write records, clear
# the registry, and call recover() -- which exercises exactly the code a real
# boot runs, without the six seconds a real boot costs.
# --------------------------------------------------------------------------


def _write_record(service, **overrides):
    """Put a job record on disk the way a previous process would have left it."""
    handle = overrides.pop("handle", None) or jobs._mint_handle()
    job = jobs.Job(
        handle=handle,
        canonical_url=BARE_URL,
        client_address="203.0.113.7",
    )
    for name, value in overrides.items():
        setattr(job, name, value)
    service.persist(job)
    return job


def _restart(service):
    """What a new process sees: no memory, the same disk."""
    service._registry.clear()
    return service.recover()


def _record_on_disk(service, handle):
    return json.loads((service._jobs_dir / f"{handle}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("state", ["waiting", "running"])
def test_a_non_terminal_job_is_recovered_as_interrupted(service, state):
    """FR-025: nothing may report running forever."""
    written = _write_record(service, state=state, started_at=1_000_000.0)

    assert _restart(service) == 1
    job = service.get(written.handle)

    assert job.state == jobs.FAILED
    assert job.failure_code == jobs.INTERRUPTED
    assert job.completed_at is not None
    assert jobs.FAILURE_MESSAGES[job.failure_code]


@pytest.mark.parametrize("state", ["waiting", "running"])
def test_the_interrupted_verdict_reaches_disk_inside_recover(service, state):
    """The write-back clause, which is the whole reason recovery persists.

    Without it the registry would say `failed` while the file still said
    `running`, and a second crash before any later write would read the job
    back as running again -- ping-ponging across restarts forever.

    Asserted by re-reading the FILE, not the registry. Reading the registry
    would pass whether or not anything was written.
    """
    written = _write_record(service, state=state, started_at=1_000_000.0)
    _restart(service)

    assert _record_on_disk(service, written.handle)["state"] == "failed"
    assert _record_on_disk(service, written.handle)["failure_code"] == "interrupted"


def test_a_second_restart_does_not_resurrect_an_interrupted_job(service):
    """The ping-pong, driven rather than argued about.

    This is the failure a single restart cannot reveal, and the only test here
    that fails if the write-back is deferred to some later transition.
    """
    written = _write_record(service, state="running", started_at=1_000_000.0)

    _restart(service)
    _restart(service)  # a second crash, a second boot, nothing in between

    job = service.get(written.handle)
    assert job.state == jobs.FAILED
    assert job.failure_code == jobs.INTERRUPTED


def test_disk_and_registry_agree_after_recovery(service, tmp_path):
    """The reconciliation property: no record may disagree with its job.

    Written as a property over EVERY record rather than as a spot check, so a
    state added later is covered without anyone remembering to extend this.
    """
    _write_record(service, state="waiting")
    _write_record(service, state="running", started_at=1_000_000.0)
    _write_record(service, state="finished", completed_at=1_000_000.0,
                  files=(tmp_path / "out" / "a.mp4",))
    _write_record(service, state="failed", failure_code=jobs.NO_VIDEO,
                  completed_at=1_000_000.0)
    _write_record(service, state="expired", completed_at=1_000_000.0,
                  files=(tmp_path / "out" / "b.mp4",))

    _restart(service)

    for path in service._jobs_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        job = service.get(path.stem)
        assert job is not None, f"{path.name} is on disk but not in the registry"
        assert service._as_record(job) == data, f"{path.name} disagrees with the registry"


def test_a_finished_job_survives_and_stays_retrievable(service, tmp_path):
    """US6 acceptance scenario 3."""
    original, files = _finished(service, tmp_path)

    assert _restart(service) == 1
    job = service.get(original.handle)

    assert job.state == jobs.FINISHED
    assert job.failure_code is None
    # Path objects, not strings. file_for calls .is_file() on these.
    assert all(isinstance(item, Path) for item in job.files)
    assert service.file_for(job).path == files[0]


def test_a_finished_job_whose_file_vanished_reports_expired(service, tmp_path):
    """An operator deleted it between runs. Never a partial body (FR-014)."""
    original, files = _finished(service, tmp_path)
    files[0].unlink()

    _restart(service)
    job = service.get(original.handle)

    assert job.state == jobs.FINISHED
    assert service.file_for(job).problem == jobs.EXPIRED


def test_an_expired_job_keeps_its_files_for_the_retry(service, tmp_path):
    """Where recovery and the Windows delete retry meet.

    A delete that failed before the restart is retried by the first sweep of the
    new process, which only works if the paths survived the round trip.
    """
    leftover = tmp_path / "out" / "still-here.mp4"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_bytes(b"not deleted yet")
    written = _write_record(service, state="expired", completed_at=1_000_000.0,
                            files=(leftover,))

    _restart(service)
    job = service.get(written.handle)
    assert job.state == jobs.EXPIRED
    assert job.files == (leftover,)

    service.sweep(now=lambda: 1_000_000.0 + service._retention + 1)
    assert not leftover.exists()


def test_recovery_does_not_requeue_anything(service, monkeypatch):
    """FR-025/Q3. A restart is usually a deploy; re-running every in-flight
    download on boot would turn one bad deploy into a thundering herd."""
    _write_record(service, state="waiting")
    _write_record(service, state="running", started_at=1_000_000.0)

    submitted = []
    real_submit = service._executor.submit
    monkeypatch.setattr(
        service._executor,
        "submit",
        lambda *args, **kwargs: submitted.append(args) or real_submit(lambda: None),
    )

    _restart(service)
    assert submitted == []


def test_recovery_is_a_no_op_on_a_fresh_install(service):
    assert _restart(service) == 0
    assert service._registry == {}


# --- Records that cannot be trusted (all skipped, none fatal) --------------


def test_unreadable_records_are_skipped_without_stopping_recovery(service):
    """One bad file costs one job. A crash-loop would cost the service."""
    good = _write_record(service, state="finished", completed_at=1.0)
    handle = jobs._mint_handle()

    # Truncated JSON.
    (service._jobs_dir / f"{handle}.json").write_text('{"handle": "trunc', encoding="utf-8")
    # Valid JSON, but not an object.
    (service._jobs_dir / f"{jobs._mint_handle()}.json").write_text("[]", encoding="utf-8")

    assert _restart(service) == 1
    assert service.get(good.handle) is not None


def test_a_record_disagreeing_with_its_filename_is_skipped(service):
    """Nothing this service writes can do that, so the directory was edited."""
    written = _write_record(service, state="finished", completed_at=1.0)
    data = _record_on_disk(service, written.handle)
    data["handle"] = jobs._mint_handle()  # valid shape, wrong file
    (service._jobs_dir / f"{written.handle}.json").write_text(json.dumps(data), encoding="utf-8")

    assert _restart(service) == 0


def test_a_record_with_an_unrecognised_state_is_skipped(service):
    """Most likely a downgrade -- a record written by a later version.

    Interrupting it would mislabel it; skipping loses one job and keeps the
    rest.
    """
    written = _write_record(service, state="finished", completed_at=1.0)
    data = _record_on_disk(service, written.handle)
    data["state"] = "quarantined-by-a-future-version"
    (service._jobs_dir / f"{written.handle}.json").write_text(json.dumps(data), encoding="utf-8")

    assert _restart(service) == 0


def test_a_record_missing_a_field_is_skipped(service):
    written = _write_record(service, state="finished", completed_at=1.0)
    data = _record_on_disk(service, written.handle)
    del data["canonical_url"]
    (service._jobs_dir / f"{written.handle}.json").write_text(json.dumps(data), encoding="utf-8")

    assert _restart(service) == 0


def test_a_leftover_temp_write_is_never_parsed(service):
    """persist() writes through .tmp-job-* siblings; a crash can strand one."""
    good = _write_record(service, state="finished", completed_at=1.0)
    (service._jobs_dir / ".tmp-job-abcdef.json").write_text("{ half written", encoding="utf-8")

    assert _restart(service) == 1
    assert service.get(good.handle) is not None


# --- The abandoned temp-directory sweep (FR-026) ---------------------------


def _temp_dir(service, name=".tmp-xvd-abandoned", age=0.0):
    path = service._output_dir / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "fragment.part").write_bytes(b"half a video")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def test_an_abandoned_temp_directory_is_removed(service):
    old = _temp_dir(service, age=service._job_timeout + 60)
    assert service.sweep_abandoned_temp_dirs() == 1
    assert not old.exists()


def test_a_recent_temp_directory_survives_the_sweep(service):
    """The age guard, demonstrated protecting something.

    A CLI download shares the output directory by design (feature 001), and it
    can be in flight at the moment the service starts. Deleting its temp
    directory would corrupt a download this service does not own. Anything
    younger than the job timeout might be exactly that, so it is left alone --
    the watchdog guarantees nothing of OURS is ever that young and still live.
    """
    live = _temp_dir(service, name=".tmp-xvd-a-cli-run-in-progress", age=0.0)

    assert service.sweep_abandoned_temp_dirs() == 0
    assert live.exists()
    assert (live / "fragment.part").exists()


def test_the_guard_is_the_job_timeout_not_a_fixed_number(service, monkeypatch):
    """Same directory, two configurations, opposite outcomes."""
    monkeypatch.setenv("XVD_JOB_TIMEOUT", "3600")
    service.init()
    path = _temp_dir(service, age=1800)
    assert service.sweep_abandoned_temp_dirs() == 0
    assert path.exists()

    monkeypatch.setenv("XVD_JOB_TIMEOUT", "600")
    service.init()
    assert service.sweep_abandoned_temp_dirs() == 1
    assert not path.exists()


def test_the_sweep_never_touches_a_file_or_an_unrelated_directory(service):
    """A file matching the pattern is not ours, and deleting it would be
    deleting somebody's data on a guess."""
    video = service._output_dir / ".tmp-xvd-looks-like-one.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"a real video, awkwardly named")
    os.utime(video, (0, 0))

    unrelated = service._output_dir / "my-videos"
    unrelated.mkdir(parents=True, exist_ok=True)
    os.utime(unrelated, (0, 0))

    assert service.sweep_abandoned_temp_dirs() == 0
    assert video.exists()
    assert unrelated.exists()


def test_a_removal_that_fails_does_not_stop_start_up(service, caplog):
    """Windows holds handles; a leftover that cannot go is not a reason to
    refuse to boot. But it must be visible."""
    stuck = _temp_dir(service, age=service._job_timeout + 60)

    def refusing(path):
        raise PermissionError(32, "The process cannot access the file")

    with caplog.at_level("WARNING", logger="xvd.jobs"):
        assert service.sweep_abandoned_temp_dirs(remove=refusing) == 0

    assert stuck.exists()
    assert any("could not remove leftover" in record.message for record in caplog.records)


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


# The one function in api.py allowed to contain a loop. Named, not a relaxed
# rule: the periodic sweep iterates over TIME, which is a transport-lifecycle
# concern, while every other loop in a request handler would be iteration over
# domain data. Adding a second name here should require the same argument this
# one got -- see tasks.md, Phase 3 decision 3.
_LOOP_EXEMPT = frozenset({"_sweep_loop"})


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_transport_layer_has_no_loops_outside_the_sweep():
    """A loop in a handler is iteration over domain data, which is logic.

    This assertion fired for the first time when the retention sweep needed
    `while True: await asyncio.sleep(...)` in the lifespan. Its old docstring
    said a genuine need was "worth a second look rather than a silent
    allowance" -- this is that second look, and the answer was to name the one
    exception rather than to weaken the check. Deleting it would give up the
    thing that keeps request handlers from growing logic.
    """
    tree = _parse("api.py")
    offenders = {
        name: sum(
            1 for node in ast.walk(func) if isinstance(node, (ast.For, ast.While))
        )
        for name, func in _functions(tree).items()
        if name not in _LOOP_EXEMPT
    }
    assert not {n: c for n, c in offenders.items() if c}, f"loops found in {offenders}"


def test_the_exempt_loop_iterates_over_time_and_not_over_jobs():
    """The exemption is only sound while the loop stays a schedule.

    If `_sweep_loop` ever walks the registry or touches a job, it has become the
    logic the rule exists to keep out of this file, and the exemption stops
    being justified.
    """
    loop = _functions(_parse("api.py"))["_sweep_loop"]

    # Identifiers, NOT a text dump of the tree. Dumping would include the
    # docstring, and a docstring that explains "this must not touch a job" would
    # fail the check for saying so -- which is precisely the T006 mistake this
    # whole section exists to avoid making again. It was made here first, and
    # caught by the test failing on its own prose.
    referenced = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(loop)
        if isinstance(node, (ast.Name, ast.Attribute))
    }

    for forbidden in ("_registry", "Job", "file_for", "get", "submit", "handle"):
        assert forbidden not in referenced, f"_sweep_loop references {forbidden}"

    # What it IS allowed to do: sleep, and hand the work to a thread.
    called = {
        ast.unparse(node.func)
        for node in ast.walk(loop)
        if isinstance(node, ast.Call)
    }
    assert "asyncio.to_thread" in called
    assert "asyncio.sleep" in called


def test_the_transport_still_handles_the_expired_state():
    """Retention adds no code to api.py, and this is what makes that a result
    rather than an oversight.

    T019 wrote the 410 branch against a state that could not occur yet -- there
    was no retention, so nothing could ever be `expired`. Now something can. A
    branch that looks unreachable is a branch someone deletes while tidying, so
    the reachability is asserted here rather than left to be inferred.

    Structural, because this file may not construct an HTTP client (Principle
    III). What it can prove is that the branch exists and names the right state;
    that `file_for` produces that problem for an expired job is proved by the
    retention tests above, and the two together close the path.
    """
    serve = _functions(_parse("api.py"))["_serve"]

    attributes = {
        node.attr for node in ast.walk(serve) if isinstance(node, ast.Attribute)
    }
    assert "EXPIRED" in attributes, "api.py no longer branches on the expired state"

    statuses = {
        node.args[0].value
        for node in ast.walk(serve)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_error"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert 410 in statuses, "the expired refusal is no longer a 410 (FR-022)"


def test_recovery_runs_before_anything_can_observe_the_registry():
    """The ordering inside lifespan, which is the half of the guarantee we own.

    That no caller sees a half-recovered state rests on two things: uvicorn
    completing lifespan startup before it accepts a connection, and recover()
    finishing before the yield. The first is a dependency's behaviour -- true,
    documented, and not ours to enforce. The second is ours, so it is what gets
    asserted.

    The sweep task matters just as much as the yield: started first, it would
    run its watchdog against a registry that was still being built, and a job
    half-recovered has no defensible deadline.
    """
    lifespan = _functions(_parse("api.py"))["lifespan"]

    order = []
    for node in ast.walk(lifespan):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in ("jobs.init", "jobs.recover", "asyncio.create_task"):
                order.append((node.lineno, name))
        elif isinstance(node, ast.Yield):
            order.append((node.lineno, "yield"))

    sequence = [name for _line, name in sorted(order)]

    assert sequence.index("jobs.init") < sequence.index("jobs.recover")
    assert sequence.index("jobs.recover") < sequence.index("asyncio.create_task")
    assert sequence.index("jobs.recover") < sequence.index("yield")


def test_the_temp_sweep_is_start_up_only():
    """FR-026's sweep must never join the periodic one.

    Running it periodically would reintroduce exactly the race its age guard
    exists to close: a CLI download can begin at any moment while the service is
    up, and the guard only bounds how long one has to survive, not whether one
    can start.
    """
    loop = _functions(_parse("api.py"))["_sweep_loop"]
    called_in_loop = {
        ast.unparse(node.func) for node in ast.walk(loop) if isinstance(node, ast.Call)
    }
    assert "jobs.sweep_abandoned_temp_dirs" not in called_in_loop

    service_sweep = _functions(_parse("jobs.py"))["sweep"]
    called_in_sweep = {
        ast.unparse(node.func)
        for node in ast.walk(service_sweep)
        if isinstance(node, ast.Call)
    }
    assert "sweep_abandoned_temp_dirs" not in called_in_sweep


def test_frozen_modules_are_not_imported_for_writing():
    """jobs.py consumes the frozen modules; it must not reach into their privates."""
    imported = _imported(_parse("jobs.py"))
    private = {name for name in imported if name.rsplit(".", 1)[-1].startswith("_")}
    assert not private, f"backend/jobs.py imports private names {sorted(private)}"
