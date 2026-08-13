---
id: 0009
title: Media Index Canonicalisation ADR
stage: misc
date: 2026-08-13
surface: agent
model: claude-opus-5
feature: 001-post-video-download
branch: 001-post-video-download
user: Muhammad Maaz
command: /sp.adr indexed-url-selects-one-media-item
labels: ["adr", "architecture", "url-canonicalisation", "FR-004", "FR-012", "FR-016", "FR-017"]
links:
  spec: specs/001-post-video-download/spec.md
  ticket: null
  adr: history/adr/0001-media-index-handling-in-url-canonicalisation.md
  pr: null
files:
 - history/adr/0001-media-index-handling-in-url-canonicalisation.md
 - specs/001-post-video-download/tasks.md
tests:
 - none (architecture record; no code changed)
 - filename-collision consequence demonstrated offline with literal info dicts
---

## Prompt

/sp.adr indexed-url-selects-one-media-item

Analyze planning artifacts and document architecturally significant decisions as ADRs.

The user has completed feature planning and needs to identify architecturally significant technical decisions from plan.md, document these decisions as Architecture Decision Records (ADRs), ensure team alignment on technical approach before implementation, and create a permanent, reviewable record of why decisions were made.

Architecture Decision Records capture decisions that impact how engineers write or structure software, have notable tradeoffs or alternatives, and will likely be questioned or revisited later.

Workflow: load planning context; extract architectural decisions as decision clusters (not atomic choices); check existing ADRs; apply the three-part significance test (impact, alternatives, scope); create ADRs for qualifying clusters using the template; report completion. Grader: the ADR must cluster related changes, list at least one alternative with rationale, include clear pros and cons, and stay concise but sufficient.

## Response snapshot

**One ADR created: ADR-0001 "Media-Index Handling in URL Canonicalisation".** Status Proposed. It is
the first ADR in the repository; `history/adr/` did not exist and was created.

**Tooling note**: the command specifies `create-adr.sh`, but this project has no `.specify/scripts/bash/`
directory and no ADR script under `powershell/` either. Fell back to the agent-native path — read
`.specify/templates/adr-template.md`, allocated ID 0001, filled every placeholder, wrote the file.

**The decision was clustered, not atomic.** Five components adopted together: capture the index in
`_POST_PATH`, add `media_index` to `PostReference`, preserve it in `canonical_url`, define indexed-URL
download semantics, and use the index as the filename component.

**Why the fifth component is what makes this an ADR rather than a code comment.** Demonstrated
offline with literal info dicts before writing anything:

```text
bare URL (2 videos)  ->  ['someone-20-1.mp4', 'someone-20-2.mp4']
indexed /video/1     ->  ['someone-20.mp4']
indexed /video/2     ->  ['someone-20.mp4']     <-- same filename
```

`download_post` derives the suffix from `multiple = len(entries) > 1`, so a single-entry result gets
no suffix. With FR-016's skip-and-succeed, an operator who downloads `/video/1` then requests
`/video/2` is told **"Already downloaded"**, exits 0, and holds the wrong video. So T013 **as drafted
in tasks.md is Alternative C, which this ADR rejects** — preserving the index without fixing the
filename turns a missing diagnosis into a silent wrong-file bug, strictly worse than the status quo.
T013 has been annotated in place with that warning.

**Four alternatives recorded**, two genuinely defensible: keep dropping the index and lower FR-004
to three categories (A); dual URL, bare for download and indexed for diagnosis only (B); preserve
without the filename fix (C, rejected as above); preserve plus filename index (D, chosen).

**One assumption left explicitly unverified**: whether yt-dlp returns a flat entry or a one-item
playlist for an indexed URL. It needs a network call, so it is assigned to T016's manual
verification rather than guessed at or hidden.

**Only one ADR, deliberately.** The other Phase D–F decisions — the `list_formats` field set and
verbatim `--format` passthrough — are already settled in research D4 and are single-component
choices with no live alternatives. Writing ADRs for them would be the over-granularity the
command's own grader warns against.

## Outcome

- ✅ Impact: the decision that changes what an indexed URL *means* is recorded before it is
  implemented, together with the silent-wrong-file failure it would cause if implemented as drafted.
- 🧪 Tests: none — architecture record. The collision consequence was demonstrated offline rather
  than asserted.
- 📁 Files: `history/adr/0001-media-index-handling-in-url-canonicalisation.md` (new),
  `specs/001-post-video-download/tasks.md` (T013 annotated with the ADR link and the warning).
- 🔁 Next prompts: accept or revise ADR-0001, then `/sp.implement` Phase D; if T016 shows the
  playlist assumption is wrong, revisit Alternative A.
- 🧠 Reflection: the ADR was worth writing for a reason that only appeared while writing it. The
  brief was "record the FR-017 interaction"; the filename collision was found by checking what
  `multiple` actually evaluates to, and it is the more serious of the two.

## Evaluation notes (flywheel)

- Failure modes observed: a planned task that fixes one defect while opening a worse one, invisible
  until the filename derivation is traced; a command referencing a helper script this project does
  not ship.
- Graders run and results (PASS/FAIL): clustered not atomic PASS (5 components, 3 files, 4 FRs);
  alternatives with rationale PASS (4, each with pros/cons); positive and negative consequences PASS
  (4 and 5); no unresolved placeholders PASS; over-granularity avoided PASS (1 ADR, not 3).
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): when an ADR rejects a drafted task, annotate the task in
  place rather than relying on the reader to open the ADR first — done here for T013.
