# ADR-0004: Filesystem Job Record Durability

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Proposed
- **Date:** 2026-08-13
- **Feature:** 002-http-download-api
- **Context:** FR-024 requires job records to survive a restart, and FR-025 requires that no job can
  report `running` forever after one. Constitution Principle IV forbids adding a database or task
  queue unless the owner asks for one, and the owner explicitly did not: *"No database, no Redis, no
  Celery, no APScheduler. Durability is filesystem-based per Principle IV."* The constitution's
  Technology Constraints section says the same thing more bluntly — *"Persistence: filesystem only."*

  So the question is not *whether* to use the filesystem but *how*, and the "how" has real substance:
  what is authoritative when memory and disk disagree, what gets written and how often, and — because
  the record is keyed by a secret the caller supplies on every request — whether a caller-supplied
  string can ever become a path component. That last part makes this a Principle V question as well
  as a persistence question, which is why it is one decision cluster rather than a storage detail.

<!-- Significance checklist (ALL must be true to justify this ADR)
     1) Impact: Long-term consequence for architecture/platform/security?
        YES -- it fixes the recovery model, and it is where the path-traversal answer lives.
     2) Alternatives: Multiple viable options considered with tradeoffs?
        YES -- four, below; sqlite3 is in the standard library and is the one a reader will ask
        about first.
     3) Scope: Cross-cutting concern (not an isolated detail)?
        YES -- jobs.py, start-up recovery, the sweep, FR-024, FR-025, FR-026, and Principle V.
-->

## Decision

One JSON file per job on the filesystem, with memory as the authority. Five components, adopted
together:

- **Layout**: `<output_dir>/.xvd-state/jobs/<handle>.json`, one record per file, directory mode
  `0700`. `XVD_STATE_DIR` overrides the location; it sits under the output directory by default so it
  is on the volume the disk guard already measures, and its name does not collide with the
  `.tmp-xvd-*` pattern that start-up recovery sweeps.
- **Atomic writes**: write to a temp file in the same directory, then `os.replace` — the identical
  pattern `_promote` established at `backend/downloader.py:315`, for the identical reason.
  `os.replace` is atomic within a filesystem and the temp file is guaranteed to share one;
  `shutil.move` degrades to copy-then-delete across filesystems and can leave a partial file at the
  destination.
- **Authority**: the **in-memory dict is authoritative** for the life of the process. Disk is a
  crash-recovery record, read exactly once during start-up and never read again. There is no
  reconciliation path, because there is never a second reader.
- **Write cadence**: **state transitions write; progress updates do not.** Progress arrives per
  chunk, and persisting it would be a write storm for information that is meaningless after a restart
  anyway — the job will be failed as interrupted regardless. If a write fails it is logged and the
  job continues on memory alone; only restart-recovery accuracy degrades, not the job.
- **The handle never becomes a path from a request.** Lookup is an in-memory dict hit, so a
  caller-supplied handle cannot reach the filesystem at all. Only handles this service generated are
  ever turned into filenames. A syntactic check (`^[A-Za-z0-9_-]{43}$`) is applied at the boundary as
  the cheap second layer, matching the belt-and-braces posture `build_target` already takes at
  `backend/validation.py:185`.

**The filename is the handle itself**, and the alternative was considered and rejected: naming files
by `sha256(handle)` to keep the secret out of directory listings protects nothing, because the file's
*contents* contain the handle. Anyone who can list the directory can already read it. A second
identifier with no security gain is exactly the speculative machinery the constitution's governance
section forbids.

## Consequences

### Positive

- **No new dependency, and no new operational surface.** Nothing to install on the VPS, nothing to
  back up separately, nothing to keep running, no connection to lose. Principle VII's "runs on a
  plain Linux VPS with Python, uv, and ffmpeg, and nothing else" stays literally true.
- **Recovery is trivially inspectable.** An operator debugging a stuck job reads a JSON file with
  `cat`. This matters more than it sounds: the same operator has no query language, no admin UI, and
  no logs beyond a text file.
- **Failure is per-job, not global.** A corrupt or unreadable record loses one job; the rest load
  normally. A single shared store would put every job behind one file's integrity.
- **The write path is already proven in this codebase.** Reusing 001's temp-then-`os.replace` pattern
  means one atomicity argument covers both features, and a reader who has understood `_promote`
  already understands this.
- **The Principle V answer is structural.** "Can a caller traverse to another file?" is answered by
  "a request never produces a path", not by trusting a sanitiser.

### Negative

- **Not fsync'd.** `os.replace` is atomic with respect to *other readers*, but the data may still sit
  in the page cache when power is lost, so a record can be stale or absent after a hard power failure.
  Accepted: the affected job would have been failed as interrupted by recovery anyway, so the
  practical loss is the record of a job that was going to be told it failed.
- **A restart discards more than it must.** Because progress is never persisted, a job that was 95%
  through a large transfer comes back as `failed / interrupted` with nothing to show for it. Feature
  001's already-downloaded check softens this — a *completed* file still in the output directory
  makes resubmission finish instantly — but partial transfers are simply lost.
- **Start-up cost is O(jobs).** Every record is read and parsed before the service accepts requests.
  Negligible at this scale, and a reason to make retention actually delete records rather than only
  the files they point at.
- **Single-process by construction.** Two uvicorn workers would each hold their own authoritative
  memory over the same directory, and the last writer would win. Nothing in the design detects this.
  It is consistent with spec Assumption 5 and with ADR-0002's cap, but it is now written in three
  places and would have to be undone in all three.
- **Directory listing is a capability leak to anyone with shell access.** Handles are filenames.
  `0700` and operator-only access is the whole of the defence — acceptable because that same operator
  can read the video files directly, but it does mean handles must never be written anywhere less
  private.

## Alternatives Considered

**Alternative A — `sqlite3` from the standard library.**
One file, ACID transactions, real atomicity including durability on commit, and a query language for
the retention and watchdog sweeps.
*Pros*: strictly better guarantees than JSON files; no third-party dependency, since it ships with
Python; concurrent access would be handled properly if the single-process assumption ever broke.
*Rejected because*: Principle IV says "Databases … MUST NOT be added unless the user explicitly
requests them", and the owner explicitly excluded a database in the same breath as naming the
alternatives they did not want. SQLite is a database by any reading of that rule, "stdlib" is not an
exemption, and the durability it buys is not needed for records whose worst-case loss is a job that
recovery would have failed anyway. **This is the strongest rejected alternative and the one to
revisit first if the job store ever needs querying, multi-process access, or true crash durability.**

**Alternative B — One JSON file holding all jobs.**
A single `jobs.json` rewritten atomically on each change.
*Pros*: one file to read at start-up; no directory to manage; trivially simple.
*Rejected because*: every transition rewrites the whole store, so cost grows with the number of jobs
rather than staying constant, and one corrupt write loses every job instead of one. It also
serialises unrelated writes behind a single file for no benefit at this concurrency.

**Alternative C — No persistence; recover by scanning the output directory.**
Hold jobs in memory only. After a restart, jobs simply do not exist, and callers holding a handle get
the standard `404`.
*Pros*: the least code by a wide margin, and arguably defensible given that FR-025 fails interrupted
jobs anyway.
*Rejected because*: it fails FR-024 outright, and it makes the `404` lie — the handle *was* valid, and
the caller has no way to distinguish "your job was lost in a restart" from "that was never a job".
A caller who successfully downloaded a file five minutes ago would be told it never existed.

**Alternative D — Chosen: one JSON file per job, atomic writes, memory authoritative, transitions
only.**
*Pros*: satisfies FR-024 with no dependency; per-job failure isolation; reuses a proven pattern; the
path-traversal answer falls out of the lookup design.
*Cons*: no fsync, progress lost on restart, single-process by construction — all recorded above.

## References

- Feature Spec: [spec.md](../../specs/002-http-download-api/spec.md) — FR-024, FR-025, FR-026,
  FR-030, FR-034; SC-010; US6; Assumption 5; Resolved Clarification Q3
- Implementation Plan: [plan.md](../../specs/002-http-download-api/plan.md) — Constitution Check
  Principles IV, V, VII
- Research: [research.md](../../specs/002-http-download-api/research.md) — D3 (durability, authority,
  filename choice, path-traversal answer), D7 (what runs recovery and the sweeps), D8 (handle
  entropy, which is what makes a handle safe as a filename)
- Data Model: [data-model.md](../../specs/002-http-download-api/data-model.md) — persistence layout
  and the configuration table
- Constitution: [constitution.md](../../.specify/memory/constitution.md) — Principle IV, Principle
  VII, "Persistence: filesystem only"
- Related ADRs: [ADR-0002](./0002-off-event-loop-job-execution-and-concurrency-control.md) (shares
  the single-process constraint and supplies the restart this ADR recovers from),
  [ADR-0003](./0003-caller-facing-disclosure-boundary.md) (the `failure_code` this record stores, and
  why it stores nothing else about a failure)
- Evaluator Evidence:
  [PHR 0013](../prompts/002-http-download-api/0013-http-api-implementation-plan.plan.prompt.md)
