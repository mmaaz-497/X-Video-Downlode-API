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

## Concurrency and the queue (US4, later phase)

```bash
XVD_MAX_CONCURRENT=2 uv run uvicorn backend.api:app &
# submit 10 distinct post URLs, then:
watch -n1 'curl -s localhost:8000/health'
# → running never exceeds 2; waiting drains; all 10 reach a terminal state (SC-006)
```

Submitting the **same** URL five times concurrently must yield one download and five identical
handles (SC-007).

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

Behind a reverse proxy, `request.client.host` is the **proxy's** address. Every caller would then
share one rate-limit bucket and the audit log would record the proxy for every submission — FR-019
and FR-031 both silently break. Start uvicorn with:

```bash
uv run uvicorn backend.api:app --proxy-headers --forwarded-allow-ips=127.0.0.1
```

`--forwarded-allow-ips` must name the proxy specifically. Trusting `X-Forwarded-For` from anyone lets
any caller spoof their address and defeat the rate limit entirely (research D9).

Proxy, TLS, and service-manager configuration are out of scope for this feature; this flag is not,
because it is an application argument.

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
