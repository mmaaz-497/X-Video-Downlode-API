# ADR-0001: Media-Index Handling in URL Canonicalisation

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Proposed
- **Date:** 2026-08-13
- **Feature:** 001-post-video-download
- **Context:** X post URLs may carry an explicit media index — `/status/<id>/photo/2`,
  `/status/<id>/video/1`. `parse_post_url` currently discards it: `_POST_PATH` never captures the
  index, and `canonical_url` is rebuilt as `https://x.com/i/web/status/<id>`. Verified 2026-08-13:

  ```text
  https://x.com/u/status/20/photo/2  ->  https://x.com/i/web/status/20
  _POST_PATH groups: {'id': 1}          yt-dlp TwitterIE groups: {'id': '20', 'index': '2'}
  ```

  Two consequences follow. First, `Media #<n> is not a video` — which research D6 identifies as the
  **only** condition under which "images but no video" is distinguishable from "no media at all" —
  can never be produced, so the `("is not a video", …)` row of `_ERROR_DIAGNOSES` is dead code and
  FR-004's second category is unsatisfiable. Second, an indexed URL is silently treated as a bare
  one, so the tool ignores a request the operator stated explicitly in the URL.

  Phase D (T013) proposes to fix this. Doing so changes what an indexed URL *means*, which touches
  FR-004, FR-012, FR-016, and FR-017 at once — hence this record rather than a code comment.

<!-- Significance checklist (ALL must be true to justify this ADR)
     1) Impact: Long-term consequence for architecture/platform/security?
        YES -- changes the URL contract and the PostReference shape, both load-bearing.
     2) Alternatives: Multiple viable options considered with tradeoffs?
        YES -- four, below; two of them are genuinely defensible.
     3) Scope: Cross-cutting concern (not an isolated detail)?
        YES -- validation.py, downloader.py, data-model.md, and four functional requirements.
-->

## Decision

Treat the media index as **part of the post reference**, not as URL noise. Five components, adopted
together:

- **Capture**: `_POST_PATH` in `backend/validation.py` gains an optional trailing group,
  `(?:/(?:photo|video)/(?P<index>\d+))?`, mirroring yt-dlp's own `TwitterIE` which already exposes
  an `index` group.
- **Model**: `PostReference` gains `media_index: int | None`. `data-model.md` updated to match.
- **Canonicalisation**: `canonical_url` preserves the index when present
  (`https://x.com/i/web/status/<id>/video/<n>`), and is unchanged for a bare URL.
- **Download semantics**: an indexed URL selects **that one media item**. A bare URL keeps today's
  behaviour exactly — every video in the post, per FR-017. FR-017 governs what "the post" means; an
  indexed URL is not a request for the post, it is a request for one item in it.
- **Filename**: when `media_index` is present it becomes the filename's index component, so
  `/video/2` yields `<handle>-<post-id>-2.<ext>`.

The fifth component is not cosmetic and is the reason this is one decision rather than four.
Without it, verified offline against literal info dicts on 2026-08-13:

```text
bare URL (2 videos)  ->  ['someone-20-1.mp4', 'someone-20-2.mp4']
indexed /video/1     ->  ['someone-20.mp4']
indexed /video/2     ->  ['someone-20.mp4']     <-- same filename
```

`download_post` derives the suffix from `multiple = len(entries) > 1`, so a single-entry result gets
no suffix at all. Combined with FR-016's skip-and-succeed, an operator who downloads `/video/1` and
then requests `/video/2` is told **"Already downloaded"** and exits 0 while holding the wrong video.
Preserving the index without also fixing the filename converts a missing diagnosis into a silent
wrong-file bug, which is strictly worse than the status quo.

## Consequences

### Positive

- FR-004's "images but no video" category becomes satisfiable for indexed URLs, and the dead
  `_ERROR_DIAGNOSES` row becomes live code rather than a comment about an unreachable branch.
- The tool honours what the URL actually says. An operator who pastes `/photo/2` gets an answer
  about media item 2, not about the post as a whole.
- FR-012 (no two files collide) is strengthened, not merely preserved: the collision demonstrated
  above exists in latent form the moment any single-entry path appears, and this closes it.
- `PostReference` carries the full parsed meaning of the URL, so no caller has to re-parse it —
  consistent with why the dataclass exists at all.

### Negative

- **The FR-017 reading is now load-bearing and must be stated.** "Post contains more than one video
  → download every one" is scoped to bare URLs. Anyone reading FR-017 alone will find this
  surprising, which is precisely why it is recorded here.
- **One behavioural break for existing users**: an indexed URL that today downloads all videos will
  download one. The feature has no released users, so the cost is documentation, not migration —
  but it is a break.
- Widens the Principle V surface. `_POST_PATH` is the security-critical regex; every change to it
  needs the T015 test coverage, and an optional trailing group is a new place for a mistake to hide.
- **One assumption is unverified and must be checked in T013 before implementing**: that yt-dlp
  returns a single flat entry (not a one-item playlist) for an indexed URL. If it returns a playlist,
  `multiple` stays false for a different reason and the filename component still applies — but the
  entry-branching path deserves a look. This cannot be confirmed without a network call, so it
  belongs to T016's manual verification, not to a test.
- Does nothing for bare URLs. An image-only post with no index still reports "no video in it", per
  the recorded FR-004 limitation. This ADR narrows that limitation; it does not remove it.

## Alternatives Considered

**Alternative A — Keep dropping the index (status quo), and delete the dead row.**
Accept that FR-004's images-vs-nothing distinction is permanently unsatisfiable, remove the
unreachable `("is not a video", …)` entry, and amend FR-004 to three categories.
*Pros*: smallest surface; no FR-017 interaction; no filename risk; Principle V regex untouched.
*Rejected because*: it resolves a defect by lowering the requirement, and it leaves the tool
ignoring information the operator explicitly put in the URL. Worth reconsidering only if T013's
unverified assumption above turns out badly.

**Alternative B — Dual URL: bare for download, indexed for diagnosis only.**
`PostReference` carries both `canonical_url` (bare) and a `diagnostic_url` (indexed), the latter
used only when the bare attempt yields no video.
*Pros*: FR-017 completely untouched; no filename change; no behavioural break.
*Rejected because*: it buys the diagnosis with a second extraction call on the error path and a
second URL that exists only to produce better error text — complexity that has to be explained
every time someone reads the dataclass. It also still ignores the operator's stated intent on the
success path.

**Alternative C — Preserve the index, leave filenames alone.**
This is T013 as originally drafted in `tasks.md`.
*Pros*: minimal diff; fixes the diagnosis.
*Rejected because*: it is the option that produces the silent wrong-file bug demonstrated above.
This alternative is the specific reason the ADR was written; the filename component is not
optional.

**Alternative D — Chosen: preserve the index, and use it as the filename index.**
*Pros*: fixes the diagnosis, honours the URL, and closes the collision in the same change.
*Cons*: the FR-017 scoping and the behavioural break, both recorded above.

## References

- Feature Spec: [spec.md](../../specs/001-post-video-download/spec.md) — FR-004 (and its recorded
  limitation), FR-012, FR-016, FR-017; US2 acceptance scenario 2
- Implementation Plan: [plan.md](../../specs/001-post-video-download/plan.md)
- Research: [research.md](../../specs/001-post-video-download/research.md) — D3 (handle and index
  groups in `TwitterIE._VALID_URL`), D6 (error diagnosis table and the images-vs-nothing limitation)
- Tasks: [tasks.md](../../specs/001-post-video-download/tasks.md) — T013 (implements this), T015
  (tests), T016 (manual verification of the unverified assumption)
- Related ADRs: none — this is the first ADR in this repository
- Evaluator Evidence: [PHR 0008](../prompts/001-post-video-download/0008-deferred-phases-d-e-f-tasks.tasks.prompt.md)
  (where the dropped index was found) and
  [PHR 0009](../prompts/001-post-video-download/0009-media-index-canonicalisation-adr.misc.prompt.md)
  (where the filename collision was demonstrated)
