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

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import output_dir as _configured_output_dir

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
