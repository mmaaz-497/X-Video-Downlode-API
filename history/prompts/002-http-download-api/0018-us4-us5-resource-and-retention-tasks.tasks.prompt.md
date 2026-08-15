---
id: 0018
title: US4 And US5 Task Breakdown
stage: tasks
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.tasks
labels: ["us4", "us5", "rate-limit", "disk-guard", "retention", "watchdog", "sweep"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md
  pr: null
files:
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0018-us4-us5-resource-and-retention-tasks.tasks.prompt.md
tests:
 - none run (planning artifact only)
---

## Prompt

Generate tasks for plan Phase 3 (US4) and Phase 4 (US5) together. Do not generate tasks for US2 or US6.

These two ship together because they are the same problem from both ends: US4 stops the disk from filling, US5 empties it. Either one alone leaves the service failing after enough use.

PHASE 3 (US4) covers:
- Per-address rate limit with a Retry-After in the refusal (FR-019)
- Free-disk threshold guard, checked before a job is created (FR-018)
- XVD_MAX_PENDING queue depth cap with an at-capacity refusal beyond it (amended FR-015)
- Job time limit enforced by raising from the progress callback, plus the deadline watchdog (FR-020)

Note from research D4: the watchdog fails the job but cannot kill a wedged ffmpeg merge. That is the accepted limitation in Complexity Tracking. Do not attempt to solve it — but the watchdog must still mark the job failed so no caller waits forever, and /health must expose the wedged worker count.

PHASE 4 (US5) covers:
- Retention sweep on a periodic asyncio task (FR-021)
- The expired state, distinct from failed (FR-022)
- Mark-before-delete ordering so a retrieval in progress is never cut off (FR-023)
- Tolerated delete failure on Windows, consistent with how feature 001 handled the same file-handle problem

CONSTRAINTS:
- Frozen modules untouched. Stop and report if a task appears to need one.
- No new dependencies. The sweep is asyncio.sleep in a loop, not a scheduler library.
- Extend tests/test_jobs.py only. No third test file.
- The rate limiter must be testable without waiting real time — inject the clock as a defaulted parameter, the same seam T007 used for the downloader.
- Include a manual verification task for each phase, and stop before both.

## Response snapshot

**22 tasks, T029–T050**, appended to `specs/002-http-download-api/tasks.md` across five phases
(8–12). Two stop points, T043 and T049, both marked 🚦 as the owner's own runs. No tasks generated
for US2 or US6.

Five decisions were settled before the breakdown, because writing tasks against unresolved versions
of them would have produced tasks that had to be rewritten mid-implementation.

**1. A conflict with an existing test, found before it fired.** `test_transport_layer_has_no_loops`
(`tests/test_jobs.py:599-607`) asserts `api.py` contains **zero** `For`/`While` nodes. The periodic
sweep is `while True: await asyncio.sleep(...)` in `api.py`, so T039 turns that test red. The test's
own docstring anticipated this — "worth a second look rather than a silent allowance" — so the
resolution is a **named, narrow exemption**: `_sweep_loop` by name, every other function still
loop-free, and the exempted function asserted to touch no job data. The amendment ships in the same
task as the loop, because either half alone leaves the suite in a lying state.

**2. Rate limiting counts submissions, not jobs — a deliberate deviation from FR-019's wording.**
FR-019 says "how many **jobs** a caller may **create**"; research D9 says "on each **submission**".
They differ for invalid URLs and deduplicated submissions. The submission reading is adopted, because
an uncounted invalid-URL path is the cheapest abuse route available and would be free forever under
the literal reading. **Flagged as the one call the owner may want to invert** — the cost is that ten
typos lock a caller out for the window — with the one-line change named so inverting it is a decision
rather than a drift.

**3. Guard ordering, with a deduplicated submission paying nothing.** Rate limit → validate → free
disk *read outside `_lock`* → dedup scan → disk and depth verdicts *applied only on the create path*
→ mint. A dedup hit creates no job and consumes no disk, so refusing it for capacity would punish a
caller for work already underway. The disk syscall stays outside the registry lock; the pending count
rides along with the dedup scan rather than adding a second O(n) pass.

**4. `submit()` changes shape — its own task, sequenced first.** Three new refusals must cross the
service boundary; Principle VI forbids a custom exception hierarchy and `ValueError` is taken by the
invalid URL. `submit()` returns a `SubmitResult` for every outcome, mirroring `FileResult` and
`DownloadOutcome` — the idiom this codebase already uses twice. Cost stated plainly: 21 call sites in
`tests/test_jobs.py`, absorbed by a local `_accept()` helper, with the suite required green **before**
any guard is added so a red test is never ambiguous between shape and behaviour.

**5. "Wedged worker" given a countable definition.** The watchdog adds the handle to
`_watchdog_failed`; `_run_job` discards it in a `finally` so every return path clears it;
`wedged_workers = len(...)`. That is exactly the contract's "failed by the watchdog and never
returned" (`openapi.yaml:190`), and it goes to zero on restart — correct, since a restart is the only
cure D4 admits.

**One stale document found.** `backend/api.py:358-364` still carries the pre-T023 proxy-header claim
that research D9, quickstart.md, and `.env.example` were all corrected for. It matters in this phase
specifically: T031's rate limit is keyed on `request.client.host`, so a docstring that is wrong about
which addresses are believed is a docstring that gets the rate limit bypassed. T041 fixes it.

**Phase 4 adds no transport code, and that is a result rather than an omission.** T019 wrote the 410
`expired` branch against a state that could not yet occur; T048 asserts the branch is still reached
rather than assuming it, and requires a stop-and-report if `api.py` turns out to need a change after
all.

The wedged-`ffmpeg` limitation generates no mitigation tasks, as instructed — only the watchdog that
frees the *caller* and the `/health` count that makes the condition visible to the operator.

## Outcome

- ✅ Impact: US4 and US5 are executable. 22 tasks, explicit sequencing, two owner-run stop points, one flagged judgment call.
- 🧪 Tests: none run — planning artifact. T037 and T047 specify the test extensions, both required to run with no `time.sleep` anywhere.
- 📁 Files: specs/002-http-download-api/tasks.md
- 🔁 Next prompts: `/sp.implement` for T029–T043, stopping before T043. Or resolve decision 2 first if the rate-limit semantics should be inverted.
- 🧠 Reflection: The loop-assertion conflict was worth finding at planning time — it would have surfaced as a mysterious red test in the middle of T039, and the tempting fix under implementation pressure is to delete the assertion rather than narrow it.

## Evaluation notes (flywheel)

- Failure modes observed: a corrected fact had propagated to three documents but not to the docstring in the code that the fact governs. Document corrections need a grep of the source, not only of the specs.
- Graders run and results (PASS/FAIL): checklist format across all 50 tasks — PASS (checkbox, ID, story label on story phases, file paths). ID continuity T029–T050, no gaps or duplicates — PASS. Dependency graph covers every new task — PASS. Constraint compliance (no new dependencies, one test file, frozen modules untouched, manual task per phase) — PASS, each encoded as a task bullet or as T050's close-out check.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): before generating tasks that add code to a module, read the AST/boundary tests that already constrain it — the conflict in decision 3 was only found because `tests/test_jobs.py` was read first.
