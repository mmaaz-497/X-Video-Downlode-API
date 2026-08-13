---
id: 0001
title: Lean Python Backend Constitution
stage: constitution
date: 2026-08-12
surface: agent
model: claude-opus-5
feature: none
branch: master
user: Muhammad Maaz
command: /sp.constitution
labels: ["constitution", "governance", "python", "cli-first", "security", "yt-dlp"]
links:
  spec: null
  ticket: null
  adr: null
  pr: null
files:
  - .specify/memory/constitution.md
  - .specify/templates/plan-template.md
  - .specify/templates/tasks-template.md
tests:
  - none (documentation-only change; no executable code introduced)
---

## Prompt

Create principles for a small, single-developer Python backend project. Keep it lean and pragmatic — this is not an enterprise system.

Core principles:

1. SINGLE BACKEND FOLDER: All application code lives inside one `backend/` directory. No monorepo structure, no separate services, no frontend folder. Do not scatter code across multiple top-level packages.

2. MINIMAL TESTING: Do NOT follow strict TDD. Do not generate large test suites. Only write a handful of tests for genuinely critical logic (URL validation and the extractor wrapper). No unit tests for trivial getters, no integration tests requiring network calls, no mocking frameworks. If a test needs a real network request, skip it. Manual verification via CLI is the primary validation method for this project.

3. CLI-FIRST, API-LATER: Every core capability must be usable from the terminal before any web layer is added. The download logic must be a plain Python module that a CLI entrypoint calls. The HTTP layer is a thin wrapper added later — it must never contain business logic.

4. LEAN DEPENDENCIES: Python 3.11+, managed with uv. Use yt-dlp as a Python library (not subprocess) and ffmpeg as the only system binary. Do not add databases, task queues, ORMs, or auth libraries unless explicitly requested.

5. SECURITY BASELINE (non-negotiable): Every user-supplied URL must be validated against an allowlist of x.com and twitter.com hostnames before being passed to any extractor. Never pass raw user input to a shell. Never interpolate user input into file paths — sanitize all output filenames.

6. SIMPLE ERRORS: Fail fast with clear, human-readable messages. No custom exception hierarchies. No retry/backoff logic unless a real failure demands it.

7. VPS-DEPLOYABLE: Must run on a plain Linux VPS with no cloud-specific services. All configuration through environment variables with sane defaults.

Governance: When in doubt, choose the simpler option. Prefer fewer files over more files. Do not add abstraction layers for hypothetical future needs.

## Response snapshot

Ratified `.specify/memory/constitution.md` at **v1.0.0** (initial baseline — the file was an
unfilled template, so no prior version existed to amend).

Seven principles written as declarative MUST/MUST NOT rules, each with a rationale:

1. **Single Backend Folder** — one `backend/` package; root holds only project-level files.
2. **Minimal Testing** — TDD explicitly disavowed; tests confined to URL validation and the
   extractor wrapper; network tests and mocking frameworks forbidden; manual CLI verification
   is sufficient evidence a change works.
3. **CLI-First, API-Later** — download logic is framework-free; logic appearing only in the
   HTTP layer is a violation that must be moved down.
4. **Lean Dependencies** — Python 3.11+/uv/yt-dlp-as-library/ffmpeg; subprocess invocation of
   yt-dlp forbidden; new deps justified against stdlib first.
5. **Security Baseline (NON-NEGOTIABLE)** — parsed-host allowlist (substring/prefix matching on
   the raw URL string explicitly forbidden), no shell interpolation, sanitized filenames with
   resolved-path containment inside the output directory.
6. **Simple Errors** — built-in exceptions only; retry/backoff added only after an observed
   failure, scoped to the operation that failed.
7. **VPS-Deployable** — plain Linux VPS, no provider-specific services, env-var config where
   every variable has a default so the app starts without a config file.

Added two supporting sections (**Technology & Deployment Constraints** with the fixed repo
layout, **Development Workflow**) and a **Governance** section carrying the "when in doubt,
choose the simpler option" rule, semantic versioning policy, amendment procedure, and a
Constitution Check gate that blocks plans on unjustified violations.

Consistency propagation — three template conflicts found and fixed:

- `plan-template.md`: replaced the `[Gates determined based on constitution file]` stub with
  seven concrete checkbox gates; replaced the three-option source tree (which offered
  `src/`, `frontend/`, `api/` layouts) with the fixed `backend/` layout required by Principle I.
- `tasks-template.md`: rewrote path conventions to `backend/`; removed the TDD ordering rules
  ("tests MUST be written and FAIL before implementation", "verify tests fail before
  implementing"); converted per-story test subsections into manual CLI verification tasks;
  replaced foundational samples that assumed a database, auth framework, and API middleware
  (Principle IV violations) with real project tasks; repointed all `src/models/`,
  `src/services/` sample paths to `backend/*.py`; dropped the multi-developer team strategy
  section as inapplicable to a single-developer project.
- `spec-template.md`: reviewed, no conflict, left unchanged.
- `CLAUDE.md`: reviewed, references the constitution generically, left unchanged.

Validation: zero unresolved bracket/brace placeholders in the constitution, version line
matches the Sync Impact Report, all dates ISO-formatted.

## Outcome

- ✅ Impact: Project constitution ratified at v1.0.0 with 7 enforceable principles; planning
  and task templates realigned so generated artifacts cannot silently reintroduce TDD, a
  multi-package layout, or heavyweight dependencies.
- 🧪 Tests: None. Documentation-only change; no executable code introduced.
- 📁 Files: `.specify/memory/constitution.md` (rewritten from template),
  `.specify/templates/plan-template.md` (gates + source tree),
  `.specify/templates/tasks-template.md` (paths, test discipline, sample tasks).
- 🔁 Next prompts: `/sp.specify` for the first feature (single-video download by URL), then
  `/sp.plan` to exercise the new Constitution Check gate.
- 🧠 Reflection: The shipped templates were enterprise-shaped and directly contradicted
  Principles I, II, and IV. Writing the constitution without propagating those changes would
  have left every future `/sp.tasks` run generating contract tests and `src/models/` paths.

## Evaluation notes (flywheel)

- Failure modes observed: `.specify/scripts/bash/` does not exist in this repo, so the
  scripted PHR path was unavailable; fell back to agent-native template fill as specified.
  The constitution template shipped 6 principle slots against 7 requested principles.
- Graders run and results (PASS/FAIL): placeholder scan PASS (no bracket or brace tokens
  remaining); version-line/report consistency PASS; ISO date format PASS; template
  cross-reference scan PASS after a second pass caught three residual `src/models/` paths.
- Prompt variant (if applicable): none
- Next experiment (smallest change to try): have the constitution command emit an explicit
  grep-based conflict report over `.specify/templates/*.md` before editing, so propagation
  targets are enumerated up front rather than discovered by post-hoc scanning.
