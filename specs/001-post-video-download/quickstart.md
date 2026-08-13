# Quickstart: Single Post Video Download

**Feature**: `001-post-video-download` | **Date**: 2026-08-12

Manual CLI verification is the primary validation method for this project (Principle II). This is
the script to run.

---

## Prerequisites

| Requirement | Check | Status in this environment |
|---|---|---|
| Python 3.11+ | `python --version` | ✅ 3.13.5 |
| uv | `uv --version` | ✅ 0.8.0 |
| ffmpeg on PATH | `ffmpeg -version` | ✅ `C:\Users\HP\scoop\shims\ffmpeg.exe` |

## Setup

```bash
uv sync
```

Windows PowerShell 5.1 and Linux both work; all paths go through `pathlib`, so nothing here is
shell-specific.

## Deployment (Linux VPS)

The whole of it — no Docker, no systemd, no CI (Principle VII):

```bash
sudo apt install -y python3.11 ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> && cd x-video-downloder
uv sync
uv run xvd https://x.com/<author>/status/<id>
```

---

## Verification script

Run these in order. Each maps to acceptance criteria in `spec.md`. Substitute a real public X post
URL containing a video for `$VIDEO_URL`.

### 1. Happy path — US1 scenario 1, FR-009, SC-003

```bash
uv run xvd "$VIDEO_URL"
echo "exit=$?"
```

**Expect**: progress on stderr, one video path on stdout, `exit=0`. The extension is usually `.mp4`
but will be the native container (e.g. `.webm`) when the post offers a progressive rendition and no
merge occurs — that is correct behavior, not a bug (research D3).

**Verify audio is present** — a silent video is a failed download, so check rather than assume:

```bash
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 <file>.mp4
```

Must list **both** `video` and `audio`.

### 2. Non-X URL rejected with no network — US1 scenario 2, FR-002, SC-002

```bash
uv run xvd "https://example.com/video/1"; echo "exit=$?"
uv run xvd "https://x.com.evil.net/a/status/1"; echo "exit=$?"
uv run xvd "https://t.co/abcdef"; echo "exit=$?"
```

**Expect**: all three rejected, `exit=1`, no file created, returns instantly.

The look-alike host is the important one — it is the case a substring check would wrongly accept.
To prove no network request was made, run one with networking disabled; it must still fail
instantly with the same message rather than a connection error.

### 3. Idempotent re-run — US1 scenario 6, FR-016, SC-009

```bash
uv run xvd "$VIDEO_URL"          # first run, downloads
md5sum <file>.mp4                # or Get-FileHash on Windows
uv run xvd "$VIDEO_URL"          # second run
echo "exit=$?"
md5sum <file>.mp4
```

**Expect**: second run prints `Already downloaded:`, returns immediately, `exit=0`, hash unchanged.

### 4. Interrupt leaves nothing behind — US1 scenario 3, FR-015, SC-004

```bash
uv run xvd "$LARGE_VIDEO_URL"    # press Ctrl+C mid-transfer
echo "exit=$?"
ls -la                           # inspect the output directory
```

**Expect**: `exit=1`, and **no** `.mp4`, no `.part`, no leftover `.tmp-*` directory.

Pick a genuinely large video so there is time to interrupt. Run this twice — once during transfer,
once during muxing — since those are different code paths.

### 5. Multi-video post — US1 scenario 7, FR-017, SC-008

```bash
uv run xvd "$MULTI_VIDEO_URL"
echo "exit=$?"
```

**Expect**: stderr states the count, one `-1.mp4` and `-2.mp4` on stdout, neither overwriting the
other, `exit=0`.

### 6. Missing ffmpeg — spec edge case, research D9

Temporarily shadow ffmpeg on PATH, then:

```bash
uv run xvd "$VIDEO_URL"; echo "exit=$?"
```

**Expect**: fails **before** any transfer with a message naming ffmpeg as the missing prerequisite,
`exit=1`. Then confirm the rejection path from step 2 still works with ffmpeg absent — validation
must not require it.

### 7. Output directory — FR-013

```bash
uv run xvd "$VIDEO_URL" --output-dir ./downloads
XVD_OUTPUT_DIR=./downloads uv run xvd "$VIDEO_URL"
```

**Expect**: file lands in `./downloads` both ways; the flag wins if both are set.

### 8. Scriptability — FR-018, SC-007

```bash
if uv run xvd "$VIDEO_URL" > paths.txt 2>/dev/null; then
  echo "ok: $(cat paths.txt)"
else
  echo "failed as expected"
fi
```

**Expect**: `paths.txt` contains only paths — no progress noise, no diagnostics. This is the test
that stdout/stderr separation actually holds.

---

## Automated tests

Two files only (Principle II). Neither makes a network request.

```bash
uv run pytest
```

| File | Covers |
|---|---|
| `tests/test_validation.py` | Accepted hosts, look-alike rejection, `t.co`, `i/web/status`, query/trailing-slash noise, handle sanitization, path containment |
| `tests/test_downloader.py` | The extractor wrapper: playlist vs flat info-dict branching, filename indexing, error-string → diagnosis mapping — all against literal info dicts |

If a test needs the network, it does not belong here — that is what the manual script above is for.
