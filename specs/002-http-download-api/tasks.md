---

description: "Task list for 002-http-download-api — Phase 1 (US1 + US3), Phase 3 (US4), Phase 4 (US5)"
---

# Tasks: HTTP Download API

**Input**: Design documents from `/specs/002-http-download-api/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/openapi.yaml](./contracts/openapi.yaml),
[quickstart.md](./quickstart.md)

**Scope of this document**, in the plan's numbering:

| Plan phase | Stories | Tasks | State |
|---|---|---|---|
| Phase 1 | US1 + US3 | T001–T028 | code complete; **T027 awaits the owner** |
| Phase 2 | US2 | — | not generated |
| Phase 3 | US4 | T029–T043 | code complete; **T043 awaits the owner** |
| Phase 4 | US5 | T044–T050 | code complete; **T049 awaits the owner** |
| **Phase 5** | **US6** | **T051–T060** | **generated below** |

All three outstanding tasks are the 🚦 manual verifications, which the owner runs. Nothing else is
open.

US2 and US6 are deliberately absent, by instruction. See *Explicitly Not In This Document*.

**Tests**: Per Constitution Principle II, exactly one test file, `tests/test_jobs.py` — extended, never
joined by a second. No HTTP integration tests, no network calls, no mocking framework. Manual
verification via `curl` closes each phase (T027, T043, T049).

## Owner decisions recorded before this breakdown

- **`XVD_MAX_PENDING` adopted.** FR-015 in spec.md now scopes "held, not dropped" to a configured
  pending depth, with an at-capacity refusal beyond it. `research.md` D2 and `plan.md` were updated
  to match. **The cap itself is Phase 3** — no task here — but the requirement was amended first so
  nothing in this phase is built against wording that will move.
- **Wedged `ffmpeg` worker accepted as a known limitation.** It stays in `plan.md` Complexity
  Tracking and [ADR-0002](../../history/adr/0002-off-event-loop-job-execution-and-concurrency-control.md).
  **No tasks are generated for it.**

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different files, no dependency on incomplete work
- **[Story]**: US1 or US3. Setup, Foundational, and Verification phases carry no story label
- Note: most tasks here touch `backend/jobs.py` or `backend/api.py`, so **[P] is rare by nature** —
  two tasks editing the same module are not parallel, and marking them so would be a lie

## Path Conventions

All application code lives in `backend/` (Principle I). Tests live in `tests/`.
**`backend/downloader.py`, `backend/validation.py`, and `backend/config.py` are frozen and are not
touched by any task below.**

---

## Phase ordering, and why it deviates from one-phase-per-story

The template groups tasks strictly by user story. This breakdown splits US1 and US3 each into a
service-layer phase and an HTTP-layer phase, so that **`backend/jobs.py` is complete and tested
before `backend/api.py` exists at all**. The reason is the one the owner gave: if `api.py` were
needed to exercise `jobs.py`, the Principle III boundary would already have failed, and a task order
that permits it invites exactly that. Writing `api.py` once against a finished service layer also
beats writing it twice.

Story independence is preserved where it matters — each story still has a checkpoint at which it is
verifiable on its own:

- **US1 is independently verifiable after Phase 5** (submit → poll → retrieve, end to end).
- **US3 is independently verifiable after Phase 6** (identical refusals, no leakage, audit record).

---

## Phase 1: Setup

**Purpose**: nothing can run without the two new dependencies.

- [X] **T001** Add `fastapi>=0.115` and `uvicorn>=0.31` to `[project.dependencies]` in
  `pyproject.toml`, then run `uv sync`.
  - Use **plain `uvicorn`, not `uvicorn[standard]`** — the extra pulls in `httptools`, `uvloop`,
    `watchfiles`, and `websockets`, none of which this feature uses, and Principle IV requires each
    dependency to be justified against the standard library first.
  - `pydantic` is **not** added explicitly; it arrives as a FastAPI dependency and is confined to
    `backend/api.py` (research D11).
  - **Verify**: `uv run python -c "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)"`
    prints both versions.

**Checkpoint**: dependencies installed, nothing else changed.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: the parts of `backend/jobs.py` that both US1 and US3 need. No user story work can begin
until this phase is complete.

All three tasks edit the same new file and therefore run in sequence, not in parallel.

- [X] **T002** Create `backend/jobs.py` with its module docstring and configuration loading.
  - The docstring MUST state that this module imports no framework — **without naming `fastapi` or
    `asyncio` as literal tokens**, so T025's check cannot match prose. This is the exact mistake
    feature 001's T006 made (`specs/001-post-video-download/tasks.md:147-151`).
  - Read the Phase-1 subset of the config table in [data-model.md](./data-model.md) from
    `os.environ`: `XVD_STATE_DIR`, `XVD_MAX_CONCURRENT`. Every value has a working default.
  - Resolve the output directory by calling the frozen `config.output_dir()` **with no argument**.
    Passing anything derived from a request would violate FR-030.
  - Create `<state_dir>/jobs/` with mode `0700`.
  - `XVD_JOB_TIMEOUT`, `XVD_RETENTION`, `XVD_SWEEP_INTERVAL`, `XVD_MIN_FREE_BYTES`,
    `XVD_RATE_LIMIT`, `XVD_RATE_WINDOW`, and `XVD_MAX_PENDING` belong to later phases — **do not add
    them now**.

- [X] **T003** Add the `Job` dataclass to `backend/jobs.py`, exactly as specified in
  [data-model.md](./data-model.md).
  - Fields: `handle`, `canonical_url`, `state`, `created_at`, `started_at`, `completed_at`,
    `downloaded_bytes`, `total_bytes`, `files`, `failure_code`, `client_address`.
  - **This dataclass MUST NOT gain a `message`, `detail`, or `error_text` field.** Add a comment
    saying so and why — the absence is the FR-029 guarantee, and a later contributor needs to find
    the reason at the point of temptation, not in a document
    ([ADR-0003](../../history/adr/0003-caller-facing-disclosure-boundary.md)).
  - Define the five state constants and encode invariant 1 as a helper that refuses to leave a
    terminal state. This is not defensive decoration: T007's watchdog and a late-returning worker
    genuinely race.

- [X] **T004** Add atomic job persistence to `backend/jobs.py`.
  - Write one JSON file per job at `<state_dir>/jobs/<handle>.json` via **temp file in the same
    directory then `os.replace`** — the pattern `_promote` establishes at `backend/downloader.py:315`,
    for the same atomicity reason ([ADR-0004](../../history/adr/0004-filesystem-job-record-durability.md)).
  - Called on **state transitions only**. Progress updates must never write to disk (research D3).
  - `files` serialises as a list of strings.
  - **Only the write side.** Reading records back is restart recovery (FR-024/FR-025) and belongs to
    Phase 5 of the plan. Writing now avoids retrofitting persistence into every transition later.

**Checkpoint**: `backend/jobs.py` imports cleanly, creates its state directory, and can persist a
record. No story work has started.

---

## Phase 3: US1 service layer — `backend/jobs.py` (Priority: P1) 🎯 MVP

**Goal**: the whole capability, callable as plain Python.

**Independent Test**: `tests/test_jobs.py` drives submission, state transitions, and file selection
with no event loop, no HTTP client, and no network.

- [X] **T005** [US1] Add handle minting and the in-memory registry to `backend/jobs.py`.
  - `secrets.token_urlsafe(32)` — 32 **bytes**, giving 256 bits in a 43-character string (verified,
    research D8). Not 16, and not a UUID.
  - `dict[str, Job]` registry plus `get(handle) -> Job | None`.
  - **`get()` MUST NOT touch the filesystem.** A caller-supplied handle never becomes a path
    component; that is the structural path-traversal answer (research D3), not the syntactic check.

- [X] **T006** [US1] Implement `submit(url, client_address) -> Job` in `backend/jobs.py`.
  - Call the frozen `validation.parse_post_url(url)` **first**. Let its `ValueError` propagate — the
    HTTP layer maps it to 400. No job, no record, no network request (FR-003).
  - Deduplicate on `canonical_url` against jobs in `waiting` or `running`; return the existing job
    (FR-016, FR-017). Note that `canonical_url` already distinguishes `/video/1` from the bare post
    URL, so indexed and bare submissions are correctly separate jobs.
  - Mint the handle, build the record, persist it (T004), then `executor.submit(...)`.
  - Disk guard, rate limit, and pending cap are Phase 3 of the plan — **not here**.

- [X] **T007** [US1] Add the executor and the worker function to `backend/jobs.py`.
  - Module-level `concurrent.futures.ThreadPoolExecutor(max_workers=XVD_MAX_CONCURRENT)`.
    `max_workers` **is** the concurrency cap — no semaphore (research D2, ADR-0002).
  - Worker's first statement: transition `waiting → running` and set `started_at`.
  - Call `download_post(url, output_dir, progress=..., on_warning=...)` with the output directory
    from T002. `on_warning` goes to the log only — it names a temp directory
    (`backend/downloader.py:309-312`).
  - Give the worker a **`download=download_post` default parameter** so `tests/test_jobs.py` can pass
    a plain stub function. One parameter with a default — not an injection framework, not a registry,
    not a protocol. Principle II permits "plain fakes and stub objects only", and this is the seam
    that makes that possible without a mocking library.
  - Every terminal transition goes through T003's guard.

- [X] **T008** [US1] Add the progress callback in `backend/jobs.py`.
  - Read `downloaded_bytes` and `total_bytes` (or `total_bytes_estimate`) from yt-dlp's status dict;
    update the record **in memory only** (research D3).
  - Tolerate a missing total — FR-008 makes progress advisory, and for a multi-video post the figures
    restart per video.
  - **Do not add the deadline check here.** The time limit is FR-020, Phase 3 of the plan. The
    callback is where it will go; it does not go there yet.

- [X] **T009** [US1] Map `DownloadOutcome` to a terminal state in `backend/jobs.py`.
  - `downloaded` and `skipped` → `finished`, storing `outcome.paths` in `files` and setting
    `completed_at`. `skipped` is a success: feature 001 returns it when the file is already present.
  - `failed` → `failed` with a `failure_code` from T013.
  - Catch `ValueError` from `download_post` (it can escape from `_extension_of` and from
    `build_target`) → `failed` / `unclassified`, raw text to the log only.
  - **Never store `outcome.message` on the record.**

- [X] **T010** [US1] Implement `file_for(job, index=None) -> Path` in `backend/jobs.py`.
  - No index and exactly one file → that file. No index and several → refuse, naming the count. An
    index → bounds-check against `job.files` (FR-035, FR-036; spec Q2).
  - **An index is never required for a single-file job** — that is the common case and the explicit
    modification the owner made to Q2 option A.
  - Reject an index that is zero, negative, out of range, or non-integer with the same refusal.
  - Re-check the file exists before returning it; if it is gone, the caller must be told, never handed
    a partial or empty body (FR-014).

- [X] **T011** [US1] Create `tests/test_jobs.py` covering the service layer, with no network and no
  HTTP.
  - Handle format: 43 characters, `[A-Za-z0-9_-]` only, and two mints differ.
  - Deduplication: two URL spellings of one post collapse to one job; `/video/1` and the bare URL do
    not.
  - `parse_post_url` rejection propagates from `submit` and creates no record.
  - State machine: a terminal state cannot be re-entered (T003's guard), driven with the T007 stub.
  - `file_for`: single-file no-index, multi-file no-index refusal, valid index, and each invalid index
    form.
  - **The `_ERROR_DIAGNOSES` drift test is deliberately NOT here — it is T014.**

**Checkpoint**: `uv run pytest` passes. The whole capability works from plain Python. `backend/api.py`
does not exist yet, which is the point.

---

## Phase 4: US3 service layer — `backend/jobs.py` (Priority: P2)

**Goal**: the disclosure boundary and the audit trail, still with no HTTP layer in existence.

**Independent Test**: the failure-code catalog and its coverage test run under `pytest`; the audit log
is inspectable as a file.

- [X] **T012** [US3] Add `FAILURE_MESSAGES: dict[str, str]` to `backend/jobs.py` — the caller-safe
  sentence catalog from [data-model.md](./data-model.md).
  - Every sentence is a **literal in the source**. None interpolates a path, filename, URL, count, or
    any text originating outside this table.
  - `service_unavailable` deliberately does not tell the caller that `ffmpeg` is missing. That is an
    operator fault and belongs in the log.
  - At the single site where `download_post` returns, log the raw `outcome.message` together with the
    job handle so FR-033 correlation works, then discard it.

- [X] **T013** [US3] Add `FAILURE_PREFIXES` classification to `backend/jobs.py`.
  - Match `DownloadOutcome.message` with **`str.startswith`** against the explanation strings in
    `downloader._ERROR_DIAGNOSES`. This is exact, not heuristic: `_partial_failure` composes
    `f"{reason} Files already saved: {names}"` (`backend/downloader.py:559`), so the diagnosis is
    always a prefix.
  - Cover all nine codes in [data-model.md](./data-model.md), including the literals at
    `backend/downloader.py:388`, `:397`, `:437`, `:442`, `:486`, and `:527`.
  - Order specific before generic; anything unmatched → `unclassified`.
  - Declare the prefix strings as **our own literals**. Do not import `_ERROR_DIAGNOSES` in
    production code — that import belongs only to T014.
  - > **Scope note**: the full table nominally belongs to US2. It is pulled forward because T014
    > cannot assert full coverage against a partial map, and the owner required T014 in this phase.
    > What US2 still owns is per-code message refinement, not the mechanism.

- [X] **T014** [US3] **Write the classification drift test in `tests/test_jobs.py`.**
  *(Its own task by explicit instruction — it is the single guard against silent classification decay,
  so it is a named deliverable rather than a bullet inside T011.)*
  - Import the private `downloader._ERROR_DIAGNOSES` **in the test only**, and assert every
    `explanation` in that table is matched by exactly one entry in `FAILURE_PREFIXES`. An upstream
    edit, addition, or reordering of that table must **fail the build**, not degrade quietly to
    `unclassified`.
  - Assert every code in `FAILURE_PREFIXES` has a `FAILURE_MESSAGES` entry, and vice versa.
  - Assert no string in `FAILURE_MESSAGES` contains `/`, `\`, or `..`.
  - **Confirm the test can actually fail**: temporarily alter one prefix, watch it go red, revert.
    A drift guarantee that passes vacuously is worth nothing — which is precisely how feature 001's
    T006 grep survived never having run.

- [X] **T015** [US3] Add the submission audit log to `backend/jobs.py`.
  - Append one JSON object per line to `<state_dir>/submissions.log`: `at`, `canonical_url`,
    `client_address`, `outcome`, `handle` (FR-031).
  - **Canonical URL only, never the raw submitted string** (FR-032). It is written only after
    `parse_post_url` has accepted it.
  - Write a record for **refusals too** — `rejected_url` and `deduplicated` — since a refusal is the
    more interesting entry when investigating abuse. `rate_limited` and `disk_low` arrive in Phase 3.

**Checkpoint**: `uv run pytest` passes including T014. The service layer is complete. **`backend/api.py`
still does not exist.**

---

## Phase 5: US1 HTTP layer — `backend/api.py` (Priority: P1) 🎯 MVP completes here

**Goal**: the capability over HTTP. Parse, call `jobs.py`, serialise — nothing else.

**Independent Test**: the `curl` sequence in [quickstart.md](./quickstart.md) § "The happy path".

- [X] **T016** [US1] Create `backend/api.py`: the app, its lifespan, and the pydantic models.
  - `FastAPI(debug=False)` so no traceback middleware is installed (research D6).
  - `SubmitRequest` with `model_config = ConfigDict(extra="forbid")` and `url: str` capped at 2048
    characters. **`extra="forbid"` is load-bearing, not tidiness** — it is what makes FR-004
    enforceable at the boundary, so a field naming an output directory or a format cannot be
    smuggled in.
  - Lifespan shuts the executor down on exit. **Restart recovery is Phase 5 of the plan — not here.**
  - Pydantic appears in this file and **nowhere else** (research D11).

- [X] **T017** [US1] Implement `POST /jobs` in `backend/api.py`.
  - Call `jobs.submit(...)`; return **202** with the `Job` shape from
    [contracts/openapi.yaml](./contracts/openapi.yaml).
  - Catch `ValueError` from validation → **400** with a fixed message. Never echo the submitted URL
    back (FR-005).
  - A deduplicated submission returns the same body as a new one — the caller cannot tell, and does
    not need to.

- [X] **T018** [US1] Implement `GET /jobs/{handle}` in `backend/api.py`.
  - Serialise from the record: `handle`, `state`, `file_count`, `progress` while running, `failure`
    when failed, timestamps.
  - `failure.message` comes from `FAILURE_MESSAGES[code]` — **the record has no other text to offer,
    which is the point** (T003, ADR-0003).

- [X] **T019** [US1] Implement `GET /jobs/{handle}/file` and `GET /jobs/{handle}/file/{index}` in
  `backend/api.py`.
  - `FileResponse` with the path from `jobs.file_for(...)` — **only** from the record, never from
    anything in the request (FR-030).
  - `Content-Disposition` filename is the file's own basename, already sanitised by the frozen
    `build_target`.
  - **409** when not finished, or when several files exist and no index was given (message names the
    count). **410** when expired. **404** for an unknown handle or an out-of-range index.
  - The `expired` state cannot occur until Phase 4 of the plan, but the branch is written now so the
    contract is complete and the status code is not retrofitted later.

**Checkpoint**: 🚦 **US1 is independently verifiable end to end.** Submit a real post URL, watch
progress advance, retrieve a playable file with picture and sound.

---

## Phase 6: US3 HTTP hardening — `backend/api.py` (Priority: P2)

**Goal**: nothing a caller receives reveals anything about the server or about other callers.

**Independent Test**: [quickstart.md](./quickstart.md) § "Multi-caller safety" — three handle shapes
give one identical response, and the leakage grep is silent.

- [X] **T020** [US3] Make every unresolvable handle produce one identical refusal in
  `backend/api.py`.
  - Unknown, malformed, and wrong-length handles all return
    `404 {"code":"not_found","message":"No such job."}` — same status, same body (FR-028).
  - Apply `pattern="^[A-Za-z0-9_-]{43}$"` as the cheap second layer, but a pattern failure must
    produce **that same 404**, not a 422 — otherwise the shape of the handle leaks whether it could
    have been real.
  - No secret-dependent branch: both paths return the same response object. Constant-time comparison
    is **not** used and **not** claimed; the 256-bit space is the defence (research D8).

- [X] **T021** [US3] Replace the leaking default handlers in `backend/api.py`.
  - Override the `RequestValidationError` handler: pydantic returns the offending input under
    `"input"` by default, which FR-005 forbids. Return a fixed body.
  - Add a catch-all `Exception` handler that logs the traceback and returns one fixed 500 body.
  - Neither handler may include a path, a directory name, an exception type, or a library name.

- [X] **T022** [US3] Pass the caller's address into the service layer in `backend/api.py`.
  - `request.client.host` → `jobs.submit(url, client_address=...)`, feeding T015's audit record.
  - **The application MUST NOT read `X-Forwarded-For` itself.** Trusting it from an untrusted source
    would let any caller spoof their address and defeat the rate limit that Phase 3 will add.

- [X] **T023** [US3] Document the proxy-header requirement as an operational contract.
  - Record in `.env.example` and [quickstart.md](./quickstart.md) that behind a reverse proxy the
    service **must** be started as
    `uvicorn backend.api:app --proxy-headers --forwarded-allow-ips=<proxy-ip>`.
  - State the consequence of omitting it plainly: `request.client.host` becomes the proxy, every
    caller collapses into one rate-limit bucket, and the FR-031 audit log records the proxy for every
    submission — it becomes worthless for the purpose it exists for.
  - `--forwarded-allow-ips` must name the proxy specifically; never `*`.
  - This is an **application argument**, which is why it is a task and not left to deployment. nginx,
    TLS, and the service manager remain out of scope.

**Checkpoint**: 🚦 **US3 is independently verifiable.** All three bad-handle shapes give one response;
the leakage grep is silent across every endpoint.

---

## Phase 7: Verification & Boundary Checks

- [X] **T024** Confirm the frozen modules are untouched.
  - **Verify**: `git diff --stat HEAD -- backend/downloader.py backend/validation.py backend/config.py`
    prints nothing.
  - A non-empty result means a task above needed something the boundary does not offer. **Stop and
    report it** rather than editing the file — that is the standing instruction for this feature.

- [X] **T025** Verify the Principle III boundary with an **AST-based** check, in
  `tests/test_jobs.py`.
  - Parse `backend/jobs.py` with `ast.parse` and walk `ast.Import` / `ast.ImportFrom` nodes. Assert
    no imported module name is or starts with `fastapi`, `starlette`, `pydantic`, or `asyncio`.
  - Parse `backend/api.py` the same way and assert it imports `backend.jobs`, and that no function
    body in it contains a `for` or `while` loop, filesystem access via `open`/`os`/`pathlib`, or a
    call to `yt_dlp` — the markers of logic that belongs one layer down.
  - **AST, not grep, and this is the whole point of the task.** Feature 001's T006 used
    `grep -nE "argparse|sys\.exit|print\("` and matched its own module docstring, which described the
    constraint using the forbidden words. It therefore never passed as written and was signed off by
    eye (`specs/001-post-video-download/tasks.md:147-151`). An AST walk sees imports and calls, and
    cannot see prose — so a docstring may state the rule honestly without breaking the check.
  - Being a test, it runs on every `pytest` invocation instead of relying on someone remembering to
    run a grep.

- [X] **T026** Verify SC-005: no response leaks anything.
  - Drive every endpoint and every error branch — valid, invalid URL, unknown handle, malformed
    handle, not-ready, multi-file-no-index, and a deliberately forced internal error — and grep every
    response body for `/home`, `/var`, `/tmp`, `C:\`, `Traceback`, `yt_dlp`, `.tmp-xvd`, and the
    output directory's own name.
  - **Nothing may match.** This is the acceptance test for the guarantee ADR-0003 exists to provide.

- [ ] **T027** 🚦 **Run the manual verification in [quickstart.md](./quickstart.md).**
  - Submission returns a handle in **under one second**, measured — `curl -w '%{time_total}'`, not
    impression (SC-001).
  - Status polling shows progress **advancing between two successive calls**.
  - The retrieved file plays with **both picture and sound**. A silent video means the merge did not
    happen and the file is wrong even though the job says finished.
  - A URL that is not an X post URL is rejected **in the submission response**, with no job file
    created under `<state_dir>/jobs/` and no outbound network traffic.
  - An unknown handle and a well-formed but unissued handle produce **byte-identical** refusals.
  - Record the results inline in this file, as feature 001 did.

- [X] **T028** [P] Update `.env.example` with the Phase-1 variables only — `XVD_OUTPUT_DIR`,
  `XVD_STATE_DIR`, `XVD_MAX_CONCURRENT` — each with its default and one line on what it governs.
  Later phases add their own; listing them now would advertise configuration that does nothing.

---

## Explicitly Not In This Document

Named so that their absence reads as a decision rather than an oversight:

| Deferred | Requirements | Belongs to | Status |
|---|---|---|---|
| Full per-code message refinement | FR-010, FR-011 | US2 / plan Phase 2 | not generated |
| Disk guard, rate limit, `XVD_MAX_PENDING`, job time limit + watchdog | FR-018, FR-019, FR-020, FR-015 cap | US4 / plan Phase 3 | **built, T029–T042** |
| Retention sweep, `expired` transition | FR-021, FR-022, FR-023 | US5 / plan Phase 4 | **built, T044–T048** |
| Restart recovery, temp-directory sweep | FR-024 (read side), FR-025, FR-026 | US6 / plan Phase 5 | **now T051–T060** |
| Wedged-worker mitigation | — | **Never.** Accepted limitation, ADR-0002 | never |
| Docker, nginx, systemd, TLS | — | Out of scope by the spec | never |

The `expired` branch in T019 and the write side of persistence in T004 are the two places where later
phases were anticipated, each for a stated reason: avoiding a retrofit that would touch every
transition. **T019's branch is why Phase 4 below adds no transport code at all** — the anticipation
paid off exactly as intended.

---

## Dependencies & Execution Order

```text
T001 (setup)
  └─> T002 ──> T003 ──> T004                     [Phase 2: foundational, all in jobs.py]
                          └─> T005 ──> T006 ──> T007 ──> T008 ──> T009 ──> T010 ──> T011
                                                                              [Phase 3: US1 service]
                                                    └─> T012 ──> T013 ──> T014 ──> T015
                                                                              [Phase 4: US3 service]
                                                                └─> T016 ──> T017 ──> T018 ──> T019
                                                                              [Phase 5: US1 HTTP] 🚦
                                                                        └─> T020 ──> T021 ──> T022 ──> T023
                                                                              [Phase 6: US3 HTTP]  🚦
                                                                              └─> T024, T025, T026, T027, T028
                                                                              [Phase 7: verification]
```

**Hard sequencing rules**:

- **T001 blocks everything.** Nothing imports without it.
- **T002 → T003 → T004 → T005 … T015 are strictly sequential**: they all edit `backend/jobs.py`.
- **T016 must not start before T015 is done.** This is the owner's constraint and the reason for the
  phase split: `backend/api.py` is written once, against a service layer that is already complete and
  already tested.
- **T013 blocks T014**, which blocks the Phase 4 checkpoint. **T009 depends on T013** for its code
  values — implement T009's mapping call after T013 exists, or leave a single `unclassified` return
  in T009 and complete it in T013.
- **T024–T027 need everything.** T028 is the only genuinely parallel task in the phase.

### Parallel Opportunities

Almost none, honestly. Two tasks editing `backend/jobs.py` are not parallel, and eleven of the
twenty-eight do. Only **T028** carries `[P]`, because it is the one task touching a file no other task
touches. Marking more would be decorative.

---

## Implementation Strategy

**MVP is T001–T019** (Setup + Foundational + US1 service + US3 service + US1 HTTP). At that point a
person with a phone and a link can fetch a video, which is the entire reason the feature exists.

Then **T020–T023** close US3 before the URL is given to anybody — the spec's own reasoning is that a
disclosure leak cannot be retrofitted once a service is public, so this is not optional polish.

Then **T024–T028** verify. T025 and T026 are the two that catch classes of mistake review does not:
a boundary that silently rotted, and a leak that only appears on an error path nobody exercised.

### Suggested commit points

After T004, T011, T015, T019, T023, and T027. Each is a state where the tree is coherent and the test
suite passes.

---

## Notes

- The three frozen modules are read but never written. If a task appears to require editing one,
  **stop and report** — that is the standing instruction, and T024 is the backstop that catches it.
- One new test file only: `tests/test_jobs.py`, holding T011, T014, and T025.
- Prefer extending `backend/jobs.py` over adding a module. Two new files is the budget.
- `[P]` marks independence, not staffing — this is a single-developer project.

---
---

# Plan Phase 3 (US4) + Plan Phase 4 (US5)

**Generated 2026-08-15.** Tasks **T029–T050**.

## Why these two ship together

They are the same problem approached from both ends. **US4 stops the disk filling; US5 empties it.**
Either alone leaves a service that fails after enough use — US4 alone turns "disk full" from a
mid-download crash into a permanent, polite refusal, and US5 alone has no floor under it while the
sweep interval elapses. Neither is a complete answer to a finite disk. The spec already says as much:
US5 is P3 "only because the disk threshold guard in US4 prevents the catastrophic version of this
failure while retention is being built" (spec.md:159-161).

They also share one mechanism — `jobs.sweep()`, built in T036 and extended in T045 — which is the
practical reason a single breakdown beats two.

---

## Decisions recorded before the breakdown

Five things had to be settled before tasks could be written honestly. Four are design choices; the
third is a conflict with an existing test that would otherwise be discovered mid-implementation.

### 1. The order of the guards in `submit()`, and what a deduplicated submission pays

```text
rate limit ─▶ parse_post_url ─▶ [ free-disk reading, outside the lock ]
                                    │
                                    ▼   ── under _lock ──
                              dedup scan ──hit──▶ return existing job   (pays nothing further)
                                    │ miss
                                    ├──▶ disk verdict      (FR-018)
                                    ├──▶ pending depth     (FR-015 amended)
                                    └──▶ mint, insert, persist, audit, dispatch
```

Three properties this ordering is chosen for:

- **A deduplicated submission is never refused for capacity.** It creates no job and consumes no
  disk, so refusing it would be punishing a caller for work the service had already decided to do.
  The dedup check therefore comes *before* the disk and depth verdicts are applied.
- **The free-disk reading is taken outside `_lock` and applied inside it.** `shutil.disk_usage` is a
  syscall; holding the registry lock across it would block every status poll and every worker
  transition on the filesystem. Read first, decide later — the reading is at most microseconds stale
  and the threshold is measured in gigabytes.
- **The pending count is computed in the same pass as the dedup scan.** Both walk `_registry.values()`;
  doing it twice would double an O(n) scan already on the hot path for no gain.

### 2. Rate limiting counts *submissions*, not *jobs* — and this deviates from FR-019's literal wording

FR-019 says "how many **jobs** a single caller may **create**". Research D9 says "on each
**submission**". These differ for two cases, and the difference matters:

| Case | Counted under "jobs created" | Counted here |
|---|---|---|
| A submission with an invalid URL | no | **yes** |
| A deduplicated submission | no | **yes** |

**The submission reading is adopted**, because the limiter's job is to bound the work one caller can
impose, and an unbounded invalid-URL path is the cheapest abuse route available — it costs a
validation pass and an audit append every time, and under the literal reading it would be free
forever. A limiter that only counts successes protects the service from its well-behaved users.

> ⚠️ **This is the one judgment call in this breakdown the owner may want to invert.** The cost is
> that a caller who mistypes ten URLs is locked out for the rest of the window (defaults: 10 per
> hour). If that is unacceptable, the change is small and local — move the `_rate_limit` call to
> after `parse_post_url` succeeds — but it should be a decision, not a drift. It is called out here
> rather than buried in a task bullet for that reason.

### 3. The sweep loop breaks T025's no-loops assertion — resolved by a named, narrow exemption

`tests/test_jobs.py::test_transport_layer_has_no_loops` (`tests/test_jobs.py:599-607`) asserts
`backend/api.py` contains **zero** `ast.For` or `ast.While` nodes. The periodic sweep is
`while True: await asyncio.sleep(...)` in `api.py`, so **T039 will turn that test red.**

That test's own docstring says it is "cheap and blunt on purpose: if formatting a response ever
genuinely needs a loop, that is worth a second look rather than a silent allowance". This is that
second look, and the answer is to keep the guarantee while naming the exception:

- The exemption is **one function, by name** — `_sweep_loop` — not a relaxed rule.
- Every other function in `api.py` must still contain no loop, and the test must assert that
  separately rather than counting a global total.
- The exempted function must itself be asserted to contain **no iteration over domain data**: its
  body sleeps and calls `asyncio.to_thread`, and it must not touch `jobs._registry` or any job.

**Deleting or weakening the assertion is not an option.** It is the check that keeps request handlers
from growing logic, and this is the first time it has fired — which is the test working, not
failing.

### 4. The clock is injected as a defaulted parameter — the T007 seam, reused

`submit(..., now: Callable[[], float] = time.time)` and `sweep(*, now: Callable[[], float] = time.time)`.
One parameter with a default, exactly as `download=download_post` is today (`backend/jobs.py:401`).
Not a clock abstraction, not a freezegun dependency, not a module-global someone monkeypatches.

This is what makes the rate limit, the deadline watchdog, and retention testable **without a single
`time.sleep` in the suite** — a test hands in `lambda: base + 4000` and the hour has passed. Both
parameters are keyword-only and `api.py` passes neither.

### 5. "Wedged worker" is given a precise definition, because a vague one cannot be counted

Research D4 requires `/health` to expose a wedged-worker count, and the contract defines it as
"workers whose job the watchdog failed but which never returned"
(`contracts/openapi.yaml:190`). Made operational:

- When the watchdog fails a `running` job, add its handle to a module-level `_watchdog_failed` set.
- `_run_job` discards its own handle from that set in a `finally`, so **every** return path clears
  it — normal completion, exception, or a late return after the watchdog already ruled.
- `wedged_workers = len(_watchdog_failed)`.

The count is therefore exactly "started, was given up on, and has still not come back". It goes to
zero on restart, which is correct: a restart is the only cure, and D4 says so.

---

## Phase 8: US4 service layer — `backend/jobs.py` (Priority: P2)

**Goal**: every resource guard, callable as plain Python, with a clock a test can move.

**Independent Test**: `tests/test_jobs.py` drives every refusal and the watchdog with no event loop,
no HTTP client, no network, and **no elapsed real time**.

All nine tasks edit `backend/jobs.py` and therefore run in sequence. None is `[P]`.

- [X] **T029** [US4] Add the Phase-3 configuration to `backend/jobs.py`.
  - Six variables from the [data-model.md](./data-model.md) table, each with its documented default:
    `XVD_MAX_PENDING` (50), `XVD_JOB_TIMEOUT` (1800), `XVD_MIN_FREE_BYTES` (2147483648),
    `XVD_RATE_LIMIT` (10), `XVD_RATE_WINDOW` (3600), `XVD_SWEEP_INTERVAL` (900).
  - `XVD_RETENTION` is **Phase 4** — T044 adds it. Do not add it now.
  - `_positive_int` (`backend/jobs.py:62`) rejects `0`, which is right for a pool size and **wrong
    for `XVD_MIN_FREE_BYTES`**: `0` is the operator's way to disable the disk guard on a volume where
    it makes no sense. Add `_non_negative_int` for that one variable and use it. Do not loosen
    `_positive_int` — a rate limit of `0` would mean "accept nothing", which is a configuration
    mistake worth naming at start-up rather than serving.
  - Read in `init()`, alongside the existing two, and hold at module level.

- [X] **T030** [US4] Change `submit()` to return a result object instead of raising, in
  `backend/jobs.py`, and update every call site.
  - **This is the one place Phase 3 changes an already-shipped signature, so it is its own task
    rather than a bullet inside another.** Three new refusals (rate limit, disk, capacity) have to
    cross the service boundary, and Principle VI forbids a custom exception hierarchy while `ValueError`
    is already spoken for by the invalid URL.
  - Add `@dataclass(frozen=True) SubmitResult(job: Job | None, problem: str | None, retry_after: int | None)`,
    mirroring `FileResult` (`backend/jobs.py:675-689`) — which is itself the idiom `DownloadOutcome`
    established in the frozen module. Returning "refused, and why" as a record is what this codebase
    already does twice; a third exception type would be the inconsistent choice.
  - `submit()` now returns `SubmitResult` for **every** outcome, including the invalid URL
    (`problem="invalid_url"`), so the function has one shape instead of two. `parse_post_url` still
    raises internally and is still caught; what changes is only how the refusal leaves this module.
  - Update `backend/api.py:266-273` to read `result.problem` instead of catching `ValueError`.
  - Update the 21 call sites in `tests/test_jobs.py` via a local helper —
    `_accept(service, url, address, **kw) -> Job` that asserts `problem is None` and returns
    `result.job`. The helper is not churn-hiding: it states at each site that the submission was
    *expected* to succeed, which the bare call never did.
  - **Verify**: `uv run pytest` is green before any guard is added. This task changes shape, not
    behaviour, and proving that separately is the whole reason it is sequenced first.

- [X] **T031** [US4] Implement the per-address rate limit in `backend/jobs.py` (FR-019).
  - `dict[str, deque[float]]`, address → submission timestamps in the window (research D9,
    data-model.md `RateLimitBucket`).
  - `submit(..., now: Callable[[], float] = time.time)` per decision 4. Evict timestamps older than
    `now() - window` from the left of the deque, compare the length to the limit, and on refusal
    return `SubmitResult(None, "rate_limited", retry_after=ceil(oldest + window - now()))`.
  - `retry_after` is **an integer count of seconds computed on the server** — not a timestamp, not a
    date string, and never anything derived from the request. It is the only number this module has
    ever handed the transport for display, so state in a comment why it is safe under FR-029.
  - Checked **first**, before `parse_post_url`, per decision 2. Put the reasoning in the code, not
    only here — the next reader will otherwise "fix" the ordering.
  - Guard the bucket dict with its own lock, not `_lock`. A submission must not queue behind a
    registry scan to learn it is being refused.

- [X] **T032** [US4] Implement the free-disk guard in `backend/jobs.py` (FR-018).
  - `shutil.disk_usage(_output_dir).free` compared against `XVD_MIN_FREE_BYTES`, **taken outside
    `_lock` and applied inside it, and only on the create path** (decision 1).
  - Skip the check entirely when the threshold is `0`.
  - `OSError` from `disk_usage` — the output volume unmounted, a permissions change — must **fail
    open with a logged warning**, not refuse every submission. A service that stops working because
    it cannot measure its disk has converted a monitoring failure into an outage.
  - Refusal is `SubmitResult(None, "disk_low", None)`. No job, no record, no dispatch — the check is
    before creation, as FR-018 requires in terms.
  - **The check cannot predict the file's size**, so a download can still exhaust the disk while
    running (spec.md:222-224). That is a `failed` job, not a crash, and it is already handled by
    `_run_job`'s existing `BaseException` arm. Do not add a second mechanism here.

- [X] **T033** [US4] Implement the pending-depth cap in `backend/jobs.py` (FR-015 as amended).
  - Count jobs in `WAITING` in the same `_registry` pass as the dedup scan (decision 1). At or above
    `XVD_MAX_PENDING`, return `SubmitResult(None, "at_capacity", None)`.
  - The default of 50 against a concurrency limit of 2 is deliberate and the amended FR-015 says so:
    ordinary over-limit submissions still wait exactly as the original wording promised, and only the
    far tail is refused. **Do not tighten the default to something that would make waiting rare** —
    that would quietly reverse the requirement this cap was written to preserve.
  - The cap exists because the per-address rate limit bounds one caller but not the aggregate across
    many addresses (spec.md:297-302). Say that at the code, since the cap looks redundant beside the
    rate limit until you have read why it is not.

- [X] **T034** [US4] Extend the audit trail with the three new outcomes in `backend/jobs.py`.
  - Add `RATE_LIMITED`, `DISK_LOW`, and `AT_CAPACITY` alongside the existing constants
    (`backend/jobs.py:350-352`), completing the `outcome` enum in
    [data-model.md](./data-model.md)'s `SubmissionRecord`.
  - `canonical_url` is `None` for a rate-limited refusal, exactly as it is for `rejected_url`: the
    rate limit is checked *before* validation, so at that moment the URL is still unvalidated
    caller-supplied text, and FR-032 forbids storing it. For `disk_low` and `at_capacity` the URL has
    passed validation and **is** recorded.
  - This asymmetry is a direct consequence of decision 2's ordering. Note it at the code — it looks
    like an inconsistency until you know why.
  - A run of `rate_limited` lines from one address is the single most useful pattern the log can
    show, which is the reason FR-031 asks for refusals at all.

- [X] **T035** [US4] Enforce the job deadline from the progress callback in `backend/jobs.py`
  (FR-020, research D4).
  - Add a `timed_out: bool = False` field to `Job` **and add the row to
    [data-model.md](./data-model.md)'s table in the same task.** The `Job` docstring
    (`backend/jobs.py:178-189`) demands that any new field be a visible change to that table; a bool
    flag carries no free text and so does not touch the FR-029 guarantee, but the table is the record
    and it must stay true.
  - In `_make_progress_hook`, when `now() > job.started_at + XVD_JOB_TIMEOUT`: set `job.timed_out`,
    then `raise RuntimeError("job time limit exceeded")`.
  - **`RuntimeError` specifically.** Principle VI names it as an approved built-in, and — more
    load-bearing — it must not subclass `OSError` or any network exception, or yt-dlp's handler at
    `YoutubeDL.py:3597` would swallow it and merely report an error instead of aborting (research
    D4.1). Write that reason in the code; it is not guessable.
  - `_run_job`'s exception arms (`backend/jobs.py:530-540`) must check `job.timed_out` and record
    `TIME_LIMIT` rather than `UNCLASSIFIED`. **Identified by the flag, never by parsing the message** —
    `download_post` wraps text as `f"download failed for video {position}: {detail}"`
    (`backend/downloader.py:534`), which no classifier should have to reverse-engineer.
  - `timed_out` is **not** added to `_as_record`. `failure_code` already carries the outcome to disk;
    the flag is a within-process signal between the callback and the worker.
  - Abort and cleanup are guaranteed by two independent paths, both verified in research D4.1 — the
    hook's exception propagating, and `download_post`'s non-zero-retcode backstop at
    `backend/downloader.py:511-512`. Either lands in a handler that runs the `finally` removing the
    temp directory. **No cleanup code is needed here**; adding some would duplicate a frozen module's
    guarantee.

- [X] **T036** [US4] Implement `sweep()` in `backend/jobs.py` — the watchdog and the bucket prune.
  - A plain synchronous function taking `*, now: Callable[[], float] = time.time`. **It must not
    import or mention `asyncio`** — `tests/test_jobs.py:556-566` walks this module's imports and
    fails on it. The periodic caller lives in `api.py` (T039); this is the work, not the schedule.
  - **Watchdog**: every job in `RUNNING` whose `started_at + XVD_JOB_TIMEOUT` has passed →
    `_finish(job, FAILED, failure_code=TIME_LIMIT)`. This decouples the *job's* state from the
    *thread's* state, so a caller is never left waiting on a wedged worker even when no callback ever
    fires to raise (research D4).
  - **Wedged tracking** per decision 5: add the handle to `_watchdog_failed` here; discard it in a
    `finally` in `_run_job`. Log a **distinctly identifiable** operator warning at this point — D4
    names this as the chosen mitigation, and a warning nobody can grep for is not one.
  - **Bucket prune**: drop rate-limit entries whose deque is empty after eviction, so the dict cannot
    grow without bound from one-off callers (research D9).
  - Retention is **T045** and slots in between the watchdog and the prune. Leave the ordering comment
    in place now so the sequence is not rearranged later by accident.
  - `_finish` already refuses a transition on an already-terminal job (`backend/jobs.py:207-231`), so
    a worker that returns after the watchdog ruled cannot overwrite the verdict. **That guard was
    built in T003 for exactly this race** — it stops being hypothetical in this task.

- [X] **T037** [US4] Extend `tests/test_jobs.py` for every Phase-3 behaviour. **No `time.sleep`
  anywhere.**
  - Rate limit: the Nth submission inside the window is refused; `retry_after` is a positive integer;
    a submission after `now` advances past the window is accepted again; a second address is
    unaffected. All driven by handing `submit` a fake `now`.
  - Rate limit counts invalid URLs and deduplicated submissions — assert decision 2's chosen
    behaviour explicitly, so inverting it later is a **failing test and a conversation**, not a
    silent change.
  - Disk guard: below threshold refuses and creates no job and no record; a threshold of `0` disables
    it; `OSError` from the measurement fails **open**. Inject the free-space reading through a
    defaulted parameter, the same seam pattern — do not fill a temp volume.
  - Pending cap: at depth the next submission is refused with `at_capacity`; **a deduplicated
    submission is still accepted at capacity**, which is decision 1's whole point.
  - Deadline: a stub `download` that invokes the progress hook after the clock has passed the
    deadline lands the job in `failed`/`time_limit`, not `unclassified`.
  - Watchdog: a job left `running` past its deadline is failed by `sweep(now=...)`; a late worker
    return does **not** overwrite it; `wedged_workers` reads 1 and returns to 0 once the worker
    returns.
  - Audit: one line per refusal with the right `outcome`, and `canonical_url` is `null` for
    `rate_limited` and non-null for `disk_low`.

**Checkpoint**: `uv run pytest` passes. Every guard works from plain Python, and the whole suite still
runs in the time it did before. `backend/api.py` is unchanged.

---

## Phase 9: US4 transport — `backend/api.py` (Priority: P2)

**Goal**: the refusals over HTTP, and the schedule that drives the sweep.

- [X] **T038** [US4] Map the new refusals to status codes in `backend/api.py`.
  - `rate_limited` → **429** with a **`Retry-After` header** carrying the integer seconds from
    `SubmitResult.retry_after`, and the body from
    [contracts/openapi.yaml](./contracts/openapi.yaml):60-71. The header is required by FR-019 —
    "a refusal MUST state when the caller may retry" — and a header a client already knows how to
    obey beats a sentence only a human reads.
  - `disk_low` → **503** `insufficient_storage`. `at_capacity` → **503** `at_capacity`.
  - Both 503 messages are literals in `api.py`. Neither names the disk, the volume, the threshold,
    the queue depth, or how many jobs are pending — those are facts about the server, and FR-029
    admits none of them.
  - The message may interpolate `retry_after` only. That number is server-computed, like the file
    count already interpolated at `backend/api.py:328-332`, and is the sole exception for the same
    reason.

- [X] **T039** [US4] Run `jobs.sweep()` periodically from `backend/api.py`'s lifespan (research D7).
  - `_sweep_loop`: `while True: await asyncio.sleep(XVD_SWEEP_INTERVAL)` then
    `await asyncio.to_thread(jobs.sweep)`. **`to_thread` is not optional** — `sweep` does filesystem
    work in T045 and must never run on the event loop.
  - Started as a task in `lifespan` (`backend/api.py:36-44`) and **cancelled with its
    `CancelledError` awaited** on shutdown, or the loop outlives the executor it depends on.
  - Wrap the body so one sweep raising cannot kill the loop. A sweep that dies silently means
    retention stops forever while the service looks healthy — the worst available failure mode, and
    invisible without this.
  - **Then amend `test_transport_layer_has_no_loops` per decision 3**: exempt `_sweep_loop` **by
    name**, assert every other function in `api.py` is still loop-free, and assert `_sweep_loop`
    itself references no job data. Update the test's docstring to record why the exemption exists —
    the next reader must find the reason at the assertion, not in this file.

- [X] **T040** [US4] Add `GET /health` to `backend/api.py`
  (contract at [contracts/openapi.yaml](./contracts/openapi.yaml):170-190).
  - Body: `status` (`ok` | `degraded`), `running`, `waiting`, `wedged_workers`. `degraded` when
    `wedged_workers > 0`.
  - The counting is a `jobs.health()` call returning a plain dict; `api.py` serialises it. Counting
    registry states in a handler would be domain iteration in the transport — and would trip T039's
    own amended loop check, which is the check doing its job.
  - **No handles, no URLs, no addresses, no paths.** This endpoint is unauthenticated and reachable
    by anyone; it may carry aggregate counts and nothing else.
  - `wedged_workers` is the visible face of the accepted limitation in ADR-0002. Exposing it is why
    an operator can tell "the service is slow" from "the service has lost half its capacity and needs
    a restart".

- [X] **T041** [P] [US4] Correct the stale proxy-header docstring in `backend/api.py:358-364`.
  - It still says "without those flags every caller collapses into the proxy's single address",
    which the T023 correction disproved: `proxy_headers` defaults to **`True`** and
    `forwarded_allow_ips` to **`"127.0.0.1"`** (`uvicorn/config.py:355-357`). research.md D9,
    quickstart.md, and `.env.example` were corrected; **this docstring was missed.**
  - Replace it with the three-case summary already in research D9, and point at that entry rather
    than restating it a fourth time.
  - **This is not cosmetic in this phase specifically.** T031's rate limit is keyed on
    `request.client.host`, and a docstring telling an operator the wrong thing about which addresses
    are believed is a docstring that gets the rate limit bypassed. A directly exposed service must
    pass `--no-proxy-headers`, and nothing in the code currently says so.

- [X] **T042** [P] [US4] Update the contract and the operator-facing documents.
  - [contracts/openapi.yaml](./contracts/openapi.yaml): the `/jobs` `503` currently documents only
    `insufficient_storage`. Add the `at_capacity` example — FR-015's amendment introduced a second
    503 and the contract never caught up.
  - `.env.example`: the six T029 variables, each with its default and one line on what it governs.
  - [quickstart.md](./quickstart.md) § "Concurrency and the queue (US4, later phase)" — drop "later
    phase" and add the `curl` sequences T043 will run: rate limit with its `Retry-After`, the
    at-capacity refusal, and `/health` reporting a wedged worker.

**Checkpoint**: 🚦 **US4 is independently verifiable.** Exceed the rate limit and get a 429 with a
usable `Retry-After`; drop the disk threshold above free space and get a clean 503 with no job
created; `/health` answers.

---

## Phase 10: US4 verification

- [ ] **T043** 🚦 **STOP. Manual verification of US4 — the owner runs this.**
  *(Per instruction: a manual verification task per phase, and stop before it.)*
  - Ten simultaneous submissions against a concurrency limit of 2: **no more than two run at any
    instant and all ten reach a terminal state** (SC-006). Poll `/health` during, do not infer it.
  - The same post URL submitted five times concurrently produces **exactly one** download (SC-007).
  - Exceed `XVD_RATE_LIMIT`: 429, a `Retry-After` header whose value is a plausible integer, and
    submission works again once it has elapsed.
  - Set `XVD_MIN_FREE_BYTES` above actual free space: every submission refused with 503, **no file
    under `<state_dir>/jobs/`**, and no outbound network traffic.
  - Set `XVD_MAX_PENDING=2` with `XVD_MAX_CONCURRENT=1` and submit four distinct URLs: the fourth is
    refused `at_capacity` while the second and third still **wait** rather than being dropped —
    FR-015's original promise must survive its own amendment.
  - Set `XVD_JOB_TIMEOUT` to a few seconds and submit a real download: the job reaches
    `failed`/`time_limit`, and the temp directory under the output directory is **gone**.
  - Record results inline in this file, as feature 001 did.

---

## Phase 11: US5 service layer — `backend/jobs.py` (Priority: P3)

**Goal**: finished files stop accumulating, and a caller who comes back too late is told so.

**Independent Test**: complete a job, hand `sweep()` a `now` past the retention period, confirm the
file is gone, the job reports `expired`, and `file_for` refuses.

- [X] **T044** [US5] Add `XVD_RETENTION` and the `finished → expired` transition to `backend/jobs.py`.
  - `XVD_RETENTION` (default 86400) via `_positive_int`, read in `init()` beside the others.
  - `_enter_terminal` **refuses** this transition — `FINISHED` is in `_TERMINAL_STATES`
    (`backend/jobs.py:225-226`) — and that refusal is correct for every other caller. Add a separate
    `_expire(job) -> bool` that permits **exactly** `FINISHED → EXPIRED` and nothing else.
  - Invariant 1 in [data-model.md](./data-model.md):59-62 already names this as the one exception to
    "a terminal state is never left". Implement it as a second, narrower function rather than by
    loosening the first: a `_enter_terminal` that could be talked into leaving a terminal state stops
    being the guard T003 built.
  - `_expire` must **not** clear `job.files`. T046's retry needs the paths, and a caller sees only
    the count regardless.

- [X] **T045** [US5] Add the retention pass to `sweep()` in `backend/jobs.py` (FR-021, FR-023).
  - Between the watchdog and the bucket prune, per T036's ordering comment.
  - Every `FINISHED` job whose `completed_at + XVD_RETENTION` has passed, measured with the injected
    `now`: **mark `expired` and persist first, then attempt deletion** (FR-023, research D7).
  - **The ordering is the requirement, not an implementation detail.** A retrieval that starts after
    the mark is refused with a clean "expired" instead of racing a file that is disappearing under
    it. A retrieval already in flight keeps its open handle: POSIX `unlink` leaves an open file
    readable until the reader closes it, so the response completes intact — that is the deployment
    target and the reason this ordering is sufficient rather than merely tidy.
  - Retention is measured **from the job's completion, not the file's age on disk**. A job that
    finished instantly by reusing a file an earlier CLI run left behind gets a full retention period
    from *its* completion (spec.md:213-216).

- [X] **T046** [US5] Tolerate a delete that fails, in `backend/jobs.py`.
  - On Windows the `unlink` raises `PermissionError` while a reader holds the file open. **Log at
    debug and move on**; the next sweep tries again. This is the same reasoning
    `_remove_temp_dir` documents at `backend/downloader.py:270-292`, for the same underlying cause —
    cite it, so the consistency is visible rather than coincidental.
  - The retry needs no new state: each pass attempts `unlink` on any file of an `EXPIRED` job that
    still exists. The job is already `expired`, so `file_for` refuses regardless of whether the bytes
    are still there — **the caller-visible guarantee does not depend on the delete succeeding**,
    which is what makes tolerating the failure safe rather than merely convenient.
  - A file deleted out from under the service by an operator is already handled: `file_for` re-checks
    existence and reports `expired` (`backend/jobs.py:733-735`). Do not add a second path.

- [X] **T047** [US5] Extend `tests/test_jobs.py` for retention. Still no `time.sleep`.
  - A job finished longer ago than the retention period is `expired` by `sweep(now=...)`, its file is
    gone, and `file_for` returns the `expired` problem.
  - A job finished **within** the period is untouched: state, file, and record all unchanged
    (US5 acceptance scenario 2).
  - `expired` is distinguishable from `failed` — different state, and `failure_code` stays `None`,
    preserving data-model invariant 4.
  - A `PermissionError` from the delete leaves the job `expired` anyway, and the next sweep retries.
    Drive it with a `unlink` seam, not by opening a file on Windows and hoping.
  - `waiting`, `running`, and `failed` jobs are never expired by the sweep, whatever their age.
  - The record on disk says `expired` after the sweep — mark-before-delete has to survive a read.

**Checkpoint**: `uv run pytest` passes. Retention works from plain Python.

---

## Phase 12: US5 close-out and verification

- [X] **T048** [US5] Confirm — by assertion, not by assumption — that US5 needs no transport change.
  - `backend/api.py` already returns **410** `expired` for an expired job
    (`backend/api.py:337-338`), and `file_for` already reports the `EXPIRED` problem
    (`backend/jobs.py:702-703`). Both were written in T019 against a state that could not yet occur.
  - Add a test asserting `jobs.EXPIRED` is handled by `_serve`'s branch table, so the branch cannot
    be deleted as dead code by someone who does not know retention reaches it.
  - Then update `.env.example` with `XVD_RETENTION`, and [quickstart.md](./quickstart.md) with the
    US5 sequence. **If `api.py` turns out to need a change after all, stop and report it** — it would
    mean T019's anticipation was wrong, which is worth knowing rather than patching over.

- [ ] **T049** 🚦 **STOP. Manual verification of US5 — the owner runs this.**
  - Complete a real job, set `XVD_RETENTION=60` and `XVD_SWEEP_INTERVAL=10`, wait, and confirm: the
    file is gone from the output directory, `GET /jobs/{handle}` reports **`expired`** and not
    `failed`, and `GET /jobs/{handle}/file` returns **410** with the expired message.
  - A job finished seconds ago is **untouched** by the same sweep.
  - **The mark-before-delete ordering, observed rather than reasoned about**: start a retrieval of a
    large file and let the sweep fire mid-transfer. The download must complete intact on Linux. On
    Windows the delete will fail and the operator log must show the tolerated failure and a
    successful retry on the following pass.
  - Confirm no finished file survives more than retention plus one sweep interval (SC-009).
  - Record results inline in this file.

- [ ] **T050** Close both phases out.
  - **Verify**: `git diff --stat HEAD -- backend/downloader.py backend/validation.py backend/config.py`
    prints nothing. Same standing instruction, same backstop as T024 — if a task above needed one of
    these, **stop and report** rather than editing.
  - `uv run pytest` green, including the amended AST checks from T039.
  - Confirm no new dependency: `git diff pyproject.toml` shows nothing. The sweep is
    `asyncio.sleep` in a loop, not a scheduler library (Principle IV).
  - Confirm one test file: `tests/` still contains exactly `test_validation.py`,
    `test_downloader.py`, and `test_jobs.py`.
  - Update this document's scope table and the *Explicitly Not In This Document* table so US4 and US5
    read as done and only US2 and US6 remain.

---

## Dependencies & Execution Order (T029–T050)

```text
T029 (config)
  └─> T030 (SubmitResult — signature change, suite green before any guard)
        └─> T031 ──> T032 ──> T033 ──> T034 ──> T035 ──> T036 ──> T037
                                                          [Phase 8: US4 service, all in jobs.py]
              └─> T038 ──> T039 ──> T040 ──> T041 [P] ──> T042 [P]
                                                          [Phase 9: US4 transport]
                    └─> T043  🚦 STOP — owner runs this
                          └─> T044 ──> T045 ──> T046 ──> T047
                                                          [Phase 11: US5 service, all in jobs.py]
                                └─> T048 ──> T049  🚦 STOP — owner runs this ──> T050
                                                          [Phase 12: close-out]
```

**Hard sequencing rules**:

- **T030 blocks every guard.** They all return a `SubmitResult`, and changing the shape while adding
  behaviour would make a red test ambiguous between the two.
- **T029 → T037 are strictly sequential**: they all edit `backend/jobs.py`.
- **T039 must not be split from its test amendment.** Adding the loop without amending the assertion
  leaves the suite red; amending the assertion without adding the loop leaves an exemption for a
  function that does not exist. One task, both halves.
- **T036 blocks T045.** Retention is a pass inside the sweep the watchdog task creates.
- **T043 gates Phase 11.** US4 is what keeps the disk from filling while US5 is being built; verifying
  it after would invert the safety argument that put them in this order.
- **T044 → T047 are strictly sequential**: `backend/jobs.py` again.

### Parallel Opportunities

Two: **T041** and **T042**. T041 edits a docstring in `api.py` that no other task touches, and T042
edits three documents and no code. Nothing in Phase 8 or Phase 11 is parallel — fourteen of these
twenty-two tasks edit `backend/jobs.py`, and marking two of them `[P]` would be a lie about a merge
conflict waiting to happen.

---

## Implementation Strategy

**T029–T043 first, as one unit.** US4 is a safety property, and a service that has retention but no
disk floor still fails the first time a caller submits a 4 GB post at 90% full. The disk guard is the
thing that makes US5's periodic sweep sufficient rather than merely hopeful.

**Then T044–T050.** Retention is where the disk actually gets emptied, and it is short — five tasks,
one of which is confirming that a branch written in T019 still fits.

### Suggested commit points

After T030 (the contract change, alone and green), T037, T042, T047, and T050. Each is a state where
the tree is coherent and the suite passes.

### What is still not built after T050

US2 (per-code message refinement) and US6 (restart recovery, temp sweep). US6 is worth naming
specifically: **T044's persistence writes an `expired` state that nothing yet reads back**, exactly as
T004 wrote records nothing read. That is the same deliberate anticipation, and US6 is where it is
finally collected.

---
---

# Plan Phase 5 (US6) — restart recovery

**Generated 2026-08-15.** Tasks **T051–T060**. The last functional gap in the feature.

Everything T004 deferred is collected here. Three prior tasks wrote state that nothing has ever read
back — T004's job records, T035's terminal transitions, T044's `expired` marks — and this phase is
where the write side finally acquires its reader.

---

## Decisions recorded before the breakdown

### 1. Authority: disk wins for the length of `recover()`, memory wins forever after

Research D3 already settles the steady state — *"the in-memory dict is authoritative for the life of
the process. Disk is a crash-recovery record, read exactly once during start-up and never read
again."* What it does not say is what happens **during** recovery, which is the only moment the two
can disagree. The rule for this phase:

```text
   before recover()   registry is empty; disk is the only truth
   during recover()   disk is read; memory is built from it; any record recovery
                      CHANGES is written back before the function returns
   after recover()    memory is authoritative; disk is a write-only mirror
```

**The load-bearing clause is the middle one.** A job recovered as `failed`/`interrupted` must be
persisted *inside* `recover()`, not left for some later transition — otherwise a second crash before
the next write would resurrect it as `running` again, and it could ping-pong indefinitely.

**T056 asserts this as a property, not a hope**: after `recover()` returns, every record on disk
deserialises to a job equal to the one in the registry. That is the requirement "a record on disk and
the in-memory registry must not disagree" turned into something that can fail.

### 2. A record we cannot trust is skipped, logged, and counted — never guessed at

Four ways a file under `<state_dir>/jobs/` can be unusable, and all four get the same treatment:

| Case | Why not "repair" it |
|---|---|
| Truncated or invalid JSON | A half-written record has no correct interpretation. `os.replace` makes this nearly impossible, but "nearly" is not a reason to crash on it. |
| Missing a required field | Same. |
| `handle` field ≠ the filename stem, or not handle-shaped | Nothing we wrote can produce this, so the state directory has been edited or corrupted. A record that disagrees with its own name is not evidence of anything. |
| A `state` string we do not recognise | Most likely a **downgrade** — a record written by a later version. Interrupting it would mislabel it; skipping loses one job and keeps the rest. |

**Start-up must not fail because one file is bad.** One unreadable record costs one job; a crash-loop
costs the service. Logged at WARNING with the filename, and a single summary line with the count, so
"three records were skipped" is greppable rather than buried.

### 3. FR-026's temp sweep needs an age threshold — this corrects research D7

D7 justifies the temp-directory sweep like this:

> *"Safe at start-up specifically because the service is single-process (Assumption 5) and nothing is
> downloading yet. Note the CLI could in principle be running concurrently; the sweep is therefore
> start-up-only and never periodic, so it cannot delete a live CLI download's temp directory."*

**The last clause does not follow.** Being start-up-only limits *how often* the sweep runs; it does
nothing to prevent a CLI download being in flight at that instant. An operator who restarts the
service while a `xvd` CLI download is running would have its `.tmp-xvd-*` directory deleted underneath
it — corrupting a download the service does not own and never knew about.

**Resolution (T054): only remove a `.tmp-xvd-*` directory whose mtime is older than
`XVD_JOB_TIMEOUT`.** No download of ours can legitimately outlive that — the watchdog fails it at
exactly that age — so anything older is certainly abandoned. A CLI download younger than the job
timeout is left alone and swept on some later restart, which costs one interval of disk and removes
the whole failure mode.

This is a correction to a planning document, so it goes into research.md as a dated note rather than
a silent edit, exactly as the D9 proxy-header correction did.

### 4. Interrupted jobs are not requeued, and the reason is in the spec

FR-025 as resolved by Q3: *"Interrupted jobs MUST NOT be requeued automatically."* A restart is
frequently a deploy or an OOM kill, and a service that re-ran every in-flight download on boot would
turn one bad deploy into a thundering herd against X. The caller is told plainly and resubmits if
they still want it — `FAILURE_MESSAGES[INTERRUPTED]` already says "Submit it again to retry."

### 5. Recovery is called once, from the lifespan, before anything else can observe the registry

`init()` → `recover()` → start the sweep task → `yield`. Ordering matters twice over: the sweep must
not run against a half-built registry, and uvicorn must not accept a connection until recovery is
done. The second is free — uvicorn completes lifespan startup before binding to traffic — but it is
asserted rather than assumed, because "free because of what a dependency happens to do" is exactly
the kind of guarantee that quietly stops being true.

---

## Phase 13: US6 service layer — `backend/jobs.py` (Priority: P3)

**Goal**: a process that starts up knowing what the previous one was doing.

**Independent Test**: `tests/test_jobs.py` writes records to a state directory, calls `recover()`, and
inspects the registry — no restart, no subprocess, no HTTP.

All six tasks edit `backend/jobs.py` and run in sequence.

- [ ] **T051** [US6] Add `_from_record(data, expected_handle) -> Job | None` to `backend/jobs.py`.
  - The exact inverse of `_as_record` (`backend/jobs.py:358`). `files` comes back as a list of
    strings and MUST become `tuple(Path(...))` — the same type `DownloadOutcome.paths` produces, or
    `file_for` will be comparing the wrong thing.
  - Returns `None` for every untrustworthy case in decision 2, rather than raising: a per-file
    failure is expected input at this boundary, not an exception (Principle VI).
  - Validate `data["handle"] == expected_handle` **and** `is_valid_handle(...)`. Nothing this service
    writes can violate either, so a violation means the state directory was edited.
  - Reject a `state` not in the five constants.
  - `timed_out` is deliberately absent from the record (T035) — default it to `False`. A restart ends
    the process that could have been timing out.

- [ ] **T052** [US6] Implement `recover()` in `backend/jobs.py` — the read side of FR-024.
  - Iterate `<state_dir>/jobs/*.json`, skipping the `.tmp-job-*` files `persist` may have left behind
    (`backend/jobs.py:401`). Those are half-written by definition and must never be parsed.
  - Build the registry from what parses. Log one WARNING per skipped file and one summary line with
    the count (decision 2).
  - **Call once, from the lifespan, and never from `init()`.** `init()` is called repeatedly by
    tests and by nothing else in production; making it read the disk would give every test a
    surprise registry.
  - A missing or empty jobs directory is a normal first start, not an error.
  - `recover()` returns a count of what it loaded, so T057 can log one line an operator can read on
    boot rather than leaving start-up silent about it.

- [ ] **T053** [US6] Resolve non-terminal recovered jobs in `backend/jobs.py` (FR-025).
  - Any record read as `waiting` or `running` → `failed` with `failure_code=INTERRUPTED`, and
    **persisted before `recover()` returns** (decision 1). The code and its caller-safe sentence
    already exist (`backend/jobs.py:FAILURE_MESSAGES`); this is the first thing that assigns it.
  - Use `_enter_terminal`, which sets `completed_at` and refuses a job already terminal — no second
    transition path.
  - **Do not requeue** (decision 4, FR-025/Q3). No `_executor.submit` anywhere in this phase.
  - `downloaded_bytes` and `total_bytes` from the record are stale by definition; leave them as read.
    They are never shown for a terminal job (`backend/api.py:_as_response` only reports progress
    while `running`).
  - An `expired` record keeps its `files` list, so a delete that failed on Windows before the restart
    is retried by the first sweep of the new process. That continuity is free and worth a comment —
    it is the one place where recovery and T046's retry meet.

- [ ] **T054** [US6] Sweep abandoned `.tmp-xvd-*` directories in `backend/jobs.py` (FR-026).
  - Match `.tmp-xvd-*` directories directly under `_output_dir` — the prefix
    `backend/downloader.py:493` uses. Directories only; never touch a file at that level, which would
    be somebody's video.
  - **Only those with an mtime older than `XVD_JOB_TIMEOUT`** (decision 3). Write the reasoning at
    the code: no download of ours can outlive the watchdog, so an older directory is certainly
    abandoned, and a younger one may belong to a CLI run this service does not own.
  - `shutil.rmtree` with the failure **tolerated and logged**, exactly as T046 and
    `backend/downloader.py:270-292` do it. A leftover directory that cannot be removed is a disk
    issue, not a reason to refuse to start.
  - **Start-up only. Never added to `sweep()`.** State why in the code, or a later reader will
    reasonably wonder why this one piece of cleanup sits apart from all the others.
  - **research.md D7 already carries the dated correction** — it was made while planning this phase
    rather than left for implementation, since the flaw was known the moment it was found. Read it
    before writing the threshold; do not re-derive the number.

- [ ] **T055** [US6] Confirm recovered `finished` jobs are still retrievable, in `backend/jobs.py`.
  - Mostly an assertion that nothing extra is needed: `file_for` re-checks existence
    (`backend/jobs.py:733-735`) and reports `expired` when a file is gone, which is the correct answer
    for a file an operator deleted between runs.
  - What IS needed is the `tuple[Path, ...]` round-trip from T051. A `files` list left as strings
    would make `chosen.is_file()` fail with `AttributeError` on the first retrieval after a restart —
    a bug that no test before this phase could have caught, because nothing ever read a record back.
  - If anything beyond the type conversion turns out to be required, **stop and report**: it would
    mean the record shape is lossy, which is a data-model problem rather than a coding one.

- [ ] **T056** [US6] Extend `tests/test_jobs.py` for recovery.
  - **The reconciliation property (decision 1)**: after `recover()`, every file in the jobs directory
    deserialises to a job equal to the registry's. This is the "must not disagree" requirement made
    falsifiable.
  - `waiting` and `running` records both become `failed`/`interrupted`, with `completed_at` set.
  - **The record on disk says `failed` too** — not just memory. Assert by re-reading the file, which
    is what catches the ping-pong failure decision 1 describes.
  - A `finished` record round-trips: state preserved, `files` are `Path` objects, and `file_for`
    returns the real path.
  - An `expired` record stays `expired` and keeps its files for the retry.
  - Each of the four untrustworthy cases in decision 2 is skipped without raising, and the survivors
    still load. Include a `.tmp-job-*` leftover and assert it is ignored.
  - Recovery is a no-op on an empty or absent jobs directory.
  - **Nothing is requeued**: recovery with a `waiting` record submits no work. Assert against the
    executor, not by observing that nothing happened to download.
  - Temp sweep: a `.tmp-xvd-*` directory older than the timeout is removed; one younger is **kept**;
    a plain file with a similar name is untouched; and an rmtree failure does not propagate.

**Checkpoint**: `uv run pytest` passes. A process can be handed a state directory and reconstruct
what the last one was doing. `backend/api.py` is still unchanged by this phase.

---

## Phase 14: US6 transport — `backend/api.py` (Priority: P3)

- [ ] **T057** [US6] Call `recover()` from the lifespan in `backend/api.py`, before anything else.
  - Order: `jobs.init()` → `jobs.recover()` → `asyncio.create_task(_sweep_loop())` → `yield`
    (decision 5). The sweep must not run against a half-built registry.
  - Log one line with the recovered count, so a boot is not silent about having adopted state.
  - **`recover()` is synchronous and does filesystem work.** It runs directly in the lifespan rather
    than via `to_thread` — deliberately, because blocking start-up is exactly what we want here and
    the event loop has nothing else to serve yet. Say so at the code; it looks inconsistent with
    `_sweep_loop` otherwise.
  - This is the whole transport change. If more is needed, **stop and report**.

---

## Phase 15: Verification and close

- [ ] **T058** [US6] Assert recovery completes before the service can be observed, in
  `tests/test_jobs.py`.
  - Structural, since this file may not construct an HTTP client: parse `api.py`, find `lifespan`,
    and assert `jobs.recover()` is called **before** `create_task` and before the `yield`.
  - This is decision 5's second half. The guarantee currently rests on uvicorn completing lifespan
    startup before accepting connections, which is true and is *not ours* — so what we can own is the
    ordering inside our own function, and that is what gets asserted.
  - Confirm the existing boundary tests still hold: `jobs.py` imports no framework, `api.py` has no
    loop outside `_sweep_loop`, and `_sweep_loop` still touches no job data.

- [ ] **T059** 🚦 **STOP. Manual verification of US6 — the owner runs this.**
  - Start a real download, then **`kill -9`** the service mid-transfer — no graceful shutdown, or the
    thing being tested does not happen.
  - Restart. Then confirm:
    - `GET /jobs/<handle>` reports **`failed`** with `failure.code == "interrupted"`, **never**
      `running` (FR-025);
    - no `.tmp-xvd-*` directory survives in the output directory (FR-026) — check after the restart,
      and remember T054's age threshold means a *fresh* one is kept by design, so wait past
      `XVD_JOB_TIMEOUT` or set it low for the test;
    - a job that had **finished** before the kill still reports `finished` and its file still
      downloads intact (US6 acceptance scenario 3);
    - the interrupted job was **not** restarted — nothing is downloading after the boot.
  - Kill it a second time immediately after the first recovery and restart again: the job must still
    read `failed`/`interrupted`, not flip back to `running`. That is decision 1's ping-pong, and it is
    the one failure a single restart cannot reveal.
  - Record results inline in this file.

- [ ] **T060** Close the feature out.
  - **Verify**: `git diff --stat 3a3918e HEAD -- backend/downloader.py backend/validation.py backend/config.py`
    prints nothing. Standing instruction, same backstop as T024 and T050.
  - `uv run pytest` green; `git diff pyproject.toml uv.lock` empty; `tests/` still holds exactly three
    files.
  - Update this document's scope table: every phase generated is then code-complete, with only the
    three 🚦 manual verifications (T027, T043, T049, T059) outstanding.
  - Note in `plan.md` that Phase 5 is delivered and US2 is the only story never built.

---

## Dependencies & Execution Order (T051–T060)

```text
T051 (parse one record)
  └─> T052 (read the directory) ──> T053 (resolve non-terminal) ──> T054 (temp sweep)
        └─> T055 (finished still retrievable) ──> T056 (tests)
              └─> T057 (lifespan wiring)          [Phase 14]
                    └─> T058 ──> T059  🚦 STOP ──> T060   [Phase 15]
```

**Hard sequencing rules**:

- **T051 blocks everything.** Nothing can be read back until one record can be.
- **T051 → T056 are strictly sequential**: all in `backend/jobs.py`.
- **T053 must land with T052, not after it.** A `recover()` that loads `running` jobs and leaves them
  running is worse than no recovery at all — it produces exactly the permanently-misleading job
  FR-025 exists to prevent, and it would look like it worked.
- **T054 implements a correction research.md already records.** The document was fixed at planning
  time; the code must match the threshold it now states rather than a freshly invented one.
- **T058 must not be folded into T057.** The ordering guarantee is the point of the phase's second
  half; asserting it in the same task that writes it invites asserting what was written rather than
  what was required.

### Parallel Opportunities

None. Six of the ten tasks edit `backend/jobs.py`, and the remaining four are strictly ordered behind
them. No `[P]` markers in this phase, and adding any would be decorative.

---

## Implementation Strategy

One sitting. The phase is small and every task depends on its predecessor; there is no useful
intermediate state where half of recovery is shipped.

### Suggested commit points

After T056 (the service layer, complete and tested) and after T058. T059 is the owner's, and T060 is
the close-out commit.

### What remains after T060

**US2 only** — per-code message refinement (FR-010, FR-011). Worth stating plainly: the *mechanism*
shipped in Phase 1 and the drift test in T014 pins it, so what US2 would add is better sentences, not
new capability. The feature is functionally complete at T060.
