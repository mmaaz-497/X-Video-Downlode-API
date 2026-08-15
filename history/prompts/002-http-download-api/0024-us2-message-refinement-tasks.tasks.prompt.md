---
id: 0024
title: US2 Message Refinement Task Breakdown
stage: tasks
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.tasks
labels: ["us2", "failure-messages", "fr-010", "fr-029", "caller-facing-copy"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0003-caller-facing-disclosure-boundary.md
  pr: null
files:
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0024-us2-message-refinement-tasks.tasks.prompt.md
tests:
 - none run (planning artifact only)
---

## Prompt

Generate tasks for plan Phase 2 (US2) — per-code failure message refinement. This is the last unbuilt phase.

The mechanism already shipped in Phase 1: FAILURE_PREFIXES classification, FAILURE_MESSAGES catalog, and T014's drift test. US2 refines the caller-facing sentences, not the plumbing.

Scope:
- Review each sentence in FAILURE_MESSAGES for clarity to someone who is not the operator and does not know the service internals.
- Every sentence stays a source literal. None may interpolate a path, filename, URL, count, or any text originating outside the table — that constraint is what makes FR-029 hold structurally.
- The recorded FR-004 limitation stands: "images but no video" is not distinguishable from "no media at all" for bare post URLs. Do not write a sentence that implies a distinction the extractor cannot make.
- service_unavailable must still not tell the caller that ffmpeg is missing. That is an operator fault.

Constraints: frozen modules untouched, no new dependencies, tests/test_jobs.py only. T014's coverage assertions must still pass unchanged.

## Response snapshot

**7 tasks, T061–T067**, in two phases. One 🚦 stop point (T066). The smallest phase in the feature —
it changes the values of one dict — but the review turned up **two defects of fact, not of style**,
which is what makes it worth running.

**`service_unavailable` claims something untrue.** It ends *"The operator has been notified."*
Nothing notifies the operator. There is no alerting, no paging, no email — the condition goes to a log
a human may read eventually. Telling a caller they have been notified invites them to wait for a fix
nobody knows is needed. Worse than vagueness, and it shipped.

**`not_a_video` describes the wrong subject.** It reads *"This post contains media, but it is not a
video"*, but the code is reachable only through yt-dlp's `Media #<n> is not a video`
(`twitter.py:1359`), which fires **only when the caller supplied an explicit index**. A bare post URL
can never produce it. The truthful statement is about the item they asked for, not about the post —
and as written it implies the service characterised the post's contents, which is precisely what the
FR-004 limitation says it cannot do.

**The organising question is "can retrying ever help?"** — taken from US2's own story, which asks a
caller to tell *"this post will never work"* from *"something went wrong, try again"*. Every code
sorts into permanent (5), transient (2), or server-side (2), and a sentence that leaves a caller
unable to place their failure has failed at its only job however grammatical it is. T064 encodes the
classification as a frozenset so it is machine-checked rather than remembered.

**The FR-004 limitation constrains wording directly, and the risk runs the other way from usual.**
`no_video` currently reads "This post does not contain a video", which is already correct — for a bare
URL, yt-dlp cannot tell an image-only post from a text-only one (`twitter.py:1349`, research D6). The
hazard in this phase is a well-meaning edit *adding* "but it has images", which sounds more helpful and
would be a claim the service cannot support. T064 asserts against `"image"` and `"photo"` so a future
edit cannot reintroduce it.

**One rule shapes everything: US2 changes values, never keys.** Adding or renaming a code would move
`FAILURE_PREFIXES`, `_classify`, and T014's coverage assertions — that is plumbing, and the plumbing
shipped. If a task appears to need a new code, that is a research question about what the classifier
can distinguish, not a wording one, and the instruction is to stop and report.

**T064 also closes a gap that has been open since T012**: that every sentence is a source literal is
currently guaranteed by everyone having behaved. After a phase that edits the table by hand, it should
be guaranteed by a test — so T064 walks the dict with `ast` and asserts every value is a constant,
never an f-string or a `.format`. That is the structural half of FR-029.

**Also fixed while here**: the note recording the FR-033 logging fix cited "(T061)", a task ID that did
not exist and that this phase was about to claim. Corrected to say why that fix was deliberately not
numbered — it repaired an already-shipped requirement rather than delivering new scope.

## Outcome

- ✅ Impact: US2 is executable, and the audit that produced it already identified the two sentences worth changing. 7 tasks, one owner-run stop point.
- 🧪 Tests: none run — planning artifact. T064 specifies the new assertions; T014's four must pass unchanged.
- 📁 Files: specs/002-http-download-api/tasks.md
- 🔁 Next prompts: `/sp.implement` for T061–T065, stopping before T066. Then T050, T060, T067 close the feature.
- 🧠 Reflection: A "wording polish" phase turned out to contain a false statement to callers and a sentence aimed at the wrong subject. Reviewing copy against the mechanism that produces it — rather than reading it for tone — is what surfaced both.

## Evaluation notes (flywheel)

- Failure modes observed: caller-facing copy asserting a process the system does not perform ("the operator has been notified"), and copy whose subject did not match the condition that triggers it. Neither is visible without tracing each sentence back to the extractor path that reaches it.
- Graders run and results (PASS/FAIL): checklist format across all 67 tasks — PASS. ID continuity T061–T067, no gaps or duplicates — PASS. Dangling "(T061)" reference removed — PASS. Constraint compliance (no new dependencies, one test file, frozen modules untouched, T014 unedited, manual verification task) — PASS, each as a task bullet or T067's close-out check.
- Prompt variant (if applicable): scope plus three explicit prohibitions (no interpolation, no images/no-media distinction, no ffmpeg). The prohibitions did most of the work — two of the three map directly onto T064 assertions.
- Next experiment (smallest change to try): when a phase is "copy only", still trace each string back to the code path that emits it. Reading the nine sentences as prose would have caught neither defect.
