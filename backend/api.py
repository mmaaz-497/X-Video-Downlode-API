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

import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend import jobs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Resolve configuration and build the pool before the first request lands."""
    jobs.init()
    yield
    # wait=False: a download runs for minutes and blocking shutdown on one would
    # hang every deploy. Jobs abandoned this way are recovered as interrupted on
    # the next start-up, which is a later phase.
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
# Routes
# --------------------------------------------------------------------------


@app.post(
    "/jobs",
    status_code=202,
    response_model=JobResponse,
    responses={400: {"model": Error}},
)
def submit_job(body: SubmitRequest) -> JobResponse | JSONResponse:
    """Accept a URL and answer immediately with a handle (FR-001).

    The response for a deduplicated submission is identical to a fresh one. The
    caller cannot tell, and has no reason to care -- they hold a handle for the
    work they asked for either way (FR-016).
    """
    try:
        # No `download` argument. That parameter is a test seam and this layer
        # must never be the thing that supplies it.
        job = jobs.submit(body.url, _caller())
    except ValueError:
        # parse_post_url's ValueError names the URL and the accepted hosts. It
        # is useful to an operator and is written to the log by the service
        # layer; it is not repeated here, because echoing a caller's own string
        # back to them is what FR-005 forbids.
        return _error(400, "invalid_url", "That is not a valid X post URL.")
    return _as_response(job)


@app.get("/jobs/{handle}", response_model=JobResponse, responses={404: {"model": Error}})
def get_job(handle: str) -> JobResponse | JSONResponse:
    job = jobs.get(handle)
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
    job = jobs.get(handle)
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


def _caller() -> str:
    """The calling address, for the audit record and the future rate limit.

    A placeholder until T022 threads the request through. It deliberately does
    NOT read X-Forwarded-For: trusting that header from an untrusted source
    would let any caller choose their own identity and defeat the rate limit
    outright.
    """
    return "unknown"
