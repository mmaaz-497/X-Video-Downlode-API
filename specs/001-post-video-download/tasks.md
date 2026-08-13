---
description: "Task list for 001-post-video-download — User Stories 1 through 4"
---

# Tasks: Single Post Video Download (US1–US4)

**Input**: Design documents from `/specs/001-post-video-download/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/cli-interface.md](./contracts/cli-interface.md),
[quickstart.md](./quickstart.md), [ADR-0001](../../history/adr/0001-media-index-handling-in-url-canonicalisation.md)

**Scope**: Phases A–F — all four user stories. Phases A–C (US1) and the code for D–F (US2, US3, US4)
are complete; the three manual verification gates T016, T020, and T025 are outstanding and are the
operator's to run.

**Tests**: Exactly two files, written *after* the code they cover (Constitution Principle II). No
test-per-requirement. No network. No ffmpeg. No mocking framework. The manual `quickstart.md` runs
are the primary validation gates, not the test suite.

## Format: `[ID] [P?] Description`

- **[P]**: can run in parallel — different file, no dependency on another incomplete task
- Every task names the exact file path it touches

## Path Conventions

All application code in `backend/` (Principle I). Tests in `tests/` at the repository root.

---

## Phase A: Foundation (blocking)

**Purpose**: Project skeleton, configuration, and the Principle V security surface. Nothing in
Phase B can start until A is complete.

- [X] **T001** Create `pyproject.toml` at the repository root.
  - uv-managed, `requires-python = ">=3.11"`.
  - Runtime dependency: `yt-dlp` only. Dev dependency: `pytest` only.
  - Console entrypoint `xvd = "backend.cli:main"`.
  - Create `backend/__init__.py` (empty) and `.env.example` documenting `XVD_OUTPUT_DIR`.
  - Do **not** add click, typer, rich, pydantic, or any HTTP framework (Principle IV).
  - **Verify**: `uv sync` succeeds and `uv run python -c "import yt_dlp"` exits 0.

- [X] **T002** [P] Implement `backend/config.py`.
  - `output_dir(override: Path | None = None) -> Path` resolving **flag → `XVD_OUTPUT_DIR` → CWD**,
    in that precedence (data-model "Configuration").
  - Create the directory with `mkdir(parents=True, exist_ok=True)` if absent.
  - `pathlib` only — no string path joining, no hardcoded separators (works on Windows and the VPS).

- [X] **T003** Implement `backend/validation.py` — **the Principle V surface**.
  - `ACCEPTED_HOSTS: frozenset[str]` with the **eight** hostnames from spec Assumptions:
    `x.com`, `www.x.com`, `m.x.com`, `mobile.x.com`, `twitter.com`, `www.twitter.com`,
    `m.twitter.com`, `mobile.twitter.com`. No `.onion` (research D1).
  - `parse_post_url(url) -> PostReference`: `urlsplit`, lowercase host, strip `:port`, compare with
    `==` against `ACCEPTED_HOSTS`. **Exact match only — never substring or `startswith`** (FR-003).
    Scheme must be `http`/`https`. Path must match
    `^/(?:i/web|[^/]+)/status(?:es)?/(?P<id>\d+)`; query and fragment discarded, trailing slash
    tolerated. Raise `ValueError` naming the URL and reason (FR-019). **Makes no network request**
    (FR-002) — in particular `t.co` is rejected here rather than resolved.
  - `sanitize_handle(handle) -> str`: reduce to `[A-Za-z0-9_-]`, truncate to 64, empty → `"unknown"`.
  - `build_target(output_dir, handle, post_id, ext, index) -> OutputTarget`: compose
    `<handle>-<post_id>[-<index>].<ext>`, then assert `path.resolve().is_relative_to(output_dir.resolve())`
    and raise `ValueError` if not (FR-011). **`ext` is a parameter — never hardcode `.mp4`** (see
    T004 and research D3).
  - Define the `PostReference` and `OutputTarget` frozen dataclasses here per `data-model.md`.
  - This module imports nothing from `backend/` — it is the leaf of the dependency graph.

**Checkpoint**: `uv sync` works; validation is importable and has no yt-dlp dependency.

---

## Phase B: User Story 1 — the deliverable (Priority: P1) 🎯 MVP

**Goal**: `xvd <url>` writes a playable video file to the output directory.

**Independent Test**: run the command with a public X post URL containing a video; a complete,
playable file with both video and audio appears in the output directory.

- [X] **T004** Implement `backend/downloader.py` — core logic, framework-free.

  Single task because these steps share state and splitting them would create artificial seams in a
  four-module project. Must contain, in order:

  1. **Shared options builder** — every `YoutubeDL(...)` construction passes
     `allowed_extractors: ["twitter"]`. **Security-critical, not optional**: without it a post
     containing a link makes the extractor hand an author-chosen third-party URL back to yt-dlp
     *after* our allowlist gate passed (research D8). Also set `quiet`, `no_warnings`, `noprogress`,
     `format: "bestvideo+bestaudio/best"`, `merge_output_format: "mp4"`.
  2. **`fetch_metadata`** — `extract_info(reference.canonical_url, download=False)`. Transfers no
     video bytes, which is what lets FR-016 short-circuit.
  3. **Entry branching** — `info["entries"]` when `info.get("_type") == "playlist"`, else `[info]`
     (research D5). Index 1-based, matching yt-dlp's own `#N`.
  4. **Handle resolution** — `uploader_id` → `uploader` → `"unknown"`. **Not from the URL**:
     `i/web/status/<id>` carries no handle (research D3).
  5. **Extension resolution** — `Path(ydl.prepare_filename(entry_info)).suffix`. **Never hardcode
     `.mp4`** — `merge_output_format` applies only when a merge happens, so a progressive rendition
     from the `/best` fallback keeps its native container (probe-verified: `webm`). Hardcoding makes
     the FR-016 check test a path that never exists and re-download every run.
  6. **Existence check** — `build_target(...)`; if it exists, return
     `DownloadOutcome("skipped", ...)` immediately (FR-016, exit 0). No ffmpeg needed on this path.
  7. **ffmpeg preflight** — `shutil.which("ffmpeg")`; on `None` fail with a message naming the
     missing prerequisite (research D9). **A step here, not its own task.** Runs only when a
     download will actually occur — never on the reject or skip paths.
  8. **Atomic download** — `tempfile.mkdtemp(dir=output_dir)` so temp and target share a filesystem,
     download there, then promote with **`os.replace`**. **Promote the file that actually landed in
     the temp directory**, not a path assembled from assumptions — the temp dir is created empty and
     holds exactly one finished file per entry. **Never `shutil.move`**: it degrades to copy+delete
     across filesystems and can leave a partial file at the destination (research D2).
  9. **Cleanup** — `try/finally` with `shutil.rmtree(tmp, ignore_errors=True)`, covering
     `KeyboardInterrupt` and every exception, so FR-015 holds on Ctrl+C.
  10. **Error mapping** — catch `yt_dlp.utils.DownloadError`, ordered case-insensitive substring
      match against the six strings in research D6, falling back to a generic message that includes
      yt-dlp's own text verbatim. **No custom exception classes** (Principle VI).
  11. **Public entrypoint** — `download_post(reference, output_dir, progress=None) -> DownloadOutcome`.
      Never prints. Never calls `sys.exit`. Calls `parse_post_url` internally so no caller can skip
      the Principle V gate.
  12. **Partial-success rule** — multi-video post where one entry fails: `status="failed"`, `paths`
      lists the completed files, message names both (data-model "Partial-success rule").

- [X] **T005** Implement `backend/cli.py` — argparse shell, **no business logic**.
  - `argparse` only: one positional `url`, one optional `--output-dir`.
  - Progress hook writing a rewriting line to **stderr**; fall back to `total_bytes_estimate` when
    `total_bytes` is absent, which is the normal case for HLS (research D7). Never divide by `None`.
  - **stdout: output paths only, one per line.** All diagnostics and progress to stderr, so
    `xvd <url> > paths.txt` yields a clean file (FR-018, SC-007).
  - Exit-code map — the only decision this module makes: `downloaded`→0, `skipped`→0, `failed`→1.
    argparse supplies 2 for usage errors.
  - Catch `ValueError` from validation and `KeyboardInterrupt`, print to stderr, exit 1.
  - `main()` is the `xvd` entrypoint.

- [X] **T006** Verify the module boundary holds (Principle III).
  - `backend/downloader.py` and `backend/validation.py` MUST NOT import `argparse` or `sys`, and
    MUST NOT call `print` or `sys.exit`.
  - `backend/cli.py` MUST NOT import `yt_dlp`.
  - **Verify**: `grep -nE "argparse|sys\.exit|print\(" backend/downloader.py backend/validation.py`
    returns nothing, and `grep -n "yt_dlp" backend/cli.py` returns nothing.
  - A failure here means business logic leaked into the CLI or vice versa — fix before T008.

  > ✅ **Re-verified 2026-08-13 — both greps silent, rule unmodified.** The T008 Ctrl+C fix had
  > added `import sys` and one `print(...)` to `backend/downloader.py`; the rule was kept and the
  > code changed to satisfy it. `download_post` now takes
  > `on_warning: Callable[[str], None] | None`, following the existing `progress` callback pattern.
  > `_remove_temp_dir` hands the failure text to that callback instead of writing anywhere;
  > `backend/cli.py` supplies the hook that renders it to stderr, so the operator sees the identical
  > line T008 produced. A `None` callback drops the warning — the caller's choice, not the module's.
  >
  > **Separate finding, now also fixed:** this grep never actually passed as literally written, even
  > before T008. The module docstring read "no argparse, no printing, no sys.exit" and another line
  > read "never calls sys.exit", so `argparse` and `sys\.exit` matched as *prose* on two lines. T006
  > was evidently eyeballed rather than run, or its matches dismissed by hand. The docstrings now
  > describe the constraint without naming the forbidden tokens, so the check is honest.

- [X] **T007** [P] Fold the accepted-hostname list into `.env.example` and confirm
  `backend/validation.py` matches `spec.md` Assumptions exactly — all eight hosts, no more, no
  fewer. Cheap guard against the five-host list from the original spec draft reappearing.

- [X] **T008** 🚦 **Run the manual verification script in [quickstart.md](./quickstart.md).**
  **This is the primary validation gate for User Story 1** (Principle II) — US1 is not complete until
  this passes. Record actual observed output for each step.

  Minimum required coverage:

  | # | Case | Expect |
  |---|---|---|
  | 1 | A **real public X post with video** | file on stdout, exit 0, and `ffprobe` shows **both** `video` and `audio` streams (FR-009, SC-003) |
  | 2 | A **non-X URL** (`https://example.com/video/1`) | rejected instantly, exit 1, no file, no network request (FR-002, SC-002) |
  | 3 | **Ctrl+C** during a large download | exit 1 and **no** `.mp4`/`.part`/`.tmp-*` left behind (FR-015, SC-004) |
  | 4 | **Re-run an already-downloaded URL** | `Already downloaded:`, exit **0**, file hash unchanged (FR-016, SC-009) |

  Also run, from `quickstart.md`: look-alike host `x.com.evil.net` and `t.co` rejection (step 2),
  multi-video post (step 5), missing-ffmpeg preflight (step 6), `--output-dir` and `XVD_OUTPUT_DIR`
  precedence (step 7), and clean stdout when piped (step 8).

  ### T008 result — run 2026-08-13, Windows 11 / PowerShell 5.1, Python 3.13.5, yt-dlp 2026.07.04

  **Passed**

  | Case | Observed |
  |---|---|
  | 1 — real public post with video | file path on stdout, exit 0; `ffprobe` confirmed **both** a video and an audio stream (FR-009, SC-003) |
  | 2 — non-X URL, look-alike host, `t.co` | all three rejected instantly, exit 1, no file written, no network request (FR-002, FR-003, SC-002) |
  | 4 — re-run an already-downloaded URL | `Already downloaded:`, exit **0**, file hash unchanged (FR-016, SC-009) |
  | — transient HTTP 522 | fell through to the generic diagnosis carrying yt-dlp's own message verbatim, exactly as research D6 intends when no substring matches. Unplanned, and the most useful evidence in the run: the fallback path is real, not theoretical. |

  **Passed with a platform exception — case 3, Ctrl+C (FR-015, SC-004)**

  Exit code 1 ✅ · error message names the interrupt ✅ · stderr warning names the leftover path ✅ ·
  **removal of the `.tmp-xvd-*` directory ✗ on Windows.**

  The OS still holds the partial file's handle when our `finally` runs, so `rmtree` raises
  `PermissionError [WinError 32]`. Cause established by reading installed yt-dlp source, not
  inferred: `downloader/fragment.py:504` shuts its worker pool down with `wait=False` before
  re-raising, and `downloader/external.py:571` documents that on Windows the console delivers Ctrl+C
  to ffmpeg as a process-group event concurrently with our unwinding. Fix applied: five retries
  0.2s apart, then a stderr warning naming the exact directory. A *visible* leftover is the accepted
  outcome; the silent one was the defect.

  > **UNVERIFIED ON LINUX — re-check on the VPS.** POSIX unlinks open files without complaint, so
  > the first attempt is expected to succeed there and the retry loop never to be consumed. That is
  > a reasoned expectation, **not an observation**. Re-run case 3 on the VPS and record the result
  > here. No further fix is to be attempted before that measurement exists.

  **Not run — case 5, multi-video post: an untested path**

  No multi-video post was exercised. This leaves a specific, named gap:

  > **The `playlist_items` download approach has never executed.** In `backend/downloader.py`,
  > `options["playlist_items"] = str(position)` is set only when `multiple` is true — the
  > single-video path never reaches that line. Everything downstream of it is therefore unproven in
  > practice: per-entry format selection under `playlist_items`, the `-1`/`-2` filename suffixes on
  > real metadata, and the partial-success rule when entry 2 of 2 fails. T010 covers the *pure*
  > parts of this (entry branching and target naming from literal dicts) but **cannot** cover the
  > download call itself without a network request. Requires a real multi-video post to close.

**Checkpoint**: User Story 1 is functionally complete and manually verified, with the two exceptions
recorded above. Phase C may begin.

---

## Phase C: Tests (the permitted handful)

**Purpose**: Lock the two areas Principle II permits. Written *after* Phase B; **not a gate on it**.
Exactly two files — do not add a third.

- [X] **T009** [P] Write `tests/test_validation.py`.
  - Accepted: all eight hostnames, `i/web/status/<id>`, `statuses/<id>`, trailing slash, and query
    noise (`?s=20&t=abc`).
  - Rejected: `x.com.evil.net`, `notx.com`, `t.co`, `.onion`, non-HTTP schemes, a valid host with a
    non-post path, and a post path with a non-numeric ID.
  - `sanitize_handle`: unicode, path separators, `..`, an over-64-char handle, and empty → `unknown`.
  - `build_target`: correct name with and without an index, non-`mp4` extension passed through, and
    a containment failure raising `ValueError`.
  - No network. No ffmpeg. Pure functions and literal strings only.

- [X] **T010** [P] Write `tests/test_downloader.py` — the extractor wrapper.
  - **Literal info dicts as inputs. No mocking framework, no monkeypatching of yt-dlp internals.**
  - Entry branching: a `_type: "playlist"` dict with two entries yields two targets with `-1`/`-2`
    suffixes; a flat dict yields one unsuffixed target.
  - Handle resolution precedence: `uploader_id` present; only `uploader`; neither → `unknown`.
  - Error mapping: each of the six research-D6 strings maps to its expected diagnosis, and an
    unrecognized `DownloadError` message falls through to the generic case carrying the original
    text.
  - Assert the shared options builder always includes `allowed_extractors: ["twitter"]` — this is
    the Principle V regression guard, and it needs no network to check.

- [X] **T011** Run `uv run pytest` and confirm green. Two files, no network, no ffmpeg required.

  **Result — 2026-08-13**: `117 passed in 5.00s`. Exactly two files, no `conftest.py`.
  **Re-run after the Principle III and `_extension_of` fixes: `120 passed in 6.40s`**, same two
  files, `120 passed` again under the network/subprocess blocker.

  The no-network / no-ffmpeg claim was *measured*, not assumed: the suite was re-run with
  `socket.socket.connect`, `socket.create_connection`, `socket.getaddrinfo`, `subprocess.Popen`, and
  `subprocess.run` all replaced by hard errors. Still `117 passed`. The blocker was a throwaway
  plugin outside the repository, so no third file was added.

  **Finding raised while writing T010, fixed 2026-08-13** — `_extension_of` returned `.NA`, not
  `.mp4`, for an entry with no `ext`: yt-dlp's `prepare_filename` substitutes the literal `NA` for
  absent fields, so the suffix was non-empty and the `or ".mp4"` fallback never ran. Now both an
  empty suffix and `.NA` raise `ValueError`. **Chose refusing over defaulting** because `_promote`
  renames whatever actually landed to the name built from this value — so guessing `.mp4` would not
  produce an mp4, it would produce a webm called `.mp4`. `ValueError` specifically, so it reaches
  `cli.py`'s existing handler and exits 1 with a message instead of a traceback (verified).
  Still unreachable in practice; `extract_info` populates `ext` on every downloadable format.

  **Not covered, and not coverable under the no-mocking constraint**: `download_post`'s own loop —
  the `KeyboardInterrupt` and empty-`str(exc)` branches from the T008 fix, the ffmpeg preflight, and
  the `playlist_items` call. All require either a download or a mock. The pure parts around them
  (entry branching, target naming, error mapping, promotion, cleanup) are covered.

---

## Dependencies & Execution Order

```text
T001 ──> T002 [P] ──┐
    └──> T003 ──────┴──> T004 ──> T005 ──> T006 ──> T008 🚦 ──> T009 [P] ──┐
                     T007 [P] ────────────────────┘                T010 [P] ┴──> T011
```

- **T001** blocks everything — no dependencies resolve without `pyproject.toml`.
- **T002** and **T003** are parallel: different files, neither imports the other.
- **T004** needs T002 and T003 (imports both).
- **T005** needs T004 (imports `download_post`).
- **T006** needs T005 — it inspects both modules.
- **T007** is parallel with T004–T006; it touches only `.env.example` and reads `validation.py`.
- **T008** is the gate. Everything in Phase B must be done first.
- **T009** and **T010** are parallel: different files, and both only need the Phase B code to exist.
- **T011** needs both test files.

**Single-developer note**: `[P]` marks independence, not staffing (Principle: this is a
one-developer project).

---

## Implementation Strategy

1. **T001–T003** — skeleton and the security surface. Validation lands before anything can call it.
2. **T004–T005** — the capability and its shell.
3. **T006** — cheap structural check; catches a Principle III leak before manual verification.
4. **T008** — the real gate. If this does not pass, US1 is not done.
5. **T009–T011** — lock the two critical areas.

Commit after each task or logical group. Prefer extending an existing `backend/` module over
creating a new one.

---

## Phase D: User Story 2 — distinct no-video diagnoses (Priority: P2)

**Goal**: A URL that yields no video says *why*, in one of the four FR-004 categories.

**Independent Test**: run against a text-only post, an image-only post, a deleted post, and a
protected-account post; each produces a distinct, accurate explanation.

> **Read this before writing code — measured 2026-08-13, not assumed.**
> `_ERROR_DIAGNOSES` in `backend/downloader.py` **already produces all four FR-004 categories**, and
> five distinct messages rather than four:
>
> | FR-004 category | Current output | Status |
> |---|---|---|
> | post has no media | `this post has no video in it.` | ✅ already |
> | images but no video | `this post contains media, but it is not a video.` | ⚠️ **unreachable — see T013** |
> | not found / deleted | `this post could not be found. It may have been deleted.` | ✅ already |
> | not publicly accessible | protected-account **and** age-restricted variants | ✅ already |
>
> So this phase is mostly **verification plus one real defect**, not construction. Do not rebuild the
> table. The generic fallback that carried the HTTP 522 message during T008 stays exactly as it is.

- [X] **T012** [US2] Record the audit above as executed fact in this file, under a `Phase D result`
  heading. Run each of the six research-D6 strings through `_diagnose` and paste the actual output.
  **No code changes in this task** — its purpose is to stop US2 being rebuilt from scratch, and to
  prove the four categories exist before anything is added.

  ### Phase D result — audit executed 2026-08-13

  Each string run through the shipped `_diagnose`. Output pasted verbatim, no code changed:

  | yt-dlp message | `_diagnose` output |
  |---|---|
  | `No video could be found in this tweet` | this post has no video in it. |
  | `Media #2 is not a video` | this post contains media, but it is not a video. |
  | `You are not authorized to view this protected tweet` | this post belongs to a protected account and is not publicly accessible. This tool does not authenticate. |
  | `NSFW tweet requires authentication` | this post is age-restricted and is not publicly accessible. This tool does not authenticate. |
  | `Twitter API says: _Missing: No status found` | this post could not be found. It may have been deleted. |
  | `Requested tweet is unavailable` | this post could not be found. It may have been deleted. |
  | `HTTP Error 522` (unmatched) | could not extract video from this post. yt-dlp said: ERROR: unable to download video data: HTTP Error 522: Origin Connection Time-out |

  **Conclusion**: all four FR-004 categories already ship, as five distinct messages. The generic
  fallback still carries yt-dlp's own text verbatim for an unmatched string. **No new diagnosis
  strings were written in Phase D** — the only construction is T013, which makes row 2 reachable.

- [X] **T013** [US2] Fix the unreachable images-but-no-video diagnosis in `backend/validation.py`.
  - **The defect** (verified 2026-08-13): `parse_post_url` rebuilds `canonical_url` as
    `https://x.com/i/web/status/<id>`, which **discards the media index**. `_POST_PATH` never
    captures it. yt-dlp's own `TwitterIE` does capture it (`{'id': '20', 'index': '2'}`).
  - **Consequence**: `Media #<n> is not a video` — the *only* condition under which "images but no
    video" is distinguishable at all — can never be produced. The `("is not a video", …)` row of
    `_ERROR_DIAGNOSES` is dead code today.
  - Capture the index in `_POST_PATH` (`/(?:photo|video)/(?P<index>\d+)` suffix, optional), add it to
    the `PostReference` dataclass, and preserve it in `canonical_url` when present.
  - **Decision recorded in [ADR-0001](../../history/adr/0001-media-index-handling-in-url-canonicalisation.md)**
    — read it before starting. An indexed URL downloads *that one media item* rather than every video
    in the post; a bare URL keeps its current all-videos behaviour exactly (FR-017 is scoped to bare
    URLs).
  - ⚠️ **This task as originally drafted is Alternative C, which ADR-0001 rejects.** Preserving the
    index without also fixing the filename produces a silent wrong-file bug: `multiple` is false for
    a single-entry result, so `/video/1` and `/video/2` both yield `<handle>-<post-id>.<ext>`, and
    FR-016 then reports "Already downloaded" while handing over the wrong video. **The filename
    component is not optional** — when `media_index` is present it must become the filename's index
    component.
  - **Verify before implementing** (ADR-0001's one unverified assumption): whether yt-dlp returns a
    flat single entry or a one-item playlist for an indexed URL. Needs a network call, so confirm it
    in T016 rather than in a test.
  - Update `data-model.md`'s `PostReference` entry to match. That is a document, not a fifth module.

- [X] **T014** [P] [US2] Render diagnoses as complete sentences in `backend/cli.py`.
  - `_diagnose` returns lowercase-leading text on purpose, so `_partial_failure` can embed it
    mid-sentence (`"{reason} Files already saved: …"`). Capitalising inside `downloader.py` would
    break that composition.
  - Capitalise the first character at print time in `main()` instead. Presentation lives here
    (Principle III). One line; do not restructure the message pipeline.

- [X] **T015** [P] [US2] Extend the existing two test files — no third file.
  - `tests/test_validation.py`: index captured from `/photo/<n>` and `/video/<n>`, absent for a bare
    URL, and preserved through `canonical_url`. Pure string work, no network.
  - `tests/test_downloader.py`: assert the four FR-004 categories map to four *distinct* strings, and
    that an unmatched message still falls through to the generic case carrying yt-dlp's own text.
    The existing HTTP 522 test already guards the fallback — extend, do not duplicate.

- [ ] **T016** 🚦 [US2] **Manual verification.** Four real URLs — text-only post, image-only post,
  deleted post, protected account — plus one indexed `/photo/<n>` URL for T013. Confirm four distinct
  messages, exit 1, no file. Record actual output. **Do not fake the images-vs-nothing distinction
  for bare URLs**; a bare image-only post correctly reports "no video in it" (research D6, FR-004).

**Checkpoint**: US2 complete. Its diagnosis path is shared by US3, so Phase E depends on it.

---

## Phase E: User Story 3 — `--list-formats` (Priority: P2)

**Goal**: `xvd --list-formats <url>` shows the quality options and writes nothing.

**Independent Test**: run against a post with several qualities; options are displayed, no file is
created, exit 0.

- [X] **T017** [US3] Add `list_formats(url) -> FormatListing` to `backend/downloader.py`.
  - Reuses the existing `fetch_metadata` (`download=False`) — no bytes transferred, **no ffmpeg
    preflight, no output directory**.
  - Calls `parse_post_url` internally, exactly as `download_post` does, so the Principle V gate
    cannot be skipped (US3 acceptance scenario 3).
  - Applies the same `DownloadError` → `_diagnose` mapping, so a deleted or protected post gets the
    US2 explanation rather than an empty list. This is the Phase D dependency.
  - Returns `format_id`, `resolution`, `ext`, `filesize_approx`, and `tbr` **verbatim** from
    `info["formats"]` (research D4). No derived, reformatted, or invented fields — FR-007 requires
    the identifier to be passable straight back.
  - Framework-free: no printing, no `sys.exit`, no `argparse`. Warnings via the existing
    `on_warning` hook if any are needed.

- [X] **T018** [US3] Add `--list-formats` to `backend/cli.py`.
  - Mutually exclusive with `--format` (argparse enforces; that flag arrives in Phase F).
  - **The listing goes to stdout.** stdout normally carries output paths only (FR-018, SC-007), and
    here the listing *is* the output — there are no paths. State this in a comment so it does not
    read as a violation of the rule.
  - **Do not call `output_dir()` on this path.** It creates the directory as a side effect
    (`mkdir(parents=True, exist_ok=True)`), and listing must write nothing at all. `main()` currently
    resolves the output directory before anything else — reorder so this flag short-circuits first.
  - Exit 0 on a successful listing; the US2 diagnoses still exit 1.

- [X] **T019** [P] [US3] Extend `tests/test_downloader.py` — literal info dicts only.
  - A literal `info` with three formats yields three rows carrying the five fields unchanged.
  - A format missing `filesize_approx` or `tbr` is passed through as absent rather than crashing or
    substituting a zero.
  - A single-format post yields one row (US3 acceptance scenario 2).
  - **No test of the live `format_id` shape** — see T020.

- [ ] **T020** 🚦 [US3] **Manual verification.** A real multi-quality post and a real single-quality
  post. Confirm no file is written and no directory is created. **Closes research D4's deferred
  item**: record the actual `format_id` values a live X post returns, and confirm one of them passes
  back into `--format` in Phase F. This needs a network call, so it is manual by Principle II, not a
  test.

**Checkpoint**: US3 complete. Its listing output is the input to US4.

---

## Phase F: User Story 4 — `--format ID` (Priority: P3)

**Goal**: `xvd --format <id> <url>` downloads the quality the operator picked.

**Independent Test**: list qualities, request one by its displayed identifier, confirm the file
matches that option.

- [X] **T021** [US4] Add an optional `format_id` parameter to `download_post` in
  `backend/downloader.py`.
  - When supplied, it replaces `bestvideo+bestaudio/best` in the options builder. When absent,
    behaviour is byte-for-byte what it is today — US1 must not shift.
  - Passed through **verbatim**; do not validate, normalise, or second-guess the string. yt-dlp owns
    that vocabulary, and T020 recorded what real identifiers look like.
  - `allowed_extractors: ["twitter"]` still applies — a format override must not reopen research D8.
  - The extension still comes from `_extension_of`; a chosen format may well not be mp4.

- [X] **T022** [US4] Add the unavailable-format diagnosis to `_ERROR_DIAGNOSES` in
  `backend/downloader.py`.
  - FR-008 and US4 scenario 2: the message must name **what was requested and what is actually
    available**, exit 1, write no file.
  - The available list comes from the metadata pass already in hand — the same `info["formats"]`
    T017 reads. Do not make a second network call to build the error.
  - Add it as one more ordered row; the generic fallback stays last and stays untouched.

- [X] **T023** [US4] Add `--format ID` to `backend/cli.py`. Mutually exclusive with `--list-formats`.
  Pass the value straight to `download_post(format_id=…)`. No business logic here.

- [X] **T024** [P] [US4] Extend `tests/test_downloader.py` — no network.
  - The options builder uses the supplied `format_id` when given and the US1 default when not.
  - `allowed_extractors` survives a format override (Principle V regression guard, same shape as the
    existing test).
  - The unavailable-format message names both the requested id and the available ones, built from a
    literal `info` dict.

- [ ] **T025** 🚦 [US4] **Manual verification.** Take one `format_id` recorded in T020, download it,
  confirm the file matches that option. Then request a deliberately bogus id and confirm the message
  names both sides, exit 1, no file.

- [X] **T026** Re-run the full suite and T006's grep, both unmodified. Confirm: still exactly two
  test files, still four modules in `backend/`, both greps silent, all tests green.

  **Result — 2026-08-13**: `151 passed` (was 120); `151 passed` again with sockets and subprocess
  spawning hard-blocked. Both T006 greps silent, unmodified. Still exactly four modules in
  `backend/` and two files in `tests/`. No new dependencies.

  **Two deviations from the task text, both flagged rather than improvised:**

  1. **T022 was impossible as written.** It specified adding the unavailable-format diagnosis to
     `_ERROR_DIAGNOSES` "as one more ordered row", but every row in that table is a *static* string
     and FR-008 requires naming the requested id and the available ids. It is implemented as
     `_diagnose_format(error, requested, info)`, which sits in front of the table and delegates to
     `_diagnose` for everything else — so the table and its generic fallback are untouched.
  2. ~~**`--format` can still produce a mis-suffixed filename.**~~ **FIXED 2026-08-13** — see below.

  ### Deviation 2 resolved — format-aware extension, 2026-08-13

  `_extension_for_format(entry, format_id)` now derives the extension from the **chosen** format's
  own entry in `info["formats"]`, not from yt-dlp's default selection. Verified end-to-end against a
  post whose default is mp4 and whose `http-256` rendition is webm:

  | invocation | before | after |
  |---|---|---|
  | `--format http-256` (webm) | `someone-20.mp4` — a webm named mp4 | `someone-20.webm`, exit 0 |
  | `--format http-9999` | yt-dlp error, then FR-008 message | FR-008 message before any download, exit 0 files: none |

  An unknown id returns `None` and the caller reports the FR-008 unavailable-format message built by
  the shared `_unavailable_format_message` — **no fallback guess**. A format that exists but reports
  no container raises `ValueError`, matching `_extension_of`'s refusal to invent one.

  > ⚠️ **Behaviour change worth knowing about.** `--format` now accepts only literal `format_id`
  > values from the listing. yt-dlp *selector expressions* — `best`, `bestvideo+bestaudio`,
  > `137+140`, `bestvideo[height<=720]` — were passed through verbatim before and worked; they are
  > now rejected with the FR-008 message, because they are not ids in `info["formats"]` and no
  > container can be derived without guessing. This matches FR-007/FR-008 and the flag's own help
  > text ("download this specific format id (see `--list-formats`)"), and selectors were never
  > specified. **Recorded rather than decided** — if selector support is wanted, merge specs resolve
  > deterministically to `merge_output_format` (mp4) and could be handled without a guess.

---

## Phase D–F Dependencies

```text
Phase C (done) ──> T012 ──> T013 ──> T015 [P] ──> T016 🚦 ──> T017 ──> T018 ──> T019 [P] ──> T020 🚦 ──┐
                      └──> T014 [P] ──┘                                                                │
                                                                                                       v
                                              T021 ──> T022 ──> T023 ──> T024 [P] ──> T025 🚦 ──> T026
```

- **T012 first, always.** It is what stops US2 being rebuilt; four of its five diagnoses already ship.
- **T013 is the only real construction in Phase D**, and it is in `validation.py`, not `downloader.py`.
- **Phase E depends on Phase D** — US3 acceptance scenario 3 requires the US2 diagnoses to run before
  listing. This is why the three phases are ordered D → E → F rather than built independently.
- **Phase F depends on T020**, which is where real `format_id` values get recorded. Building `--format`
  before knowing what identifiers look like is guesswork.
- `[P]` marks independence, not staffing.

## Constraints carried into Phases D–F

- **Four modules only.** `validation.py`, `config.py`, `downloader.py`, `cli.py`. No new file in
  `backend/`.
- **`download_post` and `list_formats` stay framework-free**: no printing, no `sys.exit`, no
  `argparse`. Progress and warnings go through the existing `progress` / `on_warning` hooks.
- **Two test files only.** Extend `tests/test_validation.py` and `tests/test_downloader.py`.
- **Tests only where they need no network**, and only over URL validation or the extractor wrapper
  (Principle II). Live format listing is T020's job, not a test's.
- **Do not touch the two open T008 items** — the Windows Ctrl+C cleanup and the untested
  `playlist_items` path. Both stay recorded exactly as they are.

---

## Deferred — DO NOT BUILD

Explicitly **not** in this feature: Docker, CI pipelines, systemd units, logging configuration,
README authoring, deployment automation, `--force` re-download (FR-016 is resolved as
skip-and-succeed), and any HTTP layer. Deployment is the documented shell sequence in
`quickstart.md`, nothing more.
