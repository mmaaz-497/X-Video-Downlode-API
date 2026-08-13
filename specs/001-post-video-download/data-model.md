# Phase 1 Data Model: Single Post Video Download

**Feature**: `001-post-video-download` | **Date**: 2026-08-12

There is no database and no persistent state (Principle IV). These are in-process value objects that
live for the duration of one CLI invocation. All are plain `@dataclass(frozen=True)` — no ORM, no
pydantic, no schema layer.

---

## PostReference

Produced by `backend/validation.py` from the input URL, **before any network access**. Its existence
is the proof that FR-002/FR-003 passed.

| Field | Type | Notes |
|---|---|---|
| `post_id` | `str` | Digits only, from the URL path. The uniqueness key (SC-008). |
| `canonical_url` | `str` | Scheme + host + `/i/web/status/<id>`, plus `/<photo\|video>/<n>` when the input carried a media index. Query and fragment stripped. What gets handed to yt-dlp. |
| `media_index` | `int \| None` | `None` for a bare post URL — the ordinary case, keeping FR-017's every-video behaviour. Set when the URL named one media item, in which case it also becomes the filename's index component (FR-020, [ADR-0001](../../history/adr/0001-media-index-handling-in-url-canonicalisation.md)). |

**Validation rules**

- Host, lowercased and port-stripped, MUST be `==` a member of the accepted set (FR-003). Eight
  hostnames per research D1.
- Path MUST match `^/(?:i/web|[^/]+)/status(?:es)?/(?P<id>\d+)` (FR-001).
- Scheme MUST be `https` or `http`.
- No author handle field. The URL form `i/web/status/<id>` carries none (research D3), so the handle
  is not part of this object — it arrives later on `PostMetadata`.

**Failure mode**: raises `ValueError` with a message naming the URL and the reason (FR-019). The
caller in `cli.py` turns that into exit status 1.

---

## PostMetadata

Built from `extract_info(url, download=False)`. Carries only what the output path needs.

| Field | Type | Notes |
|---|---|---|
| `author_handle` | `str` | From `uploader_id`, falling back to `uploader`, then `"unknown"` (research D3). Sanitized. |
| `post_id` | `str` | Echoed from `PostReference`, not re-derived. |
| `videos` | `tuple[VideoEntry, ...]` | Length ≥ 1. Length > 1 triggers the indexed filename scheme (FR-017). |

**Derivation rule** — `videos` comes from `info["entries"]` when `info.get("_type") == "playlist"`,
otherwise from `[info]` (research D5).

---

## VideoEntry

One downloadable video within a post. A single-video post has exactly one.

| Field | Type | Notes |
|---|---|---|
| `index` | `int` | 1-based, matching yt-dlp's own `#N` title suffix (research D5). |
| `info` | `dict` | The raw yt-dlp info dict for this entry. Passed straight back to yt-dlp; never parsed beyond the fields named here. |

---

## OutputTarget

The computed destination. Built **before** download so FR-016 can short-circuit.

| Field | Type | Notes |
|---|---|---|
| `path` | `Path` | Final resting place. Extension derived from `prepare_filename`, **never hardcoded** (research D3, corrected). |
| `exists` | `bool` | Checked once, at construction. `True` means skip-and-succeed (FR-016). |

**Filename rule** (FR-010, FR-012):

```text
single video : <author_handle>-<post_id>.<ext>
multi video  : <author_handle>-<post_id>-<index>.<ext>
```

`<ext>` comes from `Path(ydl.prepare_filename(processed_info)).suffix` — usually `mp4`, but a
progressive rendition taken by the `/best` fallback keeps its native container (commonly `webm`).
Hardcoding `.mp4` would make the FR-016 existence check test a path that never exists for those
posts, re-downloading on every run.

**Sanitization rule** (FR-011) — applied to `author_handle` only; `post_id` is already digit-only
and `index` is an `int`:

- Reduce to `[A-Za-z0-9_-]`, replacing anything else with `_`.
- Truncate to 64 characters.
- Empty result becomes `unknown`.
- After joining, `path.resolve()` MUST be relative to `output_dir.resolve()` — verified with
  `Path.is_relative_to`. A failure here is a bug, not a user error, and MUST abort.

The resolve check is belt-and-braces: the character filter already makes `..` and separators
unrepresentable. Both are kept because this is the Principle V boundary and it is cheap.

---

## DownloadOutcome

What `downloader.py` returns to `cli.py`. This is the seam that keeps business logic out of the CLI
(Principle III) — `cli.py` formats this and picks an exit code, nothing more.

| Field | Type | Notes |
|---|---|---|
| `status` | `str` | One of `"downloaded"`, `"skipped"`, `"failed"`. |
| `paths` | `tuple[Path, ...]` | Files that now exist. Non-empty for `downloaded` and `skipped`. |
| `message` | `str` | Human-readable, already final. `cli.py` prints it verbatim (FR-019). |

**Exit-code mapping** (FR-018), applied in `cli.py`:

| `status` | Exit | Rationale |
|---|---|---|
| `"downloaded"` | 0 | Success. |
| `"skipped"` | 0 | FR-016 — already having the file is success. |
| `"failed"` | 1 | Any error, including validation rejection. |

**Partial-success rule** (FR-015 + FR-017): if some videos in a multi-video post succeed and one
fails, `status` is `"failed"`, `paths` lists the completed files, and `message` names both what
succeeded and what did not. The operator is never told "failed" while silently keeping files they
do not know about.

---

## Configuration

`backend/config.py`. Environment variables with defaults; no config file (Principle VII).

| Variable | Default | Meaning |
|---|---|---|
| `XVD_OUTPUT_DIR` | current working directory | Where finished files land (FR-013). |

Resolution order: `--output-dir` flag → `XVD_OUTPUT_DIR` → CWD. The flag wins so a scripted override
never has to mutate the environment.

---

## Flow

```text
argv
  └─> validation.parse_post_url()      -> PostReference      [no network yet; FR-002]
        └─> downloader.fetch_metadata() -> PostMetadata       [extract_info(download=False); FR-003 already passed]
              └─> validation.build_target() -> OutputTarget   [FR-010, FR-011; ext from prepare_filename]
                    ├─ exists ──────────> DownloadOutcome("skipped")        [FR-016, exit 0]
                    └─ missing ─> preflight ffmpeg            [D9]
                                   └─> download to temp dir inside output dir
                                         └─> os.replace(actual temp file)   [FR-015 atomic]
                                               └─> DownloadOutcome("downloaded")
```

No state survives the process. Re-running is idempotent because the filesystem is the only record.
