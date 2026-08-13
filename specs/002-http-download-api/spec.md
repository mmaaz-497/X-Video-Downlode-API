# Feature Specification: HTTP Download API

**Feature Branch**: `002-http-download-api`
**Created**: 2026-08-13
**Status**: Draft
**Input**: User description: "Build an HTTP API that exposes the existing video download capability over the network, so a video can be fetched by submitting a URL to a request rather than typing a terminal command. This service will be reachable from the internet and used by people other than the operator."

## Summary

Feature 001 delivered the download capability as a terminal command. Using it requires SSH access to
the VPS, so only the operator can use it. This feature puts the same capability behind an HTTP
interface so that anyone the operator points at the service — from a browser or a phone — can submit
an X post URL and get the video back.

The defining constraint is duration. A download is not a request-shaped unit of work: a 95 MB video
took over a minute on a good connection, and larger posts take longer. A request that waits for the
transfer will be cut off by the browser and by any reverse proxy in front of the service, and holds a
server worker for the entire transfer. **The submission must therefore be decoupled from the work**:
the service accepts a URL, answers immediately with a handle, performs the download in the
background, and lets the caller ask about progress and collect the file afterwards.

The second defining constraint is that this service is exposed to the public internet and used by
people who are not the operator. Anything a caller can influence is untrusted input; anything the
service tells a caller is public disclosure; and any resource a caller can consume — bandwidth, CPU,
disk — is finite and shared.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fetch a video without a terminal (Priority: P1)

Someone with a link to an X post wants the video. They hand the URL to the service, get back a handle
straight away, check on it now and then while it works, and collect the finished file when it is
ready. At no point do they need SSH access, a terminal, or knowledge of where the file lives on the
server.

**Why this priority**: This is the entire reason the feature exists. Without it there is no product;
with it alone, the capability is genuinely usable by other people. Every other story protects or
explains this one.

**Independent Test**: Submit a known-good X post URL over HTTP, confirm a handle comes back
immediately, poll until the job reports finished, retrieve the file, and play it. Delivers the whole
value of the feature on its own.

**Acceptance Scenarios**:

1. **Given** a valid X post URL, **When** it is submitted, **Then** a job handle is returned in under
   one second and the download proceeds in the background.
2. **Given** a job that is downloading, **When** its state is requested, **Then** it reports "running"
   with a progress figure that advances between successive checks.
3. **Given** a job that has finished with exactly one file, **When** the file is requested without
   naming an index, **Then** a playable video with both picture and sound is returned.
4. **Given** a job that has not finished yet, **When** the file is requested, **Then** the caller is
   told the job is not ready rather than receiving a partial file.
5. **Given** a URL that is not an X post URL, **When** it is submitted, **Then** it is rejected in the
   submission response itself, no job is created, and no network request is made to any external
   host.
6. **Given** a job that has finished with several files, **When** the file is requested without an
   index, **Then** the request is refused with a message naming how many files there are; naming an
   index then returns that file.

---

### User Story 2 - Understand why a download failed (Priority: P2)

A caller submits a post that cannot be downloaded — it has no video, it belongs to a protected
account, it is age-restricted, or it has been deleted. They need to know which of those happened, so
they can tell the difference between "this post will never work" and "something went wrong, try
again".

**Why this priority**: Feature 001 already distinguishes these cases and produces a specific
explanation for each. Collapsing them into one generic failure would throw away work already done and
would make the service look broken when it is behaving correctly.

**Independent Test**: Submit a post with images but no video, a protected-account post, and a deleted
post. Confirm each failed job reports a different, named reason, and that the same submission always
produces the same named reason.

**Acceptance Scenarios**:

1. **Given** a post containing no video, **When** the job's state is checked, **Then** it reports
   failed with a reason identifying "no video in this post", not a generic error.
2. **Given** a post from a protected account, **When** the job's state is checked, **Then** it
   reports failed with a reason identifying that the post is not publicly accessible.
3. **Given** a failure the service cannot classify, **When** the job's state is checked, **Then** it
   reports failed with an explicit "unclassified" reason rather than pretending to be one of the
   known ones.
4. **Given** any failed job, **When** its state is checked, **Then** the reason is a stable
   machine-readable value accompanied by a short human-readable sentence.

---

### User Story 3 - Callers cannot reach each other (Priority: P2)

Several unrelated people use the service at the same time. None of them should be able to find,
observe, or download anything belonging to anyone else, and none of them should learn anything about
the server's internals from the responses they get.

**Why this priority**: The service is on the public internet with no accounts. Handle secrecy and
response hygiene are the only things standing between callers, so they are not deferrable — a leak
here cannot be retrofitted after the service is public.

**Independent Test**: Create a job as caller A. As caller B, attempt to retrieve it, attempt to
retrieve a handle that does not exist at all, and attempt a variety of malformed handles. Confirm all
attempts are refused identically and that no response body contains a filesystem path, a directory
name, or a stack trace.

**Acceptance Scenarios**:

1. **Given** a job handle that a caller is not entitled to, **When** they request its state or its
   file, **Then** they are refused, and the refusal is indistinguishable from the refusal given for a
   handle that has never existed.
2. **Given** any error condition at all, **When** the caller reads the response, **Then** it contains
   no filesystem path, no internal directory name, no host detail, and no stack trace.
3. **Given** a submission that includes extra fields attempting to name an output location or a file
   to serve, **When** it is processed, **Then** those fields have no effect on where anything is
   written or which file is returned.
4. **Given** any submission, **When** the operator inspects the service's own records, **Then** they
   can see when it happened, what URL was submitted, and the calling address.

---

### User Story 4 - The server survives being used (Priority: P2)

Multiple people submit at once, or one person submits repeatedly. The VPS has one modest connection,
a small disk, and limited CPU. The service must stay responsive and must not fill its disk or
saturate its link, refusing work cleanly when it must rather than degrading or failing halfway
through a transfer.

**Why this priority**: A public service with unbounded work per request is a self-inflicted denial of
service. This must exist before the URL is shared with anyone, but it is behind US1 because it
protects a capability that must first work.

**Independent Test**: Submit more URLs simultaneously than the configured concurrency limit and
confirm the excess wait rather than run. Submit the same URL twice and confirm one job runs. Fill the
disk below the threshold and confirm new submissions are refused with a clear message. Exceed the
per-caller rate limit and confirm refusal with a retry time.

**Acceptance Scenarios**:

1. **Given** the maximum number of downloads already running, **When** another valid URL is
   submitted, **Then** the job is accepted and reports "waiting" until a slot frees, rather than
   being dropped or running anyway.
2. **Given** a post that is already being downloaded, **When** the same post is submitted again,
   **Then** no second download starts.
3. **Given** free disk below the configured safety threshold, **When** a URL is submitted, **Then**
   the submission is refused with a message saying the service is temporarily out of space, and no
   job is created.
4. **Given** a caller who has exceeded the submission rate limit, **When** they submit again,
   **Then** they are refused with a message stating when they may retry.

---

### User Story 5 - Finished files do not accumulate (Priority: P3)

Videos are large and the VPS disk is small. Files that have been collected — or abandoned — must not
sit on disk forever, and a caller who comes back too late must be told the file has expired rather
than being handed a broken response.

**Why this priority**: Without this the service works perfectly until the disk fills, then fails
permanently. It is P3 only because the disk threshold guard in US4 prevents the catastrophic version
of this failure while retention is being built.

**Independent Test**: Complete a job, advance the clock past the retention period, run the cleanup,
and confirm the file is gone, the job reports expired, and a request for the file says so explicitly.

**Acceptance Scenarios**:

1. **Given** a job that finished longer ago than the retention period, **When** its file is
   requested, **Then** the caller is told it has expired, and receives no partial or empty file.
2. **Given** a job that finished within the retention period, **When** the cleanup runs, **Then** its
   file is untouched.
3. **Given** a file that has been removed, **When** the job's state is checked, **Then** it reports
   "expired" and is distinguishable from "failed".

---

### User Story 6 - A restart does not strand jobs (Priority: P3)

The service is restarted — deployment, reboot, crash. Jobs that were running at that moment must not
be left claiming to be running forever, and the leftovers of interrupted downloads must not
accumulate on disk.

**Why this priority**: A stuck job is permanently misleading and a leaked temporary directory is a
slow disk leak. Both are real, but they only bite after the service has been running long enough to
be restarted.

**Independent Test**: Start a job, kill the service mid-download, restart it, and confirm the job
reports a terminal state with an "interrupted" reason and that no leftover temporary download
directory remains.

**Acceptance Scenarios**:

1. **Given** a job that was running when the service stopped, **When** the service starts again,
   **Then** that job reports a terminal state — never "running" — with a reason identifying that it
   was interrupted.
2. **Given** a download interrupted by an abrupt stop, **When** the service starts again, **Then**
   leftover temporary download directories are removed and no partial file is presented as a
   finished one.
3. **Given** a job that had finished before the stop, **When** the service starts again, **Then** it
   still reports finished and its file is still retrievable.

---

### Edge Cases

- **A post containing several videos.** The existing capability downloads every video in a post and
  can therefore produce more than one file for a single submission. The job reports the count and
  each file is retrievable by index; an index-less retrieval succeeds only when there is exactly one
  file (FR-035, Q2).
- **The video is already on disk.** The existing capability treats an already-present target file as
  a success and downloads nothing. Such a job finishes almost immediately having reported no progress
  at all, so "progress advances" cannot be an invariant of every job.
- **Retention clock versus a pre-existing file.** If a file was left in the output directory by an
  earlier CLI run, a new job may complete instantly by reusing it. Retention is measured from the
  job's completion, not the file's age on disk, so such a file is not deleted immediately after being
  handed over.
- **Progress that goes backwards or never appears.** Progress figures come from the underlying
  downloader. For a multi-video post the figure restarts per video, and for a post whose total size
  is unknown, no percentage may be reportable at all. Progress is best-effort, not a guarantee.
- **A download that stalls indefinitely.** A hung transfer holds a concurrency slot forever, which
  defeats the concurrency limit. Jobs need a maximum duration (FR-020).
- **Disk fills mid-download.** The pre-submission threshold check cannot predict the file's size, so a
  download can still exhaust the disk while running. It must end as a failed job with a named reason,
  not as a crash or a silently truncated file.
- **The same caller submits the same URL twice in quick succession.** Deduplication must be on the
  canonical post identity, and must treat a URL naming a specific media item as distinct from the
  bare post URL, because they produce different files.
- **A submission whose body is malformed, oversized, or not a URL at all.** Rejected at the boundary
  with a generic message; no job created, nothing echoed back to the caller.
- **`ffmpeg` is missing on the server.** The existing capability reports this as a failure only once
  a download is actually attempted. It is an operator misconfiguration, not a caller error, so the
  caller must see a neutral "service cannot process downloads right now" reason while the operator's
  log names the real cause.
- **A caller polls status thousands of times per minute.** Status checks are cheap but not free; they
  are subject to their own limit, separate from the submission limit.

## Requirements *(mandatory)*

### Functional Requirements

#### Submission

- **FR-001**: The service MUST accept a submission containing exactly one X post URL and return a job
  handle without waiting for the download to start or finish.
- **FR-002**: The service MUST validate the submitted URL using the existing validation from feature
  001, unchanged, and MUST reject an invalid URL in the submission response itself.
- **FR-003**: A rejected URL MUST NOT create a job, MUST NOT reserve any resource, and MUST NOT cause
  any network request to any external host.
- **FR-004**: The service MUST NOT accept any caller-supplied parameter that influences where files
  are written, which file is returned, what quality is selected, or how the downloader is configured.
  A submission carries a URL and nothing else; unknown fields are ignored or rejected, never honoured.
- **FR-005**: The service MUST refuse a submission that is malformed, oversized, or not a single URL,
  with a generic message that does not echo the submitted content back to the caller.

#### Job lifecycle and status

- **FR-006**: Every job MUST be in exactly one of these states: **waiting** (accepted, not yet
  started), **running** (download in progress), **finished** (file available), **failed** (terminated
  without a file), or **expired** (finished, but the file has since been removed by retention).
- **FR-007**: The service MUST provide a way to read a job's current state by its handle.
- **FR-008**: While a job is running, its state MUST include a best-effort progress indication.
  Progress is advisory: it MUST NOT be required to be present, monotonic, or complete for a job to be
  valid.
- **FR-009**: A failed job's state MUST include a machine-readable reason code and a short
  human-readable sentence.
- **FR-010**: The service MUST expose the distinct failure diagnoses that feature 001 already
  produces as separate reason codes, at minimum: post has no video; media present but not a video;
  post belongs to a protected account; post is age-restricted; post is unavailable or deleted; the
  job was interrupted; the service cannot process downloads at present (operator-side fault); the job
  exceeded its time limit; and an explicit unclassified code for anything else.
- **FR-011**: The unclassified reason code MUST be a deliberate, visible outcome, never a silent
  substitution for one of the specific codes.

#### File retrieval

- **FR-012**: The service MUST expose, for a finished job, how many files that job produced, and MUST
  provide a way to retrieve each of them by the job's handle.
- **FR-013**: The served file MUST be exactly the bytes that were downloaded, with no transcoding,
  re-encoding, or modification, and MUST be identified by a filename derived by the server, never by
  the caller.
- **FR-014**: A request for the file of a job that is waiting, running, failed, or expired MUST be
  refused with a response that names which of those applies — never a partial, empty, or placeholder
  file.
- **FR-035**: A retrieval that names no index MUST return the file when the job produced exactly one.
  When the job produced several, it MUST be refused with a message naming the count. An index MUST
  NOT be required for the single-file case, which is the common one. *(Resolves Q2.)*
- **FR-036**: A retrieval index that is out of range, zero, negative, or not an integer MUST be
  refused without disclosing anything the caller could not already read from that job's status.

#### Resource protection

- **FR-015**: The service MUST limit the number of downloads running simultaneously to an
  operator-configured maximum. Submissions beyond that limit MUST be accepted and held in the
  **waiting** state — not dropped and not run anyway — **up to an operator-configured maximum
  pending depth**. Beyond that depth the service MUST refuse the submission with a clear message
  stating that it is at capacity.
  *(Amended 2026-08-13 by the project owner.* The original wording implied an unbounded queue, which
  on a public service is a memory-exhaustion path: the per-address rate limit in FR-019 bounds one
  caller but not the aggregate across many addresses. The pending cap is set far above the
  concurrency limit, so ordinary over-limit submissions still wait exactly as this requirement
  originally described; only the far tail is refused. The cap is implemented in Phase 3, but the
  requirement is amended now so no earlier phase is built against wording that will move.)*
- **FR-016**: A submission naming a post for which a job is already waiting or running MUST NOT start
  a second download. The caller MUST receive a usable handle for the work that is already underway.
- **FR-017**: Deduplication MUST be keyed on the canonical post identity produced by the existing
  validation, so that URL variations (host, query string, trailing slash) collapse to one job while a
  URL naming a specific media item remains distinct from the bare post URL.
- **FR-018**: The service MUST refuse new submissions when free disk space on the output volume is
  below an operator-configured threshold, with a clear message, and MUST make that check before
  creating a job.
- **FR-019**: The service MUST limit how many jobs a single caller may create within a configured
  period, and a refusal MUST state when the caller may retry.
- **FR-020**: A job that has been running longer than an operator-configured maximum duration MUST be
  terminated and reported as failed with the time-limit reason code, so that a stalled download
  cannot hold a concurrency slot indefinitely.

#### Retention

- **FR-021**: The service MUST delete a finished job's file automatically once it is older than the
  operator-configured retention period, measured from the job's completion.
- **FR-022**: A job whose file has been deleted by retention MUST report **expired**, distinct from
  **failed**, and a request for its file MUST say it has expired.
- **FR-023**: Retention MUST run without operator intervention, and MUST NOT delete a file that a
  retrieval is actively reading.

#### Restart behaviour

- **FR-024**: Job records MUST survive a restart of the service.
- **FR-025**: On start-up, any job recorded as waiting or running MUST be resolved to **failed** with
  the interrupted reason code, so no job can report "running" indefinitely. Interrupted jobs MUST NOT
  be requeued automatically. *(Resolves Q3.)*
- **FR-026**: On start-up, the service MUST remove leftover temporary download directories from the
  output directory, since an abrupt stop bypasses the cleanup the download capability performs
  itself.

#### Multi-caller safety

- **FR-027**: A job handle MUST be generated from a cryptographically secure random source and MUST
  be long enough that guessing or enumerating one is not feasible. It MUST NOT be derived from the
  URL, the post ID, a counter, a timestamp, or the caller's address.
- **FR-028**: Possession of a job's handle is what entitles a caller to that job — the handle is the
  capability, and there is no other credential. A request carrying a handle that is unknown, or that
  is malformed, MUST be refused with a response identical to every other such refusal: same status,
  same body, and no observable timing difference that distinguishes "no such job" from any other
  reason for refusal. *(Resolves Q1.)*
- **FR-029**: No response reaching a caller may contain a filesystem path, a directory name, an
  internal identifier other than the job handle, a stack trace, a library name, or the verbatim text
  of an underlying library's error.
- **FR-030**: The output directory MUST come only from server configuration, and the file served for
  a job MUST be resolved only from that job's own recorded result — never from anything in a request.
- **FR-031**: The service MUST record, for every submission, at minimum the time, the submitted URL,
  the calling address, and the outcome, in a form the operator can inspect.
- **FR-032**: The service MUST NOT store any caller-supplied free text. The URL is recorded because it
  is required for abuse investigation, and it is recorded only after passing validation, in its
  canonical form.
- **FR-033**: Detailed diagnostic information MUST be available to the operator for every failure,
  and MUST be correlatable to the caller-visible response without that response carrying the detail.

#### Configuration

- **FR-034**: Concurrency limit, **maximum pending depth**, retention period, disk threshold, rate
  limit, job time limit, and output directory MUST all be operator configuration with working
  defaults, so the service starts and runs correctly with no configuration supplied.

### Key Entities

- **Job**: One submission's unit of work. Carries an unguessable handle, the canonical post identity
  it is downloading, its state, when it was created and when it reached a terminal state, its
  progress while running, its failure reason if it failed, and a reference to the resulting file if
  it finished. Its handle is the only name for it that ever reaches a caller.
- **Failure Reason**: A closed set of machine-readable codes, each paired with a short caller-safe
  sentence. Deliberately includes an "unclassified" member.
- **Downloaded Artifact**: The finished video file produced by a job, together with the completion
  time that starts its retention clock. Located only by way of its job; never addressed directly by a
  caller.
- **Submission Record**: The operator-facing audit entry for one submission — time, canonical URL,
  calling address, outcome. Contains no caller free text.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A submission receives its handle in under one second, measured at the 95th percentile,
  regardless of how large the video turns out to be or how many downloads are already running.
- **SC-002**: A person with only a phone and a link can obtain the video file without SSH access, a
  terminal, or knowledge of the server's filesystem.
- **SC-003**: A download of a 95 MB video completes without any request being cut short by a browser
  or proxy timeout.
- **SC-004**: Every failure a caller can trigger resolves to one of the named reason codes, and the
  same input always produces the same code across runs.
- **SC-005**: No response body produced by the service, under any tested condition including forced
  internal errors, contains a filesystem path, directory name, or stack trace.
- **SC-006**: With ten simultaneous submissions and a concurrency limit of two, no more than two
  downloads are in progress at any instant, and all ten jobs reach a terminal state.
- **SC-007**: Submitting the same post URL five times concurrently results in exactly one download.
- **SC-008**: Free disk on the output volume never falls below the configured threshold as a result
  of accepted submissions.
- **SC-009**: No finished file remains on disk more than the retention period plus one cleanup
  interval after its job completed.
- **SC-010**: After an abrupt service kill and restart, zero jobs report "running" and zero leftover
  temporary download directories remain.
- **SC-011**: Job handles carry at least 128 bits of entropy; an attacker making continuous requests
  has a negligible chance of hitting a valid handle.

## Assumptions

1. **Open access, rate-limited.** The user placed accounts and login out of scope and asked for a
   per-caller rate limit, so submission is assumed to be open to anyone who can reach the service,
   with abuse controlled by rate limiting and audit logging rather than by authentication.
2. **A caller is identified by network address.** With no accounts, the rate limit and the audit
   record identify a caller by the address the request arrives from. This is understood to be
   imprecise — callers behind one NAT share a limit — and is accepted as the only identity available.
3. **Defaults.** Where the user specified a control but not a number, these defaults apply and are all
   operator-configurable: concurrency limit **2**; retention **24 hours**; free-disk threshold
   **2 GB**; submission rate limit **10 per hour per address**; job time limit **30 minutes**;
   retention sweep every **15 minutes**.
4. **Quality selection is not exposed.** Feature 001 supports listing and choosing formats. Exposing
   that would add a caller-supplied parameter that reaches the downloader, which FR-004 forbids. Jobs
   use the default quality selection only.
5. **The service runs as a single process on one VPS.** Nothing here assumes or requires multiple
   instances, and the concurrency, deduplication, and rate limits are per-service, not cluster-wide.
6. **Progress is best-effort.** The underlying downloader reports progress for the transfer phase
   only; metadata resolution and the final merge report nothing. Callers must tolerate gaps.
7. **Status polling is the mechanism.** No requirement here implies push notification, callbacks, or
   long-lived connections; the caller asks for state when it wants it.

## Dependencies and Constraints

### Frozen modules

`backend/downloader.py`, `backend/validation.py`, and `backend/config.py` MUST NOT be modified. This
feature consumes them through their existing public surface: `parse_post_url()` for validation,
`download_post()` for the work, and `output_dir()` for the destination.

Four requirements meet friction against that boundary. Per the instruction to report rather than
change, they are recorded here, each with the way it is satisfied without touching those files:

1. **FR-010 (distinct failure codes) — the module exposes no codes.** `download_post()` returns a
   `DownloadOutcome` whose `status` is only `downloaded`, `skipped`, or `failed`; the specific
   diagnosis exists solely as English prose in `message`, produced by a private table in
   `backend/downloader.py`. To emit distinct machine-readable codes, this feature must classify that
   prose by matching it. **Consequence**: the classification duplicates knowledge of a private table.
   If that wording ever changes, classification silently degrades to the unclassified code — which is
   why FR-011 requires that code to be a visible outcome, and why the classification must be pinned
   by tests that will fail loudly rather than drift. The clean fix — a reason code on
   `DownloadOutcome` — requires modifying `downloader.py` and is therefore **not** taken here.

2. **FR-029 (no path leakage) — the module's messages contain paths.** `DownloadOutcome.message` and
   the exceptions `download_post()` raises can carry absolute filesystem paths: the promotion failure
   names the temporary directory, the cleanup warning names it, and the path-containment check in
   `validation.py` names both the candidate path and the output root. **Consequence**: the message
   from the module MUST NOT be passed through to a caller under any circumstances. Caller-visible
   text comes from this feature's own catalog of safe sentences; the module's message goes to the
   operator's log. This costs nothing except the discipline of never forwarding it.

3. **FR-020 (job time limit) — the module offers no cancellation.** `download_post()` runs to
   completion and has no cancel hook or timeout parameter. It does accept a progress callback that
   the downloader invokes during transfer, and raising from that callback aborts the download and
   returns a failed outcome with temporary files cleaned up — so a time limit is achievable without
   modifying the module. **Residual gap**: the callback is not invoked during metadata resolution, so
   a hang before any bytes move is not interruptible by that route and needs a coarser guard. This is
   a real limitation of the boundary and is recorded rather than solved by changing the module.

4. **Principle III (thin HTTP layer) — job orchestration is new logic that has nowhere to go.**
   The constitution requires the HTTP layer to parse, call down, and serialize, with no business
   logic of its own. Job state, scheduling, deduplication, retention, disk guarding, rate limiting,
   and restart recovery are none of them download logic, and they cannot be added to the frozen
   modules. **Resolution**: they belong in a new module under `backend/`, below the HTTP layer and
   beside the downloader — not inside the request handlers. The HTTP layer stays thin; the new logic
   is service logic, not transport logic. The `/sp.plan` stage should confirm this against the
   Constitution Check gate.

### Other constraints

- **Constitution Principle IV (lean dependencies)**: FR-024 requires job records to survive a
  restart, and Principle IV forbids adding a database or task queue unless explicitly requested. The
  requirement is therefore stated as durability, not as storage technology; the plan must satisfy it
  with the filesystem, consistent with the "persistence: filesystem only" constraint.
- **Constitution Principle I**: all code lives under `backend/`.
- **Constitution Principle V**: the URL allowlist gate is satisfied by calling the existing
  `parse_post_url()`; this feature adds no second validation path and no bypass.
- **`ffmpeg`** remains a required system binary, and its absence is an operator fault surfaced
  neutrally to callers.

## Out of Scope

- Any web page, HTML interface, or frontend. This feature is the API only.
- User accounts, login, sessions, or per-user history.
- Streaming, transcoding, or re-encoding. Files are served exactly as downloaded.
- Downloading from any site other than X.
- Batch submission, timelines, or bulk operations.
- Quality or format selection by the caller (see Assumption 4).
- HTTPS termination, domain setup, and reverse proxy configuration — deployment concerns.
- Any measure to evade X's rate limiting, including proxying or IP rotation.
- Push notifications, webhooks, or callbacks on job completion.
- Any modification to `backend/downloader.py`, `backend/validation.py`, or `backend/config.py`.

## Resolved Clarifications

All three open questions were answered by the project owner on 2026-08-13. No clarifications remain.

### Q1 — What entitles a caller to a job? → **Capability model**

Possession of the handle is the authorization; there is no second credential. Unknown, malformed, and
unauthorized handles all receive one identical refusal, so a caller cannot learn whether a handle
corresponds to a real job.

**Consequences carried into the design**: FR-027's entropy requirement is what makes this sound, so
it is not negotiable. A handle that leaks — pasted into a chat, recorded by a proxy log — grants
access to whoever holds it, which is accepted. Deduplication (FR-016) is unaffected: two callers
submitting the same post receive the same handle and both are entitled by holding it.

**Rejected**: address binding (breaks a phone moving between networks, and breaks deduplication);
a separate secret token (needs a rule for the second submitter of a deduplicated job, for no gain
the entropy requirement does not already provide).

### Q2 — What happens when a post contains several videos? → **List of files, index only when needed**

The job reports how many files it produced and each is retrievable. Retrieval without an index
returns the file when there is exactly one; when there are several, it is refused with a message
naming the count, and the caller retries with an index. **The single-video case — the overwhelmingly
common one — never requires an index.**

**Consequences carried into the design**: encoded as FR-012, FR-035, and FR-036. The refusal for a
multi-file job is a normal, expected response, not an error condition, and the count it discloses is
the same count the job's status already carries.

**Rejected**: serving only the first video (downloads bytes no one can fetch, leaving retention to
clean up waste); refusing multi-video posts (discards a capability feature 001 deliberately built).

### Q3 — What state do restart-interrupted jobs end in? → **Failed, with the interrupted reason**

Jobs recorded as waiting or running at start-up are failed with the interrupted reason code. They are
not requeued.

**Consequences carried into the design**: encoded as FR-025. A caller learns immediately rather than
waiting on work that will never resume, and a job that crashed the service is not retried on every
subsequent start-up. Resubmission is cheap in practice, because a file that did complete before the
stop is still in the output directory and feature 001's already-downloaded check finishes such a job
without transferring anything.

**Rejected**: automatic requeue (retries a job that may have caused the crash, and re-queues an
entire burst at once; would need a retry cap to be safe).