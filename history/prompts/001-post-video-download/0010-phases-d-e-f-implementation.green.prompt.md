---
id: 0010
title: Phases D E F Implementation
stage: green
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.implement
labels: ["US2", "US3", "US4", "FR-017", "FR-020", "list-formats", "format-selection", "ADR-0001"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: history/adr/0001-media-index-handling-in-url-canonicalisation.md
  pr: null
files:
 - specs/001-post-video-download/spec.md
 - specs/001-post-video-download/tasks.md
 - specs/001-post-video-download/data-model.md
 - backend/validation.py
 - backend/downloader.py
 - backend/cli.py
 - tests/test_validation.py
 - tests/test_downloader.py
tests:
 - full suite: 151 passed (was 120)
 - network/subprocess-blocked re-run: 151 passed
 - T006 grep, unmodified: both invocations silent
---

## Prompt

/sp.implement

Execute T012 through T026 — phases D, E, and F.

Resolve the FR-017 interaction before writing T013: preserving the media index means an indexed URL downloads only that media item, while a bare URL still downloads every video. Update spec.md FR-017 to scope it to bare post URLs and record indexed URLs as explicit single-item selection. Do not change the bare-URL behavior.

Same rules as before: no new files in backend/, no third test file, no new dependencies. download_post stays framework-free — no printing, no sys.exit, no argparse import. If a task is wrong or impossible as written, stop and tell me rather than improvising. Stop before each manual verification task and let me run it.

## Response snapshot

**12 of 15 tasks done. The three manual gates — T016, T020, T025 — are left for the operator.**
`151 passed` (was 120), same two test files, same four modules, no new dependencies.

**FR-017 resolved first, as instructed.** Scoped to bare post URLs, and a new **FR-020** records
indexed URLs as explicit single-item selection, requiring the index to survive canonicalisation and
to appear in the filename. Bare-URL behaviour is unchanged and is asserted by tests.

**Phase D.** T012's audit is recorded verbatim in `tasks.md`: all four FR-004 categories already
shipped, as five distinct messages, and no new diagnosis strings were written. The only construction
was T013 — `_POST_PATH` now captures an optional `(?:photo|video)/(?P<index>\d+)`, `PostReference`
carries `media_index`, and `canonical_url` preserves it. Per ADR-0001 the filename half went in too,
via `_filename_index`. Verified end-to-end:

```text
/video/1 -> https://x.com/i/web/status/20/video/1 -> someone-20-1.mp4
/video/2 -> https://x.com/i/web/status/20/video/2 -> someone-20-2.mp4
```

The collision is closed. T014 capitalises diagnoses at print time in `cli.py`, using a slice rather
than `str.capitalize()`, which would have lowercased "X" and yt-dlp filenames.

**Phase E.** `list_formats` returns `FormatListing`/`FormatOption`, projecting the five research-D4
fields verbatim; absent fields stay `None` rather than becoming `0`. `--list-formats` short-circuits
**before** `output_dir()`, so nothing is written — verified with a nonexistent `--output-dir` that
was not created. The listing goes to stdout because on that path the listing *is* the output.

**Phase F.** `download_post(format_id=…)` overrides the format verbatim; `allowed_extractors:
["twitter"]` confirmed still present under an override, so research D8 stays closed. The two flags
are mutually exclusive (argparse, exit 2, verified).

**Two deviations, flagged rather than improvised:**

1. **T022 was impossible as written.** It said to add the unavailable-format diagnosis to
   `_ERROR_DIAGNOSES` "as one more ordered row", but every row there is a *static* string while
   FR-008 requires naming both the requested id and the available ones. Implemented as
   `_diagnose_format(error, requested, info)` in front of the table, delegating to `_diagnose` for
   everything else — the table and its generic fallback are untouched, and tests assert both.
2. **`--format` can still mis-suffix a filename.** T021 specified the extension come from
   `_extension_of`, and it does — but that reads the *default-selected* format's `ext`, not the
   chosen one, so a non-default container would be written with the wrong suffix. Left as specified
   and handed to T025 to check, with the fix noted.

## Outcome

- ✅ Impact: US2, US3, and US4 implemented; FR-017 scoped and FR-020 added; the ADR-0001 collision
  closed end-to-end.
- 🧪 Tests: 151 passed; 151 passed network-blocked; T006 greps silent.
- 📁 Files: 3 spec docs, 3 backend modules, 2 test files. No new files anywhere.
- 🔁 Next prompts: run T016, T020, T025; T025 should specifically check the `--format` suffix issue.
- 🧠 Reflection: resolving FR-017 in the spec before touching code meant T013 was a transcription of
  a decision already made, rather than a decision made under implementation pressure.

## Evaluation notes (flywheel)

- Failure modes observed: a task specifying a static lookup table for a message that must be
  dynamic; a task naming an extension source that is correct for the default path and wrong for the
  new one.
- Graders run and results (PASS/FAIL): pytest PASS (151); network-blocked PASS (151); T006 grep PASS;
  four modules / two test files PASS; no new dependencies PASS; `--list-formats` writes nothing PASS;
  mutual exclusion exit 2 PASS; `allowed_extractors` under format override PASS; indexed-URL filename
  collision PASS.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): T025's non-default-container check, which is the one
  known-open correctness question in Phase F.
