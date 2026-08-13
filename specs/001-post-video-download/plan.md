# Implementation Plan: Single Post Video Download

**Branch**: `001-post-video-download` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-post-video-download/spec.md`

## Summary

Deliver User Story 1: a terminal command that takes one X post URL and writes a single playable
video file to the output directory. The capability lives in `backend/downloader.py` as a framework-free module;
`backend/cli.py` is an argparse shell that formats its result and picks an exit code. yt-dlp is used
as a library through the `YoutubeDL` class, with ffmpeg as the only system binary.

Two findings from Phase 0 shape the design beyond the brief:

- **`allowed_extractors: ["twitter"]` is a security requirement, not a tidiness one.** A post
  containing no video but carrying a link makes the extractor hand an author-chosen third-party URL
  back to yt-dlp (`twitter.py:1374-1380`). That happens *after* our allowlist gate. Constraining the
  loaded extractor set is what closes it (research D8).
- **The temp directory must live inside the output directory.** `shutil.move` is not atomic across
  filesystems, so promoting from `/tmp` can leave a partial file at the destination and violate
  FR-015. Placing temp inside the output dir makes `os.replace` atomic by construction (research D2).

A third correction was folded in after the initial plan: **the output extension must not be
hardcoded to `.mp4`.** `merge_output_format` applies only when a merge occurs, so a progressive
rendition taken by the `/best` fallback keeps its native container. Probe-verified. The extension
comes from `prepare_filename` on the processed info dict, and promotion moves the file that actually
landed in the temp directory rather than a path assembled from assumptions (research D3, corrected).

US2, US3, and US4 are scoped as follow-on phases and are not built now.

## Technical Context

**Language/Version**: Python 3.11+ (verified locally: 3.13.5)
**Primary Dependencies**: `yt-dlp` (library, via `YoutubeDL`; verified against 2026.7.4) — the only
runtime dependency. `argparse`, `pathlib`, `urllib.parse`, `shutil`, `tempfile`, `os`, `re` from the
standard library.
**System Binaries**: `ffmpeg`, located with `shutil.which`, never a hardcoded path
**Storage**: filesystem only — no database, no persistent state
**Testing**: `pytest`, two files, zero network calls (Principle II)
**Target Platform**: Linux VPS (deploy), Windows + PowerShell 5.1 (develop). All paths via `pathlib`.
**Project Type**: single `backend/` package (Principle I)
**Performance Goals**: none specified beyond SC-005 — progress visible within 3s of transfer start.
Throughput is bounded by the network and yt-dlp, not by our code.
**Constraints**: no HTTP layer, no `click`/`typer`/`rich`/`pydantic`, no subprocess invocation of
yt-dlp, no Docker/CI/systemd
**Scale/Scope**: one URL per invocation, by design (spec Constraints)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Initial evaluation** — passed, no violations.
**Post-design re-evaluation (after Phase 1)** — passed, no violations. Design strengthened gate V.

- [x] **I. Single Backend Folder** — four modules under `backend/`, `tests/` at root. No new
      top-level packages, no services, no frontend.
- [x] **II. Minimal Testing** — exactly two test files, covering URL validation and the extractor
      wrapper. No network tests, no mocking framework (literal info dicts instead of mocks). Manual
      CLI verification via `quickstart.md` is the primary gate. No test-per-FR.
- [x] **III. CLI-First, API-Later** — `download_post()` is framework-free, never prints, never calls
      `sys.exit`. `cli.py` holds argparse, formatting, and the exit-code map only. Validation is
      invoked *inside* `download_post`, so a future HTTP caller cannot skip it by forgetting.
- [x] **IV. Lean Dependencies** — one runtime dependency (`yt-dlp`) plus one dev dependency
      (`pytest`). argparse chosen over click/typer; stdlib covers the whole surface. No DB, queue,
      ORM, or auth library.
- [x] **V. Security Baseline (NON-NEGOTIABLE)** — exact host match against a frozen eight-hostname
      set before any network access (D1); no shell anywhere, yt-dlp used as a library; filename
      sanitized to `[A-Za-z0-9_-]` with a `Path.is_relative_to` containment assertion (D data-model).
      **Design addition**: `allowed_extractors: ["twitter"]` closes the post-validation extractor
      redirect (D8) — probe-verified to load exactly one extractor and zero others.
- [x] **VI. Simple Errors** — `ValueError` and yt-dlp's own `DownloadError` only; no custom
      hierarchy. Error strings mapped to plain messages by ordered substring match, with an honest
      generic fallback. No retry/backoff anywhere.
- [x] **VII. VPS-Deployable** — plain Linux VPS, `XVD_OUTPUT_DIR` with a CWD default, deployment is
      a five-line documented shell sequence. No cloud services, no Docker, no systemd.

## Project Structure

### Documentation (this feature)

```text
specs/001-post-video-download/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — 9 decisions, empirically verified
├── data-model.md        # Phase 1 output — in-process value objects
├── quickstart.md        # Phase 1 output — the manual verification script
├── contracts/
│   └── cli-interface.md # Phase 1 output — CLI + module boundary contract
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/sp.tasks — NOT created by /sp.plan)
```

### Source Code (repository root)

Fixed by Principle I.

```text
backend/
├── __init__.py
├── cli.py           # argparse; calls download_post; formats; sets exit code. No business logic.
├── downloader.py    # metadata fetch, target computation, atomic download, error mapping
├── validation.py    # host allowlist, post-ID parse, handle sanitization, path containment
└── config.py        # XVD_OUTPUT_DIR with CWD default

tests/
├── test_validation.py   # hosts, look-alikes, t.co, i/web, noise, sanitization, containment
└── test_downloader.py   # playlist-vs-flat branching, indexing, error-string mapping

pyproject.toml       # uv-managed; yt-dlp runtime, pytest dev, `xvd` entrypoint
.env.example         # XVD_OUTPUT_DIR
```

**Structure Decision**: Single `backend/` package per Principle I, with the four modules the user
fixed. `cli.py` imports from `downloader.py`; `downloader.py` imports from `validation.py` and
`config.py`; `validation.py` imports nothing from the package. The dependency graph is acyclic and
points away from the CLI, which is what makes the later HTTP layer a drop-in caller.

## Implementation Phases

### Phase A — Foundation (blocking)

1. `pyproject.toml` with uv, `yt-dlp` runtime dep, `pytest` dev dep, `xvd` console entrypoint.
2. `backend/config.py` — `output_dir(override)` resolving flag → env → CWD, creating if absent.
3. `backend/validation.py` — `parse_post_url`, `sanitize_handle`, `build_target`. This is the
   Principle V surface; it lands first and gets tested first.

### Phase B — User Story 1 (the deliverable)

4. `backend/downloader.py` — `fetch_metadata` via `extract_info(download=False)` with
   `allowed_extractors: ["twitter"]`; playlist-vs-flat branching; ffmpeg preflight; temp-dir-inside-
   output download; `os.replace` promotion; `try/finally` cleanup; error-string → diagnosis mapping.
5. `backend/cli.py` — argparse, progress hook to stderr, paths to stdout, exit-code map.
6. Manual verification: run `quickstart.md` steps 1-8.

### Phase C — Tests (the permitted handful)

7. `tests/test_validation.py`
8. `tests/test_downloader.py`

Written after the code they cover, per Principle II. They are not a gate on Phase B; the
`quickstart.md` run is.

### Follow-on phases (NOT built now)

| Phase | Story | Adds |
|---|---|---|
| D | US2 (P2) | Four distinct no-video diagnoses. Limited: "images vs nothing" is indistinguishable for bare URLs (research D6). |
| E | US3 (P2) | `--list-formats`, printing `format_id`/resolution/size without downloading. |
| F | US4 (P3) | `--format ID` passthrough plus an unavailable-format error. |

## Complexity Tracking

No Constitution Check violations. Table intentionally empty.

## Risks

- **yt-dlp error strings are unversioned.** The FR-004 mapping matches on message text, which can
  change between releases. Mitigated by ordered substring matching with a truthful generic
  fallback — a string change degrades a specific diagnosis, it does not break the download or
  produce a wrong answer. Only US2 depends on the fine-grained mapping.
- **X extractor breakage is upstream and unfixable by us.** X changes its API and yt-dlp follows.
  The response is `uv lock --upgrade-package yt-dlp`, not code. This is the strongest argument for
  keeping our own layer thin.
- **A stale `.tmp-*` directory can survive `SIGKILL`.** Inert and harmless, but visible in the
  operator's output directory. Accepted rather than adding startup sweep logic for a hypothetical.

## Spec amendments recommended

Surfaced by Phase 0; neither blocks implementation.

1. **Assumptions → accepted hostnames**: widen from five to eight, adding `m.twitter.com`,
   `m.x.com`, `mobile.x.com`. yt-dlp accepts all eight; the spec's list would reject three valid X
   URLs (research D1).
2. **FR-004 / US2**: record that "images but no video" cannot be distinguished from "no media at
   all" for bare post URLs with the current extractor (research D6).
