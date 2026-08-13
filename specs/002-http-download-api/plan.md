# Implementation Plan: HTTP Download API

**Branch**: `002-http-download-api` | **Date**: 2026-08-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-http-download-api/spec.md`

## Summary

Expose feature 001's download capability over HTTP without modifying any of its three frozen modules.
A submission is validated by the existing `parse_post_url`, answered immediately with a 256-bit
handle, and dispatched to a **service-owned `ThreadPoolExecutor`** whose size *is* the concurrency
cap. `backend/jobs.py` holds all orchestration and imports neither FastAPI nor asyncio;
`backend/api.py` parses, calls down, and serialises.

Two design choices carry most of the feature's risk and are settled in
[research.md](./research.md):

- **Leakage is prevented structurally, not by discipline.** The job record has no field capable of
  holding raw text, so `DownloadOutcome.message` — which demonstrably contains absolute paths
  (`downloader.py:332`, `:309`; `validation.py:186`) — cannot reach a serializer. Absence of a field,
  not a sanitising function someone could route around.
- **The time limit is honestly partial.** Reading yt-dlp 2026.07.04 disproved the spec's assumption
  about where the gap is: metadata resolution *is* bounded (`DEFAULT_TIMEOUT = 20`,
  `networking/common.py:34`), while the `ffmpeg` merge is called with **no timeout**
  (`postprocessor/ffmpeg.py:356`) and fires no hook we can reach. A hung merge wedges a worker thread
  until restart. Recorded as a limitation, not solved.

**Deliverable scope**: US1 (fetch a video without a terminal) and US3 (callers cannot reach each
other). US2, US4, US5, and US6 are structured as follow-on phases below.

## Technical Context

**Language/Version**: Python 3.11+ (3.13.5 local), `uv`-managed
**Primary Dependencies**: FastAPI 0.115 + uvicorn 0.31 (new); pydantic 2.11 (arrives with FastAPI,
used in `api.py` only); yt-dlp 2026.07.04 and `ffmpeg` (existing, unchanged)
**Storage**: Filesystem only — one JSON file per job, temp-then-`os.replace`. No database, queue, or
cache (Principle IV)
**Testing**: pytest; one new file `tests/test_jobs.py`, no network, no HTTP integration tests, no
mocking framework
**Target Platform**: Linux VPS (deployment), Windows/PowerShell (development)
**Project Type**: Single `backend/` package (Principle I)
**Performance Goals**: Submission answered < 1 s at p95 (SC-001) regardless of load; status polls are
in-memory dict reads
**Constraints**: Three modules frozen; no caller-supplied parameter may reach the downloader; no
response may carry a path or stack trace; single process on one VPS
**Scale/Scope**: One operator, a handful of concurrent callers, 2 simultaneous downloads by default

## Constitution Check

*GATE: passed before Phase 0. Re-checked after Phase 1 — result at the end of this section.*

- [x] **I. Single Backend Folder** — adds exactly `backend/jobs.py` and `backend/api.py`. No new
      top-level directory; `tests/` already exists.
- [x] **II. Minimal Testing** — one new test file for the service layer. No network tests, no
      mocking framework, no TDD gate. The failure-code map is tested because it is the one thing
      demonstrably able to drift silently (research D5); the rest is verified from the CLI and
      `curl`, per [quickstart.md](./quickstart.md).
- [x] **III. CLI-First, API-Later** — the capability already works from the CLI and is unchanged.
      `jobs.py` is framework-free and holds the orchestration; `api.py` parses, calls, serialises.
      **See the note below** — this gate needed real argument, not a checkbox.
- [x] **IV. Lean Dependencies** — FastAPI + uvicorn only, both explicitly requested by the owner.
      **No** database, queue, ORM, cache, auth library, or scheduler. Durability is the filesystem;
      the periodic sweep is `asyncio.sleep` in a loop, not APScheduler.
- [x] **V. Security Baseline (NON-NEGOTIABLE)** — allowlisting is the frozen `parse_post_url`, called
      once, with no second validation path and no bypass. No shell interpolation anywhere.
      Filenames still come from the frozen `build_target`. **A caller-supplied handle never becomes a
      path component**: lookup is an in-memory dict, so the filesystem is unreachable from a request
      (research D3), with a syntactic handle check as the cheap second layer.
- [x] **VI. Simple Errors** — built-in exceptions only. The time-limit signal is a plain
      `RuntimeError`, chosen partly because it must *not* subclass `OSError` or a network exception,
      which yt-dlp's handler at `YoutubeDL.py:3597` would swallow. No retry or backoff is added.
- [x] **VII. VPS-Deployable** — plain Linux VPS, Python + uv + ffmpeg. Nine environment variables,
      every one with a working default; the service starts with no configuration at all.

**Principle III, argued rather than asserted.** spec.md flagged this as the gate most at risk: job
state, scheduling, deduplication, retention, disk guarding, rate limiting, and restart recovery are
new logic, they are not download logic, and they cannot go into the frozen modules. The principle
says *"Any logic that appears only in the HTTP layer is a violation and MUST be moved down."* This
plan moves it down — into `backend/jobs.py`, which imports no FastAPI, uses only dataclasses and the
standard library, and is exercised by tests that construct no HTTP client and start no event loop.
That last property is the objective proof the boundary holds: **if `jobs.py` were entangled with the
transport, its test file could not exist in the form planned.** No violation; no Complexity Tracking
entry needed.

**Post-Phase-1 re-check**: all seven gates still pass. Phase 1 introduced no new dependency, no new
directory, and no logic in `api.py` beyond parsing, calling, and serialising. The one addition beyond
spec.md (`XVD_MAX_PENDING`, research D2) is deferred to Phase 4 and flagged for the owner rather than
adopted silently.

## Project Structure

### Documentation (this feature)

```text
specs/002-http-download-api/
├── plan.md              # This file
├── spec.md              # Requirements; Q1/Q2/Q3 now resolved
├── research.md          # Phase 0 — D1..D11, all verified against installed source
├── data-model.md        # Phase 1 — Job, FailureCode, SubmissionRecord, config
├── quickstart.md        # Phase 1 — manual verification sequence
├── contracts/
│   └── openapi.yaml     # Phase 1 — the five endpoints
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 — created by /sp.tasks, NOT by this command
```

### Source Code (repository root)

```text
backend/
├── cli.py            # unchanged
├── downloader.py     # FROZEN — not modified
├── validation.py     # FROZEN — not modified
├── config.py         # FROZEN — not modified
├── jobs.py           # NEW — service logic; imports no FastAPI, no asyncio
└── api.py            # NEW — HTTP layer; parses, calls jobs.py, serialises

tests/
├── test_validation.py   # existing
├── test_downloader.py   # existing
└── test_jobs.py         # NEW — service layer, no network, no HTTP
```

**Structure Decision**: single `backend/` package per Principle I. Two new modules, no new top-level
directory, no Complexity Tracking entry required.

### What each new module owns

**`backend/jobs.py`** — the whole service layer, synchronous and framework-free:

- `Job` dataclass and the state machine (data-model.md), with terminal states never re-entered
- `submit(url, client_address) -> Job` — validate via frozen `parse_post_url`, disk guard, rate
  limit, dedup on `canonical_url`, mint the handle, persist, dispatch
- The worker: set `running`, build the deadline-checking progress callback, call `download_post`,
  classify the outcome, record a code, persist
- `get(handle) -> Job | None` — in-memory dict only; never touches the filesystem
- `file_for(job, index) -> Path` — resolves against the record's own stored tuple
- `sweep()` — deadline watchdog, retention expiry, rate-bucket pruning
- `recover()` — start-up: load records, fail non-terminal jobs, remove `.tmp-xvd-*`
- `FAILURE_PREFIXES` and `FAILURE_MESSAGES`, the two module-level dicts of D5 and D6

**`backend/api.py`** — transport only:

- pydantic request/response models (`additionalProperties: false` on the request is what makes FR-004
  enforceable at the boundary)
- Five routes, mapping `jobs.py` returns to status codes
- `lifespan`: `jobs.recover()` before serving, then the periodic sweep task
- Replaced 422 handler and a catch-all handler, both returning fixed bodies

## Implementation Phases

**Phase 1 — US1 + US3 (the deliverable).** Executor and job records; submission with validation, the
handle, and dedup; status; retrieval including the FR-035 index rule; the message-safety choke point
with a minimal failure-code set; the identical-refusal path; the audit log; `tests/test_jobs.py`.

**Two dependencies worth stating plainly**, because they make the phase boundaries less clean than
they look:

1. The concurrency cap (FR-015, nominally US4) **ships in Phase 1 whether or not it is planned**,
   because it is a property of the executor's size, not separate code. Phase 4 adds only the queue
   depth cap and the disk guard.
2. FR-029 is in scope (US3) and requires a code → safe-sentence map to exist. So the *choke point*
   and a minimal code set ship in Phase 1; US2's contribution is the full classification table and
   its drift test, not the mechanism.

**Phase 2 — US2**: the complete failure-code table, the `_ERROR_DIAGNOSES` coverage test, and
per-code caller sentences.

**Phase 3 — US4**: disk-threshold guard, per-address rate limit with `Retry-After`, the deadline
watchdog, and the `XVD_MAX_PENDING` cap (adopted by the owner; FR-015 amended accordingly).

**Phase 4 — US5**: retention sweep, the `expired` state, mark-before-delete ordering, and tolerated
delete failure on Windows.

**Phase 5 — US6**: start-up recovery to `failed`/`interrupted` and the `.tmp-xvd-*` sweep.

## Complexity Tracking

No Constitution Check gate failed, so no justification is required.

Two items are recorded not as violations but because they are places where this plan knowingly falls
short of an ideal, and a later reader deserves to find that written down rather than discover it:

| Item | Why accepted | What the alternative would cost |
|---|---|---|
| A hung `ffmpeg` merge wedges a worker thread until restart (research D4) | Threads cannot be killed; the merge is invoked with no timeout by the frozen path and fires no hook we can reach. The watchdog still fails the *job*, so no caller waits forever; capacity degrades from 2 to 1 and a restart clears it. | `ProcessPoolExecutor` solves it completely, at the cost of moving progress across a process boundary and re-importing the package per worker under Windows `spawn`. A re-plan, not a patch. |
| Failure classification matches prose from a private table in a frozen module (research D5) | It is the only route to FR-010 without modifying `downloader.py`, which is forbidden. | A `code` field on `DownloadOutcome` — one clean line, and explicitly off-limits. Mitigated by a test that reads `_ERROR_DIAGNOSES` and fails loudly on any drift. |

## Architecture Decision Records

Three decision clusters from this plan are recorded as ADRs. Each carries the alternatives that were
weighed and the consequences accepted; the plan states *what*, the ADRs state *why* and *what it
costs*.

- **[ADR-0002](../../history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md)** —
  Off-Event-Loop Job Execution and Concurrency Control. The thread pool, pool-size-as-cap, the
  framework-free service layer, and the deadline mechanism, including the accepted wedged-worker
  limitation and the process-pool fix if it proves intolerable.
- **[ADR-0003](../../history/adr/0003-caller-facing-disclosure-boundary.md)** — Caller-Facing
  Disclosure Boundary. Message safety by record shape, the literal catalog, prefix classification
  with its drift test, and one identical refusal for every unresolvable handle.
- **[ADR-0004](../../history/adr/0004-filesystem-job-record-durability.md)** — Filesystem Job Record
  Durability. One JSON file per job, atomic writes, memory as authority, and why `sqlite3` — despite
  being standard library — is excluded by Principle IV.

## Owner decisions (2026-08-13)

Both items previously open are now settled.

1. **`XVD_MAX_PENDING` — adopted.** FR-015 has been amended so that "held, not dropped" is scoped to
   a configured pending depth, with an at-capacity refusal beyond it. Implementation stays in Phase 3
   as planned; the requirement was amended immediately so Phase 1 is not built against wording that
   will move.
2. **The wedged-worker gap — accepted as a known limitation.** It remains in Complexity Tracking
   above and generates **no tasks**. `ProcessPoolExecutor` is the named fix and a re-plan, not a
   patch.
