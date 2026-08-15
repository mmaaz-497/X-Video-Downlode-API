---
id: 0022
title: US6 Restart Recovery Implementation
stage: green
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["us6", "restart-recovery", "temp-sweep", "durability", "fr-033-gap"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0004-filesystem-job-record-durability.md
  pr: null
files:
 - backend/jobs.py
 - backend/api.py
 - tests/test_jobs.py
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0022-us6-restart-recovery.green.prompt.md
tests:
 - tests/test_jobs.py (23 added; 286 pass)
 - three mutation checks: recovery write-back, temp-sweep mtime guard, lifespan ordering
 - live boot over a staged crashed state directory
---

## Prompt

Execute T051 through T058. Stop before T059 — that is the manual verification and I will run it.

Same constraints: frozen modules untouched, no new dependencies, tests/test_jobs.py only, stop and report rather than improvising.

Two things I want held to the standard the last three phases set:
- The write-back inside recover() must be mutation-verified, not merely tested. Remove it and confirm a test goes red, then revert. Without that write-back a second crash resurrects the job, and that is the whole reason the clause exists.
- The mtime guard on the temp sweep must be verified against a directory younger than XVD_JOB_TIMEOUT surviving the sweep. A guard that never demonstrably protects anything is decoration.

## Response snapshot

**T051–T058 complete. 286 tests pass, 23 added. One commit.** Stopped before T059.

**Both requested mutations run and reverted.**

*Write-back removed* (`persist(job)` → `pass` inside `recover()`): **three tests red**, including
`test_a_second_restart_does_not_resurrect_an_interrupted_job`, which restarts twice with nothing in
between — the ping-pong a single restart cannot reveal. The reconciliation property test
(`_as_record(job) == data` for every file) went red too, which is the point of writing it as a
property rather than a spot check.

*mtime guard removed*: **two tests red**, one of them
`test_a_recent_temp_directory_survives_the_sweep` — a directory that must still exist afterwards, so
the guard is demonstrated protecting something rather than merely present. The second test flips the
same directory's fate by changing only `XVD_JOB_TIMEOUT`, proving the threshold is the configured
value and not a constant that happens to agree with it.

*Third mutation, unrequested but the same class*: moving `create_task(_sweep_loop())` above
`recover()` in the lifespan turns T058's ordering assertion red.

**Verified against a live boot**, not only in unit tests. A staged state directory containing a
`running` record, a `finished` record with a real file, an hour-old temp directory and a fresh one
produced: `failed`/`interrupted` for the first, `finished` plus a 200 with the right bytes for the
second, the old temp directory gone and the fresh one untouched.

**FR-033 is not satisfied in a default deployment — found during T057, deliberately not fixed.**
Watching the live boot, none of the service's own log lines appeared. Checked rather than assumed:
uvicorn configures exactly three loggers (`uvicorn`, `uvicorn.access`, `uvicorn.error`), so `xvd.*`
has no handlers and falls through to `logging.lastResort`, whose level is WARNING. Every
`_log.info(...)` in the package is therefore invisible in production — including
`_record_outcome`'s `"job %s failed: %s"`, which carries the raw downloader message and **is** the
FR-033 correlation between a caller's failure code and the real cause. Confirmed empirically: an INFO
line under uvicorn's config produced no output while a WARNING did.

Worth being precise about the direction of the failure: the disclosure boundary is intact. This is
the opposite problem — detail withheld from the *operator*, not leaked to the caller. It belongs to
US3, not US6, so it is recorded in tasks.md with the evidence and the smallest fix rather than
improvised into this phase.

**Design points carried from the plan into the code**: the four untrustworthy-record cases share one
policy (skip, log, count — never repair), with the unknown-state case reasoned as a downgrade; the
`.tmp-job-*` leftovers `persist` can strand are never parsed; and an `expired` record keeps its
`files` so a Windows delete that failed before the restart is retried by the new process's first
sweep — tested end to end.

## Outcome

- ✅ Impact: US6 complete. A restarted process adopts what the last one was doing, nothing can report `running` forever, and abandoned temp directories are cleaned without endangering a concurrent CLI download.
- 🧪 Tests: 286 pass (23 added). Three mutations run and reverted, each turning the intended test red. Live boot verified.
- 📁 Files: backend/jobs.py, backend/api.py, tests/test_jobs.py, tasks.md
- 🔁 Next prompts: T059 — the owner runs the manual verification, including the second kill. Then the FR-033 logging gap, which needs its own small task.
- 🧠 Reflection: The logging gap was invisible to 286 passing tests and to three phases of live `curl` checks, because every previous check read a *response*. It surfaced the first time something looked at what the operator would see instead.

## Evaluation notes (flywheel)

- Failure modes observed: a requirement (FR-033) whose code was written, tested by inspection, and shipped — while the mechanism that would deliver it to a human was never configured. Nothing in the suite could have caught it, because the suite asserts on records and responses, never on operator output.
- Graders run and results (PASS/FAIL): full suite 286 — PASS. Mutation, recovery write-back — PASS (3 red). Mutation, temp-sweep mtime guard — PASS (2 red). Mutation, lifespan ordering — PASS (1 red). Live boot over a staged crash state — PASS. Frozen modules untouched since 3a3918e — PASS. No new dependencies — PASS. One test file — PASS.
- Prompt variant (if applicable): explicit task range with two named mutations required in advance — the strongest variant used so far, and the one that removed all ambiguity about what "verified" meant.
- Next experiment (smallest change to try): add one check that reads the operator's log rather than the caller's response, for any requirement phrased as "available to the operator". FR-033 was the only such requirement in the spec and it was the one that failed.
