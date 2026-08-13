---
id: 0014
title: ADR Review Execution Disclosure Durability
stage: misc
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.adr thread-pool-execution-and-message-safety
labels: ["adr", "architecture", "concurrency", "security-boundary", "persistence"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md
  pr: null
files:
 - history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md
 - history/adr/0003-caller-facing-disclosure-boundary.md
 - history/adr/0004-filesystem-job-record-durability.md
 - specs/002-http-download-api/plan.md
 - history/prompts/002-http-download-api/0014-adr-review-execution-disclosure-durability.misc.prompt.md
tests:
 - none (documentation stage; no code written)
---

## Prompt

/sp.adr thread-pool-execution-and-message-safety

(Full command body: analyze planning artifacts and document architecturally significant decisions as
ADRs. Extract decision *clusters* rather than atomic choices; check existing ADRs for coverage and
conflicts; apply the three-part significance test — impact, alternatives, cross-cutting scope — and
create ADRs only for candidates passing all three. Guard against the two named failure modes:
over-granular ADRs, and ADRs missing alternatives. Grader requires each ADR to cluster related
changes, list at least one alternative with rationale, include pros and cons, and stay concise but
sufficient for future reference.)

## Response snapshot

`create-adr.sh` does not exist in this repo — only `.specify/scripts/powershell/` is present, with no
bash script directory — so the ADRs were written agent-natively from
`.specify/templates/adr-template.md`, matching ADR-0001's established depth.

**Split the title the user supplied, and added a third ADR.** The suggested slug,
`thread-pool-execution-and-message-safety`, conflated two decisions that are independent: the
disclosure boundary could switch to a sanitiser without touching the executor, and the executor could
become a process pool without touching the disclosure boundary. Under the command's own clustering
rule — group what changes together, separate what can diverge — they are two clusters. A third,
persistence, surfaced from the same artifacts and passed the significance test on its own.

Created:

- **ADR-0002 — Off-Event-Loop Job Execution and Concurrency Control.** Five components: the
  service-owned `ThreadPoolExecutor`; `max_workers` as the sole cap; `jobs.py` importing neither
  FastAPI nor asyncio; the deadline raised from the progress callback with a watchdog backstop; and
  the accepted wedged-worker limitation. Alternatives: async handler (fatal), `BackgroundTasks` /
  `asyncio.to_thread` (anyio's shared unowned pool), oversized-pool-plus-semaphore (defers the
  problem while adding a second source of truth), and `ProcessPoolExecutor` (named as *the* fix if
  the limitation proves intolerable — a re-plan, not a patch). Records that the ADR corrects
  spec.md's assumption about which window is unguarded.
- **ADR-0003 — Caller-Facing Disclosure Boundary.** The record has no field capable of holding raw
  text; a catalog of literal sentences; prefix-exact classification against `_ERROR_DIAGNOSES` with a
  coverage test; one identical refusal for every unresolvable handle. The strongest rejected
  alternative is the sanitiser-on-the-way-out — the option most teams reach for first — rejected
  because it makes safety a property of a code path rather than of the data. The *correct* fix (a
  `code` field on `DownloadOutcome`) is recorded precisely because it is forbidden by the freeze, so
  a future reader knows what to do first if the freeze lifts.
- **ADR-0004 — Filesystem Job Record Durability.** One JSON file per job, temp-then-`os.replace`,
  memory authoritative, transitions written but progress never. The alternative a reader will raise
  first — `sqlite3`, which ships with Python — is addressed head-on: Principle IV bars databases
  unless requested, "stdlib" is not an exemption, and the owner excluded databases explicitly. Also
  records why hashing the filename protects nothing (the file's contents hold the handle).

Each ADR states negatives that are genuinely uncomfortable rather than decorative: capacity halving
from one wedged worker; callers getting strictly less detail than the CLI operator;
`service_unavailable` deliberately misdescribing a missing `ffmpeg` to the caller; no fsync; progress
lost on restart; and the single-process constraint now written in three places.

No conflicts with ADR-0001. It is *referenced* by ADR-0003, since ADR-0001 is what made the
`is not a video` row of `_ERROR_DIAGNOSES` reachable — the same table ADR-0003's classifier matches
against. plan.md gained a linking section.

## Outcome

- ✅ Impact: Three decision clusters from feature 002 permanently recorded with alternatives, costs, and the named conditions under which each should be revisited.
- 🧪 Tests: None — documentation stage.
- 📁 Files: `history/adr/0002-…md`, `history/adr/0003-…md`, `history/adr/0004-…md`, plan.md (linking section)
- 🔁 Next prompts: `/sp.tasks` for Phase 1 (US1 + US3). Decide `XVD_MAX_PENDING` before Phase 3.
- 🧠 Reflection: The title I proposed at the end of the plan was itself the over-granularity failure mode in reverse — it *under*-separated. Applying the command's clustering rule to my own suggestion, rather than accepting it as given, is what produced three coherent ADRs instead of one incoherent one.

## Evaluation notes (flywheel)

- Failure modes observed: `create-adr.sh` is referenced by the command but absent from this repo (PowerShell-only scripts directory); fell back to the template without blocking. The command's own suggested title under-clustered, which the workflow's Step 2 rule caught.
- Graders run and results (PASS/FAIL): Significance test — PASS for all three (each impacts how code is structured, each lists three or more alternatives with rationale, each is cross-cutting). Checklist grader — PASS: all three cluster multiple components, all list alternatives with reasons, all carry explicit positive and negative consequences.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): When `/sp.tasks` runs, check that the ADR-0003 coverage test appears as its own task rather than as a sub-bullet of the classifier task — it is the guarantee, not a detail of the implementation.
