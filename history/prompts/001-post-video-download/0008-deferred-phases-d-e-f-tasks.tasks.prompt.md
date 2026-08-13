---
id: 0008
title: Deferred Phases D E F Tasks
stage: tasks
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.tasks
labels: ["tasks", "US2", "US3", "US4", "phase-d", "phase-e", "phase-f", "deferred-phases"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/001-post-video-download/tasks.md
tests:
 - none (planning artifact; no code changed)
 - verification probes run read-only against existing backend/ code
---

## Prompt

/sp.tasks

Generate tasks for the deferred phases D, E, and F — User Stories 2, 3, and 4. The specification for all three already exists in spec.md; do not re-specify them. Phases A, B, and C are complete and must not be regenerated.

Build all three in one task list, in this order, since E and F share the format-listing code path:

PHASE D — US2: distinct no-video diagnoses.
The error-mapping table from research D6 is already implemented in downloader.py; this phase surfaces the distinctions rather than building them from scratch. Verify what the existing mapping already produces before writing new code — the generic fallback proved itself on the HTTP 522 case during T008 and must remain intact for unmatched strings. Honour the recorded limitation on FR-004: "images but no video" is not distinguishable from "no media at all" for bare post URLs, so do not attempt to fake the distinction.

PHASE E — US3: --list-formats.
Prints format_id, resolution, ext, filesize_approx, and tbr from info["formats"] verbatim, per research D4. No download, no ffmpeg preflight, no output directory needed. The same URL validation and no-video diagnosis apply first (US3 acceptance scenario 3). Note that research D4 deferred confirming the actual format_id shape for a live X post to this phase — it needs a real network call, so it belongs in manual verification, not in a test.

PHASE F — US4: --format ID.
Passes the operator's chosen identifier through to yt-dlp's format option, replacing the default bestvideo+bestaudio/best. An unavailable format must fail with a message naming what was requested and what is actually available, exit 1, no file written.

CONSTRAINTS:
- All three phases must stay inside the existing four modules. Do not create new files in backend/.
- download_post must remain framework-free: no printing, no sys.exit, no argparse import. Warnings and progress go through the existing callback hooks.
- Extend the existing two test files only. Do not create a third.
- Per Principle II, add tests only where they cover URL validation or the extractor wrapper, and only where they need no network. Format listing against a live post is manual verification, not a test.
- Include one manual verification task per phase. Keep each short.
- Do not touch the two open items from T008: the Windows Ctrl+C cleanup and the untested playlist_items path. Both stay recorded as-is.

## Response snapshot

**15 tasks, T012–T026**, appended to `tasks.md`. Phases A–C untouched.

**The verification you asked for changed Phase D's shape.** Running all six research-D6 strings
through the shipped `_diagnose` shows `_ERROR_DIAGNOSES` **already produces all four FR-004
categories** — five distinct messages, not four (protected-account and age-restricted are separate).
US2 is therefore mostly already built. The measured table is embedded in `tasks.md` above Phase D so
nobody rebuilds it, and T012 exists purely to re-run and record that audit before any code is
written.

**One real defect found, and it is the whole of Phase D's construction work.**
`parse_post_url` rebuilds `canonical_url` as `https://x.com/i/web/status/<id>`, which **discards the
media index**; `_POST_PATH` never captures it. yt-dlp's own `TwitterIE` does
(`{'id': '20', 'index': '2'}`). Consequence: `Media #<n> is not a video` — the *only* condition
under which "images but no video" is distinguishable at all — can never be produced, so the
`("is not a video", …)` row of `_ERROR_DIAGNOSES` is dead code today. T013 fixes it in
`validation.py`, which means Phase D's real work is not in `downloader.py` at all.

The recorded FR-004 limitation is honoured: a bare image-only post still correctly reports "no video
in it", and T016 says so explicitly rather than faking the distinction.

**Two non-obvious details caught while planning, both written into the tasks:**

- T018 — `main()` currently resolves `output_dir()` before anything else, and that call *creates* the
  directory. `--list-formats` must write nothing at all, so the flag has to short-circuit before it.
- T014 — `_diagnose` returns lowercase-leading text deliberately, so `_partial_failure` can embed it
  mid-sentence. Capitalising must happen at print time in `cli.py`, not in `downloader.py`.

Ordering D → E → F is enforced rather than incidental: US3 acceptance scenario 3 requires the US2
diagnoses to run before listing, and Phase F depends on T020 recording what real `format_id` values
look like. Building `--format` before that is guesswork.

All six constraints carried into a dedicated section: four modules, framework-free core, two test
files, no-network tests only, one manual gate per phase, and the two open T008 items left untouched.

## Outcome

- ✅ Impact: three deferred phases planned as one ordered list, with US2 correctly scoped as
  verification-plus-one-defect rather than a rebuild.
- 🧪 Tests: none — planning artifact. The probes run were read-only against existing code.
- 📁 Files: `specs/001-post-video-download/tasks.md`.
- 🔁 Next prompts: `/sp.implement` for Phase D starting at T012; decide T013's FR-017 interaction
  before implementing it.
- 🧠 Reflection: the instruction to verify before writing was load-bearing. Planning US2 from spec.md
  alone would have produced four tasks to build a table that already exists, and would have missed
  the index-dropping defect entirely — the actual reason the fourth category never appears.

## Evaluation notes (flywheel)

- Failure modes observed: a diagnosis row that has never been reachable in production, masked because
  its unit-level mapping is correct — the gap is upstream in URL canonicalisation.
- Graders run and results (PASS/FAIL): checklist format PASS (15/15 tasks carry checkbox + ID + file
  path; all 14 phase tasks carry a story label, T026 correctly has none); phase ordering PASS;
  constraint coverage PASS (6/6 stated).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): before each future phase, run its existing code paths
  once and paste the output into the task list — it changed the plan materially here.
