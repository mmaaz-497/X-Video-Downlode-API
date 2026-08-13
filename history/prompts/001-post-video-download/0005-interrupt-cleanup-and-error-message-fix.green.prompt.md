---
id: 0005
title: Interrupt Cleanup And Error Message Fix
stage: green
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: (direct request — T008 defect follow-up)
labels: ["bugfix", "keyboardinterrupt", "cleanup", "windows", "yt-dlp", "FR-015", "FR-019"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
 - backend/downloader.py
 - backend/cli.py
tests:
 - none added (test files intentionally untouched per instruction; verification done via ad-hoc harness)
---

## Prompt

T008 found a real defect. Fix it — minimal diff, do not redesign.

WHAT HAPPENED (observed, not hypothetical):
I interrupted a download with Ctrl+C at 3.4% of 95.4MiB. Two problems:

a) The temp directory .tmp-xvd-hzq2h668 survived in the output directory. FR-015 and SC-004 say nothing may remain. Exit code was correctly 1.

b) The error printed was exactly: "download failed for video 1: " — with an empty message after the colon. FR-019 requires the error to name what went wrong.

DO NOT GUESS THE CAUSE. Investigate first, then fix:

STEP 1 — Determine what exception actually propagates.
Find out what yt-dlp raises when a download is interrupted mid-transfer. The empty message means KeyboardInterrupt did not reach our handler as KeyboardInterrupt — something converted it. Read the installed yt-dlp source to determine the actual path. Report what you find before writing the fix.

STEP 2 — Fix cleanup so it cannot fail silently.
The current shutil.rmtree(tmp, ignore_errors=True) swallows PermissionError, which on Windows is raised when yt-dlp or ffmpeg still holds a handle on the partial file. Replace it with:
- A retry: attempt removal up to 5 times with a short sleep between attempts, giving the OS time to release handles after the writing process stops.
- On final failure, do NOT stay silent. Write a warning to stderr naming the exact directory path that could not be removed, so the operator can see and delete it. A visible leftover is acceptable; an invisible one is not.
- Keep it in the finally block so it still covers KeyboardInterrupt and every exception path.

STEP 3 — Fix the empty error message.
When the underlying error carries no text, substitute a truthful message rather than printing a bare colon. Distinguish an operator interruption from a genuine download failure if step 1 shows they are distinguishable — an interrupt is not an error and should say so plainly. If they are not distinguishable, say that honestly in one message rather than inventing a distinction.

STEP 4 — Minor: fix the progress line.
Output showed "done, combining streams...   iB/s" — leftover characters from the previous longer progress line, because the rewriting line is not cleared before the shorter text is written. Pad the line to a fixed width or clear it before each write.

CONSTRAINTS:
- Only backend/downloader.py and backend/cli.py should change. If you think another file needs to change, stop and tell me why first.
- No new dependencies. time.sleep from the stdlib is fine.
- Do not add a startup sweep that deletes stale temp directories. The plan explicitly accepted stale dirs after SIGKILL as harmless; this fix is about the normal interrupt path only.
- Do not touch the test files yet.
- Note explicitly in your report whether the cleanup failure is Windows-specific, since this will run on Linux.

## Response snapshot

**Step 1 finding — the premise was wrong, in our favour.** Nothing converted the exception.
`KeyboardInterrupt` reaches our handler *as* `KeyboardInterrupt`; the empty message is our own
bug, because `str(KeyboardInterrupt())` is `''` and `downloader.py:246` folded it into
`f"download failed for video {position}: {exc}"`.

Verified against installed yt-dlp 2026.07.04 at `C:\Python313\Lib\site-packages\yt_dlp`. Every
layer's broadest clause is `except Exception`, and `KeyboardInterrupt` is a `BaseException`:

- `YoutubeDL.py:3684` `__download_wrapper` — `CookieLoadError`, `UnavailableVideoError`, `DownloadCancelled`
- `YoutubeDL.py:1727` `_handle_extraction_exceptions` — broadest is `except Exception`
- `YoutubeDL.py:3597-3604` `process_info` — `network_exceptions`, `OSError`, `ContentTooShortError`
- `downloader/http.py:373` — bare `except:` that calls `close_stream()` then **re-raises**
- `downloader/fragment.py:502,516` — re-raise; `:426` raises a bare `KeyboardInterrupt` (also empty str)

Empirically confirmed by patching `extract_info` to raise: `KeyboardInterrupt` escapes
`ydl.download()` unchanged with `str == ''`; `RuntimeError('boom')` escapes with its text intact.
So interrupt and genuine failure **are** distinguishable, by type.

**Step 1b — why the temp dir survived.** Reproduced the mechanism locally: `shutil.rmtree` over a
directory holding an open file raises `PermissionError [WinError 32]`, and `ignore_errors=True`
swallows it. Two in-library races leave a handle open at the moment our `finally` runs, both
clearing in milliseconds: `downloader/fragment.py:504` shuts its pool down with `wait=False`
before re-raising, and `downloader/external.py:571` documents that on Windows the console
delivers Ctrl+C to ffmpeg as a process-group event concurrently with our unwinding.

**Fixes applied (2 files, as constrained):**

- `backend/downloader.py` — new `_remove_temp_dir()`: up to 5 `shutil.rmtree` attempts, 0.2s
  apart (~0.8s span), `FileNotFoundError` treated as success, and on final failure a stderr
  warning naming the exact path. Called from the existing `finally`.
- `backend/downloader.py` — split `except (KeyboardInterrupt, Exception)` into a
  `KeyboardInterrupt` branch ("Interrupted by the operator. Video N was not completed.") and an
  `Exception` branch that falls back to `f"{type(exc).__name__} (no message)"` when `str(exc)` is
  empty.
- `backend/cli.py` — `_PROGRESS_WIDTH = 60`; both the progress line and the "done, combining
  streams..." line are left-padded to it, so a shorter rewrite fully covers a longer one.

**Windows-specific?** The *silent failure* is. POSIX unlinks open files without complaint, so on
the Linux VPS the first attempt succeeds and no retry is ever consumed. The retry loop is inert
there, not wasted. The warning path stays reachable on both (permissions, a read-only mount, NFS
`.nfs*` silly-rename), which is why it is not guarded by a platform check.

**Verified** (ad-hoc harness, no test files touched): closed-handle dir removed silently; missing
dir a silent no-op; permanently-held handle warns after 0.81s naming the path; handle released at
0.3s recovered silently at 0.41s; all three message paths correct with `status=failed` (exit 1)
and no leftover temp dir; both progress lines exactly 60 chars.

## Outcome

- ✅ Impact: FR-015/SC-004 leftover temp dir now either removed or loudly reported; FR-019 empty
  error message replaced with a truthful, type-aware message; progress line no longer smears.
- 🧪 Tests: no test files added or modified, per explicit instruction. All verification via an
  ad-hoc harness plus `python -m py_compile`. The two planned pytest files do not exist yet.
- 📁 Files: `backend/downloader.py` (imports, `_CLEANUP_ATTEMPTS`/`_CLEANUP_RETRY_SECONDS`,
  `_remove_temp_dir`, exception split, `finally`), `backend/cli.py` (`_PROGRESS_WIDTH`, two prints).
- 🔁 Next prompts: write the T008 regression tests covering interrupt cleanup and the message
  branches; decide whether `cli.py`'s now-narrow `except KeyboardInterrupt` claim ("No partial
  file was left behind") should be softened.
- 🧠 Reflection: the reported symptom implied a conversion that did not exist. Reading the
  installed source before writing anything is what kept the fix from chasing a phantom.

## Evaluation notes (flywheel)

- Failure modes observed: `str(KeyboardInterrupt())` is `''`, so any `f"...: {exc}"` over a
  `BaseException` can silently produce an empty explanation; `ignore_errors=True` converts a real
  Windows `PermissionError` into an invisible leftover.
- Graders run and results (PASS/FAIL): compile PASS; cleanup 4/4 scenarios PASS; message branches
  3/3 PASS; progress width PASS; pytest — no tests collected (none exist).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): add the two pytest files so this defect is covered by
  a regression test rather than an ad-hoc harness.
