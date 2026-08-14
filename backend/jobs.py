"""Job orchestration: records, scheduling, and the caller-safe failure surface.

Framework-free by rule, not by accident. This module imports nothing from the
web stack and starts no event loop, which is what lets the whole service layer
be exercised by plain function calls in tests/test_jobs.py. If that ever stops
being true, the Principle III boundary has already failed -- tests/test_jobs.py
walks this file's import statements and will say so.

api.py sits above this module and does three things: parse, call in here, and
serialize. downloader.py, validation.py, and config.py sit below it and are
frozen; this module consumes their public surface and never edits them.

The orchestration lives here rather than in the request handlers because none of
it is transport logic: a job record, a queue position, and a failure code mean
the same thing whether they arrived over HTTP or not.
"""

import datetime
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import output_dir as _configured_output_dir
from backend.downloader import DownloadOutcome, download_post
from backend.validation import parse_post_url

# Every value has a working default so the service starts with no configuration
# at all (Constitution Principle VII, FR-034). config.py is frozen, so these are
# read here with os.environ -- the same pattern config.output_dir uses, not an
# extension of it.
#
# The rest of the table in data-model.md -- job timeout, retention, sweep
# interval, free-disk floor, rate limit, pending cap -- belongs to later phases
# and is deliberately absent. Reading a variable the service does not act on
# would advertise configuration that does nothing.
ENV_STATE_DIR = "XVD_STATE_DIR"
ENV_MAX_CONCURRENT = "XVD_MAX_CONCURRENT"

_log = logging.getLogger("xvd.jobs")

_DEFAULT_MAX_CONCURRENT = 2

# Set by init(). Module-level rather than passed around because there is exactly
# one of each per process, and threading them through every call would be
# ceremony without a second instance to justify it.
_output_dir: Path | None = None
_state_dir: Path | None = None
_jobs_dir: Path | None = None
_max_concurrent: int = _DEFAULT_MAX_CONCURRENT
_executor: ThreadPoolExecutor | None = None


def _positive_int(name: str, default: int) -> int:
    """Read an integer environment variable, refusing values that make no sense.

    A max_concurrent of 0 would accept jobs and never run them, and a negative
    one would raise inside ThreadPoolExecutor with a message about an argument
    the operator never passed. Both are configuration mistakes worth naming at
    start-up rather than discovering later.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def init() -> None:
    """Resolve configuration and prepare the state directory. Call once, at start-up.

    Takes no arguments **on purpose**. Configuration comes from the environment
    and from nowhere else, so there is no parameter here that a request could
    ever be wired into -- which is FR-030 enforced by the absence of a seam
    rather than by a rule someone has to remember. Tests set the environment
    variables and call this again.
    """
    global _output_dir, _state_dir, _jobs_dir, _max_concurrent

    # No argument. The frozen config.output_dir accepts an override for the CLI's
    # --output-dir flag; passing anything here would put the destination one
    # parameter away from a request body (FR-004, FR-030).
    _output_dir = _configured_output_dir()

    configured = os.environ.get(ENV_STATE_DIR)
    _state_dir = Path(configured).expanduser() if configured else _output_dir / ".xvd-state"

    # Under the output directory by default, so it sits on the volume the disk
    # guard will measure in a later phase. The name does not collide with the
    # .tmp-xvd-* pattern that restart recovery will sweep.
    _jobs_dir = _state_dir / "jobs"
    _jobs_dir.mkdir(parents=True, exist_ok=True)

    # Job files are named by the handle, which is the capability itself. 0700 is
    # the whole of the defence against another local account reading them.
    # os.chmod is a no-op on Windows; the deployment target is Linux and the
    # development machine is not multi-user, so this is not worked around.
    try:
        os.chmod(_state_dir, 0o700)
    except OSError:
        pass

    _max_concurrent = _positive_int(ENV_MAX_CONCURRENT, _DEFAULT_MAX_CONCURRENT)

    # max_workers IS the concurrency cap (FR-015). There is deliberately no
    # semaphore: the pool's queue already draws the waiting/running line, and a
    # second mechanism guarding the same invariant is a second source of truth
    # that can disagree with the first (research D2, ADR-0002).
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False)
    _executor = ThreadPoolExecutor(
        max_workers=_max_concurrent, thread_name_prefix="xvd-download"
    )


def shutdown(wait: bool = False) -> None:
    """Stop accepting work. Called at exit.

    Defaults to NOT waiting: a download runs for minutes, and blocking process
    exit on one would make every restart and deploy hang. An abandoned job is
    recovered as interrupted on the next start-up, which is what FR-025 already
    specifies should happen to it.

    `wait=True` exists for tests. Without it a worker outlives the process state
    it was started under, and since _jobs_dir is module-global, a straggler from
    one test writes its record into the next test's directory.
    """
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None


def _require_init() -> None:
    """Fail with a clear sentence rather than a None-typed mystery later."""
    if _jobs_dir is None:
        raise RuntimeError("jobs.init() has not been called")


# The five states of FR-006. EXPIRED is unreachable until retention exists, but
# it is declared now so the set of terminal states is complete and no later
# phase has to revisit what "terminal" means.
WAITING = "waiting"
RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
EXPIRED = "expired"

_TERMINAL_STATES = frozenset({FINISHED, FAILED, EXPIRED})

# Guards the registry and every mutation of a Job. Both are touched from request
# threads and from pool workers at the same time, and two of the races are real
# rather than theoretical: check-then-insert in submit() would let five
# simultaneous submissions of one post start five downloads (SC-007), and the
# terminal-state guard below is only a guard if the check and the write cannot
# be separated.
_lock = threading.Lock()


@dataclass
class Job:
    """One submission's unit of work.

    NOTE: this record deliberately has NO field able to hold free text -- no
    `message`, no `detail`, no `error_text`. That absence is what guarantees
    FR-029: DownloadOutcome.message and the exceptions download_post raises
    contain absolute filesystem paths (downloader.py:332 and :309,
    validation.py:186), and a serializer cannot forward a field that does not
    exist. Adding one would move the guarantee from "structurally impossible" to
    "nobody has made the mistake yet", which is a different and much weaker
    thing. The raw text goes to the log at the single site that receives it.

    `failure_code` carries the meaning across the boundary; api.py renders it
    through FAILURE_MESSAGES. See ADR-0003.
    """

    handle: str
    canonical_url: str
    client_address: str
    state: str = WAITING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    # Memory only. Progress arrives per chunk and is never persisted -- it would
    # be a write storm for a value that is meaningless after a restart anyway,
    # since an interrupted job is failed rather than resumed (research D3).
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    files: tuple[Path, ...] = ()
    failure_code: str | None = None


def _enter_terminal(
    job: Job,
    state: str,
    *,
    failure_code: str | None = None,
    files: tuple[Path, ...] = (),
) -> bool:
    """Move a job to a terminal state, unless it is already in one.

    Returns False when the transition was refused, so the caller can tell the
    difference between "recorded" and "too late" instead of assuming.

    The refusal is not defensive decoration. A later phase adds a watchdog that
    fails a job whose deadline passed; its worker thread may still be alive and
    may still return an outcome afterwards, and without this the worker would
    quietly overwrite the watchdog's verdict with a stale one. Caller must hold
    _lock.
    """
    if job.state in _TERMINAL_STATES:
        return False
    job.state = state
    job.completed_at = time.time()
    job.failure_code = failure_code
    job.files = files
    return True


def _as_record(job: Job) -> dict:
    """The on-disk shape. Paths become strings; nothing else is transformed."""
    return {
        "handle": job.handle,
        "canonical_url": job.canonical_url,
        "client_address": job.client_address,
        "state": job.state,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "downloaded_bytes": job.downloaded_bytes,
        "total_bytes": job.total_bytes,
        "files": [str(path) for path in job.files],
        "failure_code": job.failure_code,
    }


def persist(job: Job) -> None:
    """Write the job's record atomically. Called on state transitions only.

    Temp file in the same directory, then os.replace -- the pattern _promote
    established at downloader.py:315, for the same reason. os.replace is atomic
    within a filesystem and a sibling temp file is guaranteed to share one;
    shutil.move degrades to copy-then-delete across filesystems and can leave a
    partial file at the destination.

    NOT called from the progress callback. Progress changes many times a second
    and means nothing after a restart, so persisting it would be a write storm
    for no recoverable value (research D3).

    A failed write is logged and swallowed. The in-memory record is
    authoritative for the life of the process, so the job itself still works;
    what degrades is the accuracy of a restart recovery that does not exist yet.
    Raising here would fail a download over a bookkeeping problem.

    Only the write side. Reading records back is FR-024/FR-025 and belongs to a
    later phase; writing now avoids retrofitting persistence into every
    transition then.
    """
    _require_init()
    assert _jobs_dir is not None  # narrowed by _require_init
    target = _jobs_dir / f"{job.handle}.json"
    try:
        handle_fd, temp_name = tempfile.mkstemp(dir=_jobs_dir, prefix=".tmp-job-")
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as stream:
                json.dump(_as_record(job), stream)
            os.replace(temp_name, target)
        except BaseException:
            # The temp file is ours and half-written; leaving it behind would
            # accumulate in a directory nothing else sweeps yet.
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except OSError:
        _log.exception("could not persist job record for %s", job.handle)


# --------------------------------------------------------------------------
# Handles and the registry
# --------------------------------------------------------------------------

# 32 bytes -> 256 bits of entropy in a 43-character string. SC-011 asks for at
# least 128; 16 bytes would meet it exactly and 32 costs nothing. This is the
# only credential in the system -- possession of the handle IS the authorization
# (spec Q1) -- so the margin is the security argument, not decoration.
_HANDLE_BYTES = 32

# base64url of 32 bytes is 43 characters once the padding is stripped. Derived
# here rather than written as a literal so the pattern cannot drift away from
# the minter if _HANDLE_BYTES ever changes.
HANDLE_LENGTH = len(secrets.token_urlsafe(_HANDLE_BYTES))
HANDLE_PATTERN = re.compile(rf"\A[A-Za-z0-9_-]{{{HANDLE_LENGTH}}}\Z")

_registry: dict[str, Job] = {}


def _mint_handle() -> str:
    return secrets.token_urlsafe(_HANDLE_BYTES)


def is_valid_handle(candidate: str) -> bool:
    """Whether a string is shaped like a handle this service could have minted.

    Lives here rather than in the transport because this module mints handles
    and therefore owns their shape; a pattern written down anywhere else would
    be free to drift away from the minter.

    A cheap second layer only. The first and real one is that get() is a dict
    lookup, so a caller-supplied string never becomes a path component whatever
    it contains (research D3). A false answer here must produce exactly the same
    refusal as an unknown handle, or the shape of the input would tell a caller
    something the lookup does not.
    """
    return bool(HANDLE_PATTERN.match(candidate))


def get(handle: str) -> Job | None:
    """Look a job up by its handle. Never touches the filesystem.

    That is the point, not an optimisation. Because the lookup is a dict hit, a
    caller-supplied string cannot become a path component no matter what it
    contains -- there is no code path from a request to the filesystem by handle
    at all. Only handles this module minted are ever turned into filenames
    (research D3). api.py applies a syntactic check as a cheap second layer.
    """
    with _lock:
        return _registry.get(handle)


# --------------------------------------------------------------------------
# The operator's audit trail (FR-031, FR-032)
# --------------------------------------------------------------------------

ACCEPTED = "accepted"
DEDUPLICATED = "deduplicated"
REJECTED_URL = "rejected_url"

# A separate lock so an audit append never waits behind a registry scan, and so
# two concurrent submissions cannot interleave halves of a line.
_audit_lock = threading.Lock()


def _audit(outcome: str, *, client_address: str, canonical_url: str | None, handle: str | None) -> None:
    """Append one line to the operator's submission log.

    Written for refusals as well as acceptances -- a refusal is the more
    interesting entry when investigating abuse, and a run of them from one
    address is the pattern worth seeing.

    canonical_url is None for a rejected URL, and that is not an oversight.
    FR-032 forbids storing caller free text, and a string that failed validation
    is exactly that: unvalidated, attacker-chosen, and destined for a file the
    operator will open in a terminal. The address and the timestamp are what
    identify the abuse; the rejected text is not needed to do it.

    Failure to write is logged and swallowed. An audit append must not be able
    to fail a submission.
    """
    _require_init()
    assert _state_dir is not None  # narrowed by _require_init
    record = {
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "outcome": outcome,
        "client_address": client_address,
        "canonical_url": canonical_url,
        "handle": handle,
    }
    try:
        with _audit_lock:
            with open(_state_dir / "submissions.log", "a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
    except OSError:
        _log.exception("could not append to the submission log")


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------


def submit(
    url: str,
    client_address: str,
    *,
    download: Callable[..., DownloadOutcome] = download_post,
) -> Job:
    """Accept a URL and return immediately with a job. Does not download.

    `download` is a test seam and nothing else: tests/test_jobs.py passes a
    plain stub so the state machine can be exercised without a network call
    (Principle II permits fakes, not mocking frameworks). It is keyword-only and
    api.py never passes it. It MUST NOT be wired to anything a caller supplies.

    Raises ValueError for a rejected URL, which api.py maps to 400. No job is
    created and no network request is made on that path (FR-003).
    """
    _require_init()

    # The Principle V gate, first, before anything is created or reserved. The
    # frozen parse_post_url is the only allowlist in the service; this adds no
    # second validation path and no bypass. download_post validates again
    # internally, which is the gate no caller can skip -- this call does not
    # replace it.
    try:
        reference = parse_post_url(url)
    except ValueError:
        _audit(REJECTED_URL, client_address=client_address, canonical_url=None, handle=None)
        raise

    with _lock:
        # Deduplicate on the canonical URL rather than the post id, because
        # canonical_url already distinguishes /video/1 from the bare post URL
        # and those produce different files (FR-017, ADR-0001). Check and insert
        # are inside one lock, or five simultaneous submissions of one post
        # would start five downloads (SC-007).
        for existing in _registry.values():
            if existing.canonical_url == reference.canonical_url and existing.state in (
                WAITING,
                RUNNING,
            ):
                duplicate_of = existing
                break
        else:
            duplicate_of = None

        if duplicate_of is None:
            job = Job(
                handle=_mint_handle(),
                canonical_url=reference.canonical_url,
                client_address=client_address,
            )
            _registry[job.handle] = job

    if duplicate_of is not None:
        # The caller gets a usable handle for work already underway (FR-016).
        # They cannot tell this from a fresh acceptance, and do not need to.
        _audit(
            DEDUPLICATED,
            client_address=client_address,
            canonical_url=duplicate_of.canonical_url,
            handle=duplicate_of.handle,
        )
        return duplicate_of

    persist(job)
    _audit(
        ACCEPTED,
        client_address=client_address,
        canonical_url=job.canonical_url,
        handle=job.handle,
    )

    assert _executor is not None  # narrowed by _require_init
    _executor.submit(_run_job, job, download)
    return job


# --------------------------------------------------------------------------
# The worker
# --------------------------------------------------------------------------


def _make_progress_hook(job: Job) -> Callable[[dict], None]:
    """Update the job's progress from yt-dlp's status dict. Memory only.

    Never persists (research D3). Best-effort by requirement: a missing total is
    normal for HLS, and for a multi-video post the figures restart per video, so
    FR-008 makes progress advisory rather than monotonic.

    The job time limit will be enforced from inside this callback in a later
    phase. It is not enforced here yet.
    """

    def hook(status: dict) -> None:
        if status.get("status") != "downloading":
            return
        job.downloaded_bytes = status.get("downloaded_bytes")
        job.total_bytes = status.get("total_bytes") or status.get("total_bytes_estimate")

    return hook


def _make_warning_hook(job: Job) -> Callable[[str], None]:
    """Route downloader warnings to the log and nowhere else.

    The only warning it currently sends names a temporary directory
    (downloader.py:309-312), so this text is exactly what must never reach a
    caller (FR-029).
    """

    def hook(message: str) -> None:
        _log.warning("job %s: %s", job.handle, message)

    return hook


def _run_job(job: Job, download: Callable[..., DownloadOutcome]) -> None:
    """Run one download to completion. Executes on a pool worker, never on a request."""
    with _lock:
        if job.state != WAITING:
            return
        job.state = RUNNING
        job.started_at = time.time()
    persist(job)

    assert _output_dir is not None  # narrowed by _require_init in submit()
    try:
        outcome = download(
            job.canonical_url,
            _output_dir,
            progress=_make_progress_hook(job),
            on_warning=_make_warning_hook(job),
        )
    except ValueError:
        # download_post raises this for metadata it refuses to guess at, and
        # build_target raises it naming both the candidate path and the output
        # root (validation.py:186). Logged in full, never carried forward.
        _log.exception("job %s: download raised ValueError", job.handle)
        _finish(job, FAILED, failure_code=UNCLASSIFIED)
        return
    except BaseException:
        _log.exception("job %s: download raised unexpectedly", job.handle)
        _finish(job, FAILED, failure_code=UNCLASSIFIED)
        return

    _record_outcome(job, outcome)


def _finish(
    job: Job,
    state: str,
    *,
    failure_code: str | None = None,
    files: tuple[Path, ...] = (),
) -> None:
    """Apply a terminal transition and persist it, if the job is not already done."""
    with _lock:
        applied = _enter_terminal(job, state, failure_code=failure_code, files=files)
    if applied:
        persist(job)


# --------------------------------------------------------------------------
# Outcome -> terminal state
# --------------------------------------------------------------------------

# The deliberate, visible fallback of FR-011. Declared here because _run_job
# needs it before the classification table exists; the rest of the codes and the
# prefix map arrive with _classify below.
NO_VIDEO = "no_video"
NOT_A_VIDEO = "not_a_video"
PROTECTED_ACCOUNT = "protected_account"
AGE_RESTRICTED = "age_restricted"
POST_UNAVAILABLE = "post_unavailable"
INTERRUPTED = "interrupted"
SERVICE_UNAVAILABLE = "service_unavailable"
TIME_LIMIT = "time_limit"
UNCLASSIFIED = "unclassified"

# Every sentence a caller can ever be shown about a failure. Each one is a
# literal here; none interpolates a path, a filename, a URL, a count, or any
# text originating outside this table. That is the whole of FR-029's positive
# half -- the negative half is that the Job record has no field able to carry
# the downloader's own message (see the Job docstring, ADR-0003).
#
# service_unavailable deliberately does not tell the caller that ffmpeg is
# missing. That is an operator fault, and naming it would describe the server's
# installation to the public.
FAILURE_MESSAGES: dict[str, str] = {
    NO_VIDEO: "This post does not contain a video.",
    NOT_A_VIDEO: "This post contains media, but it is not a video.",
    PROTECTED_ACCOUNT: (
        "This post is from a protected account and is not publicly accessible."
    ),
    AGE_RESTRICTED: "This post is age-restricted and is not publicly accessible.",
    POST_UNAVAILABLE: "This post could not be found. It may have been deleted.",
    INTERRUPTED: (
        "The download was interrupted and did not complete. Submit it again to retry."
    ),
    SERVICE_UNAVAILABLE: (
        "The service cannot process downloads right now. "
        "The operator has been notified."
    ),
    TIME_LIMIT: "The download took too long and was stopped.",
    UNCLASSIFIED: "The download failed for an unexpected reason.",
}

# Ordered prefix -> code. Matched with str.startswith, which is exact rather
# than heuristic here: _partial_failure composes the message as
# f"{reason} Files already saved: {names}" (downloader.py:559), and the plain
# path returns the reason alone, so the diagnosis is ALWAYS a prefix.
#
# These strings are our own literals, deliberately. downloader._ERROR_DIAGNOSES
# is private and importing it into production code would couple the two modules
# by their internals; the test suite imports it instead and asserts this table
# still covers every row, so an upstream edit fails the build rather than
# decaying quietly to unclassified (research D5, FR-011).
#
# A tuple rather than a dict because order is part of the meaning -- specific
# before generic -- mirroring how the frozen module writes the same idea.
FAILURE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("this post has no video in it.", NO_VIDEO),
    ("this post contains media, but it is not a video.", NOT_A_VIDEO),
    ("this post belongs to a protected account", PROTECTED_ACCOUNT),
    ("this post is age-restricted", AGE_RESTRICTED),
    ("this post could not be found.", POST_UNAVAILABLE),
    ("ffmpeg was not found on PATH.", SERVICE_UNAVAILABLE),
    ("Interrupted by the operator.", INTERRUPTED),
    # The frozen module's own fallback (downloader.py:132). It quotes yt-dlp
    # verbatim, so it is the one row that is doubly bound by FR-029 -- matched
    # only to be classified, never to be shown.
    ("could not extract video from this post.", UNCLASSIFIED),
)

# Codes this module assigns from its own knowledge rather than by reading a
# message. The drift test uses this to tell "not in the prefix table" apart from
# "missing from the prefix table".
SELF_ASSIGNED_CODES = frozenset({TIME_LIMIT})


def _classify(message: str) -> str:
    """Map a DownloadOutcome.message to a failure code.

    Returns UNCLASSIFIED for anything unrecognised, which is a deliberate and
    visible outcome rather than a silent substitution (FR-011). The messages
    that legitimately land here include f"download failed for video {n}: ..."
    (downloader.py:534), which can embed a temp-directory path -- another reason
    the text is classified and dropped rather than forwarded.
    """
    for prefix, code in FAILURE_PREFIXES:
        if message.startswith(prefix):
            return code
    return UNCLASSIFIED


def _record_outcome(job: Job, outcome: DownloadOutcome) -> None:
    """Turn a DownloadOutcome into a terminal state.

    "skipped" is a success, not a lesser one: feature 001 returns it when the
    target file is already on disk, and the caller gets the same file either way
    (FR-016).

    outcome.message is logged and then dropped. It is never stored on the job,
    because the record has no field that could hold it -- see the Job docstring.
    """
    if outcome.status in ("downloaded", "skipped"):
        _finish(job, FINISHED, files=tuple(outcome.paths))
        return

    _log.info("job %s failed: %s", job.handle, outcome.message)
    _finish(job, FAILED, failure_code=_classify(outcome.message))


# --------------------------------------------------------------------------
# File selection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileResult:
    """Which file to serve, or why none can be served.

    Returns a problem code rather than raising, mirroring how downloader.py
    returns DownloadOutcome instead of inventing an exception hierarchy
    (Principle VI). api.py maps `problem` to a status code and a fixed sentence;
    `file_count` is what lets it name the count in an index-required refusal
    without this module composing any caller-visible text.
    """

    path: Path | None
    problem: str | None
    file_count: int


def file_for(job: Job, index: int | None = None) -> FileResult:
    """Resolve which of a finished job's files to serve (FR-035, FR-036).

    No index and exactly one file returns that file: an index is NEVER required
    for the single-video case, which is the common one (spec Q2). No index and
    several files is a refusal naming the count, not an error -- the caller then
    asks again with an index.

    The path comes only from the job's own recorded result, never from anything
    in a request (FR-030).
    """
    if job.state == EXPIRED:
        return FileResult(None, EXPIRED, len(job.files))
    if job.state == FAILED:
        return FileResult(None, FAILED, 0)
    if job.state != FINISHED:
        return FileResult(None, "not_ready", 0)

    count = len(job.files)
    if count == 0:
        # A finished job with no files means an invariant broke rather than a
        # caller doing anything wrong. Reported as expired, which is the truthful
        # answer to "can I have the file": no, and not because you asked wrongly.
        _log.error("job %s is finished but recorded no files", job.handle)
        return FileResult(None, EXPIRED, 0)

    if index is None:
        if count > 1:
            return FileResult(None, "index_required", count)
        chosen = job.files[0]
    else:
        # 1-based, matching the index the frozen build_target puts in filenames.
        # Anything out of range is refused; there is deliberately no clamping,
        # because silently serving file 1 to someone who asked for file 9 is the
        # wrong-file class of bug ADR-0001 exists to prevent.
        if index < 1 or index > count:
            return FileResult(None, "not_found", count)
        chosen = job.files[index - 1]

    # Re-check at serve time. Retention does not exist yet, but an operator can
    # still delete a file, and FR-014 forbids handing back a partial or empty
    # body in that case.
    if not chosen.is_file():
        _log.warning("job %s: recorded file is gone", job.handle)
        return FileResult(None, EXPIRED, count)

    return FileResult(chosen, None, count)
