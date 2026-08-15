---
id: 0021
title: US6 Restart Recovery Task Breakdown
stage: tasks
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.tasks
labels: ["us6", "restart-recovery", "temp-sweep", "durability", "research-correction"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0004-filesystem-job-record-durability.md
  pr: null
files:
 - specs/002-http-download-api/tasks.md
 - specs/002-http-download-api/research.md
 - history/prompts/002-http-download-api/0021-us6-restart-recovery-tasks.tasks.prompt.md
tests:
 - none run (planning artifact only)
---

## Prompt

Generate tasks for plan Phase 5 (US6) — restart recovery. This is the last functional gap.

Covers:
- Job records read back from disk on start-up (FR-024, the read side that T004 deliberately deferred)
- Any job recorded as waiting or running resolved to failed with an interrupted reason, so nothing can report running forever (FR-025)
- Leftover .tmp-xvd-* directories removed from the output directory (FR-026)
- Jobs that had already finished still report finished and their files still retrievable

Two things to get right:
- A record on disk and the in-memory registry must not disagree after recovery. Decide which is authoritative and make it explicit.
- Recovery runs before the server accepts requests, in the lifespan startup, so no caller can observe a half-recovered state.

Same constraints: frozen modules untouched, no new dependencies, tests/test_jobs.py only. Include a manual verification task — kill the service mid-download, restart, confirm the job is terminal and no temp directory survives.

## Response snapshot

**10 tasks, T051–T060**, across three phases. One 🚦 stop point (T059). Five decisions recorded
before the breakdown.

**A flaw in research D7, found and corrected during planning.** D7 justified the temp-directory sweep
with: *"the sweep is therefore start-up-only and never periodic, so it cannot delete a live CLI
download's temp directory."* That does not follow — being start-up-only limits how *often* the sweep
runs, not whether a CLI download is in flight at that instant. An operator restarting the service
during a CLI download would have its `.tmp-xvd-*` directory deleted underneath it, corrupting a
download this service does not own.

Resolution: sweep only directories whose mtime is older than `XVD_JOB_TIMEOUT`. Nothing of ours can
legitimately be older, because the watchdog fails it at exactly that age. **research.md was corrected
immediately rather than at implementation time**, with a dated note in the D9 style — the flaw was
known the moment it was found, so leaving the document wrong until T054 would have been a choice.

**Authority (the first thing asked for) is resolved as a three-phase rule.** D3 already covered the
steady state; what it never addressed is the one moment the two *can* disagree — during recovery
itself. The rule: disk is the only truth before `recover()`, disk is read and memory built during it,
memory is authoritative after. The load-bearing clause is that any record recovery **changes** is
written back *inside* `recover()`. Without that, a second crash before the next write would resurrect
a job as `running` and it could ping-pong indefinitely. T056 asserts the property — every file on disk
deserialises to a job equal to the registry's — so "must not disagree" can actually fail.

**Ordering (the second thing asked for) is asserted, not assumed.** `init()` → `recover()` →
`create_task(_sweep_loop())` → `yield`. That no caller observes a half-recovered state currently
rests on uvicorn completing lifespan startup before accepting connections — true, and *not ours*. So
T058 asserts what we do own: the call order inside our own `lifespan`, checked structurally.

**Untrustworthy records get one policy, not four.** Truncated JSON, a missing field, a `handle` that
disagrees with its filename, and an unrecognised state are all skipped, logged, and counted — never
repaired. An unknown state is most likely a *downgrade* (a record from a later version), where
interrupting it would mislabel it. One bad file costs one job; a start-up crash-loop costs the
service.

**The manual verification includes a second kill.** Killing once and restarting proves the job goes
terminal; killing again immediately after the first recovery and restarting is what proves it does
not flip back to `running`. That ping-pong is the one failure a single restart cannot reveal.

Sequencing note carried into the dependency rules: **T053 must land with T052**, because a `recover()`
that loads `running` jobs and leaves them running produces exactly the permanently-misleading job
FR-025 exists to prevent — and would look like it worked.

## Outcome

- ✅ Impact: US6 is executable. 10 tasks, explicit authority rule, one owner-run stop point, one planning-document correction already applied.
- 🧪 Tests: none run — planning artifact. T056 and T058 specify the test extensions; still one test file.
- 📁 Files: specs/002-http-download-api/tasks.md, specs/002-http-download-api/research.md
- 🔁 Next prompts: `/sp.implement` for T051–T058, stopping before T059. After that only US2 remains, and it adds sentences rather than capability.
- 🧠 Reflection: The D7 flaw was visible only because writing the task forced the question "what stops this deleting a live CLI download?" to be answered concretely rather than cited. A citation had been standing in for an argument.

## Evaluation notes (flywheel)

- Failure modes observed: a research decision whose justification did not support its conclusion, unnoticed through plan, tasks, and two implementation phases because every later document cited it rather than re-reading it.
- Graders run and results (PASS/FAIL): checklist format across all 60 tasks — PASS. ID continuity T051–T060, no gaps or duplicates — PASS. Story labels on the eight story-phase tasks — PASS. Dependency graph covers every new task — PASS. Constraint compliance (no new dependencies, one test file, frozen modules untouched, manual verification task) — PASS, each as a task bullet or T060's close-out check.
- Prompt variant (if applicable): explicit coverage list plus two named correctness requirements — the same shape as the US5 prompt, which also produced the phase's two sharpest tests.
- Next experiment (smallest change to try): when a task cites a research decision as its justification, re-read that decision's *argument* rather than its conclusion. Two phases cited D7's conclusion without anyone re-checking that the reasoning held.
