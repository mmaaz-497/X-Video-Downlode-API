# Quickstart: HTTP Download API

**Feature**: 002-http-download-api | **Date**: 2026-08-13

Manual verification is the primary validation method for this project (Constitution Principle II).
This is the sequence that proves the feature works.

## Run it

```bash
uv sync
uv run uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

No configuration is required — every variable has a working default (FR-034, Principle VII). Files
land in the current directory unless `XVD_OUTPUT_DIR` says otherwise.

`ffmpeg` must be on `PATH`. Without it, downloads fail with `service_unavailable`, which is correct
behaviour: it is an operator fault and the caller is not told why.

## The happy path (US1)

```bash
# 1. Submit. This must return immediately, not after the download.
curl -s -X POST localhost:8000/jobs \
  -H 'content-type: application/json' \
  -d '{"url":"https://x.com/i/web/status/1234567890123456789}'
# → 202 {"handle":"FQPTq5…","state":"waiting","file_count":0,…}

# 2. Poll. Progress should advance between calls.
curl -s localhost:8000/jobs/FQPTq5…
# → {"state":"running","progress":{"downloaded_bytes":41943040,"total_bytes":99614720,"percent":42.1}}

# 3. Collect. No index needed for a single-video post.
curl -sOJ localhost:8000/jobs/FQPTq5…/file
```

**What to check**: step 1 returns in well under a second (SC-001) — time it with `curl -w '%{time_total}'`.
The downloaded file plays with **both picture and sound**; a silent video means the merge did not
happen. For a multi-video post, step 3 returns `409 index_required` naming the count, and
`/file/1`, `/file/2`, … each return one file (FR-035).

## Multi-caller safety (US3)

```bash
# Unknown, malformed, and wrong-length handles must all give the same 404 body.
curl -si localhost:8000/jobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | tail -1
curl -si localhost:8000/jobs/../../etc/passwd                            | tail -1
curl -si localhost:8000/jobs/short                                       | tail -1
# → all three: {"code":"not_found","message":"No such job."}

# A field trying to steer output must be ignored or rejected, never honoured (FR-004).
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' \
  -d '{"url":"https://x.com/x/status/20","output_dir":"/etc","format":"worst"}'
# → 422 with a fixed body; nothing echoed back

# Nothing anywhere may contain a path (FR-029, SC-005).
curl -s localhost:8000/jobs/<handle> | grep -Ei '/home|/var|C:\\\\|Traceback|yt.dlp|Temp'
# → no output. Run the same grep against every error response above.
```

The grep is the acceptance test for SC-005, so run it against **every** response the service can
produce, including a forced internal error.

## Rejection before any network request (FR-003)

```bash
for u in https://evil.com/x/status/20 \
         https://x.com.evil.net/x/status/20 \
         https://t.co/abc \
         https://x.com/home ; do
  curl -s -o /dev/null -w "%{http_code} $u\n" -X POST localhost:8000/jobs \
    -H 'content-type: application/json' -d "{\"url\":\"$u\"}"
done
# → 400 for every one
```

Confirm with `tcpdump`/Wireshark or by pulling the network cable: a rejected URL must generate no
outbound traffic at all. Confirm no job file appeared under `.xvd-state/jobs/`.

## Resource protection (US4)

Each check below needs its own start-up, because every limit is read once at start-up.

### Concurrency and the queue

```bash
XVD_MAX_CONCURRENT=2 uv run uvicorn backend.api:app &
# submit 10 distinct post URLs, then:
watch -n1 'curl -s localhost:8000/health'
# → running never exceeds 2; waiting drains; all 10 reach a terminal state (SC-006)
```

Submitting the **same** URL five times concurrently must yield one download and five identical
handles (SC-007).

### The pending cap, and what it must NOT break

```bash
XVD_MAX_CONCURRENT=1 XVD_MAX_PENDING=1 uv run uvicorn backend.api:app &
# four DISTINCT post URLs:
# 1 → 202 running
# 2 → 202 waiting        ← FR-015's original promise, which the cap must preserve
# 3 → 503 {"code":"at_capacity",…}
# 4 → 503 at_capacity
```

The second submission **waiting** matters as much as the third being refused. A cap that made
ordinary over-limit submissions fail would have reversed the requirement it was added to bound.

Then, still at capacity, submit a URL for the job that is already running: it must come back
**202 with the existing handle**. A dedup hit creates nothing, so the cap does not apply to it.

### The rate limit

```bash
XVD_RATE_LIMIT=2 XVD_RATE_WINDOW=120 uv run uvicorn backend.api:app &
for i in 1 2 3; do
  curl -s -D- -X POST localhost:8000/jobs -H 'content-type: application/json' \
    -d "{\"url\":\"https://x.com/a/status/$i\"}" | grep -iE '^(HTTP|retry-after)|code'
done
# → 202, 202, then:
#   HTTP/1.1 429 Too Many Requests
#   retry-after: 119
#   {"code":"rate_limited","message":"Too many submissions. Try again in 119 seconds."}
```

Check the counting rule too: with `XVD_RATE_LIMIT=2`, **two invalid URLs must exhaust the
allowance**, so a third submission of a perfectly valid URL is refused. That is deliberate —
see tasks.md Phase 3 decision 2 — and if it is unwanted it is a one-line change, not a bug.

### The free-disk floor

```bash
XVD_MIN_FREE_BYTES=99999999999999 uv run uvicorn backend.api:app &
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' \
  -d '{"url":"https://x.com/someone/status/123"}'
# → 503 {"code":"insufficient_storage","message":"The service is temporarily out of space…"}
ls "$XVD_STATE_DIR/jobs/"   # → empty. The check runs BEFORE a job exists (FR-018).
```

### The job time limit and the wedged-worker count

```bash
XVD_JOB_TIMEOUT=5 XVD_SWEEP_INTERVAL=5 uv run uvicorn backend.api:app &
# submit a real post URL, then poll:
curl -s localhost:8000/jobs/<handle>
# → {"state":"failed","failure":{"code":"time_limit",…}}
ls -a "$XVD_OUTPUT_DIR" | grep tmp-xvd   # → nothing; the frozen module's finally cleaned up
curl -s localhost:8000/health
# → wedged_workers is 0 if the worker returned, 1 if it is stuck in the ffmpeg merge
```

`wedged_workers` above zero means capacity has been permanently reduced until a restart. That is
the accepted limitation in ADR-0002, not a bug to chase — but it must be **visible**, which is why
the count exists. The operator log line to grep for is `xvd-wedged-worker`.

## Restart recovery (US6, later phase)

```bash
# start a download, then kill the process outright — no graceful shutdown
kill -9 <pid>
uv run uvicorn backend.api:app --port 8000
curl -s localhost:8000/jobs/<handle>
# → {"state":"failed","failure":{"code":"interrupted",…}}   never "running" (FR-025)
ls -a "$XVD_OUTPUT_DIR" | grep tmp-xvd   # → nothing (FR-026)
```

## Deployment note that is *not* optional

The application reads `request.client` and **never** a header — deciding whether a proxy header may
be believed is uvicorn's job, because only the operator knows what sits in front of the service.

**Verified against uvicorn 0.52.2** (`uvicorn/config.py:355-357`), and it is not what the planning
documents originally assumed:

```text
proxy_headers       = True          # ON by default
forwarded_allow_ips = "127.0.0.1"   # or $FORWARDED_ALLOW_IPS
```

`X-Forwarded-For` is therefore honoured out of the box, but only from `127.0.0.1`. Three cases, and
only one needs a flag:

| Deployment | Command | Why |
|---|---|---|
| Reverse proxy on the **same host** | *(nothing)* | The proxy connects from `127.0.0.1` and is already trusted. |
| Reverse proxy on a **different host** | `--forwarded-allow-ips=<proxy IP>` | Otherwise the proxy is untrusted, every caller collapses into its single address, and the audit log stops identifying anyone. |
| **No proxy**, exposed directly | `--no-proxy-headers` | Otherwise anything reaching the port from `127.0.0.1` — another local process, a container sharing the namespace, someone's SSH tunnel — can set its own address, forge the audit trail, and escape a per-address rate limit. |

Never `--forwarded-allow-ips='*'`. That believes the header from anyone who can reach the port, which
is the same as not checking it.

Confirm it before trusting the log:

```bash
curl -s -X POST localhost:8000/jobs -H 'content-type: application/json' \
  -H 'X-Forwarded-For: 6.6.6.6' -d '{"url":"https://not-x.example/nope"}'
tail -1 "$XVD_STATE_DIR/submissions.log"
# client_address must be the real caller. If it says 6.6.6.6, the header was
# believed and your configuration is wrong for this deployment.
```

Proxy, TLS, and service-manager configuration are out of scope for this feature; these flags are not,
because they are application arguments.

## Known limitation to watch for

If `wedged_workers` on `/health` is non-zero, a download's `ffmpeg` merge hung and that worker thread
is gone until restart (research D4). With the default `XVD_MAX_CONCURRENT=2`, one wedged worker
halves throughput. The service degrades rather than failing, and a restart clears it.

## Tests

```bash
uv run pytest
```

One new file, `tests/test_jobs.py`, covering the service layer with no network and no HTTP. The
load-bearing test asserts that **every** explanation in `downloader._ERROR_DIAGNOSES` is covered by
the failure-code map — if an upstream edit changes that table, this fails loudly instead of letting
classification decay silently to `unclassified` (research D5).
