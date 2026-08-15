---
id: 0020
title: US5 Retention Implementation
stage: green
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["us5", "retention", "expired-state", "mark-before-delete", "windows-file-handle"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0004-filesystem-job-record-durability.md
  pr: null
files:
 - backend/jobs.py
 - tests/test_jobs.py
 - .env.example
 - specs/002-http-download-api/tasks.md
 - specs/002-http-download-api/data-model.md
 - specs/002-http-download-api/quickstart.md
 - history/prompts/002-http-download-api/0020-us5-retention.green.prompt.md
tests:
 - tests/test_jobs.py (15 added; 263 pass)
 - four mutation checks: reversed ordering, crash on failed delete, silent swallow, deleted 410 branch
---

## Prompt

Execute T044 through T050 — US5 retention. Stop before the manual verification task, as before.

Same constraints: frozen modules untouched, no new dependencies, tests/test_jobs.py only, stop and report rather than improvising.

Two things to get right:
- Mark-before-delete ordering must be verified, not assumed. A retrieval that started before the sweep ran must complete rather than being cut off mid-stream.
- Windows delete failure is tolerated the same way feature 001 handled it: the file handle problem is real there and the sweep must not crash, but the failure must be visible in the log rather than swallowed silently.

Retention default stays 24 hours. Do not shorten it in code — it is operator configuration.

## Response snapshot

**T044–T048 complete plus T050's checks. 263 tests pass, 15 added. One commit.** Stopped before
T049.

**Mark-before-delete is verified, not assumed** — the specific thing asked for. The `unlink` seam
records `job.state` at the moment it is called, so `test_the_mark_happens_before_the_delete` asserts
`observed == [EXPIRED]`. **Reversing the two lines in `_expire_due` turns it red**, confirmed by
running the mutation. A second test resolves a retrieval's path *before* the sweep, holds the file
open across it, and reads the whole thing back unchanged — passing on either platform, since POSIX
keeps an open handle working after `unlink` while Windows refuses the `unlink` instead. Both roads
end with the reader whole; only the disk differs.

**The Windows failure is tolerated exactly as feature 001 handled it** (`downloader.py:270-292`,
cited at the code). The sweep does not crash, the job is expired regardless, and the file is retried
next pass with **no extra state** — "still expired and the file still exists" is already the complete
description of the work. Logged at WARNING rather than swallowed, because a delete failing on every
pass forever is a real leak. **Both mutations turn the test red**: raising instead of tolerating, and
`pass` instead of logging.

**The `finished → expired` edge is a second function, not a flag on `_enter_terminal`.** Invariant 1
names it as the single exception to "a terminal state is never left", and teaching the existing guard
to make an exception would make it one that can be argued with — which is exactly wrong for the
watchdog race it exists to settle. A test asserts `_enter_terminal` still refuses `finished →
expired` while `_expire` permits it and nothing else.

**Retention needed no transport change, and that was checked rather than believed.** `git diff
backend/api.py` is empty against the commit. T019 wrote the 410 branch against a state that could not
then occur; a new structural test asserts `_serve` still branches on `EXPIRED` and still answers 410,
so it cannot be deleted as dead code by someone who does not know retention now reaches it. Deleting
the branch turns that test red.

**Retention default is untouched at 86400.** Every test moves the clock instead — no `time.sleep`
anywhere in the new block, and the `now` and `unlink` seams are defaulted parameters in the T007
style.

**One weak test of my own, caught and fixed.** `test_only_finished_jobs_are_ever_expired` originally
asserted `job.state == state or state == "running"`, which is vacuous for the `running` case —
the watchdog legitimately changes that one. Rewritten to assert the claim that actually holds for
all three: none of them became `expired`.

## Outcome

- ✅ Impact: US5 complete. Finished files stop accumulating, a caller who returns too late is told `expired` rather than handed a broken response, and a retrieval in flight is never cut off.
- 🧪 Tests: 263 pass (15 added). Four mutations run and reverted, each turning the intended test red.
- 📁 Files: backend/jobs.py, tests/test_jobs.py, .env.example, data-model.md, quickstart.md, tasks.md
- 🔁 Next prompts: T049 — the owner runs the manual verification, including the mid-transfer sweep. Then US2 (plan Phase 2) or US6 (plan Phase 5), neither of which has tasks yet.
- 🧠 Reflection: The two requirements singled out in the prompt were both ones where a passing test proves little unless the mutation is also run — "the ordering is right" and "the failure is tolerated" are exactly the claims a green suite can make vacuously.

## Evaluation notes (flywheel)

- Failure modes observed: a `-k` filter that silently matched none of the tests it was meant to select, which briefly made a mutation look uncaught; and a self-written assertion with a vacuous disjunct.
- Graders run and results (PASS/FAIL): full suite 263 — PASS. Mutation, reversed mark/delete ordering — PASS (red). Mutation, crash on failed delete — PASS (red). Mutation, silent swallow — PASS (red). Mutation, deleted 410 branch — PASS (red). Frozen modules untouched since 3a3918e — PASS. No new dependencies — PASS. One test file — PASS. `api.py` unchanged by this phase — PASS (empty diff).
- Prompt variant (if applicable): explicit task range with two named correctness requirements.
- Next experiment (smallest change to try): run mutations before declaring a suite green, not after — the reversed-ordering check took thirty seconds and was the only thing that distinguished a real guarantee from a test that happened to pass.
