---
id: 0006
title: Phase C Test Suite
stage: green
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.implement
labels: ["tests", "pytest", "phase-c", "T008-record", "T009", "T010", "T011"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - specs/001-post-video-download/tasks.md
 - tests/test_validation.py
 - tests/test_downloader.py
tests:
 - tests/test_validation.py (58 cases)
 - tests/test_downloader.py (59 cases)
 - full run: 117 passed in 5.00s
 - re-run with sockets and subprocess spawning hard-blocked: 117 passed
---

## Prompt

/sp.implement

Execute T009, T010, and T011 only — Phase C.

Before writing tests, update tasks.md to record T008's actual result:
- Passed: real download with audio verified via ffprobe, all three rejection cases, idempotent skip with exit 0, and the transient HTTP 522 which correctly fell through to the generic error carrying yt-dlp's own message.
- Passed with a platform exception: Ctrl+C leaves a .tmp-xvd-* directory on Windows because the OS holds the file handle. The error message and the visible warning are correct; only the removal fails. Record this as unverified on Linux, to be re-checked on the VPS. Do not attempt another fix.
- Not run: the multi-video case. Record it as an untested path, specifically noting that the playlist_items download approach from deviation 3 has never executed.

Then write the two test files per the task text. Constraints unchanged: exactly two files, literal info dicts, no network, no ffmpeg, no mocking framework, no monkeypatching of yt-dlp internals. Do not add a third test file. Do not modify backend/ code to make tests easier to write — if something is hard to test, tell me instead of changing it.

## Response snapshot

Checklist gate: `requirements.md` 16/16 → PASS, proceeded without prompting.

**tasks.md updated.** T001–T008 marked `[X]`. T008 gained a full result record: the passing cases
in a table (including the HTTP 522 fallthrough, noted as the most useful evidence in the run
because it proves the D6 generic path is real); the Ctrl+C platform exception with an explicit
**UNVERIFIED ON LINUX** block and an instruction not to fix before the VPS measurement exists; and
the multi-video gap written as a named untested path — `options["playlist_items"] = str(position)`
has never executed, since the single-video path never reaches that line.

**Two test files written, 117 passing.**

- `tests/test_validation.py` — eight hosts parametrised off `ACCEPTED_HOSTS` itself plus a separate
  exact-set assertion (so deleting a host fails rather than silently losing a case); accepted URL
  shapes; ~24 rejection cases centred on the look-alike family; `sanitize_handle`; `build_target`.
- `tests/test_downloader.py` — `allowed_extractors` regression guard on both construction paths;
  entry branching; handle precedence; extension resolution; all six D6 strings; promotion; cleanup.

**Three things worth reporting rather than silently handling:**

1. **T006's literal grep now fails** because the T008 fix added `import sys` and one `print(...)`
   to `downloader.py`. Recorded inline in tasks.md as an open question for the operator (amend the
   rule, or route the warning through a callback). Not decided, not silently fixed.
2. **`_extension_of` returns `.NA`, not `.mp4`,** for an entry with no `ext` — yt-dlp's
   `prepare_filename` substitutes the literal `NA`, so the suffix is non-empty and `or ".mp4"`
   never runs. Unreachable in practice. Test asserts actual behaviour with the discrepancy
   documented; no code changed.
3. **The containment `ValueError` was initially tested by monkeypatching our own `sanitize_handle`
   — replaced.** Probing found two real vectors that reach the guard through ordinary arguments
   (`post_id` and `ext`, neither of which is sanitised), so the patch was removed. Also learned
   that a `..` *handle* is neutralised rather than rejected, and asserted that mechanism directly.

**No-network claim measured, not assumed**: re-ran with `socket.connect`, `create_connection`,
`getaddrinfo`, `subprocess.Popen`, and `subprocess.run` replaced by hard errors → still 117 passed.
Blocker lived in the scratchpad, so no third file was added.

**Not coverable under the constraints**: `download_post`'s loop — the `KeyboardInterrupt` branch and
the empty-`str(exc)` fallback from the T008 fix, the ffmpeg preflight, and the `playlist_items`
call. Each needs a download or a mock. Reported rather than worked around.

## Outcome

- ✅ Impact: Phase C closed. T008's real result — including two honest gaps — is now in the
  artifact instead of in conversation. 117 tests lock the Principle V surface and the extractor
  wrapper.
- 🧪 Tests: 117 passed in 5.00s; 117 passed again with network and subprocess spawning blocked.
- 📁 Files: `specs/001-post-video-download/tasks.md`, `tests/test_validation.py`,
  `tests/test_downloader.py`. No `backend/` file touched.
- 🔁 Next prompts: decide T006's rule vs. a callback for the cleanup warning; re-run T008 case 3 on
  the Linux VPS and record it; find a real multi-video post to close the `playlist_items` gap.
- 🧠 Reflection: three of my own expected values were wrong before the code was — two miscounted
  substitutions and one wrong assumption about `prepare_filename`. Running the suite before
  believing it is what separated "my arithmetic is off" from "the code is wrong."

## Evaluation notes (flywheel)

- Failure modes observed: reaching for a monkeypatch before checking whether the guard was
  reachable through real arguments; assuming `or ".mp4"` did what it reads as.
- Graders run and results (PASS/FAIL): pytest PASS (117); network-blocked pytest PASS (117); exactly
  two test files PASS; no `backend/` modification PASS; T006 literal grep FAIL (recorded, open).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): decide the T006 rule, since it is the one open item
  that currently makes a recorded verification task read as failing.
