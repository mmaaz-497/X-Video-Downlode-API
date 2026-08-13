---

description: "Task list template for feature implementation"
---

# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Per Constitution Principle II (Minimal Testing), test tasks are the exception, not the
default. Include a test task ONLY for URL validation or the extractor wrapper. Do not generate
contract tests, network-dependent integration tests, or mock-based tests. Manual CLI
verification is the primary validation method — make it an explicit task instead.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- All application code lives in `backend/` (Constitution Principle I). There is no `src/`,
  `frontend/`, or `api/` directory.
- The permitted tests live in `tests/` at the repository root.
- Sample tasks below use generic paths; rewrite them as real `backend/*.py` paths.

<!-- 
  ============================================================================
  IMPORTANT: The tasks below are SAMPLE TASKS for illustration purposes only.
  
  The /sp.tasks command MUST replace these with actual tasks based on:
  - User stories from spec.md (with their priorities P1, P2, P3...)
  - Feature requirements from plan.md
  - Entities from data-model.md
  - Endpoints from contracts/
  
  Tasks MUST be organized by user story so each story can be:
  - Implemented independently
  - Tested independently
  - Delivered as an MVP increment
  
  DO NOT keep these sample tasks in the generated tasks.md file.
  ============================================================================
-->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure per implementation plan
- [ ] T002 Initialize [language] project with [framework] dependencies
- [ ] T003 [P] Configure linting and formatting tools

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

Examples of foundational tasks for this project (no databases, queues, ORMs, or auth
frameworks — see Constitution Principle IV):

- [ ] T004 Implement URL host allowlist validation in backend/validation.py
- [ ] T005 [P] Implement output filename sanitization and path containment in backend/validation.py
- [ ] T006 [P] Load configuration from environment variables with defaults in backend/config.py
- [ ] T007 Wrap yt-dlp (as a library) in backend/downloader.py
- [ ] T008 Wire the CLI entrypoint in backend/cli.py

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - [Title] (Priority: P1) 🎯 MVP

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Verification for User Story 1

> **NOTE: Manual CLI verification is the default (Principle II). Add a test task only if this
> story touches URL validation or the extractor wrapper.**

- [ ] T010 [US1] Manually verify via CLI: [exact command and expected output]
- [ ] T011 [P] [US1] (Only if applicable) Test for URL validation / extractor wrapper in tests/test_[name].py

### Implementation for User Story 1

- [ ] T012 [P] [US1] Add [helper] in backend/validation.py
- [ ] T013 [P] [US1] Add [setting] in backend/config.py
- [ ] T014 [US1] Implement [capability] in backend/downloader.py (depends on T012, T013)
- [ ] T015 [US1] Expose [capability] as a CLI command in backend/cli.py
- [ ] T016 [US1] Add fail-fast error messages using built-in exceptions (Principle VI)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - [Title] (Priority: P2)

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Verification for User Story 2

- [ ] T018 [US2] Manually verify via CLI: [exact command and expected output]

### Implementation for User Story 2

- [ ] T020 [P] [US2] Add [helper] in backend/[module].py
- [ ] T021 [US2] Implement [capability] in backend/downloader.py
- [ ] T022 [US2] Expose [capability] as a CLI command in backend/cli.py
- [ ] T023 [US2] Integrate with User Story 1 components (if needed)

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - [Title] (Priority: P3)

**Goal**: [Brief description of what this story delivers]

**Independent Test**: [How to verify this story works on its own]

### Verification for User Story 3

- [ ] T024 [US3] Manually verify via CLI: [exact command and expected output]

### Implementation for User Story 3

- [ ] T026 [P] [US3] Add [helper] in backend/[module].py
- [ ] T027 [US3] Implement [capability] in backend/downloader.py
- [ ] T028 [US3] Expose [capability] as a CLI command in backend/cli.py

**Checkpoint**: All user stories should now be independently functional

---

[Add more user story phases as needed, following the same pattern]

---

## Phase N: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] TXXX [P] Documentation updates (README, .env.example)
- [ ] TXXX Code cleanup — remove dead code, do not add abstractions
- [ ] TXXX Re-check Principle V: host allowlist, no shell input, path containment
- [ ] TXXX Confirm the app starts on a plain Linux VPS with env-var defaults only
- [ ] TXXX Run quickstart.md validation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1 but should be independently testable
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - May integrate with US1/US2 but should be independently testable

### Within Each User Story

- Validation helpers before the logic that calls them (Principle V)
- Core module before CLI wiring; CLI before any HTTP layer (Principle III)
- Manual CLI verification closes the story
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Tasks touching different `backend/` modules can run in parallel
- Note: this is a single-developer project — [P] marks independence, not staffing

---

## Parallel Example: User Story 1

```bash
# Tasks touching different backend/ modules can run together:
Task: "Implement URL host allowlist in backend/validation.py"
Task: "Load configuration from environment variables in backend/config.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo
4. Add User Story 3 → Test independently → Deploy/Demo
5. Each story adds value without breaking previous stories

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and verifiable from the CLI
- Prefer fewer files; extend an existing `backend/` module before creating a new one
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
