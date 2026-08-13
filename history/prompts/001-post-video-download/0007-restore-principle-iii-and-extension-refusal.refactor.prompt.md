---
id: 0007
title: Restore Principle III And Extension Refusal
stage: refactor
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.implement
labels: ["principle-iii", "callback", "T006", "extension-fallback", "minimal-diff"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - backend/downloader.py
 - backend/cli.py
 - tests/test_downloader.py
 - specs/001-post-video-download/tasks.md
tests:
 - full suite: 120 passed in 6.40s
 - network/subprocess-blocked re-run: 120 passed
 - T006 grep, unmodified: both invocations silent
---

## Prompt

/sp.implement

Two fixes. Minimal diffs. Do not refactor anything else.

FIX 1 — Restore Principle III in downloader.py (resolves the open T006 question).
Do NOT amend T006. The rule is correct and the check did its job; loosening it the first time it fires makes it decorative.

- Remove `import sys` and the `print(...)` from backend/downloader.py.
- Add an optional callback parameter to download_post, following the same pattern as the existing `progress` callback: on_warning: Callable[[str], None] | None = None.
- The temp-directory removal failure calls on_warning(message) instead of printing. When on_warning is None, the warning is dropped — that is the caller's choice to make, not the module's.
- backend/cli.py supplies a function that writes the warning to stderr, preserving exactly the T008 output the operator already saw.
- The callback must work from inside the finally block, so it still fires on the KeyboardInterrupt path. That path is the whole reason the warning exists.
- Verify T006's grep passes again, unmodified.

FIX 2 — Make the extension fallback honest in _extension_of.
prepare_filename substitutes the literal "NA" for absent fields, so the `or ".mp4"` fallback is unreachable while reading as a live default.
- Treat a suffix of ".NA" as absent, alongside an empty suffix.
- Decide between raising and defaulting to .mp4, and state which you chose and why. Raising is defensible: this means yt-dlp reported a format with no container, and guessing mp4 would write a file whose name does not describe its contents.
- Update the test that documents the current .NA behavior to assert the new one.

Then re-run the full suite and confirm still-green plus no new files.

## Response snapshot

**FIX 1 — done, rule unmodified.** `import sys` and the `print(...)` are gone from
`backend/downloader.py`. Added `WarningHook = Callable[[str], None]` beside the existing
`ProgressHook`, and `on_warning: WarningHook | None = None` to both `download_post` and
`_remove_temp_dir`. The module hands over message *text only*; `backend/cli.py`'s
`_make_warning_hook` adds the `Warning:` prefix and the stderr routing, which is the same
text-vs-presentation split the progress hook already uses. `on_warning is None` returns early —
no stderr fallback, since that would put terminal output back in the module under another name.

Verified end-to-end on the path that matters, with a `KeyboardInterrupt` raised while a handle was
still held open inside the temp directory: the callback fires **from the finally block**, and the
resulting stderr line is byte-identical to what T008 produced.

**Finding — T006's grep never passed as literally written, even before T008.** The module docstring
read `no argparse, no printing, no sys.exit` and another line read `never calls sys.exit`, so
`argparse` and `sys\.exit` matched as *prose* on two lines independent of any real violation. T006
was evidently eyeballed rather than run. The docstrings now state the constraint without naming the
forbidden tokens, so both greps are genuinely silent and the check means something.

**FIX 2 — chose raising, not defaulting.** Both an empty suffix and `.NA` now raise.

The deciding argument is `_promote`: it renames whatever actually landed to the name built from
this value. So guessing `.mp4` would not produce an mp4 — it would produce a webm called `.mp4`, a
filename that lies about its contents while the FR-016 existence check happily agrees with itself
forever. That is the same judgement `_promote` already makes when it refuses to choose between two
files, so the module stays internally consistent.

**`ValueError` specifically**, not `RuntimeError`: `_extension_of` is called from the target-building
loop, which sits outside the per-entry `try`. `RuntimeError` would escape `download_post` as an
uncaught traceback; `ValueError` lands in `cli.py`'s existing handler. Verified — exit 1, clean
message on stderr, clean stdout, no traceback, and no change to `cli.py`'s exception handling.

**Tests**: the `.NA` test now asserts refusal, plus one that the error names the entry. The three
cleanup tests asserted on `capsys` stderr — that contract changed, so they now assert on a
collecting callback and additionally assert the core module writes nothing at all. Added coverage
for the `None`-callback drop.

`120 passed in 6.40s`; `120 passed` again with sockets and subprocess spawning hard-blocked. Still
exactly two test files, no `conftest.py`, no new files anywhere.

## Outcome

- ✅ Impact: Principle III holds again without weakening the rule that caught the violation; the
  extension fallback now does what it reads as doing.
- 🧪 Tests: 120 passed (was 117); 120 passed under the network/subprocess blocker; T006's two greps
  both silent, unmodified.
- 📁 Files: `backend/downloader.py`, `backend/cli.py`, `tests/test_downloader.py`,
  `specs/001-post-video-download/tasks.md`. No new files.
- 🔁 Next prompts: re-run T008 case 3 on the Linux VPS and record it; find a real multi-video post
  to close the `playlist_items` gap.
- 🧠 Reflection: keeping the rule and moving the code was the right call twice over — it produced a
  better design (the core no longer decides where warnings go) *and* exposed that the check had been
  passing on inspection rather than execution the whole time.

## Evaluation notes (flywheel)

- Failure modes observed: a verification task whose literal command never passed, masked because it
  was read rather than run; a fallback that read as a live default while being unreachable.
- Graders run and results (PASS/FAIL): pytest PASS (120); network-blocked pytest PASS (120); T006
  grep PASS (silent, unmodified); Ctrl+C callback-from-finally PASS; T008 stderr wording
  byte-identical PASS; `_extension_of` ValueError through CLI PASS (exit 1, no traceback); no new
  files PASS.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): run every verification task's literal command once, to
  find any other check that has only ever been eyeballed.
