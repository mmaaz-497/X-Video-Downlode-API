---
id: 0023
title: Operator Logging FR-033 Fix
stage: green
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["fr-033", "logging", "observability", "us3", "regression-guard"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0003-caller-facing-disclosure-boundary.md
  pr: null
files:
 - backend/jobs.py
 - backend/api.py
 - tests/test_jobs.py
 - .env.example
 - specs/002-http-download-api/data-model.md
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0023-operator-logging-fr033.green.prompt.md
tests:
 - tests/test_jobs.py (9 added; 295 pass)
 - mutation: removing addHandler reproduces the original bug and turns 4 tests red
 - live boot under uvicorn showing both halves of the FR-033 correlation
---

## Prompt

Fix the FR-033 logging gap. Smallest change that works: configure the xvd logger namespace at start-up so the package's own log lines are actually emitted under uvicorn.

Requirements:
- xvd.jobs and xvd.api must emit at INFO in a default deployment, without the operator configuring anything.
- Lines must carry a timestamp and the logger name — the wedged-worker warning currently appears with neither.
- _record_outcome's raw downloader message must reach the log. That line is the FR-033 correlation and is the reason this matters.
- Do not touch uvicorn's own three loggers or replace its formatter globally.
- The log level must be operator-configurable via an environment variable with INFO as the default.

Add a test asserting the xvd namespace has a handler after start-up, so this cannot silently regress the way it silently existed.

Constraints unchanged: frozen modules untouched, no new dependencies, tests/test_jobs.py only.

## Response snapshot

**Fixed. 295 tests pass, 9 added. One commit.** All five requirements met, frozen modules untouched,
no new dependencies, one test file.

`jobs.configure_logging()` adds **one handler to the `xvd` namespace** — not `basicConfig` (which
configures root) and not `dictConfig` (which would replace what uvicorn installed for its own three
loggers). Level from `XVD_LOG_LEVEL`, default INFO, case-insensitive, with an unrecognised value
logging a warning and falling back rather than refusing to start. Format
`%(asctime)s %(levelname)-8s %(name)s: %(message)s`. Idempotent via a tagged handler, so a reload or
a second lifespan cannot stack duplicates.

**Called first in the lifespan, before `init()`.** Otherwise start-up's own report — what recovery
adopted, what it refused to read — is written before anything can write it.

**`propagate` stays True, deliberately, and this was the one real design choice.** Setting it False
would guarantee exactly one line per record, but would also cut those records off from everything
attached at root: pytest's `caplog` and any log shipper an operator has already configured. Three
existing tests depend on that propagation and would have broken. Losing a line is worse than printing
one twice, and the duplicate only occurs for an operator who has configured root themselves — which
is an opt-in.

**Verified live on a single job**, which is the only way to show a correlation rather than assert one:

```text
operator: 2026-08-15 12:33:32,031 INFO  xvd.jobs: job 1a9X2S4j… failed: this post has no video in it.
caller:   {"code": "no_video", "message": "This post does not contain a video."}
```

Both halves, same handle, and the disclosure boundary still intact — the caller's sentence is the
literal from the catalog, not the downloader's text.

**Mutation-verified**: deleting the `addHandler` call reproduces the original bug exactly, and
**four tests go red** — including `test_the_record_outcome_message_reaches_the_log`, which is FR-033
itself, and the handler-presence guard that was requested. Reverted and green.

Also confirmed uvicorn's own three loggers are byte-identical before and after — asserted as a test
rather than by inspection, since "did not touch" is the kind of claim that rots.

## Outcome

- ✅ Impact: FR-033 is satisfied in a default deployment for the first time. The operator can now answer "why did this job fail" for any handle a caller quotes.
- 🧪 Tests: 295 pass (9 added). One mutation run and reverted. Live boot verified with both halves of the correlation.
- 📁 Files: backend/jobs.py, backend/api.py, tests/test_jobs.py, .env.example, data-model.md, tasks.md
- 🔁 Next prompts: the four 🚦 manual verifications remain — T027, T043, T049, T059. Only US2 is unbuilt, and it refines sentences rather than adding capability.
- 🧠 Reflection: The fix took twenty minutes; finding it took three phases and a live boot. The test that would have caught it — assert the namespace has a handler — is three lines, and now exists.

## Evaluation notes (flywheel)

- Failure modes observed: none new. This closes the one from PHR 0022, where a requirement's code was written and shipped while the mechanism that delivered it to a human was never configured.
- Graders run and results (PASS/FAIL): full suite 295 — PASS. Mutation, removed handler — PASS (4 red). Live boot showing the correlation line and the caller's response for one job — PASS. uvicorn's loggers unmodified — PASS (asserted). Frozen modules untouched — PASS. No new dependencies — PASS. One test file — PASS.
- Prompt variant (if applicable): a five-point requirement list plus an explicitly requested regression guard. The "add a test asserting X, so this cannot silently regress the way it silently existed" phrasing named the failure class, not just the fix.
- Next experiment (smallest change to try): for any requirement phrased as "available to the operator", assert on the operator's output stream. This was the spec's only such requirement and the only one that shipped broken — a pattern worth checking for in the next feature's spec before writing tests.
