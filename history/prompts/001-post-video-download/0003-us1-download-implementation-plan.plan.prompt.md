---
id: 0003
title: US1 Download Implementation Plan
stage: plan
date: 2026-08-12
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.plan
labels: ["plan", "research", "yt-dlp", "security", "atomicity", "cli"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-post-video-download/plan.md
  - specs/001-post-video-download/research.md
  - specs/001-post-video-download/data-model.md
  - specs/001-post-video-download/contracts/cli-interface.md
  - specs/001-post-video-download/quickstart.md
  - specs/001-post-video-download/spec.md
  - CLAUDE.md
tests:
  - none (planning-only change; no executable code introduced)
  - offline yt-dlp probe script run for verification (scratchpad, not committed)
---

## Prompt

STACK:
- Python 3.11+, dependencies managed with uv
- yt-dlp used as a Python library via the YoutubeDL class. Never as a subprocess.
- ffmpeg as the only system binary, used by yt-dlp for muxing
- argparse from the standard library for the CLI. Do NOT add click, typer, or rich — Principle IV requires justifying dependencies against the stdlib, and argparse covers this fully.
- No FastAPI, no HTTP server, no pydantic in this feature. The HTTP layer is a separate future feature and must not appear in this plan.

ENVIRONMENT NOTE (important):
Development happens on Windows with PowerShell 5.1. Deployment target is a Linux VPS. All path handling must use pathlib, never string concatenation or hardcoded separators. ffmpeg must be located via shutil.which rather than an assumed path, and its absence must be reported as a missing prerequisite (per the spec's edge case) before any download starts, not discovered mid-mux.

MODULE LAYOUT (fixed by Constitution Principle I and III):
backend/
  cli.py          — argparse setup, calls downloader, formats output, sets exit code. No business logic.
  downloader.py   — core logic, framework-free: metadata extraction, format selection, download, muxing
  validation.py   — URL host allowlist, post reference extraction, filename sanitization
  config.py       — env vars with defaults
tests/            — only the two areas Principle II permits

KEY TECHNICAL DECISIONS to resolve in research.md:

1. URL validation: parse with urllib.parse, compare the netloc exactly against the accepted hostname set from the spec's Assumptions, after stripping a port if present. Extract author handle and post ID from the path with a compiled regex. Query parameters and trailing slashes are stripped before matching, per the spec's URL-noise edge case.

2. Atomic output (FR-015): yt-dlp must write to a temporary working directory, and the finished file is moved into the output directory only after a successful mux. A KeyboardInterrupt or any exception must clean up the temp directory. Verify whether shutil.move is atomic across filesystems here — if the temp dir and output dir can be on different volumes, the temp dir must be created inside the output directory instead.

3. Idempotent skip (FR-016): the target output path must be computed from post metadata BEFORE the download begins, so an existing file short-circuits without fetching video data. Determine the minimum yt-dlp call that yields author, post ID, and extension without downloading — extract_info with download=False.

4. Format selection: default to bestvideo+bestaudio/best with merge_output_format mp4. Research how yt-dlp reports formats for X's HLS streams so that the listing in US3 shows a stable, passable format_id.

5. Multi-video posts (FR-017): research how yt-dlp represents an X post carrying several videos — playlist entries or separate formats — since the indexed filename scheme depends on it.

6. Error diagnosis (FR-004): yt-dlp raises DownloadError with message text for private accounts, deleted posts, and posts with no video. Research the actual message strings for each case so they can be mapped to the four distinct explanations. Do not build an exception hierarchy — map to plain messages per Principle VI.

7. Progress (FR-014): use yt-dlp's progress_hooks, writing to stderr per the spec's Assumptions. Suppress yt-dlp's own stdout noise with the quiet and no_warnings options.

SCOPE CONTROL:
- Plan for User Story 1 as the deliverable. US2, US3, and US4 should be structured as follow-on phases, not built now.
- Per Constitution Principle II, produce at most a handful of test tasks covering only URL validation and the extractor wrapper. Do NOT generate a test task per functional requirement. Do NOT plan any test that makes a network request.
- Do not plan Docker, CI, systemd units, logging configuration, or deployment automation. Deployment is a documented shell sequence, nothing more.

## Response snapshot

Produced five planning artifacts for US1. All seven technical questions were resolved by
**installing yt-dlp 2026.7.4 into a throwaway uv venv, reading the installed extractor source, and
running an offline probe script** — zero network requests, nothing recalled from memory
(Authoritative Source Mandate).

**Constitution Check: all 7 gates pass, both initial and post-design. Complexity Tracking empty.**

### Two findings that changed the design

**1. `allowed_extractors: ["twitter"]` is a security control, not tidiness.** At
`twitter.py:1374-1380`, a post with no video but carrying a link does
`return self.url_result(expanded_url, ...)` — handing an **author-chosen third-party URL back to
yt-dlp for extraction**, *after* our allowlist gate has passed. Pasting a link-bearing X post URL
could make yt-dlp fetch from any site on the internet. Probe-verified that
`allowed_extractors: ["twitter"]` loads exactly one extractor and zero others (the value is matched
as an anchored full match, so `twitter:card`, `twitter:spaces`, and `twitter:shortener` are all
excluded). This single option closes the redirect hole, blocks `t.co` at the library level, and
enforces the "no Spaces audio" scope boundary.

**2. The temp directory must live inside the output directory.** This was the user's open question
and the answer is that `shutil.move` is unusable as the promotion step — it degrades to copy-then-
delete across filesystems, so a crash mid-copy leaves a partial file at the destination, violating
FR-015. `/tmp` on a separate volume from `~/videos` is the normal VPS case. Creating the temp dir
inside the output directory makes same-filesystem true by construction, which makes `os.replace`
atomic on both POSIX (`rename(2)`) and Windows (`MoveFileEx`), and removes the cross-device question
rather than detecting it at runtime.

### Other resolved questions

- **Multi-video (Q5)**: confirmed **playlist entries**, not extra formats —
  `twitter.py:1384-1390` returns `playlist_result(entries)` only when `len(entries) > 1`, a flat
  dict otherwise. Branch on `_type == "playlist"`; yt-dlp's own 1-based `#N` index matches our
  filename suffix.
- **Author handle (Q3)**: `TwitterIE._VALID_URL` exposes only `id` and `index` — **no author group
  at all**. Probe confirms `x.com/i/web/status/20` is a valid, handle-free URL form. The filename's
  handle must come from metadata `uploader_id`, not the URL, with fallbacks to `uploader` then
  `"unknown"`.
- **Error strings (Q6)**: six exact source strings mapped to the four diagnoses. Matching is ordered
  substring, case-insensitive, with a truthful generic fallback, because these strings are
  unversioned third-party text.
- **Progress (Q7)**: `noprogress: True` plus our own hook to stderr; `total_bytes` is absent for HLS
  formats so the hook must fall back to `total_bytes_estimate`.

### Two spec defects found and amended

1. **The accepted-hostname list was wrong.** The spec listed five; the extractor's own
   `_BASE_REGEX` accepts eight (`{"", www., m., mobile.} × {twitter.com, x.com}`). Probe confirms
   `m.x.com`, `mobile.x.com`, and `m.twitter.com` are real working X URLs the spec would have
   rejected. Widened in `spec.md` with an inline amendment note; still exact-match, so no security
   is lost.
2. **FR-004 promises a distinction the extractor cannot make.** For a bare post URL, "images but no
   video" collapses into the same message as "no media at all" — the extractor filters photos at
   `twitter.py:1349` without reporting what it removed. Recorded as a known limitation on FR-004;
   affects US2 only, not US1.

### Artifacts

| File | Contents |
|---|---|
| `research.md` | 9 decisions (7 asked + 2 discovered), each with rationale, alternatives, and source line references |
| `data-model.md` | 5 frozen dataclasses, no DB; filename/sanitization/exit-code rules |
| `contracts/cli-interface.md` | CLI surface, exit codes, worked console examples, module boundary |
| `quickstart.md` | 8-step manual verification script — the primary validation gate |
| `plan.md` | Constitution Check, structure, phases A-C plus deferred D-F, risks |

`contracts/` holds a CLI contract rather than OpenAPI, with the deviation stated inline: Principle
III makes the CLI the contract and forbids an HTTP layer in this feature.

### Tooling defect encountered

`update-agent-context.ps1` reported "✓ Updated" and listed three additions it **did not make** — the
only change it wrote to `CLAUDE.md` was a UTF-8 BOM plus a full LF→CRLF conversion (210 lines
rewritten for zero content). Reverted the churn with `git checkout`, then added the Active
Technologies section by hand between manual-addition markers: 29 insertions, no BOM, no line-ending
change.

## Outcome

- ✅ Impact: US1 fully planned against 7 passing constitution gates; a post-validation SSRF-shaped
  hole and a non-atomic-promotion bug caught at design time; two spec defects corrected before they
  reached code.
- 🧪 Tests: None written. Planning-only. An offline yt-dlp probe script was run in the scratchpad for
  verification and is not committed.
- 📁 Files: `plan.md`, `research.md`, `data-model.md`, `contracts/cli-interface.md`, `quickstart.md`
  (all new); `spec.md` (two amendments); `CLAUDE.md` (Active Technologies section).
- 🔁 Next prompts: `/sp.tasks` to generate the dependency-ordered task list for phases A-C. Consider
  `/sp.adr` for the two design decisions flagged below.
- 🧠 Reflection: The two most valuable findings came from reading the extractor source rather than
  answering the question as asked. Q5 asked *how* multi-video is represented; the source also
  revealed the `url_result` redirect three lines away. Installing the dependency and grepping it
  cost about two minutes and produced facts no amount of reasoning would have yielded.

## Evaluation notes (flywheel)

- Failure modes observed: `update-agent-context.ps1` silently no-ops on a hand-authored `CLAUDE.md`
  lacking its expected marker sections, while reporting success and enumerating additions it never
  wrote — a false-success report that would go unnoticed without diffing. It also rewrites file
  encoding (BOM + CRLF) as a side effect. Second SpecKit script defect this session, after the
  PowerShell 5.1 `Join-Path` bug in `create-new-feature.ps1`.
- Graders run and results (PASS/FAIL): unresolved-placeholder scan on `plan.md` PASS; all 7
  constitution gates PASS at both checkpoints; `CLAUDE.md` encoding unchanged after manual edit
  PASS; 5/5 expected artifacts present PASS.
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): after any SpecKit script reports success, diff the files
  it claims to have touched before trusting the report — two of two scripts this session reported
  outcomes that did not match what they wrote.
