<!--
SYNC IMPACT REPORT
==================
Version change: (unversioned template) → 1.0.0
Bump rationale: Initial ratification. All placeholder tokens replaced with concrete,
project-specific principles. No prior version existed, so this is a new baseline
rather than an amendment.

Modified principles (template slot → concrete principle):
  [PRINCIPLE_1_NAME] → I. Single Backend Folder
  [PRINCIPLE_2_NAME] → II. Minimal Testing
  [PRINCIPLE_3_NAME] → III. CLI-First, API-Later
  [PRINCIPLE_4_NAME] → IV. Lean Dependencies
  [PRINCIPLE_5_NAME] → V. Security Baseline (NON-NEGOTIABLE)
  [PRINCIPLE_6_NAME] → VI. Simple Errors
  (new slot added)   → VII. VPS-Deployable

Added sections:
  - Technology & Deployment Constraints (was [SECTION_2_NAME])
  - Development Workflow (was [SECTION_3_NAME])
  - Principle VII (template shipped 6 slots; user specified 7)

Removed sections: none

Templates requiring updates:
  ✅ .specify/templates/plan-template.md   — Constitution Check gates filled;
                                             source tree fixed to single backend/ layout
  ✅ .specify/templates/tasks-template.md  — path conventions, test-discipline notes,
                                             and foundational task samples realigned
  ✅ .specify/templates/spec-template.md   — reviewed, no constitution conflict, unchanged
  ✅ CLAUDE.md                             — reviewed, references constitution generically,
                                             no outdated principle names, unchanged
  ⚠ README.md                              — does not exist yet; create when the project
                                             gains a public surface

Deferred TODOs: none
-->

# X Video Downloader Constitution

## Core Principles

### I. Single Backend Folder

All application code MUST live inside one top-level `backend/` directory. There MUST NOT be
a monorepo structure, separate deployable services, a `frontend/` directory, or multiple
top-level Python packages. Repository root holds only project-level files (`pyproject.toml`,
`README.md`, `.env.example`, `.specify/`, `specs/`, `history/`) and the `backend/` package.

**Rationale**: One developer, one deployable. Directory sprawl costs navigation time and
import complexity without buying isolation that a single-process app can use.

### II. Minimal Testing

Strict TDD is explicitly NOT followed. Tests are written after or alongside code, never as
a gate before it.

- Tests MUST exist for exactly two areas: URL validation, and the extractor wrapper.
- Tests MUST NOT be written for trivial accessors, dataclass construction, or pass-through code.
- Integration tests requiring live network calls MUST NOT be written; if a test would need a
  real request to x.com, twitter.com, or any CDN, skip it.
- Mocking frameworks MUST NOT be introduced. Plain fakes and stub objects only, if anything.
- Manual verification via the CLI is the primary validation method and is sufficient evidence
  that a change works.

**Rationale**: The critical-risk surface is narrow (what URLs we accept, and how we call the
extractor). Everything else is either trivial or dominated by upstream behavior that a test
suite cannot pin down anyway.

### III. CLI-First, API-Later

Every core capability MUST be usable from the terminal before any web layer exists.

- Download logic MUST be a plain Python module with no framework imports.
- A CLI entrypoint MUST call that module; the CLI is a thin argument-parsing shell.
- An HTTP layer, when added, MUST be a thin wrapper that parses requests, calls the same
  module, and serializes responses. It MUST NOT contain business logic, validation rules, or
  extractor configuration.
- Any logic that appears only in the HTTP layer is a violation and MUST be moved down.

**Rationale**: The CLI is both the fastest debugging tool and the proof that business logic
is transport-independent. If it works from a terminal, the API layer is trivial.

### IV. Lean Dependencies

- Python 3.11 or later, with dependencies managed by `uv`.
- `yt-dlp` MUST be used as a Python library. Invoking it as a subprocess is forbidden.
- `ffmpeg` is the only permitted system binary dependency.
- Databases, task queues, ORMs, caching servers, and auth libraries MUST NOT be added unless
  the user explicitly requests them.
- Each new third-party dependency MUST be justified against the standard library first.

**Rationale**: Every dependency is a version conflict, a security advisory, and a VPS install
step. The standard library covers most of what a small downloader needs.

### V. Security Baseline (NON-NEGOTIABLE)

These rules admit no exceptions and no "temporary" bypasses.

- Every user-supplied URL MUST be validated against an allowlist of `x.com` and `twitter.com`
  hostnames (including their permitted subdomains) BEFORE being passed to any extractor.
  Validation MUST parse the URL and compare the host exactly; substring or prefix matching on
  the raw string is forbidden.
- Raw user input MUST NEVER be passed to a shell. No `shell=True`, no string-built commands.
- User input MUST NEVER be interpolated into a file path. All output filenames MUST be
  sanitized, and resolved paths MUST be confirmed to stay within the configured output
  directory.

**Rationale**: This service takes untrusted URLs from the internet and writes files to a VPS
disk. Host allowlisting, shell avoidance, and path containment are the three controls that
keep that from becoming remote code execution or arbitrary file write.

### VI. Simple Errors

- Fail fast with a clear, human-readable message naming what went wrong and what input caused
  it.
- Custom exception hierarchies MUST NOT be created. Use built-in exceptions (`ValueError`,
  `RuntimeError`, `FileNotFoundError`) and let messages carry the meaning.
- Retry and backoff logic MUST NOT be added speculatively. It is added only after a specific
  failure has actually been observed, and only around the operation that failed.

**Rationale**: A single-developer tool is debugged by reading the error, not by catching typed
exceptions. Speculative resilience hides the bugs it was supposed to survive.

### VII. VPS-Deployable

- The application MUST run on a plain Linux VPS with Python, `uv`, and `ffmpeg` installed, and
  nothing else.
- Cloud-provider-specific services (managed queues, object storage SDKs, serverless runtimes,
  provider metadata endpoints) MUST NOT be used.
- All configuration MUST come from environment variables, and every variable MUST have a sane
  default that lets the app start without a config file.
- Deployment MUST be reproducible from a documented sequence of shell commands.

**Rationale**: Portability across cheap hosts is worth more than any managed service, and
env-var configuration with defaults means the app runs immediately after clone.

## Technology & Deployment Constraints

**Stack**: Python 3.11+, `uv` for dependency and virtualenv management, `yt-dlp` as a library,
`ffmpeg` as the sole system binary.

**Repository layout** (fixed by Principle I):

```text
backend/            # all application code
  cli.py            # terminal entrypoint
  downloader.py     # core download logic, framework-free
  validation.py     # URL allowlist and filename sanitization
  config.py         # environment variable loading with defaults
tests/              # the handful of tests permitted by Principle II
pyproject.toml
.env.example
```

**Configuration**: environment variables only, with defaults. `.env.example` MUST list every
variable the app reads. Secrets MUST NOT be committed; `.env` MUST be git-ignored.

**Persistence**: filesystem only. No database until explicitly requested.

## Development Workflow

- Changes MUST be the smallest viable diff. Unrelated refactoring is out of scope for any task.
- Prefer editing an existing file over creating a new one. A new module needs a reason beyond
  organization.
- Validation of a change is: run it from the CLI and confirm the observed behavior. Add a test
  only if it falls in the two areas named in Principle II.
- Security-relevant code (URL validation, filename sanitization, path resolution) MUST be
  reviewed against Principle V before it is considered complete.
- Architecturally significant decisions SHOULD be captured as ADRs under `history/adr/` with
  user consent, per the project's ADR process.

## Governance

This constitution supersedes other practices and preferences for this project. Where guidance
conflicts, the constitution wins.

**When in doubt, choose the simpler option.** Prefer fewer files over more files. Do not add
abstraction layers, interfaces, plugin systems, or configuration hooks for hypothetical future
needs. Speculative generality is a violation, not foresight.

**Amendment procedure**: Amendments require an explicit request from the project owner, a
recorded rationale, and a version bump in this file. Amendments that invalidate existing code
MUST include a migration note describing what changes.

**Versioning policy** (semantic):

- MAJOR: a principle is removed or redefined in a backward-incompatible way.
- MINOR: a principle or section is added, or existing guidance is materially expanded.
- PATCH: clarifications, wording, and typo fixes that do not change meaning.

**Compliance review**: Every plan produced by `/sp.plan` MUST pass a Constitution Check gate
before design work proceeds. Violations MUST either be removed or recorded in the plan's
Complexity Tracking table with a justification for why the simpler alternative was rejected.
An unjustified violation blocks the plan.

**Version**: 1.0.0 | **Ratified**: 2026-08-12 | **Last Amended**: 2026-08-12
