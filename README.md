# x-video-downloader

Download the video from a single X (Twitter) post — from the terminal, or over HTTP.

Give it one post URL. It fetches every video in that post, writes the files to a directory you
choose, and tells you what happened in a plain sentence. Nothing else: no account, no login, no
timeline scraping, no bulk crawling.

- **`xvd`** — a CLI for one-off downloads.
- **`backend.api`** — a small FastAPI service that accepts a URL, hands back a job handle, and
  serves the finished file.

Both are the same code underneath. The download logic knows nothing about terminals or HTTP.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.11+ | Developed on 3.13. |
| [`uv`](https://docs.astral.sh/uv/) | Used for dependency management and running. |
| `ffmpeg` on `PATH` | Needed to merge separate video and audio streams. Located with `shutil.which` — no hardcoded paths. |

`yt-dlp` is used as a **library**, never as a subprocess.

## Install

```bash
git clone <your-clone-url> x-video-downloader
cd x-video-downloader
uv sync
```

Configuration is optional. Copy `.env.example` to `.env` if you want to change anything; every
setting has a working default, so the tool runs immediately after `uv sync` with no `.env` at all.

---

## CLI

```bash
uv run xvd https://x.com/someone/status/1234567890
```

### Options

| Option | Meaning |
|---|---|
| `--output-dir DIR` | Where to write the video. Default: `$XVD_OUTPUT_DIR`, else the current directory. Created if absent. |
| `--list-formats` | Print the available quality options and exit. Downloads nothing and writes nothing — not even an empty directory. |
| `--format ID` | Download one specific format id from `--list-formats`. Passed to yt-dlp verbatim. |

`--list-formats` and `--format` are opposite ends of one workflow, so asking for both is rejected as
a usage error.

### Output streams

**stdout carries output paths and nothing else.** Progress, warnings, and result sentences go to
stderr. So this works, and gives you a file containing only paths:

```bash
uv run xvd https://x.com/someone/status/1234567890 > paths.txt
```

The one exception is `--list-formats`, where the listing *is* the output and there are no paths to
print.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Downloaded, or the file was already there. |
| `1` | Failed, the URL was rejected, or you interrupted it. |
| `2` | Usage error. |

Already having the file counts as success. A second run over the same post is a no-op that reports
"skipped" rather than re-downloading.

### File naming

Files are written as `<author-handle>-<post-id>.<ext>`, and `<author-handle>-<post-id>-<n>.<ext>`
when a post carries several videos. The handle is reduced to `[A-Za-z0-9_-]`, so path separators and
`..` cannot appear in a name, and the composed path is proven to stay inside the output directory
before anything is written.

The extension comes from yt-dlp's own metadata and is never hardcoded — a progressive rendition
keeps its native container instead of being given a `.mp4` name it would not deserve.

### Atomicity

Work happens in a temp directory *inside* the output directory, and the finished file is promoted
with `os.replace`. Ctrl+C leaves no partial file at the destination.

---

## HTTP API

```bash
uv run uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

Interactive docs are at `/docs`.

Submission is asynchronous: you post a URL, you get a handle back immediately, and you poll.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Submit a URL. Returns `202` with a job handle. |
| `GET` | `/jobs/{handle}` | Job status and progress. |
| `GET` | `/jobs/{handle}/file` | The file, when the job produced exactly one. |
| `GET` | `/jobs/{handle}/file/{index}` | One file of a multi-file job, 1-based. |
| `GET` | `/health` | Liveness and capacity. Aggregate counts only. |

### Submitting

```bash
curl -sX POST http://127.0.0.1:8000/jobs \
  -H 'content-type: application/json' \
  -d '{"url": "https://x.com/someone/status/1234567890"}'
```

The body accepts `url` and **nothing else** — unknown fields are refused rather than ignored, so an
`output_dir` or `format` a caller invents can never be silently accepted today and silently honoured
tomorrow. The output location is server configuration and is never taken from a request.

```json
{
  "handle": "0Xk3...",
  "state": "waiting",
  "file_count": 0,
  "progress": null,
  "failure": null,
  "created_at": "2026-08-19T10:31:02.115000+00:00",
  "completed_at": null
}
```

Submitting a URL that is already `waiting` or `running` returns the existing job's handle rather
than starting a second download. The response is identical to a fresh acceptance — you hold a usable
handle for the work you asked for either way.

### Polling

```bash
curl -s http://127.0.0.1:8000/jobs/<handle>
```

| State | Meaning |
|---|---|
| `waiting` | Accepted, queued behind the concurrency limit. |
| `running` | Downloading. `progress` carries `downloaded_bytes`, `total_bytes`, `percent`. |
| `finished` | Done. Fetch the file. |
| `failed` | Ended with a named reason in `failure.code` / `failure.message`. |
| `expired` | Finished, but the retention period passed and the file was deleted. |

`total_bytes` and `percent` may be `null` while running — HLS renditions report no total, and the
service does not invent one.

### Fetching the file

```bash
curl -sOJ http://127.0.0.1:8000/jobs/<handle>/file
```

A job that produced several files refuses the un-indexed request with `409` and names the count; ask
again with `/file/1`, `/file/2`, and so on. An index is never required for the single-video case,
which is the common one.

### Failure codes

`failure.code` on a failed job:

| Code | Kind |
|---|---|
| `no_video` | Permanent — the post has no video. |
| `not_a_video` | Permanent — the media item you named is not a video. |
| `protected_account` | Permanent — not publicly accessible. This tool does not authenticate. |
| `age_restricted` | Permanent — not publicly accessible. |
| `post_unavailable` | Permanent — not found; it may have been deleted. |
| `interrupted` | Transient — submit it again to retry. |
| `time_limit` | Transient — the job exceeded `XVD_JOB_TIMEOUT`; a slow transfer often succeeds on a second attempt. |
| `service_unavailable` | Server-side — your link is fine; the service cannot download right now. |
| `unclassified` | Unexpected; worth trying again. |

### Error responses

Every error has the same shape — `{"code": ..., "message": ...}`:

| Status | Code | When |
|---|---|---|
| `400` | `invalid_url` | Not a valid X post URL. No network request was made. |
| `404` | `not_found` | Unknown handle, malformed handle, out-of-range index, or unrouted path. All identical by design. |
| `405` | `method_not_allowed` | Wrong method for the path. |
| `409` | `not_ready` | The job has not finished yet. |
| `409` | `index_required` | Several files; ask by index. |
| `409` | `failed` | The job failed; the message is the failure sentence. |
| `410` | `expired` | The retention period passed and the file is gone. |
| `413` | `too_large` | Request body too large. |
| `422` | `invalid_request` | Body not understood. |
| `429` | `rate_limited` | Per-address limit. Carries a `Retry-After` header. |
| `500` | `internal_error` | A bug. Details reach the log, never the response. |
| `503` | `insufficient_storage` / `at_capacity` | Temporary; try again later. |

### Health

```bash
curl -s http://127.0.0.1:8000/health
```

```json
{"status": "ok", "running": 1, "waiting": 0, "wedged_workers": 0}
```

Aggregate counts only — no handles, no URLs, no addresses. This endpoint is unauthenticated and
reachable by anyone who can reach the port, and what it may carry is decided by that fact.

Watch `wedged_workers`. See [Known limitation](#known-limitation).

---

## Configuration

Every variable is optional. Precedence for the output directory is `--output-dir` flag >
environment variable > default.

| Variable | Default | Meaning |
|---|---|---|
| `XVD_OUTPUT_DIR` | current directory | Where finished video files are written. Created if absent. |
| `XVD_STATE_DIR` | `<output>/.xvd-state` | Job records and the submission audit log. Created with mode `0700`. |
| `XVD_MAX_CONCURRENT` | `2` | How many downloads run at once. This is the whole concurrency limit — it is the thread pool's size, and there is no second mechanism. |
| `XVD_MAX_PENDING` | `50` | How many jobs may sit in `waiting` before new submissions get `at_capacity`. Deliberately far above the concurrency limit: ordinary over-limit submissions are meant to *wait*, and only the far tail is refused. |
| `XVD_JOB_TIMEOUT` | `1800` | Seconds a job may run, measured from when the download **starts** — queued time is not charged against it. |
| `XVD_MIN_FREE_BYTES` | `2147483648` | Refuse new submissions below this much free space, checked before a job is created. `0` disables the check. |
| `XVD_RATE_LIMIT` | `10` | Submissions one address may make per window. |
| `XVD_RATE_WINDOW` | `3600` | Length of that window, in seconds. |
| `XVD_RETENTION` | `86400` | Seconds a finished job's file is kept, measured from the job's completion. |
| `XVD_SWEEP_INTERVAL` | `900` | How often the maintenance sweep runs. |
| `XVD_LOG_LEVEL` | `INFO` | `CRITICAL`/`ERROR`/`WARNING`/`INFO`/`DEBUG`. An unrecognised value warns and falls back to `INFO`. |

Two notes worth reading before tuning anything:

- **The rate limit counts submissions, not jobs created.** An invalid URL and a duplicate both
  consume allowance. That is deliberate — an uncounted invalid URL would be a free, unlimited abuse
  route — but it does mean a caller who mistypes `XVD_RATE_LIMIT` times is refused for the rest of
  the window. The counters live in memory and reset when the service restarts.
- **Keep `XVD_LOG_LEVEL` at `INFO`** unless you have a reason. `INFO` is the level at which the
  service logs, for each failed job, the downloader's own explanation alongside the job handle. That
  line is the only thing connecting the short sentence a caller was shown to the actual reason. At
  `WARNING` you keep the operational alarms and lose every "why did this job fail" answer.

Retention is measured from the job's completion, not from the file's timestamp on disk — so a job
that finished instantly by reusing a file an earlier run left behind still gets a full period.
Deletion happens on the sweep, so a file can outlive its retention by up to one `XVD_SWEEP_INTERVAL`.

### Accepted hostnames (not configurable)

The allowlist is compiled in rather than read from the environment — a security control that can be
widened by an environment variable is not a security control. All eight, matched exactly:

```
x.com          www.x.com          m.x.com          mobile.x.com
twitter.com    www.twitter.com    m.twitter.com    mobile.twitter.com
```

Rejected: `t.co` and other shorteners (resolving one would itself be the network request that
rejection exists to avoid), `.onion` mirrors, and look-alikes such as `x.com.evil.net`. Query
strings, fragments, and userinfo never survive into what the extractor is given.

---

## Deployment

### Who the service thinks you are

The application reads the caller's address **from the connection and never from a header**. Deciding
whether a proxy header may be believed is uvicorn's job, because only the operator knows what sits
in front of the port.

uvicorn's defaults are `proxy_headers=True` and `forwarded_allow_ips="127.0.0.1"`, so
`X-Forwarded-For` **is** honoured out of the box — but only for connections arriving from
`127.0.0.1`. Three cases follow, and only one needs no flag:

1. **Reverse proxy on the same host** (the usual nginx setup) — nothing to do. The proxy connects
   from `127.0.0.1` and is already trusted.
2. **Reverse proxy on a different host** —
   `uv run uvicorn backend.api:app --forwarded-allow-ips=<the proxy's IP>`. Without it the proxy is
   not trusted, every caller collapses into the proxy's single address, and the audit log becomes
   worthless for the abuse investigation it exists to support.
3. **No proxy; the service is exposed directly** —
   `uv run uvicorn backend.api:app --no-proxy-headers`. Turn it off, or anything that can reach the
   port from localhost can set its own address in a header, forge the audit trail, and escape the
   per-address rate limit.

Never use `--forwarded-allow-ips='*'`. That believes the header from anyone who can reach the port,
which is the same as not checking.

Whichever case applies, confirm it before trusting the log:

```bash
tail -1 "$XVD_STATE_DIR/submissions.log"
```

`client_address` must be the real caller.

### State on disk

The state directory holds one JSON file per job, written temp-then-`os.replace`, plus
`submissions.log` — one JSON line per submission, refusals included. There is no database, queue,
cache, or scheduler.

**A handle is the only credential there is.** Anyone who can read the state directory can fetch any
caller's file, which is why the directory is created `0700`.

A URL that failed validation is *not* written to the audit log: it is unvalidated, caller-chosen
text destined for a file an operator will open in a terminal. The address and the timestamp are what
identify abuse.

### Restarts

Start-up adopts the previous process's job records before accepting a single connection. Jobs that
were mid-download when the process died are resolved to `interrupted` and written back to disk
before serving resumes, so a crash cannot leave a job reporting `running` forever. Leftover temp
directories from the previous run are swept at the same time.

Shutdown does not block on in-flight downloads — a transfer can run for minutes, and blocking every
deploy on one is worse than recovering it on the next start-up.

---

## Known limitation

`ffmpeg`'s merge step is invoked with no timeout and fires no progress hook the service can reach. A
download that hangs *inside the merge* is still failed on schedule — no caller waits forever — but
its worker thread stays occupied until the service restarts.

That is what `wedged_workers` on `/health` counts, and `status` flips to `degraded` when it is
non-zero. A non-zero value means capacity has been permanently reduced and only a restart recovers
it. Metadata resolution, by contrast, *is* bounded by yt-dlp's own timeout.

---

## What a caller never sees

A deliberate constraint, not an emergent one:

- No filesystem path, output directory, calling address, or free-space figure ever reaches a
  response.
- No caller-visible message is derived from an exception's text or a library's error string. Every
  caller-facing sentence is a literal in the source.
- A job record has **no field capable of holding the downloader's own message**, so the leak is
  prevented by the absence of a field rather than by remembering to sanitise.
- A caller-supplied handle never becomes a path component. Lookup is an in-memory dictionary hit, so
  a request cannot reach the filesystem by handle at all.
- Framework defaults that leak are replaced: pydantic's validation error echoes the offending input
  verbatim, and Starlette's 404 differs in shape from ours. Both are overridden.

The operator gets all of it in the log; the caller gets one sentence.

---

## Development

```bash
uv sync
uv run pytest
```

302 tests, no network calls, no mocking framework — fakes are passed through explicit keyword-only
seams (`download`, `now`, `free_space`) that the HTTP layer never supplies.

### Layout

```
backend/
  validation.py   URL allowlist, post-reference extraction, safe filename construction
  config.py       output directory resolution
  downloader.py   the download itself; framework-free, speaks only through return values
  jobs.py         job records, scheduling, retention, the caller-safe failure vocabulary
  api.py          HTTP transport: parse, call jobs, serialise
  cli.py          terminal transport: argparse, formatting, exit codes
tests/
```

The layering is a rule the code is written to: `api.py` and `cli.py` are transports and hold no
business logic; `jobs.py` imports nothing from the web stack and starts no event loop, which is what
lets the whole service layer be exercised by plain function calls; `downloader.py`, `validation.py`,
and `config.py` sit below both and are treated as frozen. `pydantic` appears in `api.py` and nowhere
else.

---

## Scope

This downloads video from a **single public X post** you give it a URL for. It does not
authenticate, does not reach protected or age-restricted posts, does not resolve shortened links,
does not crawl timelines, and has no bulk mode. Make sure you have the right to download whatever
you point it at.
