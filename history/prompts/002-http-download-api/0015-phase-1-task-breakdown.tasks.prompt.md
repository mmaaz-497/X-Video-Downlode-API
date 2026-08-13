---
id: 0015
title: Phase 1 Task Breakdown
stage: tasks
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.tasks
labels: ["tasks", "phase-1", "us1", "us3", "ast-boundary-check", "drift-test"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md
  pr: null
files:
 - specs/002-http-download-api/tasks.md
 - specs/002-http-download-api/spec.md
 - specs/002-http-download-api/research.md
 - specs/002-http-download-api/plan.md
 - specs/002-http-download-api/data-model.md
 - history/prompts/002-http-download-api/0015-phase-1-task-breakdown.tasks.prompt.md
tests:
 - none run (planning stage; tests/test_jobs.py specified across T011, T014, T025)
---

## Prompt

Break Phase 1 (US1 + US3) into tasks. Do not generate tasks for Phases 2 through 5.

OWNER DECISIONS TO RECORD BEFORE TASKS:
- XVD_MAX_PENDING: adopted. Amend FR-015 in spec.md to scope "held, not dropped" to within a configured pending cap; submissions beyond the cap are refused with a clear message stating the service is at capacity. An unbounded queue on a public service is a memory exhaustion path, and the per-address rate limit does not bound the aggregate across many addresses. The cap itself belongs in Phase 3 as planned, but the FR wording must change now so Phase 1 is not built against a requirement that will move.
- Wedged ffmpeg worker: accepted as a known limitation. Do not add tasks for it. It stays in Complexity Tracking.

TASK REQUIREMENTS:

1. Add fastapi and uvicorn to pyproject.toml as a first task. Nothing else can run without them.

2. backend/jobs.py must be built and testable before backend/api.py exists. Order the tasks so the service layer is complete first — if api.py is needed for jobs.py to be exercised, the boundary has already failed.

3. The drift test that imports _ERROR_DIAGNOSES and asserts full coverage must be its own task, not folded into a larger test task. It is the single guard against silent classification decay, and it must be visible as a named deliverable.

4. Include a task that verifies the Principle III boundary the same way T006 did for feature 001, and learn from what T006 got wrong: the check must be AST-based, not grep, because docstrings describing the constraint matched the forbidden tokens and made T006 pass on inspection while never actually running. Verify: jobs.py imports neither fastapi nor asyncio; api.py contains no logic beyond parsing, calling, and serialising.

5. The proxy-headers requirement must be a task, not a quickstart note alone. Behind a reverse proxy, request.client.host is the proxy, which collapses every caller into one rate-limit bucket and makes the FR-031 audit log worthless. The service must document the required uvicorn flags and must not trust X-Forwarded-For from an untrusted source.

6. Include a manual verification task using curl covering at minimum: submission returning a handle in under a second, status polling showing progress, retrieval of the finished file, an invalid URL rejected in the submission response, and an unknown handle producing a refusal identical to an unauthorized one.

CONSTRAINTS:
- The three frozen modules are not touched. Any task that would require it, stop and report.
- One new test file only: tests/test_jobs.py. No HTTP integration tests. No real downloads in tests.
- No new dependencies beyond fastapi and uvicorn.
- Do not generate tasks for Docker, nginx, systemd, or TLS.

## Response snapshot

Recorded both owner decisions first, across four documents, before writing a single task: FR-015
amended in spec.md with the memory-exhaustion rationale and a note that the cap ships in Phase 3;
FR-034 extended; `XVD_MAX_PENDING` (default 50) added to the data-model config table marked
**Phase 3**; research D2's open item converted from "flagged for the owner" to "resolved"; plan.md's
"Open items" section became "Owner decisions". The wedged-worker gap was left in Complexity Tracking
and generated no tasks, as instructed.

**28 tasks across 7 phases** — 11 tagged US1, 8 tagged US3, 9 unlabelled (setup, foundational,
verification).

**The phase structure deviates from one-phase-per-story, deliberately and with the deviation stated
at the top of the file.** Requirement 2 said the service layer must be complete first, and US1 and US3
share one module and one HTTP surface — so each story is split into a service-layer phase and an
HTTP-layer phase: Phase 3 US1/jobs.py → Phase 4 US3/jobs.py → Phase 5 US1/api.py → Phase 6 US3/api.py.
Story independence is preserved where it matters: US1 is verifiable end-to-end at the Phase 5
checkpoint and US3 at the Phase 6 checkpoint. Writing `api.py` once against a finished service layer
also beats writing it twice.

**T025 (AST boundary check) is built directly on what feature 001's T006 got wrong.** T006 ran
`grep -nE "argparse|sys\.exit|print\("` against modules whose docstrings *described the constraint
using those exact words*, so it matched prose, never passed as written, and was signed off by eye
(`specs/001-post-video-download/tasks.md:147-151`). T025 walks `ast.Import`/`ast.ImportFrom` nodes
instead, which cannot see prose — so T002 is free to state the rule honestly in its docstring. Two
further changes: T002 is explicitly told **not** to name `fastapi` or `asyncio` as literal tokens in
that docstring, and T025 lives in `tests/test_jobs.py` so it runs on every `pytest` rather than
depending on someone remembering a grep.

**T014 (drift test) is its own task and carries a self-check the original guarantee lacked**: alter a
prefix, watch the test go red, revert. A vacuously-passing drift test is exactly the failure mode T006
demonstrated, and asserting coverage is worthless if the assertion never fires.

**Two scope consequences surfaced and are flagged in the file rather than absorbed silently**:

1. **T013 pulls US2's full classification table into Phase 1.** T014 cannot assert *full* coverage
   against a partial map, so requiring the drift test in this phase requires the table in this phase.
   What US2 still owns is per-code message refinement, not the mechanism.
2. **T009 has a forward dependency on T013** (outcome→code mapping needs the code table, which sits
   in the next phase). Recorded with two acceptable resolutions rather than papered over.

**One testability seam was specified explicitly so the implementer does not invent something larger**:
T007 gives the worker a `download=download_post` default parameter so `tests/test_jobs.py` can pass a
plain stub. Principle II permits "plain fakes and stub objects only" and forbids mocking frameworks —
one defaulted parameter is what makes that possible; a registry or protocol would not be.

Other judgement calls: plain `uvicorn` rather than `uvicorn[standard]` (the extra pulls `httptools`,
`uvloop`, `watchfiles`, `websockets` — none used, and Principle IV requires justifying each);
T020 makes a handle-pattern failure return the **same 404** rather than a 422, since a 422 would leak
whether the handle shape could have been real; T004 writes records but does not read them, because
recovery is US6 while retrofitting persistence into every transition later is worse than writing it
now; and only **one** task carries `[P]`, because eleven of the twenty-eight edit the same file and
marking them parallel would be decorative.

Added an *Explicitly Not In This Phase* table so each deferral reads as a decision, including the
wedged-worker row marked **"Never — accepted limitation"**.

## Outcome

- ✅ Impact: Phase 1 is executable — 28 ordered tasks with file paths, verification commands, and the two anticipations of later phases each justified in place.
- 🧪 Tests: None run. `tests/test_jobs.py` is specified across three tasks: T011 (service layer), T014 (drift), T025 (AST boundary).
- 📁 Files: `specs/002-http-download-api/tasks.md` (new); spec.md, research.md, plan.md, data-model.md (owner decisions recorded)
- 🔁 Next prompts: `/sp.implement` starting at T001. Commit points suggested after T004, T011, T015, T019, T023, T027.
- 🧠 Reflection: The most useful input was being pointed at *how* a previous verification task failed rather than just what it checked. "T006 matched its own docstring" turns a vague instruction to check the boundary into a specific one — use the AST, keep the rule out of the prose, and put it where the test runner will find it.

## Evaluation notes (flywheel)

- Failure modes observed: The strict one-phase-per-story template conflicts with the service-layer-first ordering constraint; resolved by splitting each story across two phases and stating the deviation at the top rather than silently reshaping either rule. Requirement 3 also forces US2's classification table forward into Phase 1 — a genuine scope consequence, flagged in T013 rather than absorbed.
- Graders run and results (PASS/FAIL): Checklist format — PASS, all 28 tasks carry checkbox, ID, story label where required, and a file path. Scope — PASS, no task for US2/US4/US5/US6, Docker, nginx, systemd, or TLS. Dependency constraint — PASS, T016 (first `api.py` task) is gated behind T015 (last `jobs.py` task).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): Execute T025 immediately after T002 rather than in Phase 7 — the boundary check is cheapest to satisfy while `jobs.py` is one file long, and running it once early would prove it fires before there is anything for it to catch.
