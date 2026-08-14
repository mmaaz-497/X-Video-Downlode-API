---

description: "Phase 1 task list for 002-http-download-api (US1 + US3)"
---

# Tasks: HTTP Download API — Phase 1 (US1 + US3)

**Input**: Design documents from `/specs/002-http-download-api/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/openapi.yaml](./contracts/openapi.yaml),
[quickstart.md](./quickstart.md)

**Scope**: **US1** (fetch a video without a terminal) and **US3** (callers cannot reach each other)
only. US2, US4, US5, and US6 are deliberately absent — see *Explicitly Not In This Phase* below.

**Tests**: Per Constitution Principle II, exactly one new test file, `tests/test_jobs.py`. No HTTP
integration tests, no network calls, no mocking framework. Manual verification via `curl` closes the
phase (T027).

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

- [ ] **T020** [US3] Make every unresolvable handle produce one identical refusal in
  `backend/api.py`.
  - Unknown, malformed, and wrong-length handles all return
    `404 {"code":"not_found","message":"No such job."}` — same status, same body (FR-028).
  - Apply `pattern="^[A-Za-z0-9_-]{43}$"` as the cheap second layer, but a pattern failure must
    produce **that same 404**, not a 422 — otherwise the shape of the handle leaks whether it could
    have been real.
  - No secret-dependent branch: both paths return the same response object. Constant-time comparison
    is **not** used and **not** claimed; the 256-bit space is the defence (research D8).

- [ ] **T021** [US3] Replace the leaking default handlers in `backend/api.py`.
  - Override the `RequestValidationError` handler: pydantic returns the offending input under
    `"input"` by default, which FR-005 forbids. Return a fixed body.
  - Add a catch-all `Exception` handler that logs the traceback and returns one fixed 500 body.
  - Neither handler may include a path, a directory name, an exception type, or a library name.

- [ ] **T022** [US3] Pass the caller's address into the service layer in `backend/api.py`.
  - `request.client.host` → `jobs.submit(url, client_address=...)`, feeding T015's audit record.
  - **The application MUST NOT read `X-Forwarded-For` itself.** Trusting it from an untrusted source
    would let any caller spoof their address and defeat the rate limit that Phase 3 will add.

- [ ] **T023** [US3] Document the proxy-header requirement as an operational contract.
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

- [ ] **T024** Confirm the frozen modules are untouched.
  - **Verify**: `git diff --stat HEAD -- backend/downloader.py backend/validation.py backend/config.py`
    prints nothing.
  - A non-empty result means a task above needed something the boundary does not offer. **Stop and
    report it** rather than editing the file — that is the standing instruction for this feature.

- [ ] **T025** Verify the Principle III boundary with an **AST-based** check, in
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

- [ ] **T026** Verify SC-005: no response leaks anything.
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

- [ ] **T028** [P] Update `.env.example` with the Phase-1 variables only — `XVD_OUTPUT_DIR`,
  `XVD_STATE_DIR`, `XVD_MAX_CONCURRENT` — each with its default and one line on what it governs.
  Later phases add their own; listing them now would advertise configuration that does nothing.

---

## Explicitly Not In This Phase

Named so that their absence reads as a decision rather than an oversight:

| Deferred | Requirements | Belongs to |
|---|---|---|
| Full per-code message refinement | FR-010, FR-011 | US2 / plan Phase 2 |
| Disk guard, rate limit, `XVD_MAX_PENDING`, job time limit + watchdog | FR-018, FR-019, FR-020, FR-015 cap | US4 / plan Phase 3 |
| Retention sweep, `expired` transition | FR-021, FR-022, FR-023 | US5 / plan Phase 4 |
| Restart recovery, temp-directory sweep | FR-024 (read side), FR-025, FR-026 | US6 / plan Phase 5 |
| Wedged-worker mitigation | — | **Never.** Accepted limitation, ADR-0002 |
| Docker, nginx, systemd, TLS | — | Out of scope by the spec |

The `expired` branch in T019 and the write side of persistence in T004 are the two places where later
phases are anticipated, each for a stated reason: avoiding a retrofit that would touch every
transition.

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
