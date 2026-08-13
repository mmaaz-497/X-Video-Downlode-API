# ADR-0003: Caller-Facing Disclosure Boundary

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Proposed
- **Date:** 2026-08-13
- **Feature:** 002-http-download-api
- **Context:** Feature 001 was a single-operator CLI, so its error messages were written to be
  maximally useful to the person who ran it — they name absolute paths, quote yt-dlp verbatim, and
  describe server-side conditions. Feature 002 puts the same code behind an API reachable from the
  public internet by people who are not the operator. **Every one of those messages is now a
  disclosure**, and `downloader.py` is frozen, so they cannot be changed at the source.

  Verified leak sources in the frozen modules:

  ```text
  backend/downloader.py:332-334   _promote's RuntimeError names the temp directory,
                                  folded into DownloadOutcome.message by the generic
                                  handler at :529-535
  backend/downloader.py:309-312   the cleanup warning names the temp directory
  backend/validation.py:186-188   the containment check names both the candidate path
                                  and the output root, and reaches the API as ValueError
  backend/downloader.py:132       the generic diagnosis embeds yt-dlp's verbatim text
  ```

  At the same time FR-010 requires the *opposite* pressure: the distinct diagnoses feature 001
  already produces must reach callers as separate machine-readable codes, not as one generic error.
  So the boundary must let the *meaning* through while stopping the *text*. That tension is why this
  is one decision and not several.

<!-- Significance checklist (ALL must be true to justify this ADR)
     1) Impact: Long-term consequence for architecture/platform/security?
        YES -- it is the security boundary of a public service, and it constrains what any future
        contributor is permitted to add to a data structure.
     2) Alternatives: Multiple viable options considered with tradeoffs?
        YES -- four, below; the sanitiser (A) is the option most teams would reach for first, and
        the clean fix (B) is the one explicitly forbidden.
     3) Scope: Cross-cutting concern (not an isolated detail)?
        YES -- jobs.py, api.py, the Job record shape, the OpenAPI contract, FR-005, FR-009, FR-010,
        FR-011, FR-028, FR-029, and SC-005.
-->

## Decision

Leakage is prevented by **the shape of the data structure**, not by remembering to sanitise. Five
components, adopted together:

- **The `Job` record has no field capable of holding raw text.** It carries `failure_code: str | None`
  and nothing else about the failure. `DownloadOutcome.message` is passed to the logger at the single
  call site where `download_post` returns, and then discarded.
- **One catalog of literals.** `FAILURE_MESSAGES: dict[str, str]` maps each code to a fixed English
  sentence. Every sentence is a literal in our source; none interpolates a path, a filename, a URL,
  a count, or any text originating outside the table.
- **Classification is prefix-exact.** `FAILURE_PREFIXES` matches `DownloadOutcome.message` with
  `str.startswith` against the explanation strings in `downloader._ERROR_DIAGNOSES`. This is exact
  rather than heuristic because `_partial_failure` composes the message as
  `f"{reason} Files already saved: {names}"` (`backend/downloader.py:559`), so the diagnosis is
  always a prefix.
- **Drift fails loudly.** `tests/test_jobs.py` imports the private `downloader._ERROR_DIAGNOSES` and
  asserts every explanation in it is covered by exactly one entry in our map. An upstream edit to
  that table breaks the build instead of silently degrading classification to `unclassified` — which
  is also why `unclassified` is specified as a *visible* outcome (FR-011) rather than a quiet
  default.
- **One refusal for every unresolvable handle.** Unknown, malformed, and wrong-length handles all
  return an identical `404 {"code":"not_found","message":"No such job."}`, so a caller cannot learn
  whether a handle names a real job (FR-028). The replaced 422 handler and a catch-all exception
  handler return fixed bodies for the same reason.

The interaction with the capability model (spec Q1) is deliberate and worth stating: because holding
the handle *is* the authorization, a caller who holds one is entitled to know *which* state their job
is in — so `409 not_ready` versus `409 failed` versus `410 expired` may legitimately differ. Only
handles that resolve to nothing collapse into a single response. The boundary hides server internals,
not the caller's own job.

## Consequences

### Positive

- **The guarantee is checkable by reading one dataclass.** "Can a path reach a caller?" is answered
  by looking at the record's fields, not by auditing every handler, log line, and exception path.
  Adding a raw-text field would be a visible change to a reviewed data structure — an argument
  someone has to make, not an accident someone can have.
- **Future contributors cannot regress it casually.** There is nothing to forward. A handler that
  wanted to leak would first have to add a field and thread it through, which is exactly the level of
  friction the constitution's Principle V posture wants at a security boundary.
- **FR-010 and FR-029 stop fighting.** The meaning crosses the boundary as a code; the text does not
  cross at all. Neither requirement is compromised to satisfy the other.
- **Operator diagnostics improve rather than degrade.** The full message, including the paths, goes
  to the log with the job handle attached, so FR-033's correlation requirement is met and the
  operator sees strictly more than the CLI showed.
- **SC-005 becomes a mechanical test**, not a judgement call: grep every response the service can
  produce for path-shaped and traceback-shaped strings.

### Negative

- **Classification duplicates knowledge of a private table in a frozen module.** `_ERROR_DIAGNOSES`
  is not part of `downloader.py`'s public surface, and matching its wording from outside is coupling
  that no amount of care makes clean. The coverage test converts silent decay into a loud failure; it
  does not make the coupling go away.
- **Two codes are set by us, not classified**, and the asymmetry has to be remembered: `time_limit`
  comes from the jobs layer's own flag and `interrupted` can come from restart recovery. Trying to
  detect the timeout by parsing text would fail anyway, since `download_post` rewraps it as
  `f"download failed for video {position}: {detail}"` (`backend/downloader.py:534`).
- **Callers get less than the CLI operator gets, on purpose.** A post that fails for an unusual
  reason yields "The download failed for an unexpected reason." and nothing more. That is a genuinely
  worse experience for a legitimate caller, accepted because the alternative discloses server
  internals to everyone.
- **`service_unavailable` deliberately misdescribes the cause to the caller.** A missing `ffmpeg` is
  an operator fault, and the caller is told the service cannot process downloads rather than what is
  actually wrong. This is correct for a public API and confusing for whoever is debugging it — the
  log is the place to look, and that fact needs to be in the runbook.
- **FR-028's "no observable timing difference" is argued, not enforced.** Lookup is a single dict hit
  on the full 43-character key and both branches return the same response object, so there is no
  secret-dependent branch to measure — but no constant-time comparison is used, and none is claimed.
  The defence is the 256-bit search space, which makes collecting a timing signal infeasible. Stating
  the real reason is better than asserting a property Python's dict internals would not guarantee.

## Alternatives Considered

**Alternative A — Store the message, sanitise it on the way out.**
Keep `DownloadOutcome.message` on the record and run it through a scrubber (strip anything matching a
path pattern, truncate, drop after a colon) in the response serializer.
*Pros*: preserves detail for the cases where it is harmless; one function to write; the obvious first
instinct.
*Rejected because*: it makes safety a property of a code path rather than of the data. Every new
response shape, every log-to-response copy-paste, and every future `include_detail=True` debugging
flag is a fresh opportunity to bypass it, and the failure is silent when it happens. A scrubber also
has to be *right* about paths on two platforms with two separator conventions, against text a third
party controls — an adversarial parsing problem taken on for no requirement.

**Alternative B — Add a `code` field to `DownloadOutcome`.**
One field on the dataclass in `downloader.py`, set where `_diagnose` already decides the answer.
*Pros*: unambiguously the correct design. No prose matching, no private-table coupling, no coverage
test needed, and the classification lives where the knowledge is.
*Rejected because*: `downloader.py` is frozen by explicit instruction. **This alternative is recorded
precisely because it is the right one** — if the freeze is ever lifted, this is the first thing to
do, and the `FAILURE_PREFIXES` map plus its coverage test are what get deleted.

**Alternative C — Forward the message only for codes known to be safe.**
Classify first; pass the raw text through for the specific diagnoses that never contain paths, and
suppress it for the rest.
*Pros*: better messages for the common, well-understood failures.
*Rejected because*: "known to be safe" is an assertion about a string produced by a frozen module, and
the very rows most likely to be edited upstream are the ones the allowlist would be trusting. It also
reintroduces a raw-text field on the record, losing the structural property that is the point of this
ADR — for a wording improvement the fixed catalog can deliver anyway.

**Alternative D — Chosen: no raw-text field, a literal catalog, prefix classification, coverage test,
one refusal for unresolvable handles.**
*Pros*: the guarantee is structural and reviewable; meaning crosses the boundary while text does not;
drift is loud.
*Cons*: the private-table coupling and the reduced caller detail, both recorded above.

## References

- Feature Spec: [spec.md](../../specs/002-http-download-api/spec.md) — FR-005, FR-009, FR-010,
  FR-011, FR-028, FR-029, FR-031, FR-033; SC-004, SC-005; US2, US3; Resolved Clarification Q1
- Implementation Plan: [plan.md](../../specs/002-http-download-api/plan.md) — Complexity Tracking
  (classification coupling accepted)
- Research: [research.md](../../specs/002-http-download-api/research.md) — D5 (classification and the
  drift test), D6 (structural message safety, including the FastAPI 422 handler), D8 (why timing is
  argued rather than enforced)
- Data Model: [data-model.md](../../specs/002-http-download-api/data-model.md) — "Fields that
  deliberately do not exist", and the `FailureCode` table
- Contracts: [contracts/openapi.yaml](../../specs/002-http-download-api/contracts/openapi.yaml) —
  `Error`, `Failure`, and the shared `NotFound` response
- Related ADRs: [ADR-0002](./0002-off-event-loop-job-execution-and-concurrency-control.md) (produces
  the `time_limit` code this boundary carries),
  [ADR-0001](./0001-media-index-handling-in-url-canonicalisation.md) (the `_ERROR_DIAGNOSES` table
  this ADR matches against, and the reachability of its `is not a video` row)
- Evaluator Evidence:
  [PHR 0012](../prompts/002-http-download-api/0012-http-download-api-spec.spec.prompt.md) (where the
  leak sources were first found) and
  [PHR 0013](../prompts/002-http-download-api/0013-http-api-implementation-plan.plan.prompt.md)
  (where the prefix-exactness of `_partial_failure` was established)
