---
id: 0012
title: HTTP Download API Spec
stage: spec
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.specify
labels: ["http-api", "async-jobs", "multi-user", "resource-limits", "frozen-modules"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/002-http-download-api/spec.md
 - specs/002-http-download-api/checklists/requirements.md
 - history/prompts/002-http-download-api/0012-http-download-api-spec.spec.prompt.md
tests:
 - none (specification stage; no code written)
---

## Prompt

Build an HTTP API that exposes the existing video download capability over the network, so a video can be fetched by submitting a URL to a request rather than typing a terminal command. This service will be reachable from the internet and used by people other than the operator.

WHY: The CLI requires SSH access to the VPS. The capability needs to be usable by other people from a browser or phone.

CRITICAL CONSTRAINT: backend/downloader.py, backend/validation.py, and backend/config.py must not be modified. The HTTP layer calls download_post() and formats its result. If any requirement here would need those modules changed, report it rather than changing them.

THE CENTRAL PROBLEM: downloads take minutes — a 95MB video took over a minute on a good connection. A synchronous request holding that long times out at the browser and at any reverse proxy, and blocks a worker for the whole transfer. The API must accept a request, return immediately with a way to check on it, and do the work in the background. This is a requirement, not an implementation detail.

FUNCTIONAL REQUIREMENTS:

1. Accept a request containing a single X post URL and return immediately with a job identifier. The response must not wait for the download.
2. Provide a way to check a job's state: waiting, running, finished, or failed — with progress while running and the specific reason when failed.
3. Provide a way to retrieve the finished video file.
4. Reject invalid URLs immediately in the response, before any job is created, reusing the existing validation. An invalid URL must never produce a job.
5. Surface the existing failure diagnoses as distinct, machine-readable outcomes rather than one generic error.
6. A request for a post already being downloaded must not start a second download.
7. Limit how many downloads run at once, so concurrent requests cannot exhaust the VPS's bandwidth, CPU, or disk.
8. Delete finished video files automatically after a defined retention period. A VPS disk is finite and this service writes large files.
9. Refuse new jobs once free disk falls below a safe threshold, with a clear message, rather than failing mid-download.
10. Limit how many jobs a single caller can create in a given period.
11. Survive a restart without leaving jobs permanently stuck in a running state.

MULTI-USER REQUIREMENTS (this service is not single-operator):
12. A job identifier must not be guessable. One caller must not be able to discover or retrieve another caller's file by trying identifiers.
13. Error responses must not leak filesystem paths, internal directory structure, or stack traces to callers. The operator's logs may contain detail; the response must not.
14. A caller must not be able to influence where a file is written or which file is served. The output directory is server configuration only and must never be settable from a request.
15. The service must record enough about each request for the operator to identify abuse — at minimum a timestamp, the submitted URL, and the calling address — without storing anything a caller supplies as free text.

ACCEPTANCE SCENARIOS:
- Given a valid post URL, when I submit it, then I receive a job identifier in under a second and the download proceeds in the background.
- Given a running job, when I check its state, then I see progress advancing.
- Given a finished job, when I request the file, then I receive a playable video with both picture and sound.
- Given a URL that is not an X post URL, when I submit it, then I am rejected immediately, no job is created, and no network request is made.
- Given a post with no video, when I check the job, then I see that specific reason rather than a generic failure.
- Given a job identifier I did not create, when I request its file, then I am refused, and the refusal does not reveal whether that job exists.
- Given a job that finished longer ago than the retention period, when I request its file, then I am told it expired rather than receiving a partial or missing file.
- Given the maximum concurrent downloads already running, when I submit another, then it waits rather than being dropped or running anyway.
- Given a caller exceeding the rate limit, when they submit again, then they are refused with a message stating when they may retry.

OUT OF SCOPE:
- Any web page, HTML interface, or frontend. This feature is the API only.
- User accounts, login, or per-user history.
- Streaming or transcoding. Files are served exactly as downloaded.
- Downloading from any site other than X.
- Batch submission, timelines, or bulk operations.
- HTTPS termination, domain setup, and reverse proxy configuration — deployment concerns, not application requirements.
- Any measure to evade X's rate limiting, including proxying or IP rotation.

## Response snapshot

Read the three frozen modules before writing anything, then created branch `002-http-download-api`
and wrote the specification.

**Structure**: 6 prioritized user stories (P1 fetch-without-terminal; P2 failure reasons, caller
isolation, resource protection; P3 retention, restart resilience), 10 edge cases, 34 functional
requirements in seven groups, 4 key entities, 11 measurable success criteria, 7 documented
assumptions, and an explicit out-of-scope list.

**Four constraint conflicts reported rather than resolved by changing frozen code** (spec.md
§ Dependencies and Constraints):

1. **No machine-readable failure codes exist.** `DownloadOutcome.status` is only
   `downloaded|skipped|failed`; the specific diagnosis lives as English prose in `message`, produced
   by the private `_ERROR_DIAGNOSES` table (backend/downloader.py:71-89). Requirement 5 is therefore
   satisfiable only by classifying that prose from outside — duplicated knowledge of a private table.
   Recorded with an explicit unclassified fallback (FR-011) and a requirement that the mapping be
   pinned by tests. The clean fix needs a `code` field on `DownloadOutcome`, which is refused.
2. **The module's own messages leak filesystem paths.** `_promote`'s RuntimeError (:332),
   `_remove_temp_dir`'s warning (:309), and `build_target`'s ValueError (validation.py:186) all embed
   absolute paths, and the generic handler at :529 folds them into `DownloadOutcome.message`.
   Requirement 13 therefore forbids ever forwarding `message` to a caller; caller-visible text comes
   from a separate safe catalog, with the raw text going to the operator's log.
3. **`download_post()` has no cancellation or timeout.** A stalled download would hold a concurrency
   slot forever, defeating requirement 7. Raising from the progress callback aborts a transfer
   without modifying the module, so FR-020 (job time limit) is achievable — but the callback does not
   fire during metadata resolution, so a pre-transfer hang is not interruptible that way. Residual
   gap recorded, not solved.
4. **Constitution Principle III (thin HTTP layer) has nowhere to put job orchestration.** Job state,
   scheduling, dedup, retention, disk guarding, rate limiting, and restart recovery are not download
   logic and cannot go in the frozen modules. Resolution: a new module under `backend/`, below the
   HTTP layer — the handlers stay thin. Flagged for the Constitution Check gate at `/sp.plan`.

Also noted: FR-024 (durability across restart) was phrased as a requirement, not a technology, to
avoid pre-empting Principle IV's no-database rule; and format/quality selection was ruled out of
scope because exposing it would put a caller-supplied parameter into the downloader, contradicting
requirement 14.

**Three clarifications left open**, each with three options and a recommendation: Q1 what entitles a
caller to a job (capability handle / address binding / separate secret — recommend capability);
Q2 what happens with multi-video posts, which `download_post()` supports but the requirements never
address (list with index / first only / refuse — recommend list); Q3 what state restart-interrupted
jobs land in (fail / requeue — recommend fail).

## Outcome

- ✅ Impact: Feature 002 specified end-to-end with the frozen-module boundary analysed against every requirement; four conflicts reported instead of silently worked around.
- 🧪 Tests: None — specification stage, no code written.
- 📁 Files: `specs/002-http-download-api/spec.md`, `specs/002-http-download-api/checklists/requirements.md`
- 🔁 Next prompts: Answer Q1/Q2/Q3 (or run `/sp.clarify`), then `/sp.plan`.
- 🧠 Reflection: Reading `downloader.py` in full before drafting was what surfaced conflicts 1–3; they are invisible from the function signature alone. Conflict 2 in particular would have shipped as a real path-disclosure bug had the spec assumed `message` was caller-safe.

## Evaluation notes (flywheel)

- Failure modes observed: An early draft required progress to advance on every running job, which the already-downloaded fast path makes false; corrected to advisory progress (FR-008) with the edge case recorded.
- Graders run and results (PASS/FAIL): Requirements-quality checklist — PASS on 15 of 16 items; the remaining item is the 3 intentional open clarifications.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): At `/sp.plan`, verify that classifying `DownloadOutcome.message` prose can be pinned by a test that fails loudly on upstream wording changes, rather than degrading silently.
