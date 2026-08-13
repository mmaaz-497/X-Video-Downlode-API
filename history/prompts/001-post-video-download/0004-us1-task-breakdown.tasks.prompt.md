---
id: 0004
title: US1 Task Breakdown
stage: tasks
date: 2026-08-12
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.tasks
labels: ["tasks", "user-story-1", "scope-control", "yt-dlp", "extension-bug"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-post-video-download/tasks.md
  - specs/001-post-video-download/research.md
  - specs/001-post-video-download/data-model.md
  - specs/001-post-video-download/contracts/cli-interface.md
  - specs/001-post-video-download/quickstart.md
  - specs/001-post-video-download/plan.md
tests:
  - none written (task-planning only)
  - second offline yt-dlp probe run to verify the extension correction (scratchpad, not committed)
---

## Prompt

SCOPE: Generate tasks for Phase A, B, and C only — User Story 1. Do NOT generate tasks for US2, US3, or US4. Those are follow-on phases in the plan and stay unbuilt.

APPLY THESE PLAN CORRECTIONS FIRST:

1. Fold both recommended spec amendments into spec.md as part of the task list:
   - Widen the accepted hostname list in Assumptions from five to eight, adding m.twitter.com, m.x.com, and mobile.x.com.
   - Record under FR-004 that "images but no video" is not distinguishable from "no media at all" for bare post URLs with the current extractor.

2. Correct the extension assumption from research D3. Do NOT hardcode .mp4 in the target path. merge_output_format only applies when a merge actually occurs; a progressive single-stream rendition selected by the /best fallback keeps its native extension, and the pre-computed path would then not match what yt-dlp writes. Instead:
   - Derive the actual output extension by calling ydl.prepare_filename(info) on the metadata returned by extract_info(download=False), and take the extension from that.
   - For the FR-016 existence check, this gives the real target path to test with Path.exists().
   - For the promotion step, promote the file that actually landed in the temp directory rather than a path assembled from assumptions. The temp directory is created empty and holds exactly one finished file per entry, so this is unambiguous.

TESTING DISCIPLINE (Constitution Principle II — enforce strictly):
- Exactly two test files: tests/test_validation.py and tests/test_downloader.py. No third test file.
- Do NOT generate a test task per functional requirement.
- Do NOT generate any test that makes a network request or requires ffmpeg.
- test_downloader.py uses literal info dicts as inputs. No mocking framework, no monkeypatching of yt-dlp internals.
- Test tasks belong in Phase C, after the Phase B code they cover. They are not a gate on Phase B.

TASK GRANULARITY:
- One task per file where possible. Prefer fewer, larger tasks over many small ones — this is a four-module project, not a service mesh.
- Every task must name the exact file path it touches.
- Mark parallelizable tasks with [P].
- The ffmpeg preflight (D9) must be a step inside the downloader task, not its own task.

MANUAL VERIFICATION:
Include the quickstart.md run as an explicit task at the end of Phase B, before Phase C. Per Principle II this is the primary validation gate, so it must appear as a real task rather than an afterthought. It must include at minimum: a real public X post with video, a non-X URL, a Ctrl+C interruption, and a re-run of an already-downloaded URL.

DO NOT GENERATE TASKS FOR: Docker, CI pipelines, systemd units, logging configuration, README authoring, deployment automation, or any HTTP layer.

## Response snapshot

Generated `tasks.md` — **11 tasks across Phases A, B, C**, US1 only.

### Correction 2 verified before acting on it — it was a real bug

Ran a second offline probe pushing synthetic format lists through yt-dlp's own selection:

| Formats offered | Merge? | `ext` | `prepare_filename` |
|---|---|---|---|
| separate video + audio | yes | `mp4` | `jack-20.mp4` |
| progressive webm (`/best`) | **no** | **`webm`** | **`jack-20.webm`** |
| progressive mp4 (`/best`) | no | `mp4` | `jack-20.mp4` |

The correction is right and the consequence is worse than a wrong filename: hardcoding `.mp4` makes
the FR-016 existence check test a path that never exists for a progressive-webm post, so the tool
**silently re-downloads on every run** — idempotency defeated, and quietly, since each run still
reports success. Propagated the fix through five artifacts: `research.md` (D3 corrected with the
probe table, D4 amended to stop claiming the extension is predictable), `data-model.md`
(`OutputTarget.path` rule + flow diagram), `contracts/cli-interface.md` (`build_target` gains an
`ext` parameter), `quickstart.md` (a `.webm` result is correct, not a bug), `plan.md` (summary and a
third design correction).

### Correction 1 was already applied

Both spec amendments went into `spec.md` during `/sp.plan` last turn — hostnames widened to eight at
Assumptions, FR-004 limitation recorded inline. Verified present rather than re-applied. T007 exists
as a cheap guard against the five-host list reappearing.

### Task shape

- **Phase A (T001-T003)** — `pyproject.toml`, `config.py`, `validation.py`. Validation lands in
  Phase A, before anything can call it.
- **Phase B (T004-T008)** — `downloader.py` as one task with twelve ordered steps (ffmpeg preflight
  is step 7, not its own task, as directed); `cli.py`; a structural boundary check; the quickstart
  run.
- **Phase C (T009-T011)** — exactly two test files plus the run.

**T006 is an addition not in the brief**: a grep-level check that `downloader.py`/`validation.py`
never import `argparse`/`sys` or call `print`/`sys.exit`, and that `cli.py` never imports `yt_dlp`.
Principle III is the constraint most likely to erode silently during implementation, and this makes
the erosion visible for one line of shell before manual verification runs.

**T008 is the marked gate** (🚦) carrying all four required cases as a table with the requirement
each maps to, including the `ffprobe` audio-stream check — FR-009 says a silent video is a failed
download, so "it played" is not sufficient evidence.

Three security-critical instructions are stated at the point of use inside T004 rather than left as
references: `allowed_extractors: ["twitter"]` on every construction, `os.replace` and never
`shutil.move`, and promote-the-file-that-landed rather than a predicted path.

## Outcome

- ✅ Impact: US1 broken into 11 executable tasks with an explicit manual gate; a silent idempotency
  bug corrected across five artifacts before any code was written.
- 🧪 Tests: None written — task planning only. A second offline probe was run to verify the
  extension correction; not committed.
- 📁 Files: `tasks.md` (new); `research.md`, `data-model.md`, `contracts/cli-interface.md`,
  `quickstart.md`, `plan.md` (extension correction propagated).
- 🔁 Next prompts: `/sp.implement` to execute T001-T011, or start at T001 manually. `/sp.adr` still
  open for the two decisions flagged at plan time.
- 🧠 Reflection: The user's correction was worth verifying rather than accepting on assertion — the
  probe confirmed it and, more usefully, revealed that the failure mode is silent re-download rather
  than a visible error. That detail is what justified propagating the fix through every artifact
  instead of just the task text.

## Evaluation notes (flywheel)

- Failure modes observed: the original research D3 asserted a consequence ("the container is forced
  to mp4") that did not follow from the verified fact (`merge_output_format` exists). Verifying the
  premise but not the inference drawn from it is the gap — the probe in `/sp.plan` confirmed the
  option, never that it applies unconditionally.
- Graders run and results (PASS/FAIL): task count 11 PASS; exactly two test files PASS; no US2/3/4
  leakage into task bodies PASS; no Docker/CI/systemd/README tasks PASS; residual hardcoded `.mp4`
  scan clean PASS; both spec amendments present PASS.
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): when research records a decision, separate the verified
  fact from the inference drawn from it, so a wrong inference is visible without re-probing — D3
  would have read "merge_output_format exists (verified)" + "therefore ext is always mp4 (not
  verified)".
