# Phase 1 Data Model: HTTP Download API

**Feature**: 002-http-download-api | **Date**: 2026-08-13
**Depends on**: [research.md](./research.md) D3 (durability), D6 (message safety), D11 (no pydantic
in the service layer)

All types below live in `backend/jobs.py` as plain `@dataclass` records, mirroring how
`downloader.py` defines `DownloadOutcome`. No pydantic, no ORM, no framework types.

---

## Entity: `Job`

The single unit of work. One submission produces one `Job`; a deduplicated submission returns an
existing one.

| Field | Type | Written by | Notes |
|---|---|---|---|
| `handle` | `str` | creation | `secrets.token_urlsafe(32)`, 43 chars, 256 bits (D8). **The capability** — possession is authorization (FR-028). |
| `canonical_url` | `str` | creation | From `validation.parse_post_url(...).canonical_url`. The dedup key (FR-017). Never the caller's raw string (FR-032). |
| `state` | `str` | transitions | `waiting` \| `running` \| `finished` \| `failed` \| `expired` (FR-006). |
| `created_at` | `float` | creation | Unix seconds. |
| `started_at` | `float \| None` | worker start | Starts the FR-020 deadline clock — **not** `created_at`, so queue time is not charged against the job. |
| `completed_at` | `float \| None` | terminal transition | Starts the FR-021 retention clock. |
| `downloaded_bytes` | `int \| None` | progress hook | Memory only; never written to disk (D3). |
| `total_bytes` | `int \| None` | progress hook | May be absent — yt-dlp does not always report a total (FR-008). |
| `files` | `tuple[Path, ...]` | completion | From `DownloadOutcome.paths`. Server-side only; a caller sees only the count. |
| `failure_code` | `str \| None` | terminal transition | A member of `FailureCode`. **The only failure information the record holds** (D6). |
| `client_address` | `str` | creation | For the audit record (FR-031). |
| `timed_out` | `bool` | progress callback | Set immediately before the callback raises, and read by the worker to tell a deadline abort from any other failure (FR-020, D4). **Not persisted** — `failure_code` already carries the outcome to disk. A boolean holds no free text, so it does not weaken the guarantee below; it is listed here because that guarantee is audited from this table. |

### Fields that deliberately do not exist

- **No `message`, `detail`, `error_text`, or any raw-text field.** This absence *is* the FR-029
  guarantee (D6): the serializer cannot forward what the record does not carry. Adding such a field
  would be a visible change to this table, not an accident in a handler.
- **No `output_dir` and no caller-supplied path of any kind.** The output directory comes from
  `config.output_dir()` at start-up and is held once at module level (FR-030, FR-004).
- **No owner, token, or session.** Q1 resolved to the capability model; the handle is the only
  credential.

### State transitions

```text
                    ┌──────────────── submission accepted
                    ▼
                 waiting ──── worker picks it up ────▶ running
                    │                                    │
                    │  restart recovery (FR-025)         ├── DownloadOutcome downloaded|skipped ──▶ finished
                    │                                    │
                    └──────────────┬─────────────────────┤── DownloadOutcome failed ──▶ failed
                                   ▼                     │
                                 failed ◀────────────────┴── deadline watchdog (FR-020) / ValueError
                                                              
                              finished ──── retention sweep (FR-021) ────▶ expired
```

**Invariants**:

1. **A terminal state is never left.** `finished`, `failed`, and `expired` are terminal, with the one
   exception of `finished → expired`. A worker that returns after the watchdog already failed its job
   MUST NOT overwrite the record — the check is explicit, because with a wedged thread (D4) this is a
   race that really happens.
2. `started_at` is set exactly once, by the worker, before any other work.
3. `completed_at` is set on entry to any terminal state.
4. `failure_code` is non-`None` if and only if `state == "failed"`.
5. `files` is non-empty if and only if the job reached `finished`.

---

## Entity: `FailureCode`

A closed set of string constants with a fixed caller-safe sentence each. Two module-level dicts in
`jobs.py`: the classification prefixes (D5) and the sentences (D6).

| Code | Caller-safe sentence | Source |
|---|---|---|
| `no_video` | "This post does not contain a video." | classified (D5) |
| `not_a_video` | "This post contains media, but it is not a video." | classified |
| `protected_account` | "This post is from a protected account and is not publicly accessible." | classified |
| `age_restricted` | "This post is age-restricted and is not publicly accessible." | classified |
| `post_unavailable` | "This post could not be found. It may have been deleted." | classified |
| `interrupted` | "The download was interrupted and did not complete. Submit it again to retry." | classified, or set by restart recovery (FR-025) |
| `service_unavailable` | "The service cannot process downloads right now. The operator has been notified." | classified — `ffmpeg` missing is an operator fault, never described as one to the caller |
| `time_limit` | "The download took too long and was stopped." | set by the jobs layer's own flag (D4), never by parsing text |
| `unclassified` | "The download failed for an unexpected reason." | the deliberate, visible fallback (FR-011) |

**Rule**: every sentence is a literal in the source. None interpolates a path, a filename, a URL, a
count, or any text originating outside this table.

---

## Entity: `SubmissionRecord`

Append-only operator audit trail (FR-031), one JSON object per line at
`<state_dir>/submissions.log`.

| Field | Type | Notes |
|---|---|---|
| `at` | ISO-8601 string | |
| `canonical_url` | `str` | Post-validation only. The raw submitted string is never written (FR-032). |
| `client_address` | `str` | |
| `outcome` | `str` | `accepted` \| `deduplicated` \| `rejected_url` \| `rate_limited` \| `disk_low` \| `at_capacity` |
| `handle` | `str \| None` | Present when a job was created or matched. |

Written for **every** submission including refusals — a refusal is the more interesting record when
investigating abuse. Contains no caller free text.

**`canonical_url` is `null` for `rejected_url` and `rate_limited`, and present for the rest.** Not an
inconsistency: both of those refusals are decided at or before validation, so at that moment the URL
is still unvalidated caller-supplied text and FR-032 forbids storing it. `disk_low` and `at_capacity`
are decided after validation has produced a canonical form.

> **Note**: the handle in this log is the capability, so read access to the log confers access to the
> jobs it names. Acceptable — the log is operator-only and the operator can read the video files
> directly regardless.

---

## Entity: `RateLimitBucket` (in-memory only)

`dict[str, deque[float]]`, address → submission timestamps in the current window (D9). Not persisted;
resets on restart, which is stated in research.md rather than hidden. Pruned by the periodic sweep.

---

## Persistence layout

```text
<output_dir>/                        # config.output_dir() — server configuration only
├── <handle>-<postid>.mp4            # finished videos, named by frozen build_target()
├── .tmp-xvd-*/                      # transient, created and removed by download_post()
│                                    #   leftovers swept at start-up (FR-026)
└── .xvd-state/                      # XVD_STATE_DIR, mode 0700
    ├── jobs/
    │   └── <handle>.json            # one Job, temp-then-os.replace (D3)
    └── submissions.log              # SubmissionRecord, JSONL append
```

**Serialised `Job`**: every field above except `files`, which is stored as a list of strings.
`downloaded_bytes` and `total_bytes` are written only at terminal transitions, since progress updates
never touch disk (D3).

**Authority**: memory during the process's life; disk read exactly once at start-up (D3).

---

## Configuration (FR-034)

Every value has a working default, per Principle VII. Read once at start-up.

| Variable | Default | Governs |
|---|---|---|
| `XVD_OUTPUT_DIR` | cwd | Existing, from frozen `config.py`. Read with **no override argument** (FR-030). |
| `XVD_STATE_DIR` | `<output_dir>/.xvd-state` | Job records and audit log |
| `XVD_MAX_CONCURRENT` | `2` | Pool size = the concurrency cap (D2, FR-015) |
| `XVD_MAX_PENDING` | `50` | Maximum jobs held in `waiting`; refused with an at-capacity message beyond it (FR-015 as amended). **Phase 3** |
| `XVD_JOB_TIMEOUT` | `1800` (30 min) | FR-020 deadline, from `started_at` |
| `XVD_RETENTION` | `86400` (24 h) | FR-021, from `completed_at` |
| `XVD_SWEEP_INTERVAL` | `900` (15 min) | FR-023 |
| `XVD_MIN_FREE_BYTES` | `2147483648` (2 GB) | FR-018 |
| `XVD_RATE_LIMIT` | `10` | FR-019, submissions per window per address |
| `XVD_RATE_WINDOW` | `3600` (1 h) | FR-019 |

`config.py` is frozen, so these are read in `jobs.py` via `os.environ` — the same pattern
`config.output_dir` uses, not an extension of it.
