# ADR-0002: Off-Event-Loop Job Execution and Concurrency Control

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Proposed
- **Date:** 2026-08-13
- **Feature:** 002-http-download-api
- **Context:** `download_post()` is synchronous and runs for minutes — a 95 MB video took over a
  minute on a good connection. Feature 002 puts it behind an HTTP API, and `downloader.py` is frozen,
  so the call cannot be made async, cannot be given a timeout parameter, and cannot be given a cancel
  hook. Three requirements land on the same mechanism at once: FR-001 (submission must return without
  waiting), FR-015 (a hard cap on simultaneous downloads), and FR-020 (a stalled download must not
  hold a slot forever). Whatever runs the download decides all three, which is why they are one
  decision rather than three.

<!-- Significance checklist (ALL must be true to justify this ADR)
     1) Impact: Long-term consequence for architecture/platform/security?
        YES -- it fixes the module boundary between backend/jobs.py and backend/api.py, and it is
        what makes Constitution Principle III satisfiable at all.
     2) Alternatives: Multiple viable options considered with tradeoffs?
        YES -- five, below; two of them (semaphore, process pool) are genuinely defensible and one
        is named as the fix if the accepted limitation proves intolerable.
     3) Scope: Cross-cutting concern (not an isolated detail)?
        YES -- jobs.py, api.py, the Job state machine, FR-001, FR-015, FR-020, and SC-001/SC-006.
-->

## Decision

Downloads run in a **service-owned thread pool**, and the pool's size is the only thing that limits
concurrency. Five components, adopted together:

- **Executor**: `backend/jobs.py` owns a module-level
  `concurrent.futures.ThreadPoolExecutor(max_workers=XVD_MAX_CONCURRENT)` (default 2) and dispatches
  each job with `executor.submit(...)`. Submission returns as soon as the future is queued.
- **Cap**: `max_workers` **is** the concurrency cap. There is no semaphore. The executor's own queue
  is the FR-015 waiting queue: a job is `waiting` while queued and becomes `running` as the worker's
  first statement, so the two states are the two the pool already distinguishes.
- **Isolation**: `jobs.py` imports **neither FastAPI nor asyncio**. It is a plain synchronous module.
  `api.py` handlers only read and mutate in-memory state, so no handler ever blocks the event loop.
- **Time limit**: the progress callback that `jobs.py` passes to `download_post` captures the job's
  `started_at` and raises `RuntimeError("job time limit exceeded")` once the deadline passes. A
  watchdog in the periodic sweep independently marks any over-deadline `running` job as failed, so
  the *job's* state is never hostage to the *thread's* state.
- **Accepted limitation**: a download whose `ffmpeg` merge hangs wedges its worker thread for the
  life of the process. The job is failed by the watchdog so no caller waits forever, but capacity
  degrades until restart. A `wedged_workers` counter on `/health` makes it visible.

Three facts, read out of yt-dlp 2026.07.04 rather than assumed, are what make this work:

```text
downloader/common.py:488-494   _hook_progress calls each hook with NO try/except
                               -> raising from the progress callback aborts the download
YoutubeDL.py:3597-3602         process_info catches only network_exceptions, OSError,
                               ContentTooShortError -> a RuntimeError is NOT swallowed
backend/downloader.py:511-512  download_post raises on a non-zero retcode
                               -> a second, independent path to the same abort
```

The exception type is therefore load-bearing in two directions at once: Principle VI approves
`RuntimeError` as a built-in, **and** it must not subclass `OSError` or a network exception or
yt-dlp's own handler would absorb it. Cleanup is guaranteed regardless of which path fires, because
`download_post`'s `finally` removes the temp directory (`backend/downloader.py:536-541`).

## Consequences

### Positive

- **SC-001 is structural, not tuned.** `submit()` returns in microseconds regardless of transfer
  size or how many downloads are already running, because nothing about the response path touches the
  download.
- **One source of truth for "how many are running."** The pool knows, and nothing else claims to.
  There is no second counter that can disagree after a crash, an exception, or an early return.
- **Principle III becomes provable rather than asserted.** Because `jobs.py` starts no event loop and
  imports no framework, `tests/test_jobs.py` can exercise the whole service layer with plain function
  calls. If the boundary were ever violated, that test file could not be written in the planned form
  — the test suite *is* the enforcement mechanism.
- **The `waiting`/`running` distinction costs nothing.** It falls out of the executor's queue instead
  of requiring bookkeeping that could drift from reality.
- Threads are the right shape for the work: network I/O and an `ffmpeg` subprocess both release the
  GIL, so a pool of 2 genuinely downloads 2 videos at once.

### Negative

- **A hung `ffmpeg` merge permanently costs a worker thread.** Verified: the merge calls
  `Popen.run(cmd, …)` with no `timeout` (`postprocessor/ffmpeg.py:356` → `utils/_utils.py:919-925`),
  and post-processing reports through a separate `postprocessor_hooks` list that `_base_options` does
  not populate and that we cannot add to without modifying a frozen module. No callback fires, so
  nothing raises, and Python threads cannot be killed. At the default `max_workers=2`, one wedged
  merge halves throughput until an operator restarts the service. **FR-020 is therefore partially
  satisfied, and this ADR is where that is written down.**
- **This corrects an assumption in spec.md**, which recorded the unguarded window as *pre*-transfer
  metadata resolution. That window is in fact bounded: yt-dlp applies `DEFAULT_TIMEOUT = 20` seconds
  to every request when `socket_timeout` is unset (`networking/common.py:34` and `:242`, reached via
  `YoutubeDL.py:4367`), and `_base_options` leaves it unset. The real gap is after the transfer, not
  before it. Anyone acting on the spec's wording alone would guard the wrong thing.
- **The executor queue is unbounded.** The rate limit is per-address and the disk guard is a
  threshold, so neither bounds a distributed burst. A queue depth cap is proposed for a later phase
  and is flagged as an addition beyond FR-015, which says over-limit submissions must be *held, not
  dropped*.
- **Job and thread can disagree.** After the watchdog fails a job, its worker may still be alive and
  may still return normally. The state machine must therefore refuse to leave a terminal state
  (data-model.md invariant 1). This is a real race, not a theoretical one, and it exists *because* of
  the watchdog.
- The cap is per-process, so the design assumes a single uvicorn worker. Running `--workers 2` would
  silently double the effective concurrency and give each process its own job registry. This
  constrains deployment, which is otherwise out of scope for the feature.

## Alternatives Considered

**Alternative A — Call `download_post()` from an `async def` handler.**
*Pros*: no machinery at all.
*Rejected because*: it blocks the single event loop thread for the entire transfer, freezing every
other caller including status polls. This is the exact failure the feature exists to avoid; it is
listed only because it is the thing a reader might assume was overlooked.

**Alternative B — FastAPI `BackgroundTasks`, or `asyncio.to_thread` / `run_in_executor(None, …)`.**
*Pros*: no executor to own; idiomatic FastAPI; fewer lines.
*Rejected because*: all three land in anyio's shared worker threadpool (default 40 threads), which
also serves every `def` endpoint — downloads would compete with request handling for the same
threads, and there is no bound we own, so FR-015 would have nowhere to live. `BackgroundTasks`
additionally runs *after* the response is sent with no handle on the work, so nothing could observe
progress or apply a deadline to it.

**Alternative C — Oversized pool plus a `threading.Semaphore(N)` as the cap.**
*Pros*: genuinely better in one specific case — the watchdog could force-release the semaphore when
it fails a wedged job, restoring capacity that a pool slot cannot give back.
*Rejected because*: the wedged thread still occupies the pool, so this converts "capacity lost now"
into "capacity lost once the headroom is exhausted" — it defers the problem rather than solving it,
while introducing a second source of truth about how many downloads are running that can disagree
with the first. One mechanism that degrades honestly beats two that can contradict each other.
**Worth revisiting only if paired with a genuine kill mechanism, which means Alternative D.**

**Alternative D — `concurrent.futures.ProcessPoolExecutor`.**
*Pros*: the only option that actually solves the accepted limitation. A wedged process can be
terminated, so a hung `ffmpeg` merge costs one download rather than one worker permanently.
*Rejected because*: progress callbacks would have to cross a process boundary through a queue, the
job registry would need to live in one process while work happens in another, and Windows `spawn`
re-imports the whole package per worker. The constitution's governance rule ("when in doubt, choose
the simpler option") decides it at this scale — two concurrent downloads on one small VPS.
**This is the named fix if the wedged-worker limitation proves intolerable in operation. It is a
re-plan, not a patch.**

**Alternative E — Chosen: service-owned thread pool, size as the cap, deadline in the callback.**
*Pros*: satisfies FR-001 and FR-015 completely with one mechanism; keeps `jobs.py` framework-free and
therefore testable without an event loop; enforces FR-020 for the transfer phase, which is where
nearly all of the elapsed time is.
*Cons*: the wedged-worker gap and the unbounded queue, both recorded above.

## References

- Feature Spec: [spec.md](../../specs/002-http-download-api/spec.md) — FR-001, FR-006, FR-015,
  FR-020; SC-001, SC-006; US1, US4
- Implementation Plan: [plan.md](../../specs/002-http-download-api/plan.md) — Constitution Check
  (Principle III argued), Complexity Tracking
- Research: [research.md](../../specs/002-http-download-api/research.md) — D1 (mechanism selection),
  D2 (pool size versus semaphore), D4 (time limit and the verified gap)
- Data Model: [data-model.md](../../specs/002-http-download-api/data-model.md) — `Job` state
  transitions and invariant 1 (terminal states are never left)
- Related ADRs: [ADR-0003](./0003-caller-facing-disclosure-boundary.md) (what a failed job may say),
  [ADR-0004](./0004-filesystem-job-record-durability.md) (what survives the restart this ADR's
  limitation eventually requires)
- Evaluator Evidence:
  [PHR 0013](../prompts/002-http-download-api/0013-http-api-implementation-plan.plan.prompt.md) —
  where the yt-dlp source was read and the spec's assumption about the unguarded window was corrected
