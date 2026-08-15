---
id: 0025
title: US2 Message Refinement Implementation
stage: green
date: 2026-08-15
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["us2", "failure-messages", "fr-010", "fr-029", "mutation-testing"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0003-caller-facing-disclosure-boundary.md
  pr: null
files:
 - backend/jobs.py
 - tests/test_jobs.py
 - specs/002-http-download-api/data-model.md
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0025-us2-message-refinement.green.prompt.md
tests:
 - tests/test_jobs.py (7 added; 302 pass)
 - four mutations: f-string, .format, retry cue on a permanent code, restored "notified" claim
---

## Prompt

Execute T061 through T065. Stop before T066 — that needs three real posts including an indexed URL naming a photo, and I will run it.

Same constraints: frozen modules untouched, no new dependencies, tests/test_jobs.py only, values never keys.

Hold T064 to the standard the last phases set: mutation-verify it. Add an f-string to one FAILURE_MESSAGES value and confirm the AST check goes red, then revert. A structural guarantee that has never been shown to fail is the same thing T006 was.

## Response snapshot

**T061–T065 complete. 302 tests pass, 7 added. One commit.** Stopped before T066. Four sentences
changed, five untouched, **no key moved** — T014's four assertions passed unedited throughout.

**T061's audit was done against the extractor, not against the prose**, and that is what found both
defects. Reachability of `not_a_video` was verified in yt-dlp's source rather than assumed:
`twitter.py:1351` branches on `selected_index`, and the raise at `:1359` sits in the `else` a bare URL
never takes. Confirmed separately that the frozen `parse_post_url` accepts `/video/<n>` and
`/photo/<n>` and preserves them, so the code is reachable — just far narrower than its sentence
claimed.

**The two corrections:**

- `service_unavailable` no longer says "The operator has been notified." Nothing notifies anyone.
- `not_a_video` now describes the item the caller named rather than the post.

**And two completions**: `time_limit` and `unclassified` were truthful and gave no guidance, so both
now say whether retrying is worth it — the only decision a caller makes with this information.

**T064 mutation-verified four ways**, all reverted: an f-string interpolating `_output_dir`, a
`.format` call, a retry cue added to `protected_account`, and the "notified" claim restored. The first
two are the ones that mattered — "every sentence is a source literal" was an FR-029 guarantee that had
never been shown capable of failing, which is precisely the state feature 001's T006 was in when it
was signed off by eye. The test reads the dict **from the syntax tree rather than from the imported
module**, because by import time an f-string and a literal are both just `str`, and the f-string is
the one that could have carried a path.

**Two things found and deliberately not fixed**, both reported rather than improvised:

1. **An out-of-range index reports `unclassified`.** `twitter.py:1357` raises
   `Video #<n> is unavailable`, which matches no needle in `_ERROR_DIAGNOSES`, so a caller asking for
   `/video/9` on a two-video post is told "The download failed for an unexpected reason." Truthful but
   poor. Fixing it needs a new code, which would move the classifier and T014 — the plumbing this
   phase's own rule forbids touching.

2. **A test-only race, diagnosed rather than re-run until green.**
   `test_progress_updates_memory_but_not_disk` failed once with `PermissionError`, then passed nine
   consecutive runs. `_finish` sets the terminal state inside `_lock` and calls `persist()` after
   releasing it, so `_await_terminal` returns before `os.replace` completes and a test reading the
   record can catch Windows mid-swap. Production never reads its own records except in `recover()` at
   start-up, when no worker exists — so the race is in the test, and the fix belongs to whoever next
   touches `_await_terminal`.

`openapi.yaml` needed no edit: the only sentence it quotes is `no_video`'s, which was already correct
and deliberately unchanged.

## Outcome

- ✅ Impact: US2 complete. Every failure a caller can trigger now tells them which of three things happened — the post will never work, something went wrong and retrying may help, or the service is at fault and their link is fine.
- 🧪 Tests: 302 pass (7 added). Four mutations run and reverted, each turning the intended test red.
- 📁 Files: backend/jobs.py, tests/test_jobs.py, data-model.md, tasks.md
- 🔁 Next prompts: T066 — the owner runs it, and it needs an indexed URL naming a photo to reach `not_a_video`. Then T050, T060, T067 close the feature.
- 🧠 Reflection: A phase billed as wording polish contained a false statement to callers and a sentence aimed at the wrong subject. Reading the nine sentences as prose would have caught neither; tracing each back to the extractor branch that emits it caught both.

## Evaluation notes (flywheel)

- Failure modes observed: a structural guarantee (FR-029's "literals only") that had held for five phases without any test capable of detecting its violation; and caller-facing copy asserting a process the system does not perform.
- Graders run and results (PASS/FAIL): full suite 302 — PASS. Mutation, f-string — PASS (red). Mutation, `.format` — PASS (red). Mutation, retry cue on a permanent code — PASS (red). Mutation, "notified" claim — PASS (red). T014 unedited and green — PASS. Frozen modules untouched — PASS. No new dependencies — PASS. One test file — PASS. Keys unchanged — PASS.
- Prompt variant (if applicable): the mutation was specified in advance and by example ("add an f-string to one FAILURE_MESSAGES value"). Naming the mutation removed all ambiguity about what "verified" meant, and took under a minute to run.
- Next experiment (smallest change to try): for any invariant stated as "never interpolates / never contains / always literal", write the test that reads the *syntax* rather than the value. Three such invariants exist in this codebase and only one now has that test.
