---
id: 0017
title: US3 Hardening And Verification
stage: green
date: 2026-08-14
surface: agent
model: claude-opus-5
feature: 002-http-download-api
branch: 002-http-download-api
user: Muhammad Maaz
command: /sp.implement
labels: ["us3", "security-boundary", "ast-check", "proxy-headers", "leak-verification"]
links:
  spec: specs/002-http-download-api/spec.md
  ticket: null
  adr: history/adr/0003-caller-facing-disclosure-boundary.md
  pr: null
files:
 - backend/api.py
 - backend/jobs.py
 - tests/test_jobs.py
 - .env.example
 - specs/002-http-download-api/quickstart.md
 - specs/002-http-download-api/research.md
 - specs/002-http-download-api/tasks.md
 - history/prompts/002-http-download-api/0017-us3-hardening-and-verification.green.prompt.md
tests:
 - tests/test_jobs.py (60 tests, 9 added this run)
 - full suite: 224 passed
---

## Prompt

Execute T020 through T023 — US3 HTTP hardening. Then T024 through T028 — verification.

Stop before T027 and tell me. That is the manual verification and I will run it myself.

Same rules: frozen modules untouched, no new dependencies, one test file, stop and report rather than improvising.

## Response snapshot

T020–T026 and T028 executed; stopped before T027 as instructed. 224 tests pass (9 added). One
commit. Frozen modules verified untouched by two independent measures.

**The significant finding was that my own planning documentation was wrong.**

T022 wired `request.client.host` into the audit log. Testing it, I sent `X-Forwarded-For: 6.6.6.6`
against a server started with **no** proxy flags — and the audit log recorded `6.6.6.6`. Reading
`uvicorn/config.py:355-357` (0.52.2) explained it: `proxy_headers` defaults to **`True`** and
`forwarded_allow_ips` defaults to **`"127.0.0.1"`**, so `X-Forwarded-For` is already believed from
localhost.

Research D9, quickstart.md, and `.env.example` all carried my claim that `--proxy-headers` must be
passed or every caller collapses into the proxy's address. That is false, and the corrected guidance
inverts it into three cases, only one of which needs a flag:

- reverse proxy on the **same host** — nothing to do, it already works;
- proxy on a **different host** — `--forwarded-allow-ips=<its IP>`;
- **no proxy, exposed directly** — `--no-proxy-headers`, because otherwise anything reaching the port
  from `127.0.0.1` (another local process, a container sharing the namespace, an SSH tunnel) can set
  its own address, forge the audit trail, and escape a per-address rate limit.

The third case is a real hazard the original text missed completely, and it is the *default*
configuration. All three documents were corrected, with research.md carrying a dated correction note
rather than a silent edit.

**Two gaps reported at the end of the previous run were closed and re-verified:**

- pydantic's 422 no longer echoes the caller's input (`{"input": "/etc/shadow"}` → a fixed sentence);
- a path matching no route now returns our body rather than Starlette's, so it is indistinguishable
  from an unresolvable handle.

**T020's shape check is deliberately not a route pattern.** A FastAPI `Path(pattern=...)` produces a
422 on mismatch, which would tell a caller their input was the wrong *shape* — one bit more than a
well-formed handle that does not exist receives. The check sits in the handler so both answers are
the same answer. The pattern itself is derived from `_HANDLE_BYTES` in `jobs.py` rather than written
as a literal, so it cannot drift away from the minter.

**T025 is the T006 lesson applied.** Feature 001's boundary check grepped for `argparse|sys\.exit|
print\(` against modules whose docstrings described the constraint using those words, so it matched
prose, never passed as written, and was signed off by eye. The replacement walks the AST. Because
T002 had already kept those words out of `jobs.py`'s docstring, the difference could not be
demonstrated on the real file — so `test_ast_check_is_not_fooled_by_prose` parses a literal source
string whose docstring and comment both say "fastapi", and asserts the extractor returns only
`{"json"}`. The blindness to prose is shown, not claimed.

**T026 drove 16 error paths** including a deliberately raising route whose exception message was
stuffed with every marker the grep looks for. Nothing — no path, traceback, library name, or the
output directory — appears in any response body, `openapi.json` included.

**Two self-inflicted mistakes, both caught by verifying rather than assuming:**

1. A heredoc collapsed `\\` into `\` while appending the temporary raising route, producing a
   `SyntaxError` so the server never started. The entire first capture ran against a dead port and
   would have been reported as sixteen clean results. Caught because the 500 body came back *empty*
   rather than wrong. The route was rewritten via a scratchpad script with no shell escaping, and the
   server's liveness was asserted before capturing.
2. The same escaping problem recurred in a second attempt through `python - <<EOF`. Moving the file
   generation out of the shell entirely was the fix.

The temporary route was removed and its absence verified (`grep -c __boom__` → 0).

## Outcome

- ✅ Impact: US3 complete. Every caller-visible refusal is one of five fixed bodies; no response can carry a path, trace, or library name; the boundary is enforced by a test that cannot be fooled by prose.
- 🧪 Tests: 224 passed (60 in tests/test_jobs.py). T026 verified empirically against a live server.
- 📁 Files: backend/api.py, backend/jobs.py, tests/test_jobs.py, .env.example, quickstart.md, research.md, tasks.md
- 🔁 Next prompts: T027 — the user runs the manual verification themselves. Then Phase 2 (US2) or Phase 3 (US4).
- 🧠 Reflection: The proxy-header finding only surfaced because T022's verification sent a hostile header instead of a benign request. A test that confirms the happy path would have left a wrong document in place and a real misconfiguration hazard undocumented.

## Evaluation notes (flywheel)

- Failure modes observed: Shell heredoc escaping mangled Python source twice, once silently enough that a whole verification pass ran against a server that had never started. An empty response, not a wrong one, was the only signal. Planning documentation asserted framework defaults that were never checked against the framework.
- Graders run and results (PASS/FAIL): SC-005 leak grep across 16 error paths — PASS. Frozen-module constraint — PASS (empty diff and mtimes predating the session). Principle III AST check — PASS, including the anti-prose demonstration. Dependency budget — PASS, nothing added. Test-file budget — PASS, one file.
- Prompt variant (if applicable): n/a
- Next experiment (smallest change to try): Before any future verification capture, assert the server answers a known-good endpoint and fail loudly if not — the dead-port run produced sixteen empty strings that a less careful grep would have scored as sixteen passes.
