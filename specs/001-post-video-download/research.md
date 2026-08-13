# Phase 0 Research: Single Post Video Download

**Feature**: `001-post-video-download` | **Date**: 2026-08-12

## Verification method

Every finding below was verified against **yt-dlp 2026.7.4** installed into a throwaway virtualenv
(`uv venv` + `uv pip install yt-dlp`, Python 3.13.5), by reading the installed extractor source and
running an **offline probe script** — no network requests were made, and none are needed to answer
these questions. Line references point into
`yt_dlp/extractor/twitter.py` and `yt_dlp/YoutubeDL.py` as installed.

This satisfies the constitution's Authoritative Source Mandate: nothing here is recalled from
memory.

---

## D1. URL validation and post-reference extraction

**Decision**: Parse with `urllib.parse.urlsplit`. Lowercase the hostname, strip any `:port`, and
compare with `==` against a frozen `set` of accepted hostnames. Extract the post ID from the path
with a compiled regex. **Do not extract the author handle from the URL** — take it from metadata
(see D3).

**Rationale**: Exact set membership on a parsed host is the only form that satisfies FR-003.
Substring or `startswith` matching accepts `x.com.evil.net`. The probe confirms both
`x.com.evil.net/jack/status/20` and `notx.com/jack/status/20` are claimed by no Twitter extractor,
but our own gate must reject them *before* yt-dlp is reached at all (FR-002), so we cannot rely on
yt-dlp's own matching.

**Finding — the spec's hostname list is incomplete.** yt-dlp's `TwitterBaseIE._BASE_REGEX`
(`twitter.py:38`) accepts:

```text
https?://(?:(?:www|m(?:obile)?)\.)?(?:(?:twitter|x)\.com|…onion)/
```

That is the cross-product `{"", www., m., mobile.} × {twitter.com, x.com}` — **eight** hostnames.
The spec's Assumptions list five, omitting `m.twitter.com`, `m.x.com`, and `mobile.x.com`. Probe
output confirms all three are real, working X post URLs. Recommend widening the allowlist to the
full eight; this stays exact-match and therefore loses no security. The `.onion` host is
deliberately **excluded** — it requires Tor and is outside the VPS deployment model.

| Accepted | Rejected |
|---|---|
| `x.com`, `www.x.com`, `m.x.com`, `mobile.x.com` | `t.co` (see below) |
| `twitter.com`, `www.twitter.com`, `m.twitter.com`, `mobile.twitter.com` | `x.com.evil.net`, `notx.com`, any `.onion` |

**Finding — `t.co` must be rejected by us, and this diverges from yt-dlp.** `t.co` has its own
extractor, `TwitterShortenerIE` (`twitter.py:1752`), and the probe confirms `https://t.co/abcdef` is
claimed by `twitter:shortener`. Left to itself yt-dlp would resolve the redirect — which is exactly
the network request FR-002 forbids before validation. `t.co` is not in our allowlist, so our gate
rejects it first; D6 closes the door a second time.

**Path regex**: `^/(?:i/web|[^/]+)/status(?:es)?/(?P<id>\d+)` mirrors yt-dlp's own
(`twitter.py:271`). Query string and fragment are discarded by `urlsplit` before matching, and a
trailing slash is tolerated — satisfying the spec's URL-noise edge case.

**Alternatives considered**: reusing `TwitterIE._VALID_URL` directly — rejected, it is a private
attribute of a third-party library with no stability guarantee, and it accepts hosts we exclude.
Validating with a single monolithic regex over the raw URL string — rejected, it is precisely the
pattern that admits look-alike hosts.

---

## D2. Atomic output (FR-015)

**Decision**: Create the temporary working directory **inside the output directory**
(`tempfile.mkdtemp(dir=output_dir)`), let yt-dlp download and mux there, then promote the finished
file with `os.replace()`. Wrap the whole operation in `try/finally` with
`shutil.rmtree(tmp, ignore_errors=True)` so `KeyboardInterrupt` and any exception clean up.

**Rationale**: This is the point the user flagged, and the answer is that `shutil.move` is **not**
usable as the promotion step. `shutil.move` falls back to copy-then-delete when source and
destination are on different filesystems, so a crash mid-copy leaves a partial file at the
destination — a direct FR-015 violation. `os.replace` is atomic, but *only within one filesystem*.

Putting the temp directory inside the output directory guarantees same-filesystem by construction,
which makes `os.replace` atomic and removes the cross-device question entirely rather than
detecting it at runtime. It also means the temp space is subject to the same disk that must hold
the result, so "disk fills during download" fails early in the temp dir instead of after a
successful download.

`os.replace` is atomic on both targets: POSIX `rename(2)` on the Linux VPS, and `MoveFileEx` with
`MOVEFILE_REPLACE_EXISTING` on the Windows dev machine.

**Cost accepted**: a visible `.tmp-*` directory appears briefly in the operator's output directory.
Cleaned up in `finally`; a stale one after a hard kill (`SIGKILL`) is inert and harmless.

**Alternatives considered**: system temp dir + `shutil.move` — rejected, non-atomic across devices,
which is the common case (`/tmp` on a separate volume from `~/videos` is normal on a VPS). Download
in place with a `.part` suffix and rename — rejected, yt-dlp's own `.part` handling is tuned for
resume, which is explicitly out of scope, and a `.part` file surviving a crash violates FR-015.

---

## D3. Idempotent skip (FR-016)

**Decision**: Call `ydl.extract_info(url, download=False)` first. Derive the output path from the
returned metadata, check `Path.exists()`, and return a "already downloaded" outcome before any
media transfer begins.

**Rationale**: `extract_info(download=False)` performs only the metadata API call — it resolves
formats but transfers no video bytes — which is exactly the "no network request for that file's
content" clause in FR-016.

**Finding — the author handle is not always in the URL.** `TwitterIE._VALID_URL` exposes only two
named groups, `id` and `index`; there is **no author group at all**. The probe confirms:

```text
https://x.com/jack/status/20          -> {'id': '20',  'index': None}
https://x.com/i/web/status/20         -> {'id': '20',  'index': None}   # no handle in the URL
https://x.com/jack/status/20/video/2  -> {'id': '20',  'index': '2'}
```

`i/web/status/<id>` is a canonical, commonly shared X URL form that carries no handle. So the
filename's handle component (FR-010) **must** come from the metadata field `uploader_id`, not from
the URL. Fall back to `uploader` and then to the literal `unknown` when both are absent, so a
missing handle degrades the filename instead of failing the download — the post ID alone still
satisfies uniqueness (SC-008).

**Consequence for the extension — corrected 2026-08-12.** An earlier draft of this document claimed
the container is always `mp4` because `merge_output_format` forces it. **That is wrong.**
`merge_output_format` applies *only when a merge actually occurs*. A progressive single-stream
rendition selected by the `/best` fallback keeps its native extension, so a pre-computed `.mp4` path
would not match what yt-dlp writes.

Verified with a second offline probe running synthetic format lists through yt-dlp's own selection:

| Formats offered | Merge? | Resulting `ext` | `prepare_filename` |
|---|---|---|---|
| separate video + audio | yes | `mp4` | `jack-20.mp4` |
| progressive webm (`/best`) | **no** | **`webm`** | **`jack-20.webm`** |
| progressive mp4 (`/best`) | no | `mp4` | `jack-20.mp4` |

The consequence of getting this wrong is not cosmetic: FR-016's existence check would test a `.mp4`
path that never exists for a webm post, so the tool would re-download the file on every run and
silently defeat idempotency.

**Corrected decision**: call `ydl.prepare_filename(info)` on the **processed** info dict returned by
`extract_info(url, download=False)` and take the extension from it. `extract_info` runs format
selection by default, so the dict it returns already reflects whether a merge will happen. Keep our
own sanitized basename (FR-010) and borrow only the extension.

**And at promotion time, do not trust any pre-computed path.** Promote whatever file actually landed
in the temp directory. The temp directory is created empty and holds exactly one finished file per
entry, so the result is unambiguous, and it is immune to any remaining divergence between predicted
and actual naming.

---

## D4. Format selection

**Decision**: `format: "bestvideo+bestaudio/best"`, `merge_output_format: "mp4"`.

**Rationale**: Matches FR-005 (highest quality) and the spec's tie-break assumption — yt-dlp's
`bestvideo`/`bestaudio` already order by resolution then bitrate. The `/best` fallback covers
progressive single-stream renditions, so a post whose video is not split into streams still
resolves. `merge_output_format: "mp4"` gives FR-009 a single playable container **whenever a merge
occurs**.

**It does not make the extension predictable.** The `/best` fallback path performs no merge and
keeps the source container — see the corrected note in D3. Never assume `.mp4`; derive the
extension from `prepare_filename` on the processed info dict.

**For the US3 listing (deferred, not built now)**: X renditions surface as HLS-derived formats whose
`format_id` values are stable strings suitable to echo back and pass to `format=`. The listing
should print `format_id`, `resolution`, `ext`, `filesize_approx`, and `tbr` straight from
`info["formats"]`, verbatim — satisfying FR-007's "stable identifier the operator can pass back".
Confirming the exact `format_id` shape for a live X post needs a network call and is therefore
deferred to the US3 phase, where it can be checked by manual CLI verification (Principle II).

---

## D5. Multi-video posts (FR-017)

**Decision**: Branch on `info.get("_type") == "playlist"`. Iterate `info["entries"]` and apply the
`-<n>` filename suffix using `enumerate(entries, 1)`. A single-video post returns a flat info dict
with no `entries` key and uses the unsuffixed filename.

**Rationale**: Verified in source at `twitter.py:1384-1390`:

```python
if len(entries) == 1:
    return entries[0]
for index, entry in enumerate(entries, 1):
    entry['title'] += f' #{index}'
return self.playlist_result(entries, **info)
```

So the representation is **playlist entries, not extra formats** — settling the question the user
raised. The 1-based index yt-dlp uses for its own titles is the same index our filename scheme
should use, keeping displayed order and filenames consistent.

**Related capability, deliberately unused**: the URL form `…/status/<id>/video/<n>` selects one
video natively (`index` group, `twitter.py:1351-1371`), and `noplaylist: True` would collapse a
multi-video post to its first entry. We use neither — FR-017 requires downloading all videos, so we
leave `noplaylist` at its default (`False`) and handle indexing ourselves. Worth noting that if the
operator supplies a `/video/2` URL directly, yt-dlp returns just that entry as a flat dict; our
code handles that correctly by treating any non-playlist result as a single video.

**Interaction with FR-015**: per-file atomicity. Each entry is promoted with its own `os.replace`,
so a failure on entry 3 leaves entries 1 and 2 complete and entry 3 absent — which is exactly the
per-file rule the spec records at FR-015.

---

## D6. Error diagnosis (FR-004)

**Decision**: Catch `yt_dlp.utils.DownloadError`, match its message text against the table below,
and emit one of the four plain explanations. No custom exception classes (Principle VI).

**Verified source strings** (all raised with `expected=True`, all wrapped by `YoutubeDL` into
`DownloadError` via `report_error` at `YoutubeDL.py:1749-1750`):

| Condition | yt-dlp message (source) | Maps to |
|---|---|---|
| No video in post | `No video could be found in this tweet` (`twitter.py:1377`, via `raise_no_formats`) | "post has no video" |
| Protected account | `You are not authorized to view this protected tweet` + login hint (`twitter.py:1092`) | "not publicly accessible" |
| NSFW gated | `NSFW tweet requires authentication` + login hint (`twitter.py:1090`) | "not publicly accessible" |
| Deleted / tombstoned | `Twitter API says: <cause>` (`twitter.py:1086`) | "post not found" |
| Unavailable | `<reason>` or `Requested tweet is unavailable` (`twitter.py:1093`) | "post not found" |
| Indexed media not a video | `Media #<n> is not a video` (`twitter.py:1359`) | "post has images but no video" |

**Rationale**: These strings live in a third-party library and can change between releases, so the
match must be **substring, case-insensitive, and ordered**, with an unmatched `DownloadError`
falling through to a generic "could not extract video from this post" that includes yt-dlp's own
message verbatim. That way a string change degrades a specific diagnosis into a still-truthful
generic one rather than crashing or lying.

**Note on the images-only case**: the spec asks to distinguish "images but no video" from "no media
at all". yt-dlp only distinguishes these when an explicit `/photo/<n>` or `/video/<n>` index is in
the URL; for a bare URL both collapse to `No video could be found in this tweet`. The extractor
filters on `m['type'] != 'photo'` (`twitter.py:1349`) but does not report what it filtered out.
Distinguishing them for bare URLs would require inspecting the pre-filter media list, which
`extract_info` does not expose. **Recorded as a known limitation for the US2 phase** — US1 does not
depend on it.

**Security note — `expected=True` matters.** `DownloadError.exc_info` carries the original
exception, so richer matching is possible later; we deliberately match on message text instead,
because reaching into `exc_info` couples us to yt-dlp internals for no gain at this scale.

---

## D7. Progress reporting (FR-014)

**Decision**: `quiet: True`, `no_warnings: True`, `noprogress: True`, plus our own
`progress_hooks: [hook]` writing a single rewriting line to `sys.stderr`.

**Rationale**: `noprogress: True` suppresses yt-dlp's own progress bar so it cannot fight with ours;
`quiet`/`no_warnings` keep its logging off stdout. Our hook receives dicts with `status` in
`{"downloading", "finished", "error"}` and, when downloading, `downloaded_bytes`, `total_bytes` or
`total_bytes_estimate`, `speed`, and `eta`. Writing to stderr matches the spec's Assumptions and
keeps stdout clean for the final output path, so `xdl <url>` can be piped.

`total_bytes` is absent for HLS-derived formats; the hook must fall back to
`total_bytes_estimate` and then to showing bytes-so-far only, rather than dividing by `None`.

---

## D8. Security containment — `allowed_extractors`

**Decision**: Pass `allowed_extractors: ["twitter"]` on every `YoutubeDL` construction.

**Rationale — this closes a real hole that the module layout alone does not.** At
`twitter.py:1374-1380`, when a post contains no video but does contain a link, the extractor does:

```python
expanded_url = traverse_obj(status, ('entities', 'urls', 0, 'expanded_url'), ...)
...
return self.url_result(expanded_url, display_id=twid, **info)
```

That hands an **arbitrary third-party URL, chosen by the post's author, back to yt-dlp for
extraction**. Our allowlist validated the *input* URL, but this redirect happens after validation,
inside the extractor. Without containment, pasting a link-bearing X post URL could cause yt-dlp to
fetch and extract from any site on the internet — a Principle V violation reached through a URL that
passed our gate.

**Verified by probe**: `allowed_extractors: ["twitter"]` loads **exactly one** extractor and zero
others — the value is matched as an anchored full match, so it does not pull in `twitter:card`,
`twitter:spaces`, `twitter:broadcast`, `twitter:amplify`, or `twitter:shortener`:

```text
['twitter']  -> twitter ies: ['twitter'];  non-twitter ies loaded: 0
```

This simultaneously enforces three spec boundaries at the library level rather than by convention:
the `url_result` redirect can resolve to nothing, `t.co` cannot be followed even if it somehow
reached yt-dlp, and Spaces audio (explicitly out of scope) cannot be extracted.

**Alternatives considered**: post-hoc checking `info["extractor"]` after the call — rejected, the
fetch has already happened by then. `noplaylist` — unrelated, does not constrain which extractor
runs.

---

## D9. ffmpeg preflight

**Decision**: `shutil.which("ffmpeg")` before any download begins; on `None`, fail immediately with
a message naming the missing prerequisite and how to install it. Never hardcode a path.

**Rationale**: Directly required by the spec's "required media tooling unavailable" edge case and
the user's environment note. Checking upfront turns a confusing mid-mux failure into a clear
precondition error, and `shutil.which` respects `PATH` on both the Windows dev box (where ffmpeg
resolves to a scoop shim) and the Linux VPS.

**Confirmed available in this environment**: `C:\Users\HP\scoop\shims\ffmpeg.exe`.

**Refinement**: run the check only when a download is actually going to happen — a validation
rejection (FR-002) and the idempotent skip (FR-016) must not require ffmpeg to be installed.

---

## Resolved unknowns summary

| # | Question | Resolved |
|---|---|---|
| 1 | URL validation approach | D1 — exact host set, 8 hostnames, ID-only path regex |
| 2 | Atomicity across filesystems | D2 — temp dir *inside* output dir + `os.replace`; `shutil.move` rejected |
| 3 | Minimum call for pre-download path | D3 — `extract_info(download=False)`; handle from `uploader_id`, not URL |
| 4 | Format selection and stable IDs | D4 — `bestvideo+bestaudio/best` + `mp4`; listing deferred to US3 |
| 5 | Multi-video representation | D5 — playlist `entries`, 1-based index |
| 6 | Error strings for diagnosis | D6 — six strings mapped; images-vs-nothing is a US2 limitation |
| 7 | Progress hooks | D7 — `noprogress` + own hook to stderr; HLS has no `total_bytes` |
| 8 | *(new)* Extractor redirect hole | D8 — `allowed_extractors: ["twitter"]`, probe-verified |
| 9 | *(new)* ffmpeg preflight timing | D9 — `shutil.which`, only on the download path |

## Spec amendments recommended

Neither blocks implementation; both should be folded into `spec.md` before the tasks that touch
them.

1. **Assumptions → accepted hostnames**: widen from five to eight, adding `m.twitter.com`,
   `m.x.com`, `mobile.x.com` (D1).
2. **FR-004 / US2**: record that "images but no video" is not distinguishable from "no media at
   all" for bare post URLs with the current extractor (D6).
