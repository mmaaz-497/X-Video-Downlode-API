# Phase 0 Research: HTTP Download API

**Feature**: 002-http-download-api | **Date**: 2026-08-13
**Verified against**: Python 3.13.5, yt-dlp **2026.07.04**, FastAPI 0.115.0, Starlette 0.38.6,
uvicorn 0.31.1, pydantic 2.11.7

Every claim below about yt-dlp behaviour was read out of the installed source, with file and line
cited. Nothing here is recalled from memory, and two of the findings **contradict assumptions written
into spec.md** — see D4, which corrects the spec's guess about where the unguarded window is.

---

## D1 — Running the blocking download off the event loop

**Decision**: `backend/jobs.py` owns a module-level
`concurrent.futures.ThreadPoolExecutor(max_workers=XVD_MAX_CONCURRENT)` and dispatches each job with
`executor.submit(...)`. `jobs.py` imports **no asyncio and no FastAPI** — it is a plain synchronous
module. `backend/api.py` handlers only mutate and read in-memory state, so they never block.

**Rationale**:

`download_post()` is synchronous and runs for minutes. There are four ways to get it off the event
loop, and only one is right here:

| Mechanism | Why rejected / chosen |
|---|---|
| Call it from an `async def` handler | **Fatal.** Blocks the single event loop thread for the whole transfer; every other caller — including status polls — freezes. This is the failure the spec calls the central problem. |
| `BackgroundTasks` (FastAPI) | **Rejected.** A sync background function runs in *anyio's* shared worker threadpool (default 40 threads), which also serves every `def` endpoint. Downloads would compete with request handling, there is no bound we own, and `BackgroundTasks` runs *after* the response is sent with no handle on the running work — so nothing can observe or time-limit it. |
| `asyncio.to_thread` / bare `run_in_executor(None, …)` | **Rejected.** Same shared, unowned default executor. The concurrency cap (FR-015) would have no place to live. |
| **Own `ThreadPoolExecutor`** | **Chosen.** We own its size, so the size *is* the cap. `submit()` returns immediately, satisfying SC-001. Keeps `jobs.py` free of asyncio, which means the service layer is testable with plain function calls and no event loop — directly serving Principle II's "no mocking frameworks". |

Threads rather than processes is safe for throughput here: the work is network I/O plus an `ffmpeg`
subprocess, both of which release the GIL. The one thing threads cannot do — be killed — is the
subject of D4.

**Alternatives considered**: `ProcessPoolExecutor`. It is the only option that can genuinely
*terminate* a wedged download. Rejected for Phase 1 because progress callbacks would have to cross a
process boundary through a queue, Windows `spawn` re-imports the whole package per worker, and the
constitution's governance rule ("when in doubt, choose the simpler option") applies. D4 records
exactly what this costs, rather than pretending it costs nothing.

---

## D2 — What enforces the concurrency cap: pool size, semaphore, or both

**Decision**: **The pool size alone.** `max_workers=N` is the only mechanism. No semaphore.

**Rationale**:

The executor's own queue gives the correct FR-015 semantics for free. A job record is created in
`waiting` at submission; the very first statement of the worker function transitions it to `running`.
So "waiting" means *queued in the executor*, and "running" means *a worker picked it up* — the two
states are exactly the two the pool already distinguishes, with no second bookkeeping to drift out of
sync. Submissions beyond the cap are held, not dropped, which is what FR-015 demands.

A semaphore is *not* redundant for one specific reason, and it is worth stating why it still loses.
If a worker thread wedges permanently (D4), a pool slot is lost forever, whereas a semaphore could be
force-released by the watchdog to restore capacity. But the wedged thread still occupies the pool, so
an oversized-pool-plus-semaphore design only converts "capacity lost immediately" into "capacity lost
once the headroom is used up" — while introducing a second source of truth about how many downloads
are running, which can disagree with the first. One mechanism that degrades honestly beats two that
can contradict each other.

**Consequence, and how it was resolved**: the executor's queue is unbounded by default. The disk
guard (FR-018) and the rate limit (FR-019) bound submissions per-address but not globally, so a
distributed burst could grow the queue without limit.

> **Resolved 2026-08-13 — the owner adopted `XVD_MAX_PENDING` (default 50)**, and FR-015 was amended
> to match: submissions are held in `waiting` up to that depth and refused with an at-capacity
> message beyond it. The rationale recorded with the decision is that an unbounded queue on a public
> service is a memory-exhaustion path and the per-address rate limit does not bound the aggregate
> across many addresses. The cap sits far above the concurrency limit, so ordinary over-limit
> submissions behave exactly as FR-015 originally described. **Implementation belongs to Phase 3**;
> the requirement was amended first so no earlier phase is built against wording that will move.

---

## D3 — Job record durability without a database

**Decision**: One JSON file per job at `<state_dir>/jobs/<handle>.json`, written **temp-then-
`os.replace`** into the same directory — the identical pattern `_promote` established in
`backend/downloader.py:315`, for the identical reason (atomic within a filesystem; `shutil.move` is
not).

**Authority**: the **in-memory dict is authoritative** for the life of the process. Disk is a
crash-recovery record, read exactly once during start-up and never read again.

**Consistency rule**: every *state transition* mutates memory first, then writes the file. **Progress
updates mutate memory only** — they arrive per chunk and writing each one would be a write storm for
no benefit, since progress is meaningless after a restart anyway (the job is failed as interrupted).
If a disk write fails, it is logged and the job continues on memory alone; the only thing that
degrades is restart-recovery accuracy, and the job itself still works.

**Rationale**: satisfies FR-024 with the filesystem, honouring Principle IV ("no database, no queue")
and the constitution's "persistence: filesystem only". Reading the whole directory once at start-up
is O(jobs) and trivially fast at this scale.

**Filename is the handle itself.** Considered and rejected: naming files by `sha256(handle)` to keep
the secret out of directory listings. It protects nothing — the file's *contents* contain the handle,
so anyone who can list the directory can already read it. A second identifier with no security gain
is exactly the speculative machinery the constitution forbids. The directory is created `0700`.

**Path-traversal answer (Principle V)**: a caller-supplied handle is **never** used to build a path.
Lookup goes through the in-memory dict; only handles this service generated are ever turned into
filenames. The API therefore cannot be made to touch the filesystem by handle at all, which is a
structural guarantee rather than a validation rule. A syntactic check (`^[A-Za-z0-9_-]{43}$`) is
still applied at the boundary as the cheap second layer, matching the belt-and-braces posture
`build_target` already takes at `backend/validation.py:185`.

**Not fsync'd**: `os.replace` is atomic but the data may still be in the page cache on a power loss.
Accepted — the failure mode is a stale record for a job that a restart would have failed anyway.

**Location**: `<state_dir>` defaults to `<output_dir>/.xvd-state`, configurable via `XVD_STATE_DIR`.
Placing it under the output directory keeps it on the volume the disk guard already measures. The
prefix does not collide with the `.tmp-xvd-*` pattern that FR-026's sweep matches.

---

## D4 — Time-limit enforcement, and where the unguarded window actually is

**This section corrects spec.md.** The spec assumed the unguarded window was *pre*-transfer metadata
resolution. Reading the source shows that is bounded, and that the genuinely unbounded window is
*post*-transfer.

**Verified findings**:

1. **Raising from the progress hook does abort the download.** `_hook_progress` calls each hook with
   no `try`/`except` (`yt_dlp/downloader/common.py:488-494`), so the exception propagates. The
   handler in `YoutubeDL.process_info` catches only `network_exceptions`, `OSError`, and
   `ContentTooShortError` (`yt_dlp/YoutubeDL.py:3597-3602`) — a `RuntimeError` is not among them and
   is not swallowed. Even in the event that some other layer absorbed it, `download_post` has a
   backstop: it raises when `ydl.download()` returns a non-zero retcode
   (`backend/downloader.py:511-512`). Either path lands in `download_post`'s handlers and runs the
   `finally` that removes the temp directory (`:536-541`). **Abort and cleanup are guaranteed by two
   independent paths.**

2. **Metadata resolution is bounded, contrary to the spec's assumption.** yt-dlp applies
   `DEFAULT_TIMEOUT = 20` seconds to every request when `socket_timeout` is unset
   (`yt_dlp/networking/common.py:34` and `:242`, reached via `YoutubeDL.py:4367`). `_base_options`
   does not set `socket_timeout`, so the 20-second default applies to the metadata pass. A dead host
   errors out; it does not hang forever. Retries multiply this into tens of seconds, not infinity.

3. **The `ffmpeg` merge is the unguarded window.** After the transfer completes, yt-dlp merges video
   and audio by calling `Popen.run(cmd, …)` with **no `timeout` argument**
   (`yt_dlp/postprocessor/ffmpeg.py:356`), which reaches `communicate_or_kill(timeout=None)`
   (`yt_dlp/utils/_utils.py:919-925`) and blocks indefinitely. Progress hooks have already stopped by
   then; post-processing reports through a *separate* `postprocessor_hooks` list that `_base_options`
   does not populate and that we cannot add to without modifying the frozen module.

**Decision**:

- The deadline is enforced inside the progress callback that `jobs.py` already passes to
  `download_post`. The closure captures the job's `started_at`; on each invocation, if
  `now > started_at + XVD_JOB_TIMEOUT`, it sets a flag on the job and raises
  `RuntimeError("job time limit exceeded")`.
- **`RuntimeError`, deliberately.** Principle VI names it as an approved built-in and forbids custom
  hierarchies — and equally important, it must not subclass `OSError` or any network exception, or
  yt-dlp's handler at `YoutubeDL.py:3597` would swallow it and merely report an error.
- The jobs layer identifies the timeout **by its own flag, never by parsing the message**. This
  matters: `download_post` wraps the text as `f"download failed for video {position}: {detail}"`
  (`backend/downloader.py:534`), which no classifier should have to reverse-engineer.
- A separate watchdog, running in the periodic sweep, marks any `running` job past its deadline as
  failed with the time-limit code **even if no callback ever fires**. This decouples the *job's*
  state from the *thread's* state, so the caller is never left waiting on a wedged worker.

**The honest residual gap** (FR-020 is not fully satisfiable through this boundary):

> A hang inside the `ffmpeg` merge fires no callback, so nothing raises. The watchdog will fail the
> job so the caller learns the outcome, but **the worker thread stays wedged for the life of the
> process** — Python threads cannot be killed. With the default `max_workers=2`, one wedged merge
> halves the service's capacity permanently until an operator restarts it. The service degrades; it
> does not die.

Mitigations chosen: log a distinctly identifiable operator warning when the watchdog fails a job that
the worker never returned from, and expose a wedged-worker count on the health endpoint so the
condition is visible rather than mysterious. Mitigation **not** chosen: `ProcessPoolExecutor` (D1),
which would solve it completely at a real complexity cost. This is recorded as a known limitation of
the frozen-module boundary, not as solved.

---

## D5 — Failure-code classification, pinned against drift

**Decision**: classify `DownloadOutcome.message` with `str.startswith` against the exact explanation
strings in `downloader._ERROR_DIAGNOSES`, and pin the table with a test that reads the private table
and asserts full coverage.

**Why `startswith` is exact, not a heuristic**: `_partial_failure` composes the message as
`f"{reason} Files already saved: {names}"` (`backend/downloader.py:559`), and the plain path returns
the reason alone. The diagnosis is therefore always a **prefix** of the message. Substring matching
would be looser for no gain.

**The map** (explanations read from `backend/downloader.py:71-89` and the literals at `:388`, `:397`,
`:437`, `:442`, `:486`, `:527`):

| Message prefix | Code |
|---|---|
| `this post has no video in it.` | `no_video` |
| `this post contains media, but it is not a video.` | `not_a_video` |
| `this post belongs to a protected account` | `protected_account` |
| `this post is age-restricted` | `age_restricted` |
| `this post could not be found. It may have been deleted.` | `post_unavailable` |
| `ffmpeg was not found on PATH.` | `service_unavailable` (operator fault, not caller fault) |
| `Interrupted by the operator.` | `interrupted` |
| `could not extract video from this post. yt-dlp said:` | `unclassified` — **and this one embeds verbatim yt-dlp text**, so it is doubly bound by D6 |
| anything else, incl. `download failed for video N: …` | `unclassified` |

Two codes come from the jobs layer rather than from a message: `time_limit` (D4's flag) and
`interrupted` when set by restart recovery.

**Drift detection**: the test imports `backend.downloader._ERROR_DIAGNOSES` and asserts every
`explanation` in it is matched by exactly one entry in our map. If upstream feature work edits, adds,
or reorders a row, that test fails loudly — which is precisely the behaviour spec.md demands, since
the alternative is silent decay into `unclassified`. Importing a private name is acceptable *in a
test*; production code matches on the literal strings it declares itself.

**Alternatives considered**: matching yt-dlp's own error text instead of the module's explanations —
rejected, because that couples us to a third-party library's unversioned strings *twice over*, and
the frozen module is already doing that job. Regular expressions — rejected as more machinery than
`startswith` for a set of fixed literals.

---

## D6 — Making message leakage structurally impossible

**Decision**: `DownloadOutcome.message` never enters the job record. The record holds
`failure_code: str | None` and nothing else about the failure. The raw text is passed to the logger
at the single call site where `download_post` returns, and is then discarded.

**Why this is structural rather than disciplinary**: the serializer in `api.py` can only render
fields that exist on the record. If no field holds raw text, no amount of future carelessness in a
response model can forward it — there is nothing to forward. Adding such a field would be a visible,
reviewable change, not an accident. This is the "single choke point" the plan asks for, achieved by
*absence of a field* rather than by a sanitising function that someone could route around.

Caller-visible text comes from one module-level `dict[str, str]` mapping code → fixed English
sentence. Every sentence is a literal in our source; none is interpolated from anything.

**What this defends against, concretely** — all verified present in the frozen modules:

- `backend/downloader.py:332-334` — the `_promote` failure names the temp directory, and the generic
  handler at `:529-535` folds it into `message`.
- `backend/downloader.py:309-312` — the cleanup warning names the temp directory.
- `backend/validation.py:186-188` — the containment check names both the candidate path and the
  output root, and it reaches the API as a raised `ValueError`.
- `backend/downloader.py:132` — the generic diagnosis embeds yt-dlp's verbatim error text.

**Exception handling follows the same rule**: `api.py` installs a catch-all handler that logs the
traceback and returns one fixed body. `ValueError` from `download_post` is caught in the worker,
logged, and recorded as `unclassified` — its text is never carried forward.

**FastAPI specifics that must be handled** (each would otherwise leak):

- Pydantic validation errors return the offending input under `"input"` by default. The 422 handler
  is replaced with one that returns a fixed body, satisfying FR-005's "does not echo the submitted
  content back".
- `docs`/`redoc`/`openapi.json` are left enabled — they describe the contract, not the internals —
  but the app is created with `debug=False` so no traceback middleware is installed.

---

## D7 — Retention and restart sweeps: what runs them

**Decision**: one periodic `asyncio` task started in FastAPI's `lifespan`, looping
`await asyncio.sleep(XVD_SWEEP_INTERVAL)` and calling `jobs.sweep()` via `asyncio.to_thread` (disk
I/O must not run on the loop). Start-up recovery (FR-025, FR-026) runs once in `lifespan`, **before
the server accepts requests**.

`jobs.sweep()` performs, in order: fail jobs past their deadline (D4's watchdog); expire finished
jobs past retention; prune rate-limit buckets.

**Rejected**: APScheduler (a dependency for one `sleep` loop — Principle IV); cron (not application
behaviour, and FR-023 requires it to run without operator intervention); sweeping on each request
(makes an unrelated caller pay for cleanup, and stops entirely when the service is idle — which is
exactly when files are sitting around expiring).

**FR-023, retention versus an in-flight retrieval**: the sweep **marks the job `expired` first, then
attempts deletion**. Ordering matters — a retrieval that begins after the mark is refused with
"expired" rather than racing a disappearing file. For a retrieval already in flight, POSIX `unlink`
leaves the open file readable until the reader closes it, so the response completes intact; that is
the deployment target. On Windows (development only) the delete raises `PermissionError` while the
file is open, so the sweep **tolerates the failure and retries on the next pass** — the same
reasoning `_remove_temp_dir` documents at `backend/downloader.py:270-292`, and for the same
underlying cause.

**Start-up sweep of temp directories (FR-026)**: remove `.tmp-xvd-*` directories from the output
directory. Safe at start-up specifically because the service is single-process (Assumption 5) and
nothing is downloading yet. Note the CLI could in principle be running concurrently; the sweep is
therefore start-up-only and never periodic, so it cannot delete a live CLI download's temp directory.

---

## D8 — Handle generation

**Decision**: `secrets.token_urlsafe(32)`.

**Verified**: `token_urlsafe(nbytes)` returns base64url of *nbytes* random bytes from
`secrets.token_bytes`. 32 bytes = **256 bits** of entropy in a 43-character string (measured, not
assumed). SC-011 requires ≥128 bits; 16 bytes would meet it exactly, and 32 is chosen for margin at
zero cost.

The character set is `[A-Za-z0-9_-]`, which is URL-path-safe and filename-safe on both target
platforms without escaping.

**On FR-028's "no observable timing difference"**: lookup is a single dict hit on the full 43-char
key and both branches return the identical response object, so there is no secret-dependent branch to
measure. We do **not** claim constant-time comparison, and do not need to: at 256 bits, an attacker
cannot assemble enough valid-handle samples for a timing signal to mean anything. Stating the real
reason beats asserting a property we would not be able to guarantee across Python's dict internals.

---

## D9 — Rate limiting

**Decision**: in-memory `dict[str, deque[float]]` keyed by caller address, holding submission
timestamps within the window. On each submission, evict timestamps older than the window, compare the
length to the limit, and on refusal report the retry time as `oldest_timestamp + window`. Empty
buckets are pruned by the periodic sweep so the dict cannot grow without bound from one-off callers.

**Across a restart**: the state is lost, so every caller gets a fresh allowance immediately after a
restart. This is stated rather than hidden. Persisting it was rejected: it would mean a disk write on
the hot submission path to defend against an attacker who would have to be able to restart the
service to exploit it — and anyone who can do that has already won.

**Caller identity, and a deployment requirement this creates**: the address comes from
`request.client.host`. **Behind a reverse proxy this is the proxy's address**, which would collapse
every caller into one bucket and make the FR-031 audit record useless. Proxy configuration is out of
scope for this feature, but the *application* must be able to honour forwarded headers, so uvicorn
must be started with `--proxy-headers --forwarded-allow-ips=<proxy ip>`. This is documented in
quickstart.md as an operational requirement. Trusting `X-Forwarded-For` unconditionally would let any
caller spoof their address and defeat the rate limit entirely, so it is never trusted by default.

---

## D10 — Serving files

**Decision**: Starlette's `FileResponse`, given a path taken **only** from the job record's stored
result — never from anything in the request (FR-030). The download filename offered to the caller is
the file's own basename, which `build_target` already sanitised through
`validation.sanitize_handle`, so it contains only `[A-Za-z0-9_-]` plus the extension.

Index selection (FR-035/FR-036) is resolved against the record's stored tuple of paths: no index and
exactly one file → serve it; no index and several → refuse with the count; an index → bounds-check
against the tuple. An index that is out of range, zero, negative, or non-integer is refused with the
same message as any other bad index.

**Existence re-check before serving**: the file may have been removed between the state check and the
response (retention, or an operator). If it is missing at serve time, the job is marked `expired` and
the caller is told so — never a partial or empty body (FR-014).

---

## D11 — Where pydantic is and is not allowed

**Decision**: pydantic models exist **only** in `api.py`, for the request body and the response
shapes. `jobs.py` uses plain `@dataclass` records and standard-library types exclusively, exactly as
`downloader.py` does.

**Rationale**: this is what keeps Principle III satisfiable. If the service layer's types came from
FastAPI's dependency, the layer would not be framework-free and could not be tested without it. The
dataclass boundary also makes the D6 guarantee inspectable: one can read the record definition and
see that no field holds raw text.

---

## Summary of decisions

| # | Decision | Key consequence |
|---|---|---|
| D1 | Own `ThreadPoolExecutor`; `jobs.py` has no asyncio | Handlers never block; service layer testable without an event loop |
| D2 | Pool size *alone* is the concurrency cap | One source of truth; unbounded queue needs a depth cap in Phase 4 |
| D3 | JSON per job, temp-then-`os.replace`; memory authoritative | Durable without a database; handle never becomes a path from a request |
| D4 | Deadline in the progress callback + watchdog | **Residual gap: a hung `ffmpeg` merge wedges a worker until restart** |
| D5 | `startswith` on the module's own explanations, coverage-tested | Drift fails loudly instead of decaying to `unclassified` |
| D6 | Raw message never enters the record | Leakage is impossible by absence of a field, not by discipline |
| D7 | One periodic asyncio task; start-up recovery in `lifespan` | Mark-expired-before-delete; Windows delete failure tolerated and retried |
| D8 | `secrets.token_urlsafe(32)` = 256 bits | Exceeds SC-011 with margin |
| D9 | In-memory sliding window per address | Resets on restart (stated); requires `--proxy-headers` when deployed |
| D10 | `FileResponse` from the record's stored path only | Index required only for multi-file jobs (FR-035) |
| D11 | pydantic in `api.py` only | Principle III stays satisfiable |

## Owner decisions (2026-08-13)

1. **`XVD_MAX_PENDING` (D2) — adopted.** FR-015 amended; cap implemented in Phase 3. No longer open.
2. **The wedged-worker gap (D4) — accepted as a known limitation.** It stays in the plan's Complexity
   Tracking table and generates no tasks. If it is judged unacceptable in operation, the fix is
   `ProcessPoolExecutor` (D1), and that is a re-plan rather than a patch.
