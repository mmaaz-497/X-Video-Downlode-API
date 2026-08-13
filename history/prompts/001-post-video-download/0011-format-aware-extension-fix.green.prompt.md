---
id: 0011
title: Format Aware Extension Fix
stage: green
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.implement
labels: ["US4", "FR-008", "FR-016", "ADR-0001", "extension", "minimal-diff"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: history/adr/0001-media-index-handling-in-url-canonicalisation.md
  pr: null
files:
 - backend/downloader.py
 - tests/test_downloader.py
 - specs/001-post-video-download/tasks.md
tests:
 - full suite: 164 passed (was 151)
 - network/subprocess-blocked re-run: 164 passed
 - T006 grep, unmodified: both invocations silent
---

## Prompt

/sp.implement

Fix the --format extension defect flagged as deviation 2 in the last run. Minimal diff, backend/downloader.py only.

_extension_of reads the ext off the default-selected format, not the operator's chosen one, so --format with a non-default container writes a file whose name misdescribes its contents — and FR-016's existence check then agrees with that wrong name forever. This is the same lying-filename class that ADR-0001 rejected, so leaving it is inconsistent with a decision already recorded.

When a format is explicitly requested, derive the extension from that format's entry in info["formats"] rather than from the default selection. If the requested id is not found there, that is the unavailable-format case FR-008 already handles — do not fall back to a guess.

Extend the existing test file only. Do not create a third. Then tell me T025 is ready to run.

## Response snapshot

**Fixed. `164 passed`** (was 151), `backend/downloader.py` plus the existing test file only.

`_extension_for_format(entry, format_id)` derives the extension from the **chosen** format's entry in
`info["formats"]`. Three outcomes, no guess among them: the container when the id is found; `None`
when it is absent, which the caller turns into the FR-008 message; `ValueError` when the format
exists but reports no container, matching `_extension_of`'s existing refusal.

`_unavailable_format_message` was extracted from `_diagnose_format` so the two callers that reach
the same conclusion from opposite ends — yt-dlp raising *after* a download attempt, and the
extension resolver finding the id absent *before* one — produce identical text.

Verified end-to-end against a post whose default is mp4 and whose `http-256` rendition is webm:

```text
--format http-256   exit=0  files=['someone-20.webm']
--format http-9999  exit=1  files=none   Format 'http-9999' is not available... Available: http-950, http-256
```

Before the fix the first case wrote `someone-20.mp4` containing webm. The unavailable case is now
caught *before* any download rather than after one, because the target filename cannot be built
without knowing the container and the answer was already in hand.

**Behaviour change surfaced, not decided.** `--format` now accepts only literal `format_id` values.
yt-dlp selector expressions — `best`, `bestvideo+bestaudio`, `137+140`, `bestvideo[height<=720]` —
were passed through verbatim before and worked; they are now rejected with the FR-008 message,
since they are not ids in `info["formats"]` and no container can be derived from them without
guessing. This follows the instruction exactly ("if the requested id is not found there, that is the
unavailable-format case — do not fall back to a guess") and matches FR-007/FR-008 and the flag's own
help text, but it removes an undocumented capability that did work. Recorded in `tasks.md` with the
note that merge specs resolve deterministically to `merge_output_format` and could be supported
without a guess if wanted.

## Outcome

- ✅ Impact: the last known lying-filename path is closed, consistent with ADR-0001; the FR-008 case
  is now detected before a download rather than after.
- 🧪 Tests: 164 passed; 164 passed network-blocked; T006 greps silent; still two test files, four
  modules.
- 📁 Files: `backend/downloader.py`, `tests/test_downloader.py`,
  `specs/001-post-video-download/tasks.md`. No new files.
- 🔁 Next prompts: run T016, T020, T025 — all three manual gates are now ready.
- 🧠 Reflection: the fix removed a capability nobody specified. Worth saying out loud rather than
  filing under "no behaviour change", because the person who notices `--format best` breaking will
  otherwise have no idea why.

## Evaluation notes (flywheel)

- Failure modes observed: a correctness fix that narrows an undocumented input vocabulary as a side
  effect; two code paths that must agree on an error message and would drift if each built its own.
- Graders run and results (PASS/FAIL): pytest PASS (164); network-blocked PASS (164); T006 grep PASS;
  two test files / four modules PASS; chosen-format extension PASS (webm stays webm); unknown id →
  FR-008 with no file PASS; no fallback guess PASS.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): decide whether `--format best` should keep working;
  merge specs are the only selector class with a deterministic container.
