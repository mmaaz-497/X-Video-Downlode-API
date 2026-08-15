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
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections import deque
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
# Every row of data-model.md's configuration table is now read here.
ENV_STATE_DIR = "XVD_STATE_DIR"
ENV_RETENTION = "XVD_RETENTION"
ENV_MAX_CONCURRENT = "XVD_MAX_CONCURRENT"
ENV_MAX_PENDING = "XVD_MAX_PENDING"
ENV_JOB_TIMEOUT = "XVD_JOB_TIMEOUT"
ENV_MIN_FREE_BYTES = "XVD_MIN_FREE_BYTES"
ENV_RATE_LIMIT = "XVD_RATE_LIMIT"
ENV_RATE_WINDOW = "XVD_RATE_WINDOW"
ENV_SWEEP_INTERVAL = "XVD_SWEEP_INTERVAL"

_log = logging.getLogger("xvd.jobs")

_DEFAULT_MAX_CONCURRENT = 2
# Far above the concurrency limit on purpose. FR-015 promises that an ordinary
# over-limit submission WAITS, and that promise survives its own amendment only
# while the cap sits in the far tail: at 2 concurrent and 50 pending, a caller
# has to be the 51st in the queue before anything is refused.
_DEFAULT_MAX_PENDING = 50
_DEFAULT_JOB_TIMEOUT = 1800  # 30 minutes
_DEFAULT_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_DEFAULT_RATE_LIMIT = 10
_DEFAULT_RATE_WINDOW = 3600  # 1 hour
_DEFAULT_SWEEP_INTERVAL = 900  # 15 minutes
# Measured from the job's completion, not from the file's age on disk. A job
# that finished instantly by reusing a file an earlier CLI run left behind gets
# a full period from ITS completion, so handing someone a file and deleting it
# moments later cannot happen (spec.md:213-216).
_DEFAULT_RETENTION = 86400  # 24 hours

# Set by init(). Module-level rather than passed around because there is exactly
# one of each per process, and threading them through every call would be
# ceremony without a second instance to justify it.
_output_dir: Path | None = None
_state_dir: Path | None = None
_jobs_dir: Path | None = None
_max_concurrent: int = _DEFAULT_MAX_CONCURRENT
_max_pending: int = _DEFAULT_MAX_PENDING
_job_timeout: int = _DEFAULT_JOB_TIMEOUT
_min_free_bytes: int = _DEFAULT_MIN_FREE_BYTES
_rate_limit: int = _DEFAULT_RATE_LIMIT
_rate_window: int = _DEFAULT_RATE_WINDOW
_sweep_interval: int = _DEFAULT_SWEEP_INTERVAL
_retention: int = _DEFAULT_RETENTION
_executor: ThreadPoolExecutor | None = None

# address -> submission timestamps still inside the window (research D9).
#
# Its own lock, not _lock. A caller being refused must not have to queue behind
# a registry scan to find that out, and the two structures guard nothing in
# common.
#
# Not persisted: the state is lost on restart, so every caller gets a fresh
# allowance immediately afterwards. Stated rather than hidden. Writing it to
# disk would put a write on the hot submission path to defend against an
# attacker who would need to be able to restart the service to exploit it --
# and anyone who can do that has already won.
_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = threading.Lock()

# Handles the watchdog gave up on whose worker has not returned -- the accepted
# limitation of ADR-0002 made countable. Declared here with the other process
# state because init() clears it; the functions that maintain it live with the
# sweep.
_watchdog_failed: set[str] = set()


def _int_env(name: str, default: int, *, minimum: int) -> int:
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
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _positive_int(name: str, default: int) -> int:
    """A count that is meaningless at zero: pool size, queue depth, a rate limit.

    A rate limit of 0 would mean "accept nothing from anyone", which is a
    configuration mistake rather than an intent, so it is refused rather than
    served.
    """
    return _int_env(name, default, minimum=1)


def _non_negative_int(name: str, default: int) -> int:
    """A threshold where zero is a legitimate instruction, not a mistake.

    XVD_MIN_FREE_BYTES=0 is how an operator disables the free-disk guard on a
    volume where it makes no sense -- a container with an ephemeral overlay, or
    a filesystem whose free-space figure lies. Refusing zero would leave them
    setting it to 1 byte and meaning the same thing less clearly.
    """
    return _int_env(name, default, minimum=0)


def init() -> None:
    """Resolve configuration and prepare the state directory. Call once, at start-up.

    Takes no arguments **on purpose**. Configuration comes from the environment
    and from nowhere else, so there is no parameter here that a request could
    ever be wired into -- which is FR-030 enforced by the absence of a seam
    rather than by a rule someone has to remember. Tests set the environment
    variables and call this again.
    """
    global _output_dir, _state_dir, _jobs_dir, _max_concurrent
    global _max_pending, _job_timeout, _min_free_bytes
    global _rate_limit, _rate_window, _sweep_interval, _retention

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
    _max_pending = _positive_int(ENV_MAX_PENDING, _DEFAULT_MAX_PENDING)
    _job_timeout = _positive_int(ENV_JOB_TIMEOUT, _DEFAULT_JOB_TIMEOUT)
    _rate_limit = _positive_int(ENV_RATE_LIMIT, _DEFAULT_RATE_LIMIT)
    _rate_window = _positive_int(ENV_RATE_WINDOW, _DEFAULT_RATE_WINDOW)
    _sweep_interval = _positive_int(ENV_SWEEP_INTERVAL, _DEFAULT_SWEEP_INTERVAL)
    _retention = _positive_int(ENV_RETENTION, _DEFAULT_RETENTION)

    # Zero is a valid instruction here and nowhere else above -- see
    # _non_negative_int.
    _min_free_bytes = _non_negative_int(ENV_MIN_FREE_BYTES, _DEFAULT_MIN_FREE_BYTES)

    # Neither the rate buckets nor the wedged-worker set survives init().
    # A bucket left over from a previous configuration would be measured
    # against the new window, and a wedged count is a statement about threads in
    # this process's pool -- which init() has just replaced.
    with _rate_lock:
        _rate_buckets.clear()
    with _lock:
        _watchdog_failed.clear()

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
    # Set by the progress callback immediately before it raises, and read by
    # the worker's exception handler to tell a deadline abort apart from any
    # other failure. A boolean carries no free text, so it does not weaken the
    # guarantee above -- but it IS a new field, so it is also a new row in
    # data-model.md, which is where that guarantee is audited.
    #
    # Not persisted: failure_code already carries the outcome to disk, and this
    # is only a signal between two parts of one process.
    timed_out: bool = False


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


def _expire(job: Job) -> bool:
    """Move a FINISHED job to EXPIRED. The only terminal-to-terminal transition.

    Returns False when the job was not finished, so the caller can tell "marked"
    from "not applicable" rather than assuming.

    Deliberately a SECOND function rather than a flag on _enter_terminal.
    Invariant 1 in data-model.md says a terminal state is never left, with this
    as its single exception; teaching _enter_terminal to make an exception would
    mean the guard could be talked into leaving a terminal state, and it stops
    being the guard the watchdog race needs it to be. Two functions, one of
    which permits exactly one edge, keeps that property intact.

    `files` is NOT cleared. The deletion that follows needs the paths, and on
    Windows it may need them again on the next sweep. A caller sees only the
    count, and file_for refuses on state before it looks at the tuple at all.
    Caller must hold _lock.
    """
    if job.state != FINISHED:
        return False
    job.state = EXPIRED
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

# The outcome vocabulary of data-model.md's SubmissionRecord. The three refusal
# outcomes -- rate_limited, disk_low, at_capacity -- are the same constants
# submit() returns as `problem` codes, deliberately: one string means one thing
# whether it is being logged for the operator or mapped to a status code, and
# two parallel vocabularies would drift.
#
# Whether canonical_url is recorded depends on where in submit() the refusal
# happened, and the split is not arbitrary:
#
#   rejected_url, rate_limited -> canonical_url is None. Both are decided
#       before or at validation, so the URL is still unvalidated caller-supplied
#       text at that moment, and FR-032 forbids storing it.
#   disk_low, at_capacity      -> canonical_url IS recorded. Validation has
#       passed by then, so there is a canonical form to write.
#
# It looks like an inconsistency until you know that, which is why it is
# written here.
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


# Why submit() returns a record rather than raising
# ------------------------------------------------
# Four things can refuse a submission: an invalid URL, the rate limit, the free
# disk floor, and the pending cap. Principle VI forbids a custom exception
# hierarchy, and ValueError is already spoken for by the URL -- so raising
# cannot distinguish them without inventing exactly what the principle rules
# out.
#
# Returning "refused, and why" as a frozen record is what this codebase already
# does twice: DownloadOutcome in the frozen module, and FileResult below. A
# third idiom would be the inconsistent choice, not the conservative one.
INVALID_URL = "invalid_url"
RATE_LIMITED = "rate_limited"
DISK_LOW = "disk_low"
AT_CAPACITY = "at_capacity"


def _rate_limited(client_address: str, moment: float) -> int | None:
    """Seconds until this address may submit again, or None if it may now.

    Sliding window over a deque of submission timestamps (research D9). Old
    entries are evicted from the left before the length is compared, so the
    window really slides rather than resetting on a fixed boundary -- a fixed
    boundary lets a caller spend a full allowance either side of it and get
    double the limit in an instant.

    The returned figure is computed entirely from server-side state: the oldest
    timestamp still in the window plus the window length. It is the only number
    this module has ever handed the transport to show a caller, and it is safe
    under FR-029 for that reason -- it is arithmetic on our own clock, not
    anything derived from a request.
    """
    with _rate_lock:
        bucket = _rate_buckets.setdefault(client_address, deque())
        cutoff = moment - _rate_window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= _rate_limit:
            return max(1, math.ceil(bucket[0] + _rate_window - moment))

        bucket.append(moment)
        return None


@dataclass(frozen=True)
class SubmitResult:
    """The outcome of one submission attempt.

    Exactly one of `job` and `problem` is set. `retry_after` is a count of
    seconds and is populated only for a rate-limited refusal; it is computed
    entirely from server-side state, which is what makes it safe for api.py to
    put in front of a caller (FR-029).
    """

    job: Job | None
    problem: str | None = None
    retry_after: int | None = None


def _free_bytes(measure: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free) -> int | None:
    """Free space on the output volume, or None when the guard cannot apply.

    None means "do not refuse anything", and it is returned for two different
    reasons that deserve the same treatment:

    * the operator set the threshold to 0, disabling the guard deliberately;
    * the measurement itself failed.

    The second one FAILS OPEN, with a logged warning. A service that stops
    accepting work because it cannot read its own free space has converted a
    monitoring problem into an outage, which is a worse failure than the one the
    guard exists to prevent (FR-018 guards against filling the disk, not against
    uncertainty about it).

    `measure` is the same kind of defaulted seam as `download` -- it lets a test
    state a free-space figure instead of filling a real volume.
    """
    if _min_free_bytes <= 0:
        return None
    assert _output_dir is not None  # narrowed by _require_init in submit()
    try:
        return measure(_output_dir)
    except OSError:
        _log.warning("could not measure free space on the output volume", exc_info=True)
        return None


def submit(
    url: str,
    client_address: str,
    *,
    download: Callable[..., DownloadOutcome] = download_post,
    now: Callable[[], float] = time.time,
    free_space: Callable[[], int | None] = _free_bytes,
) -> SubmitResult:
    """Accept a URL and return immediately with a job. Does not download.

    `download` is a test seam and nothing else: tests/test_jobs.py passes a
    plain stub so the state machine can be exercised without a network call
    (Principle II permits fakes, not mocking frameworks). It is keyword-only and
    api.py never passes it. It MUST NOT be wired to anything a caller supplies.

    `now` and `free_space` are the same kind of seam for the clock and for the
    disk, so the rate limit can be tested by moving time rather than waiting
    through it, and the disk guard by stating a figure rather than filling a
    volume. api.py passes none of the three.

    Never raises for a refusal. An invalid URL comes back as
    SubmitResult(None, "invalid_url"); no job is created and no network request
    is made on that path (FR-003).
    """
    _require_init()
    moment = now()

    # Checked BEFORE validation, and that ordering is deliberate.
    #
    # FR-019 says "how many jobs a caller may create", which read literally
    # would leave an invalid URL uncounted. That is the cheapest abuse route
    # available: a validation pass and an audit append per request, free
    # forever. A limiter that only counts successes protects the service from
    # its well-behaved users. Research D9 says "on each submission", and this is
    # the submission reading.
    #
    # The cost is real and accepted: ten typos spend the default hourly
    # allowance. Moving this call below parse_post_url is the one-line change
    # that inverts it -- do that deliberately or not at all.
    retry_after = _rate_limited(client_address, moment)
    if retry_after is not None:
        _audit(RATE_LIMITED, client_address=client_address, canonical_url=None, handle=None)
        return SubmitResult(None, RATE_LIMITED, retry_after)

    # The Principle V gate, before anything is created or reserved. The
    # frozen parse_post_url is the only allowlist in the service; this adds no
    # second validation path and no bypass. download_post validates again
    # internally, which is the gate no caller can skip -- this call does not
    # replace it.
    try:
        reference = parse_post_url(url)
    except ValueError:
        # Logged in full: it names the URL and the accepted hosts, which is
        # useful to an operator and forbidden to a caller (FR-005).
        _log.info("rejected submission from %s", client_address, exc_info=True)
        _audit(REJECTED_URL, client_address=client_address, canonical_url=None, handle=None)
        return SubmitResult(None, INVALID_URL)

    # Read outside _lock, applied inside it. disk_usage is a syscall, and
    # holding the registry lock across it would block every status poll and
    # every worker transition on the filesystem. The reading is at most
    # microseconds stale against a threshold measured in gigabytes.
    free_bytes = free_space()

    refusal: str | None = None
    job: Job | None = None
    with _lock:
        # One pass over the registry answers both questions. Deduplication is
        # keyed on the canonical URL rather than the post id, because
        # canonical_url already distinguishes /video/1 from the bare post URL
        # and those produce different files (FR-017, ADR-0001). Check and insert
        # are inside one lock, or five simultaneous submissions of one post
        # would start five downloads (SC-007).
        duplicate_of: Job | None = None
        pending = 0
        for existing in _registry.values():
            if existing.state == WAITING:
                pending += 1
            if (
                duplicate_of is None
                and existing.canonical_url == reference.canonical_url
                and existing.state in (WAITING, RUNNING)
            ):
                duplicate_of = existing

        if duplicate_of is None:
            # Both guards apply ONLY on the create path. A deduplicated
            # submission creates no job and consumes no disk, so refusing it for
            # capacity would punish a caller for work the service had already
            # decided to do.
            if free_bytes is not None and free_bytes < _min_free_bytes:
                refusal = DISK_LOW
            elif pending >= _max_pending:
                refusal = AT_CAPACITY
            else:
                job = Job(
                    handle=_mint_handle(),
                    canonical_url=reference.canonical_url,
                    client_address=client_address,
                )
                _registry[job.handle] = job

    if refusal is not None:
        # The URL passed validation here, so it is recorded -- unlike the
        # rate-limited and rejected paths above, where it is still unvalidated
        # caller text and FR-032 forbids storing it.
        _audit(
            refusal,
            client_address=client_address,
            canonical_url=reference.canonical_url,
            handle=None,
        )
        return SubmitResult(None, refusal)

    if duplicate_of is not None:
        # The caller gets a usable handle for work already underway (FR-016).
        # They cannot tell this from a fresh acceptance, and do not need to.
        _audit(
            DEDUPLICATED,
            client_address=client_address,
            canonical_url=duplicate_of.canonical_url,
            handle=duplicate_of.handle,
        )
        return SubmitResult(duplicate_of)

    assert job is not None  # the only remaining branch
    persist(job)
    _audit(
        ACCEPTED,
        client_address=client_address,
        canonical_url=job.canonical_url,
        handle=job.handle,
    )

    assert _executor is not None  # narrowed by _require_init
    # `now` travels with the job to its worker, so a test can drive the deadline
    # through the real path -- submit, worker, progress hook -- instead of
    # calling the hook directly and leaving the wiring between them unverified.
    _executor.submit(_run_job, job, download, now)
    return SubmitResult(job)


# --------------------------------------------------------------------------
# The worker
# --------------------------------------------------------------------------


def _make_progress_hook(
    job: Job, now: Callable[[], float] = time.time
) -> Callable[[dict], None]:
    """Update the job's progress, and enforce the deadline (FR-020).

    Never persists (research D3). Best-effort by requirement: a missing total is
    normal for HLS, and for a multi-video post the figures restart per video, so
    FR-008 makes progress advisory rather than monotonic.

    The deadline lives here because this callback is the only thing we can get
    inside a running download without touching the frozen module. Raising from
    it aborts the transfer, verified two ways (research D4.1): _hook_progress
    calls each hook with no try/except (yt_dlp/downloader/common.py:488-494), so
    the exception propagates; and even if some layer absorbed it, download_post
    raises on a non-zero retcode (downloader.py:511-512). Both paths run the
    finally that removes the temp directory, so no cleanup is needed here.
    """

    def hook(status: dict) -> None:
        if job.started_at is not None and now() > job.started_at + _job_timeout:
            # RuntimeError specifically. Principle VI names it as an approved
            # built-in, and -- the part that is not guessable -- it must NOT
            # subclass OSError or any network exception, or yt-dlp's handler at
            # YoutubeDL.py:3597-3602 would catch it and merely report an error
            # instead of letting the download abort.
            job.timed_out = True
            raise RuntimeError("job time limit exceeded")

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


def _run_job(
    job: Job,
    download: Callable[..., DownloadOutcome],
    now: Callable[[], float] = time.time,
) -> None:
    """Run one download to completion. Executes on a pool worker, never on a request."""
    with _lock:
        if job.state != WAITING:
            return
        job.state = RUNNING
        # The deadline runs from here, not from created_at, so queue time is not
        # charged against the job.
        job.started_at = now()
    persist(job)

    assert _output_dir is not None  # narrowed by _require_init in submit()
    try:
        try:
            outcome = download(
                job.canonical_url,
                _output_dir,
                progress=_make_progress_hook(job, now),
                on_warning=_make_warning_hook(job),
            )
        except ValueError:
            # download_post raises this for metadata it refuses to guess at, and
            # build_target raises it naming both the candidate path and the
            # output root (validation.py:186). Logged in full, never carried
            # forward.
            _log.exception("job %s: download raised ValueError", job.handle)
            _finish(job, FAILED, failure_code=_abort_code(job))
            return
        except BaseException:
            _log.exception("job %s: download raised unexpectedly", job.handle)
            _finish(job, FAILED, failure_code=_abort_code(job))
            return

        _record_outcome(job, outcome)
    finally:
        # Every return path clears the wedged mark, so the health count means
        # "started, given up on, and still has not come back" -- and nothing
        # else. The outer try exists for exactly this: a worker that returns
        # late, after the watchdog already ruled, must still stop being counted.
        _clear_wedged(job.handle)


def _abort_code(job: Job) -> str:
    """Which code an aborted download earns.

    The deadline is identified by the job's own flag, never by reading the
    exception's text: download_post wraps failures as
    f"download failed for video {position}: {detail}" (downloader.py:534), and
    no classifier should have to reverse-engineer that (research D4).
    """
    return TIME_LIMIT if job.timed_out else UNCLASSIFIED


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
# The periodic sweep
#
# Synchronous by rule. The schedule that drives it is one layer up in api.py,
# because a loop and a sleep are transport-lifecycle concerns and because this
# module may not import asyncio -- tests/test_jobs.py walks these imports and
# fails on it. What runs is here; when it runs is not.
# --------------------------------------------------------------------------

# _watchdog_failed is declared with the other process state near init(), which
# clears it. A hung ffmpeg merge fires no hook and Python threads cannot be
# killed, so the thread stays occupied until the process restarts -- and the
# count going to zero on restart is therefore correct rather than a reset.


def _clear_wedged(handle: str) -> None:
    with _lock:
        _watchdog_failed.discard(handle)


def _run_watchdog(moment: float) -> None:
    """Fail every running job past its deadline (FR-020, research D4).

    This decouples the JOB's state from the THREAD's state. The progress hook
    aborts a download that is still reporting; a download wedged in the ffmpeg
    merge reports nothing at all, and without this its caller would poll a
    "running" job forever. The caller is freed here even though the worker
    cannot be.
    """
    with _lock:
        overdue = [
            job
            for job in _registry.values()
            if job.state == RUNNING
            and job.started_at is not None
            and moment > job.started_at + _job_timeout
        ]

    for job in overdue:
        with _lock:
            applied = _enter_terminal(job, FAILED, failure_code=TIME_LIMIT)
            if applied:
                _watchdog_failed.add(job.handle)
        if applied:
            persist(job)
            # Deliberately greppable. Research D4 names an operator-visible
            # warning as the chosen mitigation for the wedged-worker gap, and a
            # warning nobody can search for is not one.
            _log.warning(
                "xvd-wedged-worker: job %s passed its time limit and was failed; "
                "its worker thread has not returned and one download slot is "
                "unavailable until restart",
                job.handle,
            )


def _delete_files(job: Job, unlink: Callable[[Path], None]) -> None:
    """Remove an expired job's files, tolerating a delete that cannot happen yet.

    On Windows a file being read cannot be unlinked -- the open handle makes it
    a PermissionError -- and the development machine is Windows while the
    deployment target is not. This is the same problem _remove_temp_dir
    documents at downloader.py:270-292, and it gets the same answer: log it,
    leave the file, and let the next sweep try again.

    The tolerance is safe rather than merely convenient because the caller-
    visible guarantee does not depend on the delete succeeding. The job is
    already EXPIRED by the time this runs, so file_for refuses on state and no
    caller can reach these bytes whether or not they are still on disk. What is
    at stake is disk space, and disk space can wait one interval.

    Logged at WARNING, not swallowed: a file that fails to delete on every pass
    forever is a real leak, and the operator needs to be able to see it.
    """
    for path in job.files:
        try:
            unlink(path)
        except FileNotFoundError:
            # Already gone -- a previous pass, or an operator. Nothing to do and
            # nothing worth saying.
            pass
        except OSError:
            _log.warning(
                "job %s: a retained file could not be deleted yet; "
                "the next sweep will retry",
                job.handle,
                exc_info=True,
            )


def _expire_due(moment: float, unlink: Callable[[Path], None]) -> None:
    """Expire finished jobs past their retention period (FR-021, FR-022, FR-023).

    MARK FIRST, DELETE SECOND. The ordering is the requirement, not a tidiness
    preference:

    * A retrieval that begins after the mark is refused with a clean "expired"
      rather than racing a file that is vanishing underneath it.
    * A retrieval already in flight keeps its open file handle. On POSIX,
      unlink removes the directory entry while the reader goes on reading, so
      the response completes intact -- which is why marking first is sufficient
      and no lock is held across the transfer.

    Reversing these two lines would produce exactly the failure FR-023 exists to
    prevent: a caller mid-download receiving a truncated file.

    Jobs already EXPIRED are revisited so that a delete which failed on an
    earlier pass is retried. That is the whole of the retry mechanism -- no
    extra state, because "still expired and the file still exists" is already
    the complete description of what needs doing.
    """
    cutoff = moment - _retention

    with _lock:
        due = [
            job
            for job in _registry.values()
            if job.state == FINISHED
            and job.completed_at is not None
            and job.completed_at <= cutoff
        ]
        newly_expired = [job for job in due if _expire(job)]

        # Anything expired whose files are still on disk, including the jobs
        # just marked and any whose deletion failed before.
        pending_deletion = [
            job for job in _registry.values() if job.state == EXPIRED and job.files
        ]

    for job in newly_expired:
        persist(job)
        _log.info("job %s expired after its retention period", job.handle)

    for job in pending_deletion:
        _delete_files(job, unlink)


def _prune_rate_buckets(moment: float) -> None:
    """Drop buckets that have gone empty, so one-off callers cannot grow the dict.

    Without this the address map is a slow memory leak on a public service:
    every address that ever submitted keeps an entry for the life of the
    process (research D9).
    """
    cutoff = moment - _rate_window
    with _rate_lock:
        for address in list(_rate_buckets):
            bucket = _rate_buckets[address]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del _rate_buckets[address]


def _unlink(path: Path) -> None:
    path.unlink()


def sweep(
    *,
    now: Callable[[], float] = time.time,
    unlink: Callable[[Path], None] = _unlink,
) -> None:
    """One maintenance pass. Safe to call at any time; does nothing if idle.

    Order matters and is not alphabetical:

    1. the deadline watchdog, so an overdue job is failed before anything else
       reasons about it;
    2. retention, so finished work older than the retention period is expired
       and its file deleted;
    3. the rate-bucket prune, which is pure bookkeeping and can happen last.

    Retention is inserted at step 2 rather than appended, because expiring a job
    the watchdog is about to fail in the same pass would be deciding an outcome
    twice.

    `now` is the clock seam, so an entire retention period can pass inside a
    test without any of it elapsing. `unlink` is the same kind of seam for the
    delete, so the Windows file-handle failure can be exercised on any platform
    rather than only where it happens to occur.
    """
    _require_init()
    moment = now()
    _run_watchdog(moment)
    _expire_due(moment, unlink)
    _prune_rate_buckets(moment)


def sweep_interval() -> int:
    """How often the sweep should run, in seconds.

    A function rather than the module global itself, because api.py starts its
    loop after init() has run and reading the name at import time would capture
    the default instead of the operator's setting.
    """
    return _sweep_interval


def health() -> dict:
    """Aggregate counts for the operator. No handles, no URLs, no addresses.

    This endpoint is unauthenticated and reachable by anyone, so it may carry
    totals and nothing else. Counted here rather than in api.py because walking
    the registry is domain iteration, which does not belong in a request
    handler.
    """
    with _lock:
        running = sum(1 for job in _registry.values() if job.state == RUNNING)
        waiting = sum(1 for job in _registry.values() if job.state == WAITING)
        wedged = len(_watchdog_failed)

    return {
        "status": "degraded" if wedged else "ok",
        "running": running,
        "waiting": waiting,
        "wedged_workers": wedged,
    }


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
