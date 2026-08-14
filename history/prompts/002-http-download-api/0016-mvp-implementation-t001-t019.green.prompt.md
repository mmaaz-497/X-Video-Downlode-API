---
id: 0016
title: MVP Implementation T001 T019
stage: green
date: 2026-08-14
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["implementation", "mvp", "us1", "us3", "threadpool", "fastapi"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md
  pr: null
files:
 - pyproject.toml
 - .gitignore
 - backend/jobs.py
 - backend/api.py
 - tests/test_jobs.py
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0016-mvp-implementation-t001-t019.green.prompt.md
tests:
 - tests/test_jobs.py (51 new tests)
 - full suite: 218 passed
---

## Prompt

Execute T001 through T019 — the MVP. Stop before T020.

T009's forward dependency on T013: implement T009 returning "unclassified" for every failure initially, then complete the mapping in T013. Do not reorder the tasks.

EXECUTION RULES:
- Follow the sequencing in the dependency graph. T016 must not start before T015 is complete — that gate is the whole reason for the phase split.
- After each task, state what you created and any deviation with the reason.
- If a task requires touching backend/downloader.py, validation.py, or config.py, STOP and report. Do not edit them.
- No new dependencies beyond fastapi and uvicorn. Plain uvicorn, not uvicorn[standard].
- One new test file only: tests/test_jobs.py.
- Do not implement anything from the deferred table — no disk guard, no rate limit, no retention, no watchdog, no restart recovery.
- Commit at the suggested points: after T004, T011, T015, and T019.

At T019, stop and tell me the server is ready to start. I will run the manual verification myself.

## Response snapshot

All 19 tasks executed in order. Four commits at the specified points. 218 tests pass (51 new).
The three frozen modules are untouched: `git diff HEAD~4 -- backend/downloader.py
backend/validation.py backend/config.py` is empty.

**The T015→T016 gate was honoured and checked**: `ls backend/` was run before starting T016 and
confirmed no `api.py` existed. The whole service layer was written and tested first.

**Three defects found by executing rather than by reading:**

1. **Intermittent test failure traced to a real teardown bug.** `tests/test_jobs.py` passed in the
   full suite and failed alone, then passed again — classic race. Cause: `shutdown()` hardcoded
   `wait=False`, so a straggler worker from one test survived into the next, and because `_jobs_dir`
   is module-global it wrote its record into the *new* test's directory, breaking that test's
   "created nothing" assertion. Fixed by making `wait` a parameter defaulting to the production
   behaviour — blocking process exit on a minutes-long download would hang every deploy, and FR-025
   already says such a job is recovered as interrupted — with tests passing `wait=True`. Verified
   with 8 consecutive clean runs.
2. **FastAPI refused to start** because it tried to build a response model from the
   `FileResponse | JSONResponse` return annotation on the two file routes. Fixed with
   `response_model=None`. Found by booting the server; no amount of reading would have caught it.
3. **uv resolved much newer versions than the plan recorded** — fastapi 0.141.1, starlette 1.6.0,
   pydantic 2.13.4 against the plan's 0.115/0.38.6, because the planning numbers came from the
   global interpreter rather than the project venv. Starlette 0.38 → 1.6 is a major jump, so the four
   APIs the design depends on were verified present before writing any code.

**Deviations, each with its reason:**

- `jobs.init()` takes **no parameters**. The task allowed configuration for tests; an `output_dir=`
  parameter would sit one wiring mistake from a request body, so FR-030 is enforced by the absence of
  a seam and tests set environment variables instead.
- `file_for()` returns a `FileResult` dataclass rather than raising. Mirrors how `downloader.py`
  returns `DownloadOutcome`, and avoids the custom exception hierarchy Principle VI forbids.
- A `threading.Lock` was added at T003, earlier than any task called for it. A terminal-state guard
  whose check and write can be separated by another thread is not a guard, and `submit()`'s
  check-then-insert has the same problem — five simultaneous submissions of one post would start five
  downloads and break SC-007.
- The audit log records `canonical_url: null` for a rejected URL. FR-031 asks for "the submitted
  URL", but FR-032 forbids storing caller free text and scopes the URL to *after* validation. A test
  submits a URL containing an ANSI title-setting escape sequence to prove it never reaches the
  operator's terminal.
- A `.gitignore` was created (the repo had none, and the workflow requires one). The first commit
  also swept in feature 001 and the 002 planning artifacts, which had never been committed.

**T014's self-check was performed, not assumed**: one prefix was deliberately altered, three tests
went red including the coverage assertion, and reverting turned them green. A drift guarantee that
passes vacuously is worth nothing, which is exactly how feature 001's T006 grep survived never having
run.

**Verified against a running server**: 202 in 0.21s with a 43-character handle; identical
`{"code":"not_found","message":"No such job."}` for unknown and malformed handles across all three
GET routes; an extra `output_dir` body field refused rather than honoured; an invalid URL rejected
without echoing it.

**Two gaps reported rather than silently fixed, both belonging to T020-T022:**

- Pydantic's default 422 handler echoes the offending input verbatim
  (`{"detail":[{...,"input":"/etc/shadow"}]}`) — exactly the FR-005 violation research D6 predicted.
  T021 replaces it.
- A path that matches no route returns Starlette's own `{"detail":"Not Found"}` rather than ours, so
  `/jobs/%2e%2e%2f...` is distinguishable from a handle-shaped 404. It discloses nothing about
  whether a job exists, but it is not the single identical refusal FR-028 wants.
- `_caller()` returns the literal `"unknown"` until T022 threads the request through.

## Outcome

- ✅ Impact: US1 works end to end over HTTP; the service layer is complete and tested without an event loop, proving the Principle III boundary holds.
- 🧪 Tests: 218 passed (51 new in tests/test_jobs.py). Drift test verified to fail on drift.
- 📁 Files: `backend/jobs.py`, `backend/api.py`, `tests/test_jobs.py`, `pyproject.toml`, `.gitignore`, tasks.md
- 🔁 Next prompts: user runs manual verification. Then T020-T023 (US3 hardening), which closes the two reported gaps.
- 🧠 Reflection: The instruction to keep T009's forward dependency rather than reorder was worth following — the placeholder made the seam between "what the worker does" and "what the codes mean" explicit, and T013 slotted in without touching T009's logic at all.

## Evaluation notes (flywheel)

- Failure modes observed: An intermittent test failure that passed on retry; diagnosing it rather than re-running found a real bug in `shutdown()`. Planning-stage dependency versions were read from the wrong interpreter and were badly out of date by the time code was written.
- Graders run and results (PASS/FAIL): Frozen-module constraint — PASS, zero diff across all four commits. Dependency gate — PASS, `api.py` did not exist until T015 was committed. Dependency budget — PASS, only fastapi and uvicorn, plain not [standard]. Test-file budget — PASS, one new file.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): Run T025's AST boundary check before T020 rather than in Phase 7 — `api.py` is at its smallest now, so a violation would be cheapest to see and fix today.
