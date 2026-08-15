"""HTTP transport. Parses a request, calls backend.jobs, serialises the answer.

There is no business logic here by rule (Constitution Principle III). Job state,
scheduling, deduplication, and the failure vocabulary all live one layer down,
because none of them mean anything different over HTTP than they would from a
terminal. If a decision in this file cannot be described as parsing, calling, or
formatting, it belongs in backend/jobs.py.

Two rules this module exists to keep, both from ADR-0003:

* Nothing a caller receives is ever derived from an exception's text, a
  library's error string, or a filesystem path. Every message below is a literal
  in this file or comes from jobs.FAILURE_MESSAGES, which is also all literals.
* A handle that does not resolve produces one response, whatever the reason.
  Possession of a handle is the only authorization there is (spec Q1), so
  "unknown" and "malformed" must be indistinguishable.

Pydantic appears in this file and nowhere else in the package (research D11).
"""

import asyncio
import datetime
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import jobs

_log = logging.getLogger("xvd.api")


async def _sweep_loop() -> None:
    """Call jobs.sweep() on a fixed interval, forever.

    THE ONLY LOOP PERMITTED IN THIS FILE, and tests/test_jobs.py enforces that
    by name. Every other function here must stay loop-free, because a loop in a
    handler is iteration over domain data and that is logic, which belongs one
    layer down. This one iterates over time rather than over anything the
    service knows about, and it touches no job.

    `to_thread` is not optional: sweep() does filesystem work, and running it on
    the event loop would block every in-flight request behind a directory walk
    (research D7).

    A sweep that raises must not kill the loop. Retention stopping forever while
    the service still answers every request is the worst failure available here
    and the hardest to notice, so the exception is logged and the next interval
    is waited out as normal.
    """
    while True:
        await asyncio.sleep(jobs.sweep_interval())
        try:
            await asyncio.to_thread(jobs.sweep)
        except Exception:
            _log.exception("sweep failed; the next one will run as scheduled")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Resolve configuration and build the pool before the first request lands."""
    jobs.init()
    sweeper = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        # Cancelled and awaited, or the loop outlives the executor it depends
        # on and the next sweep runs against a shut-down service.
        sweeper.cancel()
        try:
            await sweeper
        except asyncio.CancelledError:
            pass

        # wait=False: a download runs for minutes and blocking shutdown on one
        # would hang every deploy. Jobs abandoned this way are recovered as
        # interrupted on the next start-up, which is a later phase.
        jobs.shutdown()


# debug=False so no traceback middleware is installed. A stack trace rendered
# into a response is precisely what FR-029 forbids.
app = FastAPI(
    title="X Video Downloader API",
    version="1.0.0",
    debug=False,
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# Request and response shapes
# --------------------------------------------------------------------------


class SubmitRequest(BaseModel):
    """A URL and nothing else.

    extra="forbid" is load-bearing rather than tidy: it is what makes FR-004
    enforceable at the boundary. Without it a body carrying output_dir, format,
    or path would be silently accepted and ignored today, and silently honoured
    the day someone adds a matching parameter downstream. Refusing unknown
    fields means that change cannot happen by accident.
    """

    model_config = ConfigDict(extra="forbid")

    # Capped so an oversized body is refused before it is parsed as a URL.
    url: str = Field(min_length=1, max_length=2048)


class Progress(BaseModel):
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    percent: float | None = None


class Failure(BaseModel):
    code: str
    message: str


class JobResponse(BaseModel):
    """Everything a caller may see about a job.

    Absent by design: any filesystem path, the output directory, the calling
    address, and the downloader's own message. The service-layer record has no
    field holding the last of those, so this model could not expose it even if
    it tried (ADR-0003).
    """

    handle: str
    state: str
    file_count: int
    progress: Progress | None = None
    failure: Failure | None = None
    created_at: str
    completed_at: str | None = None


class Error(BaseModel):
    code: str
    message: str


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def _iso(moment: float | None) -> str | None:
    if moment is None:
        return None
    return datetime.datetime.fromtimestamp(moment, datetime.UTC).isoformat()


def _error(status: int, code: str, message: str) -> JSONResponse:
    """The only shape an error ever takes. Every message passed here is a literal."""
    return JSONResponse(status_code=status, content={"code": code, "message": message})


# One response for every handle that does not resolve -- unknown, malformed, or
# never issued. Built once so no call site can accidentally differ (FR-028).
def _not_found() -> JSONResponse:
    return _error(404, "not_found", "No such job.")


def _refused(result: jobs.SubmitResult) -> JSONResponse:
    """Render a refused submission. Every sentence below is a literal here.

    The service layer returns a code; this table turns it into a status and a
    fixed message. Nothing from the request and nothing from an exception's text
    reaches a caller by this path -- in particular, parse_post_url's ValueError
    names the URL and the accepted hosts, and is logged one layer down rather
    than repeated here (FR-005).
    """
    if result.problem == jobs.INVALID_URL:
        return _error(400, "invalid_url", "That is not a valid X post URL.")

    if result.problem == jobs.RATE_LIMITED:
        # Retry-After is required by FR-019 -- "a refusal MUST state when the
        # caller may retry" -- and a header a client already knows how to obey
        # beats a sentence only a human reads. The seconds figure is computed
        # from the service's own clock and window, so interpolating it carries
        # nothing about the request or the server's filesystem.
        seconds = result.retry_after or 1
        response = _error(
            429,
            "rate_limited",
            f"Too many submissions. Try again in {seconds} seconds.",
        )
        response.headers["Retry-After"] = str(seconds)
        return response

    if result.problem == jobs.DISK_LOW:
        # Says nothing about the volume, the threshold, or how much is left.
        # Those are facts about the server, and FR-029 admits none of them.
        return _error(
            503,
            "insufficient_storage",
            "The service is temporarily out of space. Try again later.",
        )

    if result.problem == jobs.AT_CAPACITY:
        # Likewise silent about the queue depth and how many jobs are pending.
        return _error(
            503,
            "at_capacity",
            "The service is at capacity. Try again shortly.",
        )

    # A problem this table does not know is a bug in the layer below, not a
    # caller error. It gets the generic body rather than its own code leaking
    # out as an unplanned vocabulary word.
    _log.error("unmapped submission problem %r", result.problem)
    return _error(500, "internal_error", "The service could not complete that request.")


def _lookup(handle: str) -> jobs.Job | None:
    """Resolve a handle, treating a malformed one exactly like an unknown one.

    The shape check is NOT expressed as a route pattern on purpose. A FastAPI
    `Path(pattern=...)` produces a 422 on mismatch, and a 422 would tell a caller
    that their input was the wrong shape -- which is one bit more than they get
    for a well-formed handle that simply does not exist. Both answers must be
    the same answer (FR-028).

    No constant-time comparison is used here and none is claimed. The defence is
    the 256-bit space: an attacker cannot collect enough valid-handle samples
    for a timing signal to mean anything, and asserting a property Python's dict
    internals would not guarantee would be worse than saying so plainly.
    """
    if not jobs.is_valid_handle(handle):
        return None
    return jobs.get(handle)


def _as_response(job: jobs.Job) -> JobResponse:
    progress = None
    if job.state == jobs.RUNNING:
        percent = None
        if job.total_bytes and job.downloaded_bytes is not None:
            percent = round(job.downloaded_bytes / job.total_bytes * 100, 1)
        progress = Progress(
            downloaded_bytes=job.downloaded_bytes,
            total_bytes=job.total_bytes,
            percent=percent,
        )

    failure = None
    if job.failure_code is not None:
        # The code is the only failure information the record carries; the
        # sentence comes from a table of literals. There is no path by which the
        # downloader's own text could arrive here.
        failure = Failure(
            code=job.failure_code,
            message=jobs.FAILURE_MESSAGES[job.failure_code],
        )

    return JobResponse(
        handle=job.handle,
        state=job.state,
        file_count=len(job.files),
        progress=progress,
        failure=failure,
        created_at=_iso(job.created_at),
        completed_at=_iso(job.completed_at),
    )


# --------------------------------------------------------------------------
# The handlers that replace the leaking defaults
#
# Both frameworks ship defaults that are helpful in development and wrong on a
# public service. Neither is left in place.
# --------------------------------------------------------------------------

# Fixed bodies for the statuses the framework itself can raise. A 404 from the
# router is given the same body as a 404 from a handler, so a path that matches
# no route is indistinguishable from a handle that resolves to nothing.
_STATUS_BODIES: dict[int, tuple[str, str]] = {
    404: ("not_found", "No such job."),
    405: ("method_not_allowed", "That method is not allowed here."),
    413: ("too_large", "That request was too large."),
}
_GENERIC_BODY = ("request_failed", "The service could not complete that request.")


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Replace pydantic's default 422, which echoes the caller's input.

    The default body includes an "input" field carrying the offending value
    verbatim -- so a request with {"output_dir": "/etc/shadow"} gets that string
    reflected straight back. FR-005 forbids echoing submitted content, so the
    detail goes to the log and the caller gets one fixed sentence.
    """
    _log.info("rejected malformed request: %s", exc.errors())
    return _error(422, "invalid_request", "The request body was not understood.")


@app.exception_handler(StarletteHTTPException)
async def _http_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Give framework-raised errors the same body shape as our own.

    Without this, a request to a path matching no route returns Starlette's
    {"detail": "Not Found"} while an unknown handle returns ours -- two
    different refusals for two kinds of nothing, which is exactly the
    distinction FR-028 exists to remove.

    exc.detail is deliberately not forwarded: it is framework text, and the
    rule is that no caller-visible string originates outside this file.
    """
    code, message = _STATUS_BODIES.get(exc.status_code, _GENERIC_BODY)
    return _error(exc.status_code, code, message)


@app.exception_handler(Exception)
async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
    """The last line of FR-029.

    Anything that reaches here is a bug, and a bug's traceback names files,
    directories, and library internals. The operator gets all of it in the log;
    the caller gets one sentence and no clue about what runs on the server.
    """
    _log.exception("unhandled error serving a request", exc_info=exc)
    return _error(500, "internal_error", "The service could not complete that request.")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.post(
    "/jobs",
    status_code=202,
    response_model=JobResponse,
    responses={
        400: {"model": Error},
        429: {"model": Error},
        503: {"model": Error},
    },
)
def submit_job(body: SubmitRequest, request: Request) -> JobResponse | JSONResponse:
    """Accept a URL and answer immediately with a handle (FR-001).

    The response for a deduplicated submission is identical to a fresh one. The
    caller cannot tell, and has no reason to care -- they hold a handle for the
    work they asked for either way (FR-016).
    """
    # No `download` and no `now` argument. Both are test seams and this layer
    # must never be the thing that supplies them.
    result = jobs.submit(body.url, _caller(request))

    if result.problem is not None:
        return _refused(result)

    assert result.job is not None  # a result carries a job or a problem
    return _as_response(result.job)


@app.get("/jobs/{handle}", response_model=JobResponse, responses={404: {"model": Error}})
def get_job(handle: str) -> JobResponse | JSONResponse:
    job = _lookup(handle)
    if job is None:
        return _not_found()
    return _as_response(job)


@app.get(
    "/jobs/{handle}/file",
    # response_model=None because the return type is a union of two Response
    # classes, which FastAPI would otherwise try to build a Pydantic model from.
    response_model=None,
    response_class=FileResponse,
    responses={404: {"model": Error}, 409: {"model": Error}, 410: {"model": Error}},
)
def get_job_file(handle: str) -> FileResponse | JSONResponse:
    """The file, when the job produced exactly one (FR-035).

    An index is deliberately not required here: a single-video post is the
    common case and forcing an index on it would make the ordinary path the
    awkward one.
    """
    return _serve(handle, None)


@app.get(
    "/jobs/{handle}/file/{index}",
    response_model=None,
    response_class=FileResponse,
    responses={404: {"model": Error}, 409: {"model": Error}, 410: {"model": Error}},
)
def get_job_file_by_index(handle: str, index: int) -> FileResponse | JSONResponse:
    """One file of a multi-file job, 1-based.

    An out-of-range index gives the same 404 as an unknown handle. The count is
    already available from GET /jobs/{handle}, so separating them would disclose
    nothing the caller could not already read (FR-036).
    """
    return _serve(handle, index)


def _serve(handle: str, index: int | None) -> FileResponse | JSONResponse:
    job = _lookup(handle)
    if job is None:
        return _not_found()

    result = jobs.file_for(job, index)

    if result.problem == "index_required":
        # The count is server-derived and already visible in the job's status,
        # so naming it here discloses nothing new.
        return _error(
            409,
            "index_required",
            f"This job produced {result.file_count} files. Request one by index.",
        )
    if result.problem == "not_ready":
        return _error(409, "not_ready", "This job has not finished yet.")
    if result.problem == jobs.FAILED:
        return _error(409, "failed", jobs.FAILURE_MESSAGES[job.failure_code or jobs.UNCLASSIFIED])
    if result.problem == jobs.EXPIRED:
        return _error(410, "expired", "This file has expired and is no longer available.")
    if result.path is None:
        return _not_found()

    # The path comes only from the job's own recorded result; nothing in the
    # request reaches it (FR-030). The filename offered to the caller is the
    # file's own basename, which the frozen build_target already sanitised down
    # to [A-Za-z0-9_-] plus an extension.
    return FileResponse(result.path, filename=result.path.name)


class HealthResponse(BaseModel):
    """Aggregate counts only. No handle, no URL, no address, no path.

    This endpoint is unauthenticated and reachable by anyone who can reach the
    port, so what it may carry is decided by that fact rather than by what would
    be convenient for the operator.
    """

    status: str
    running: int
    waiting: int
    wedged_workers: int


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness and capacity.

    `wedged_workers` is the visible face of the limitation ADR-0002 accepts: a
    download whose ffmpeg merge hung holds a worker thread until restart. It is
    exposed because it is the difference between "the service is slow" and "the
    service has permanently lost half its capacity and needs restarting", and an
    operator cannot tell those apart from response times.

    The counting happens in jobs.health(); walking the registry here would be
    domain iteration in a request handler.
    """
    return HealthResponse(**jobs.health())


def _caller(request: Request) -> str:
    """The calling address, for the audit record (FR-031) and the later rate limit.

    Reads request.client and NOTHING else. In particular it does not look at
    X-Forwarded-For, X-Real-IP, or any other header a caller can set. Trusting
    one of those from an untrusted source would let every caller choose their
    own identity, which defeats a per-address rate limit completely and fills
    the audit log with whatever an abuser felt like typing.

    Whether a proxy header may be believed is settled one layer out, in
    uvicorn's start-up flags, because only the operator knows what sits in front
    of the port. Note that uvicorn believes X-Forwarded-For BY DEFAULT from
    127.0.0.1 (proxy_headers=True, forwarded_allow_ips="127.0.0.1"), so the
    dangerous configuration is the one with no flags at all: a directly exposed
    service must be started with --no-proxy-headers, or anything that can reach
    the port from localhost can choose its own address and escape the rate limit
    this value feeds. research.md D9 carries the full three-case table; it is
    not restated here, because it has already been wrong in four places at once.

    request.client is None for transports that have no peer address, so the
    fallback keeps the audit line well-formed rather than crashing a submission
    over a missing field.
    """
    return request.client.host if request.client else "unknown"
