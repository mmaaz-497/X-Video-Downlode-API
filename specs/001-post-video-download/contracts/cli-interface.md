# Contract: CLI Interface

**Feature**: `001-post-video-download` | **Date**: 2026-08-12

> **Why this is not an OpenAPI document.** The plan template's Phase 1 asks for REST contracts under
> `contracts/`. This feature has no HTTP surface — Constitution Principle III makes the CLI the
> contract, and the HTTP layer is a separate future feature that must not appear here. The public
> contract of this feature is therefore the terminal interface below, plus the module boundary that
> a future HTTP layer will call **instead of** re-implementing.

---

## Command surface (User Story 1)

```text
xvd <url> [--output-dir DIR]
```

| Argument | Required | Source | Contract |
|---|---|---|---|
| `url` | yes | positional | Exactly one. Two positionals is an argparse usage error, exit 2. |
| `--output-dir DIR` | no | flag | Overrides `XVD_OUTPUT_DIR`, which overrides CWD. |

Built with `argparse` from the standard library. No `click`, `typer`, or `rich` (Principle IV).

### Streams

| Stream | Carries |
|---|---|
| stdout | The final output path(s), one per line. Nothing else — so `xvd <url>` is pipeable. |
| stderr | Progress, diagnostics, and all error messages (research D7). |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Video downloaded, **or** already present and skipped (FR-016). |
| 1 | Any failure: rejected URL, no video, network error, interrupted, ffmpeg missing. |
| 2 | argparse usage error (wrong argument count, unknown flag). argparse's own default. |

A single non-zero failure code is deliberate — the spec asked for a *distinct* failure status, and
Principle VI rejects elaborate taxonomies. Per-reason codes can be added later without breaking
this contract, since callers checking `!= 0` stay correct.

---

## Behavioral contract

Numbered by the spec requirement each clause satisfies.

1. **FR-002** — A URL whose host is not in the accepted set produces a stderr message naming the
   host and the reason, exit 1, **zero network requests**, no file created. This must hold for
   `t.co` links (research D1).
2. **FR-016** — When the target file already exists: print `Already downloaded: <path>` to stderr,
   the path to stdout, exit **0**. No video bytes fetched.
3. **FR-017** — A multi-video post writes every video, one stdout line each, indexed filenames.
   stderr states how many videos the post contained.
4. **FR-015** — On any failure or `KeyboardInterrupt`, no partial file exists at any advertised
   stdout path. Files completed earlier in a multi-video post are kept and still listed.
5. **FR-014** — Progress appears on stderr within 3 seconds of transfer starting (SC-005).
6. **FR-009** — A successfully reported file always contains both video and audio streams.
7. **D9** — If `ffmpeg` is absent, fail before any transfer with a message naming the missing
   prerequisite. Not required for paths 1 or 2 above.

### Worked examples

```console
$ xvd https://x.com/jack/status/20
Fetching metadata...
jack-20.mp4  [██████████········]  61%  2.1MiB/s  eta 3s
jack-20.mp4
$ echo $?
0
```

```console
$ xvd https://x.com/jack/status/20
Already downloaded: jack-20.mp4
jack-20.mp4
$ echo $?
0
```

```console
$ xvd https://example.com/video/1
Error: refusing https://example.com/video/1 - host 'example.com' is not an X or Twitter post URL.
       Accepted hosts: x.com, www.x.com, m.x.com, mobile.x.com,
                       twitter.com, www.twitter.com, m.twitter.com, mobile.twitter.com
$ echo $?
1
```

```console
$ xvd https://t.co/abcdef
Error: refusing https://t.co/abcdef - shortened links are not accepted.
       Open the link and pass the x.com URL it resolves to.
$ echo $?
1
```

```console
$ xvd https://x.com/someone/status/123
Fetching metadata...
Post contains 2 videos.
someone-123-1.mp4  [██████████████████] 100%
someone-123-2.mp4  [██████████████████] 100%
someone-123-1.mp4
someone-123-2.mp4
$ echo $?
0
```

---

## Module boundary contract

The seam that keeps `cli.py` logic-free (Principle III) and is what a future HTTP layer will call.

### `backend/validation.py`

```python
def parse_post_url(url: str) -> PostReference:
    """Exact-host allowlist + path parse. Raises ValueError on rejection.
    Makes no network request. This is the FR-002/FR-003 gate."""

def sanitize_handle(handle: str) -> str:
    """Reduce to [A-Za-z0-9_-], truncate to 64, empty -> 'unknown'."""

def build_target(output_dir: Path, handle: str, post_id: str,
                 ext: str, index: int | None) -> OutputTarget:
    """Compose the filename and assert containment within output_dir.
    `ext` is derived by the caller from ydl.prepare_filename() on the processed
    info dict — never hardcoded, since the /best fallback skips the mp4 merge.
    Raises ValueError if the resolved path escapes. FR-010/011/012."""
```

### `backend/downloader.py`

```python
def download_post(reference: PostReference, output_dir: Path,
                  progress: Callable[[dict], None] | None = None) -> DownloadOutcome:
    """The whole capability. Framework-free: no argparse, no printing, no sys.exit.
    Communicates solely through its return value and raised ValueError."""
```

**Invariants a future HTTP layer inherits for free** — and must not duplicate:

- `download_post` never writes to stdout/stderr; it reports through `DownloadOutcome` and the
  optional `progress` callback.
- `download_post` never calls `sys.exit`. Exit-code mapping is `cli.py`'s only decision.
- Validation happens inside `download_post` via `parse_post_url`, so no caller can skip the
  Principle V gate by forgetting to validate first.

### `backend/config.py`

```python
def output_dir(override: Path | None = None) -> Path:
    """override -> $XVD_OUTPUT_DIR -> Path.cwd(). Creates the directory if absent."""
```

---

## Out of contract

Deferred to later stories; listed so nobody builds them into US1.

| Surface | Story |
|---|---|
| `--list-formats` | US3 |
| `--format ID` | US4 |
| Distinct "images but no video" diagnosis | US2 (limited — see research D6) |
| `--force` re-download | not specified; FR-016 resolved as skip-and-succeed |
| Any HTTP endpoint | separate future feature |
