---
id: 0002
title: Single Post Video Download Spec
stage: spec
date: 2026-08-12
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.specify
labels: ["spec", "cli", "video-download", "url-validation", "x-twitter"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: null
  pr: null
files:
  - specs/001-post-video-download/spec.md
  - specs/001-post-video-download/checklists/requirements.md
  - .specify/scripts/powershell/create-new-feature.ps1
tests:
  - none (specification-only change; no executable code introduced)
---

## Prompt

Build a command-line tool that downloads a video from a single X (formerly Twitter) post URL onto the local machine.

WHY: I need to reliably save videos from X posts for offline viewing. The browser gives no download option, and existing web-based downloaders are unreliable, ad-heavy, and cannot be trusted with URLs. I want a tool I control, running on my own machine and later on my own VPS.

PRIMARY USER STORY:
As the operator, I paste an X post URL into my terminal, press enter, and after a short wait a playable video file exists in my output directory. I need to see enough progress output to know it is working and not hung.

FUNCTIONAL REQUIREMENTS:

1. Accept a single X post URL as a command-line argument.
2. Reject any URL that is not an X or Twitter post URL, with a clear message explaining why, before any network request is made.
3. Detect when a post contains no video (text-only post, images only, deleted post, or a protected/private account) and report that specific reason rather than failing generically.
4. By default, download the highest quality video available for that post.
5. Allow the operator to list the available quality options for a post without downloading, so a smaller version can be chosen deliberately.
6. Allow the operator to select a specific quality when downloading.
7. Produce a single playable video file. If the source provides video and audio as separate streams, they must be combined into one file — a silent video is a failed download.
8. Name the output file predictably and safely, derived from the post author and post ID rather than the post text, so filenames are never surprising and never collide between different posts.
9. Allow the operator to specify the output directory. Default to a sensible location when not specified.
10. Show download progress while running.
11. Never leave a partial or corrupt file in place when a download fails or is interrupted. Either a complete file exists or no file exists.
12. Exit with a success status on completion and a distinct failure status on error, so the command can be scripted later.

ACCEPTANCE SCENARIOS:
- Given a public X post containing a video, when I run the command with its URL, then a complete playable file with both video and audio appears in the output directory.
- Given a public X post containing only text, when I run the command, then I see a message saying the post has no video, no file is created, and the command exits with a failure status.
- Given a URL from a site other than X or Twitter, when I run the command, then it is rejected immediately with an explanation and no network request occurs.
- Given a running download that I interrupt with Ctrl+C, when I check the output directory, then no partial file remains.
- Given a post whose video is available in several qualities, when I list qualities and then request a specific one, then the file I get matches the quality I requested.

EXPLICITLY OUT OF SCOPE for this feature:
- Any HTTP API, web server, or web interface. This is a terminal tool only.
- Downloading threads, multiple posts, user timelines, or bulk/batch operations.
- Authentication, login, cookies, or access to private/protected accounts.
- Any user interface, database, job queue, or persistent state.
- Downloading images, GIFs, or Spaces audio. Video only.
- Rate limiting, proxying, or IP rotation.

CONSTRAINT: This tool is for saving content the operator has a legitimate reason to save. It handles one URL at a time by design and must not be built into a scraping tool.

## Response snapshot

Created branch `001-post-video-download` and wrote `specs/001-post-video-download/spec.md` —
**19 functional requirements, 9 success criteria, 4 prioritized user stories**.

**User stories** (each independently testable):

- **US1 (P1) 🎯 MVP** — Save a video from a post URL. Absorbs URL rejection, Ctrl+C safety, progress
  output, and audio/video muxing as acceptance scenarios, so shipping US1 alone solves the operator's
  actual problem.
- **US2 (P2)** — Understand why a post yields no video (four distinct diagnoses).
- **US3 (P2)** — Inspect available qualities without downloading.
- **US4 (P3)** — Download a chosen quality.

**Hardening added beyond the brief.** Three gaps were found while validating the spec against its own
quality checklist:

1. FR-002 as briefed ("reject non-X URLs") would have been satisfied by a substring check that accepts
   `x.com.example.net`. Added **FR-003** mandating exact host matching, plus edge cases for look-alike
   hostnames and `t.co` shorteners — a shortener must be rejected *without* following the redirect,
   since resolving it would itself be the network request FR-002 forbids.
2. Resume behavior was neither in scope nor out of scope, though FR-015 (no partial files) implies its
   absence. Moved to Out of Scope explicitly.
3. Seven unstated defaults were resolved and recorded in an **Assumptions** section rather than left
   implicit: output directory (CWD), filename shape, meaning of "highest quality" (resolution, bitrate
   tie-break), exit codes (0/1), progress on the error stream, selector identifiers, and the exact
   accepted hostname list.

**Two clarifications escalated to the operator** — both chosen deliberately over any default:

- **FR-016** (output file already exists) → *skip and exit success*. Re-running a URL is idempotent
  and fetches no content already on disk. → US1 scenario 6, SC-009.
- **FR-017** (post with multiple videos) → *download all, indexed filenames*. → US1 scenario 7,
  filename shape extended with `-<n>`, SC-008 widened to within-post collisions.

Resolving FR-017 exposed an ambiguity in FR-015: if one video of several fails, are the completed
ones kept? FR-015 now states the per-file rule — completed files survive, the failed one leaves
nothing.

**Incidental fix**: `create-new-feature.ps1:267` called `Join-Path` with three path segments, which
requires PowerShell 7+. Under Windows PowerShell 5.1 it threw `PositionalParameterNotFound` after the
branch and spec were already created, skipping the `history/prompts/<branch>/` step. Replaced with
nested two-argument calls. This would have broken every subsequent `/sp.specify` run on this machine.

Checklist at `specs/001-post-video-download/checklists/requirements.md`: **all 16 items pass**, with
both validation iterations recorded.

## Outcome

- ✅ Impact: Feature 001 specified and ready for planning; 19 testable FRs with no unresolved
  clarifications; a URL-validation bypass and an FR-015/FR-017 ambiguity caught at spec time rather
  than in code.
- 🧪 Tests: None. Specification-only change; no executable code introduced.
- 📁 Files: `specs/001-post-video-download/spec.md` (written),
  `specs/001-post-video-download/checklists/requirements.md` (written),
  `.specify/scripts/powershell/create-new-feature.ps1` (PS 5.1 `Join-Path` fix).
- 🔁 Next prompts: `/sp.plan` to design the `backend/` module layout against the Constitution Check
  gate. `/sp.clarify` is not needed — zero markers remain.
- 🧠 Reflection: The brief was unusually complete, so the value added was adversarial rather than
  generative — asking what a lazy implementation could do while still passing each requirement as
  written. That framing is what surfaced the substring-matching hole in FR-002.

## Evaluation notes (flywheel)

- Failure modes observed: `create-new-feature.ps1` is PowerShell 7-only at one line and failed
  mid-run on Windows PowerShell 5.1, leaving partial scaffolding (branch and spec created, prompts
  directory missing). The script is documented as run-once per feature, so recovery was manual
  completion of the skipped step rather than a re-run.
- Graders run and results (PASS/FAIL): `NEEDS CLARIFICATION` count 0 PASS; unfilled template
  placeholder scan PASS; FR/SC counts (19/9) consistent with checklist PASS; all 16 checklist items
  PASS.
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): have `/sp.specify` verify the scaffold script's outputs
  (branch, spec file, prompts dir) all exist before writing the spec, so a partial script failure is
  detected immediately rather than at PHR-writing time.
