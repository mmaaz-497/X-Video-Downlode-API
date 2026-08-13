# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]
**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/sp.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]  
**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]  
**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]  
**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]  
**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]
**Project Type**: [single/web/mobile - determines source structure]  
**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]  
**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]  
**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Answer each gate. Any "no" must be removed or justified in Complexity Tracking below.

- [ ] **I. Single Backend Folder** — all new code lands under `backend/`; no new top-level
      packages, services, or frontend directories.
- [ ] **II. Minimal Testing** — tests planned only for URL validation and the extractor
      wrapper; no network-dependent tests, no mocking frameworks, no TDD gate.
- [ ] **III. CLI-First, API-Later** — the capability is reachable from the CLI; business logic
      sits in a framework-free module, not in an HTTP handler.
- [ ] **IV. Lean Dependencies** — no new dependency beyond Python 3.11+/uv/yt-dlp (as a
      library)/ffmpeg; no DB, queue, ORM, or auth library unless the user asked for it.
- [ ] **V. Security Baseline (NON-NEGOTIABLE)** — user URLs are host-allowlisted against
      x.com/twitter.com before reaching an extractor; no shell interpolation; output filenames
      sanitized and confined to the output directory.
- [ ] **VI. Simple Errors** — built-in exceptions with clear messages; no custom hierarchy; no
      speculative retry/backoff.
- [ ] **VII. VPS-Deployable** — runs on a plain Linux VPS; configuration via environment
      variables with sane defaults; no cloud-provider-specific services.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/sp.plan command output)
├── research.md          # Phase 0 output (/sp.plan command)
├── data-model.md        # Phase 1 output (/sp.plan command)
├── quickstart.md        # Phase 1 output (/sp.plan command)
├── contracts/           # Phase 1 output (/sp.plan command)
└── tasks.md             # Phase 2 output (/sp.tasks command - NOT created by /sp.plan)
```

### Source Code (repository root)
<!--
  The layout below is FIXED by Constitution Principle I (Single Backend Folder).
  Extend it with the concrete files this feature adds; do not introduce new
  top-level directories or alternative structures.
-->

```text
backend/            # all application code
├── cli.py          # terminal entrypoint
├── downloader.py   # core download logic, framework-free
├── validation.py   # URL allowlist and filename sanitization
├── config.py       # environment variable loading with defaults
└── [new modules this feature adds]

tests/              # only URL validation + extractor wrapper (Principle II)
```

**Structure Decision**: Single `backend/` package per Principle I. List the exact files this
feature creates or modifies above. Adding a top-level directory requires a Complexity Tracking
entry.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
