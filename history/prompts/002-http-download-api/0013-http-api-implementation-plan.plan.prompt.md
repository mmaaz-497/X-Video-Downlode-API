---
id: 0013
title: HTTP API Implementation Plan
stage: plan
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.plan
labels: ["fastapi", "threadpool", "blocking-io", "yt-dlp-internals", "message-safety", "durability"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/002-http-download-api/spec.md
 - specs/002-http-download-api/plan.md
 - specs/002-http-download-api/research.md
 - specs/002-http-download-api/data-model.md
 - specs/002-http-download-api/contracts/openapi.yaml
 - specs/002-http-download-api/quickstart.md
 - specs/002-http-download-api/checklists/requirements.md
 - CLAUDE.md
 - history/prompts/002-http-download-api/0013-http-api-implementation-plan.plan.prompt.md
tests:
 - none (planning stage; tests/test_jobs.py is specified, not yet written)
---

## Prompt

CLARIFICATIONS RESOLVED — record these in spec.md before planning:
- Q1: Option A, capability model. The unguessable handle is the authorization. Unknown and unauthorized handles get identical refusals.
- Q2: Option A with one modification. The job exposes a list of files. Retrieval without an index returns the only file when the job produced exactly one, and refuses with a message naming the count when it produced several. An index is required only for multi-video jobs. Do not force an index on the common single-video case.
- Q3: Option A, fail with an interrupted reason.

STACK:
- FastAPI with uvicorn. Pydantic comes as a FastAPI dependency and may be used for request/response models only — never inside the service layer.
- No database, no Redis, no Celery, no APScheduler. Durability is filesystem-based per Principle IV.
- Standard library for everything else: asyncio, concurrent.futures, secrets, json, shutil.

THE BLOCKING-CALL PROBLEM — resolve this in research first:
download_post() is synchronous and blocks for minutes. Calling it from an async request handler or a background task on the event loop will freeze the entire server for every other caller. Research and decide how downloads run off the event loop — a bounded thread pool executor is the obvious candidate, but verify how it interacts with FR-015's concurrency limit and FR-020's time limit before committing. State clearly which mechanism enforces the concurrency cap: the pool size, a semaphore, or both, and why.

MODULE LAYOUT (Principle I — everything under backend/):
- backend/jobs.py — service logic: job records, scheduling, dedup, retention, disk guard, rate limiting, restart recovery. Framework-free, imports no FastAPI. This is where the orchestration the spec flagged belongs.
- backend/api.py — HTTP layer: routes, request/response models, status codes. Parses, calls jobs.py, serializes. No business logic.
- The three frozen modules are untouched.

KEY DECISIONS TO RESOLVE IN RESEARCH:

1. Job record durability (FR-024) without a database. One JSON file per job in a jobs directory is the likely shape. Writes must be atomic — reuse the same temp-then-os.replace pattern that 001 already established, for the same reason. Determine whether the in-memory state or the files on disk are authoritative, and how they stay consistent.

2. Failure code classification (FR-010). The diagnosis exists only as prose in DownloadOutcome.message from the private table in downloader.py. Read that table and build the classification against its actual current strings. Pin every mapping with a test so a wording change fails loudly rather than degrading silently to unclassified.

3. Message safety (FR-029). DownloadOutcome.message and the exceptions download_post raises can contain absolute paths. Design so that forwarding them to a caller is structurally impossible, not merely avoided by discipline — a single choke point that maps a code to a safe sentence, with the raw text going only to the log.

4. Time limit enforcement (FR-020). The spec found that raising from the progress callback aborts a transfer, but the callback does not fire during metadata resolution. Determine what guards the pre-transfer hang, and be honest about any window that remains unguarded rather than claiming full coverage.

5. Retention and restart sweeps (FR-021, FR-026). Decide what runs them — a periodic asyncio task, a check on each request, or startup-only — and confirm the retention sweep cannot delete a file while a retrieval is streaming it.

6. Handle generation (FR-027, SC-011). secrets.token_urlsafe with enough bytes for 128 bits. Confirm the byte count.

7. Rate limiting (FR-019) in-memory per address, and what happens to that state across a restart.

TESTING (Principle II still applies):
Extend beyond the two existing test files only as far as this feature needs — at most one new test file for the service layer. No HTTP integration tests that perform real downloads. The failure-code classification map must be tested since it is the thing most likely to drift.

SCOPE CONTROL:
Plan US1 and US3 as the deliverable — the capability plus multi-caller safety. US2, US4, US5, US6 structured as follow-on phases. Do not plan Docker, nginx, systemd, or TLS; those are deployment, explicitly out of scope.

## Response snapshot

Recorded all three clarifications in spec.md (FR-028 capability model, FR-012/FR-035/FR-036 the
index-only-when-needed rule, FR-025 fail-as-interrupted), replaced the Outstanding Clarifications
section with Resolved Clarifications carrying the rejected options and why, and cleared the
checklist's last open item. Then produced research.md (D1–D11), data-model.md, contracts/openapi.yaml,
quickstart.md, and plan.md.

**Every yt-dlp claim was read out of the installed source (2026.07.04), not recalled.** Three findings
changed the design:

1. **The spec's assumption about the time-limit gap was wrong, and research corrected it.** The spec
   said the unguarded window was *pre*-transfer metadata resolution. It is not: yt-dlp applies
   `DEFAULT_TIMEOUT = 20` to every request when `socket_timeout` is unset
   (`networking/common.py:34`, `:242`, reached via `YoutubeDL.py:4367`), and `_base_options` leaves it
   unset — so metadata is bounded. The genuinely unbounded window is *post*-transfer: the `ffmpeg`
   merge calls `Popen.run` with **no timeout** (`postprocessor/ffmpeg.py:356` →
   `utils/_utils.py:919-925`), and post-processing reports through a separate `postprocessor_hooks`
   list that `_base_options` does not populate and we cannot add to. A hung merge therefore wedges a
   worker thread for the life of the process. Recorded as an accepted limitation with a
   `wedged_workers` counter on `/health`, not papered over.
2. **Raising from the progress hook is safe and has a backstop.** `_hook_progress` calls hooks with no
   try/except (`downloader/common.py:488-494`), and `process_info` catches only `network_exceptions`,
   `OSError`, and `ContentTooShortError` (`YoutubeDL.py:3597-3602`). This dictates the exception type:
   a plain `RuntimeError`, which Principle VI approves *and* which yt-dlp will not swallow. Even if
   some layer did absorb it, `download_post` raises on a non-zero retcode (`downloader.py:511`), so
   abort and temp cleanup are guaranteed by two independent paths.
3. **`secrets.token_urlsafe(32)` measured at 43 chars / 256 bits**, double SC-011's requirement.

**Concurrency cap — pool size alone, no semaphore.** The executor's queue already gives exactly the
waiting/running distinction FR-015 needs (waiting = queued, running = worker picked it up), with no
second bookkeeping to drift. The semaphore case was argued rather than dismissed: it *could* be
force-released by the watchdog to reclaim capacity from a wedged worker — but the wedged thread still
occupies the pool, so an oversized-pool-plus-semaphore only converts "capacity lost now" into
"capacity lost later" while adding a second source of truth that can disagree with the first.

**Message safety made structural.** The `Job` record simply has **no field able to hold raw text**.
The serializer cannot forward what does not exist, so FR-029 holds by absence of a field rather than
by a sanitiser someone could route around. Verified the four leak sources this defends against:
`downloader.py:332-334` (`_promote` names the temp dir, folded into `message` by the generic handler
at `:529-535`), `:309-312` (cleanup warning), `validation.py:186-188` (containment check names both
paths), and `:132` (generic diagnosis embeds yt-dlp's verbatim text).

**Classification is prefix-exact, not heuristic.** `_partial_failure` composes
`f"{reason} Files already saved: {names}"` (`downloader.py:559`), so the diagnosis is always a prefix
— `startswith` is exact. The drift test imports `downloader._ERROR_DIAGNOSES` and asserts every
explanation is covered, so an upstream wording change fails loudly.

**Two additions flagged rather than adopted**: `XVD_MAX_PENDING` (the executor queue is unbounded, but
FR-015 says over-limit submissions must be *held, not dropped*, so a depth cap contradicts the spec's
far tail — deferred to Phase 3 for the owner's call), and the wedged-worker gap.

Constitution Check passes all seven gates before and after Phase 1. Principle III was argued rather
than ticked: the objective proof the boundary holds is that `tests/test_jobs.py` can construct no HTTP
client and start no event loop — impossible if `jobs.py` were entangled with the transport.

## Outcome

- ✅ Impact: Feature 002 planned end-to-end with every blocking-call, durability, and leakage decision resolved against verified source rather than assumption; one spec assumption corrected.
- 🧪 Tests: None run — planning stage. `tests/test_jobs.py` is specified, with the `_ERROR_DIAGNOSES` coverage test named as the load-bearing one.
- 📁 Files: spec.md (clarifications recorded), plan.md, research.md, data-model.md, contracts/openapi.yaml, quickstart.md, checklists/requirements.md, CLAUDE.md
- 🔁 Next prompts: `/sp.tasks` to break Phase 1 (US1+US3) into tasks. Decide on `XVD_MAX_PENDING` before Phase 3. `fastapi`/`uvicorn` still need adding to pyproject.toml — an implementation step, not a planning one.
- 🧠 Reflection: Reading yt-dlp's source instead of trusting the spec's summary inverted the answer to decision 4 — the window I was told to guard was already bounded, and the real one was somewhere else entirely. The user's instruction to "be honest about any window that remains unguarded" is what made the finding reportable rather than embarrassing.

## Evaluation notes (flywheel)

- Failure modes observed: The `update-agent-context.ps1` script truncates multi-line Technical Context values mid-sentence when writing CLAUDE.md; the generated entries were replaced by hand with coherent ones.
- Graders run and results (PASS/FAIL): Constitution Check — PASS on all 7 gates, pre- and post-Phase-1, with Principle III argued in prose rather than asserted. Requirements checklist — PASS on all 16 items.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): During `/sp.implement`, write the `_ERROR_DIAGNOSES` coverage test first and confirm it actually fails when a table row is edited — the drift guarantee is worthless if the test passes vacuously.
