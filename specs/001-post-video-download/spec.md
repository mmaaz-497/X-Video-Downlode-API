# Feature Specification: Single Post Video Download

**Feature Branch**: `001-post-video-download`  
**Created**: 2026-08-12  
**Status**: Draft  
**Input**: User description: "Build a command-line tool that downloads a video from a single X (formerly Twitter) post URL onto the local machine."

## Why This Exists

The operator needs to reliably save videos from X posts for offline viewing. The browser offers no
download option, and existing web-based downloaders are unreliable, ad-heavy, and cannot be trusted
with URLs. This tool is one the operator controls, running on their own machine and later on their
own VPS.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Save a video from a post URL (Priority: P1) 🎯 MVP

As the operator, I paste an X post URL into my terminal, press enter, and after a short wait a
playable video file exists in my output directory. I see enough progress output to know the tool is
working and not hung.

**Why this priority**: This is the entire reason the tool exists. Every other story is a refinement
of this one. Shipped alone, it fully solves the operator's problem for the common case.

**Independent Test**: Run the command with a public X post URL containing a video and confirm a
complete, playable file with both video and audio appears in the output directory.

**Acceptance Scenarios**:

1. **Given** a public X post containing a video, **When** I run the command with its URL, **Then** a
   complete playable file containing both video and audio appears in the output directory, and the
   command exits with a success status.
2. **Given** a URL from a site other than X or Twitter, **When** I run the command, **Then** it is
   rejected immediately with an explanation of why, no network request is made, no file is created,
   and the command exits with a failure status.
3. **Given** a running download, **When** I interrupt it with Ctrl+C, **Then** no partial file
   remains in the output directory and the command exits with a failure status.
4. **Given** a download in progress, **When** I watch the terminal, **Then** I see progress advancing
   so I can tell the tool is working rather than hung.
5. **Given** a post whose video and audio are provided as separate streams, **When** the download
   completes, **Then** the resulting single file plays with audible sound.
6. **Given** a post I have already downloaded, **When** I run the same command again, **Then** I am
   told the video is already downloaded, the existing file is left untouched, no video data is
   re-fetched, and the command exits with a success status.
7. **Given** a post containing two videos, **When** I run the command, **Then** I am told the post
   contains two videos, both are saved under indexed filenames, and neither overwrites the other.

---

### User Story 2 - Understand why a post yields no video (Priority: P2)

As the operator, when a URL does not produce a video, I want to be told the specific reason so I know
whether to fix my input, give up, or try later — rather than staring at a generic failure.

**Why this priority**: Without it the tool still downloads videos, but every non-video URL becomes a
debugging session. High value, low cost, but not required for the MVP to deliver.

**Independent Test**: Run the command against a text-only post, an image-only post, a deleted post,
and a protected-account post, and confirm each produces a distinct, accurate explanation.

**Acceptance Scenarios**:

1. **Given** a public X post containing only text, **When** I run the command, **Then** I see a
   message saying the post has no video, no file is created, and the command exits with a failure
   status.
2. **Given** a post containing only images, **When** I run the command, **Then** I see a message
   stating the post contains images but no video.
3. **Given** a URL for a deleted or nonexistent post, **When** I run the command, **Then** I see a
   message stating the post could not be found.
4. **Given** a post belonging to a protected or private account, **When** I run the command, **Then**
   I see a message stating the post is not publicly accessible and that this tool does not
   authenticate.

---

### User Story 3 - Inspect available qualities before downloading (Priority: P2)

As the operator, I want to see what quality options exist for a post without downloading anything, so
I can deliberately choose a smaller file when bandwidth or disk space matters.

**Why this priority**: Enables the deliberate-choice workflow, but the default highest-quality
behavior from US1 already serves the common case.

**Independent Test**: Run the list command against a post known to have multiple qualities and
confirm the options are displayed and no file is written.

**Acceptance Scenarios**:

1. **Given** a post whose video is available in several qualities, **When** I run the list command,
   **Then** I see each available option with enough detail to choose between them, and no file is
   created.
2. **Given** a post whose video is available in exactly one quality, **When** I run the list command,
   **Then** I see that single option.
3. **Given** any URL, **When** I run the list command, **Then** the same validation and no-video
   diagnosis rules from US1 and US2 apply before anything is listed.

---

### User Story 4 - Download a chosen quality (Priority: P3)

As the operator, having seen the available options, I want to request one specific quality so the
file I get is the size I chose.

**Why this priority**: Completes the deliberate-choice workflow started in US3. Useless without US3,
and the default behavior covers most needs.

**Independent Test**: List the qualities for a post, request one specific option by its displayed
identifier, and confirm the downloaded file matches that option.

**Acceptance Scenarios**:

1. **Given** a post whose video is available in several qualities, **When** I list qualities and then
   request a specific one, **Then** the file I get matches the quality I requested.
2. **Given** a requested quality that is not available for that post, **When** I run the command,
   **Then** I see a message naming what I asked for and what is actually available, no file is
   created, and the command exits with a failure status.

---

### Edge Cases

- **Redirector and shortener URLs**: A `t.co` or other shortened link is not an X post URL and MUST
  be rejected without a network request, even though it may ultimately redirect to X. Following the
  redirect to discover the destination would violate the "no network request before validation" rule.
- **Look-alike hostnames**: URLs such as `x.com.example.net` or `notx.com` MUST be rejected. Host
  matching is exact, not substring.
- **Alternate X hostnames**: `www.x.com`, `mobile.twitter.com`, and `twitter.com` are accepted; third-party
  mirror front-ends are not.
- **URL noise**: Tracking query parameters (e.g. `?s=20&t=abc`) and trailing slashes do not prevent a
  valid post URL from being accepted.
- **Output file already exists**: See FR-016.
- **Post containing more than one video**: See FR-017.
- **Output directory missing or not writable**: The operator is told which directory failed and why,
  before any download begins.
- **Required media tooling unavailable**: If the tool cannot combine separate video and audio streams,
  it reports that prerequisite as missing rather than producing a silent file.
- **Network drops mid-download**: Treated identically to an interruption — no partial file survives.
- **Unusual author handle**: A handle containing characters that are unsafe in a filename is
  sanitized; the resulting filename never escapes the output directory.
- **Disk fills during download**: The partial file is removed and the operator is told the write
  failed.

## Requirements *(mandatory)*

### Functional Requirements

**Input and validation**

- **FR-001**: The tool MUST accept exactly one X post URL as a command-line argument.
- **FR-002**: The tool MUST reject any URL that is not an X or Twitter post URL, with a message
  explaining why it was rejected, **before any network request is made**.
- **FR-003**: URL acceptance MUST be based on an exact match of the URL's host against a permitted
  set of X and Twitter hostnames. Substring or prefix matching against the raw URL text is
  prohibited.

**Diagnosis**

- **FR-004**: When a URL is valid but yields no video, the tool MUST report the specific reason,
  distinguishing at minimum: post has no media, post has images but no video, post not found or
  deleted, and post not publicly accessible. *Known limitation, recorded 2026-08-12 during planning:
  for a bare post URL, "images but no video" is not distinguishable from "no media at all" — the
  extractor filters photos out without reporting what it filtered. The two collapse into one
  message unless the URL carries an explicit media index. See research D6.*

**Quality selection**

- **FR-005**: By default the tool MUST download the highest quality video available for the post.
- **FR-006**: The tool MUST provide a way to list the available quality options for a post without
  downloading anything.
- **FR-007**: Each listed quality option MUST carry a stable identifier the operator can pass back to
  the tool to request that exact option.
- **FR-008**: The tool MUST allow the operator to select a specific quality when downloading, and MUST
  fail with a clear message if the requested quality is unavailable.

**Output**

- **FR-009**: The tool MUST produce a single playable video file. If the source provides video and
  audio as separate streams, they MUST be combined into one file. A silent video is a failed
  download, not a partial success.
- **FR-010**: The output filename MUST be derived from the post author and post ID, never from the
  post text.
- **FR-011**: Output filenames MUST be sanitized such that no value derived from remote data can
  cause the written file to land outside the output directory.
- **FR-012**: Two different posts MUST never produce the same output filename.
- **FR-013**: The tool MUST allow the operator to specify the output directory, and MUST use a
  documented default when none is given.

**Execution behavior**

- **FR-014**: The tool MUST display download progress while running.
- **FR-015**: The tool MUST never leave a partial or corrupt file in place. On failure, interruption,
  or interrupt signal, either a complete file exists or no file exists. This applies per output file:
  when a post yields several videos and one fails, the videos that already completed are kept and the
  failed one leaves nothing behind.
- **FR-016**: When the intended output file already exists, the tool MUST NOT overwrite it. It MUST
  report that the video is already downloaded, name the existing file, make no network request for
  that file's content, and exit with a **success** status. Re-running the same URL is therefore
  idempotent and cheap.
- **FR-017**: When a **bare post URL** contains more than one video, the tool MUST download every
  video in the post. Each file MUST carry a positional index in its name so that no video overwrites
  another and the ordering is stable across runs. The tool MUST state how many videos the post
  contained. *Scoped to bare post URLs, 2026-08-13 — see FR-020 and
  [ADR-0001](../../history/adr/0001-media-index-handling-in-url-canonicalisation.md).*
- **FR-020**: When a post URL carries an explicit media index (`/status/<id>/photo/<n>` or
  `/status/<id>/video/<n>`), the tool MUST treat it as a request for **that one media item**, not for
  the post as a whole. The index MUST survive URL canonicalisation, and MUST appear in the output
  filename so that two different items of the same post can never collide. This is what makes the
  "images but no video" case of FR-004 reachable at all; for a bare URL that distinction remains
  unavailable (research D6).
- **FR-018**: The tool MUST exit with a success status on completion and a distinct failure status on
  error, so it can be driven from a script.
- **FR-019**: Error messages MUST name the input that caused the failure and what went wrong, in
  plain language.

### Key Entities

- **Post Reference**: The identity of a single X post — the author handle and the post ID — extracted
  from the supplied URL. Sole basis for the output filename.
- **Quality Option**: One downloadable rendition of the post's video, carrying a stable selector
  identifier and enough descriptive detail (such as resolution) for the operator to choose between
  options.
- **Download Outcome**: The result of a run — either a completed file at a known path, or a failure
  carrying a specific reason and leaving no file behind.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The operator saves a video from a post using a single command with no arguments beyond
  the URL.
- **SC-002**: A URL that is not an X or Twitter post URL is rejected in under one second, with zero
  network requests made.
- **SC-003**: 100% of files reported as successful downloads play with both picture and audible sound.
- **SC-004**: After any failure, interruption, or Ctrl+C, zero partial files remain in the output
  directory.
- **SC-005**: Visible progress appears within three seconds of a download starting, so the operator
  never has to guess whether the tool is hung.
- **SC-006**: Every one of the four no-video conditions produces a distinct, accurate explanation
  rather than a generic error.
- **SC-007**: Exit status correctly reflects success or failure on 100% of runs, verified by scripting
  the command against known-good and known-bad URLs.
- **SC-008**: No two distinct posts produce the same output filename, and no two videos within one
  post overwrite each other.
- **SC-009**: Re-running the tool on an already-downloaded post completes without re-fetching video
  data and leaves the existing file byte-for-byte unchanged.

## Assumptions

Recorded defaults where the brief did not specify. Each is a decision that can be revisited without
reopening the feature's scope.

- **Default output directory**: the current working directory. This is the least surprising default
  for a terminal tool and requires no configuration to start using.
- **Filename shape**: `<author-handle>-<post-id>.<extension>` for a single-video post, and
  `<author-handle>-<post-id>-<n>.<extension>` when the post carries several videos (FR-017). The post
  ID guarantees uniqueness across posts (SC-008); the handle makes files recognizable at a glance.
- **"Highest quality"** means highest resolution, with bitrate as the tie-break when two options share
  a resolution.
- **Exit statuses**: `0` on success and `1` on any failure. The brief asked only for a *distinct*
  failure status, and a single non-zero code keeps the contract simple; per-reason codes can be added
  later if scripting demands it.
- **Progress destination**: progress is written to the terminal's error stream so that piping the
  tool's normal output does not mix progress noise into it.
- **Quality selector identifiers** are those reported by the underlying source, shown verbatim in the
  listing so that what the operator sees is exactly what they can pass back.
- **Accepted hostnames** (eight): `x.com`, `www.x.com`, `m.x.com`, `mobile.x.com`, `twitter.com`,
  `www.twitter.com`, `m.twitter.com`, `mobile.twitter.com`. *Amended 2026-08-12 during planning: the
  original list of five omitted `m.twitter.com`, `m.x.com`, and `mobile.x.com`, which are valid X
  post URLs and would have been wrongly rejected. Verified against the extractor's own host pattern
  — see research D1. Tor `.onion` mirrors remain excluded.*

## Out of Scope

Explicitly excluded from this feature. Each would require its own specification.

- Any HTTP API, web server, or web interface. This is a terminal tool only.
- Downloading threads, multiple posts, user timelines, or bulk/batch operations.
- Authentication, login, cookies, or access to private/protected accounts.
- Any graphical user interface, database, job queue, or persistent state.
- Downloading images, GIFs, or Spaces audio. Video only.
- Rate limiting, proxying, or IP rotation.
- Resuming a previously interrupted download.

## Constraints

- This tool is for saving content the operator has a legitimate reason to save. It handles one URL at
  a time by design and MUST NOT be built into a scraping tool. The absence of batch input, timeline
  traversal, and rate-limit evasion is a deliberate design property, not a missing feature.
