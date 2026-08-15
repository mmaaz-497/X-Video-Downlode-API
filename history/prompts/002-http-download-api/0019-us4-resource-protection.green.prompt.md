---
id: 0019
title: US4 Resource Protection Implementation
stage: green
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["us4", "rate-limit", "disk-guard", "pending-cap", "watchdog", "health"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md
  pr: null
files:
 - backend/jobs.py
 - backend/api.py
 - tests/test_jobs.py
 - .env.example
 - specs/002-http-download-api/tasks.md
 - specs/002-http-download-api/data-model.md
 - specs/002-http-download-api/quickstart.md
 - specs/002-http-download-api/contracts/openapi.yaml
 - history/prompts/002-http-download-api/0019-us4-resource-protection.green.prompt.md
tests:
 - tests/test_jobs.py (24 added; 248 pass)
 - mutation checks on all three new guards and both boundary assertions
---

## Prompt

(no argument given — `/sp.implement` with an empty body)

Scope taken: **T029–T042**, stopping before **T043**, which tasks.md marks 🚦 as the owner's own
manual verification.

## Response snapshot

**T029–T042 complete. 248 tests pass, 24 added. Two commits.** Stopped before T043.

**T030 — `submit()` now returns `SubmitResult`.** Four refusals had to cross the service boundary,
Principle VI forbids a custom exception hierarchy, and `ValueError` was already spoken for. Done as
its own commit-sized step with the suite green *before* any guard was added, so a later red test
could never be ambiguous between "shape changed" and "behaviour changed". The 21 call sites moved to
an `_accept()` helper that states at each site that acceptance was expected.

**Two ordering decisions, argued at the code rather than only in tasks.md:**

- The rate limit is checked **before** validation, so an invalid URL spends allowance. This deviates
  from FR-019's literal "jobs created" wording. Two tests pin it (`test_rate_limit_counts_invalid_urls`,
  `..._counts_deduplicated_submissions`) so inverting it is a failing test and a conversation.
- The disk and depth verdicts apply **only on the create path**. A deduplicated submission consumes
  nothing, so refusing it at capacity would withhold a handle that already exists. Verified live: at
  concurrency 1 / pending 1, submission 2 still **waits** while 3 and 4 are refused.

**T039 — the no-loops assertion fired for the first time**, exactly as predicted at planning. Its own
docstring had said a genuine need was "worth a second look rather than a silent allowance", so the
check was narrowed, not weakened: `_sweep_loop` exempt **by name**, every other function still
asserted loop-free, plus a new test asserting the exempt loop references no job data.

**Writing that second test reproduced the T006 mistake in miniature.** It compared against
`ast.dump(loop)`, which includes docstrings — so the loop's own docstring explaining "it touches no
job" matched the forbidden-identifier check and failed the test. Rewritten to walk `ast.Name`/
`ast.Attribute` identifiers instead. The prose-matching trap is evidently easy to fall into even
while writing the test whose purpose is to avoid it.

**Three self-inflicted failures, all caught by running rather than reasoning:**

1. The deadline never fired through `submit()` — `_run_job` built the progress hook with the default
   clock. Fixed by threading `now` from `submit` → `_run_job` → the hook, which also means the
   deadline is now tested through the real path instead of by calling the hook directly.
2. `_watchdog_failed` leaked between tests (a wedged count of 2 before anything ran). Same class as
   the earlier straggler-worker bug; cleared in `init()` alongside the rate buckets, since a wedged
   count is a statement about threads in a pool that `init()` has just replaced.
3. A malformed edit left a stray `_unused_docstring_anchor` function in `api.py`. Caught by parsing
   the file rather than assuming the edit landed clean.

**Mutation-verified, not just green.** Disabling each of the three guards turned 7 tests red;
smuggling a loop into a handler and a `_registry` reference into the sweep loop turned the 2 boundary
tests red. Both reverted and confirmed green.

**Verified against a live server** (three start-ups, since every limit is read once at start-up):
`429` with `retry-after: 119`, both 503 bodies, `/health` reporting `running: 1, waiting: 1`, and the
at-capacity refusal alongside a still-waiting job.

**T041 corrected a stale docstring** in `_caller` that still carried the pre-T023 proxy-header claim.
It matters in this phase specifically: the rate limit is keyed on that value.

## Outcome

- ✅ Impact: US4 complete. The service refuses cleanly instead of degrading — per-address rate limit with Retry-After, free-disk floor, pending cap, job deadline with a watchdog, and a health endpoint that makes the accepted wedged-worker limitation countable.
- 🧪 Tests: 248 pass (24 added). No `time.sleep` in any new test; the clock and the free-space reading are both defaulted parameters.
- 📁 Files: backend/jobs.py, backend/api.py, tests/test_jobs.py, .env.example, data-model.md, quickstart.md, contracts/openapi.yaml, tasks.md
- 🔁 Next prompts: T043 — the owner runs the manual verification. Then T044–T050 (US5 retention).
- 🧠 Reflection: The planning pass predicted the loop-assertion conflict and chose the narrow exemption in advance; implementation cost for it was minutes rather than a mid-task decision under pressure. Worth repeating — read the tests that constrain a module before planning code that will change it.

## Evaluation notes (flywheel)

- Failure modes observed: a test seam threaded only halfway (the clock reached `submit` but not the hook); module state cleared in the fixture but not at its source; and the prose-matching trap re-entered while writing the very test that guards against it.
- Graders run and results (PASS/FAIL): full suite 248 — PASS. Mutation of three guards — PASS (7 red). Mutation of both boundary assertions — PASS (2 red). Frozen modules untouched — PASS (empty diff). No new dependencies — PASS (empty diff on pyproject/uv.lock). One test file — PASS. Live-server verification of 429/503/503/health — PASS.
- Prompt variant (if applicable): empty `/sp.implement`; scope inferred from tasks.md's own stop markers.
- Next experiment (smallest change to try): when adding a defaulted seam, assert in the same task that it reaches the *deepest* consumer — the deadline test passed at the hook level while the wiring between submit and the hook was broken.
