"""URL allowlisting, post-reference extraction, and safe filename construction.

This module is the Constitution Principle V surface. Everything here runs before
any network request is made, and nothing here imports from the rest of the
package -- it is the leaf of the dependency graph.
"""

import re
from dataclasses import dataclass
from pathlib import Path

# The eight hostnames X serves posts on, per spec.md Assumptions and verified
# against the extractor's own host pattern (research D1).
#
# Exact membership only. A substring or startswith check would accept
# "x.com.evil.net", which is precisely the bypass FR-003 exists to prevent.
# The .onion mirror is deliberately excluded: it requires Tor and is outside
# the VPS deployment model.
ACCEPTED_HOSTS = frozenset(
    {
        "x.com",
        "www.x.com",
        "m.x.com",
        "mobile.x.com",
        "twitter.com",
        "www.twitter.com",
        "m.twitter.com",
        "mobile.twitter.com",
    }
)

# Mirrors the extractor's own path pattern. "i/web" is a real, handle-free URL
# form, which is why no author group is captured here -- the handle comes from
# metadata instead (research D3).
#
# The trailing media-index group is optional and matches yt-dlp's own TwitterIE,
# which exposes exactly this (verified: /status/20/video/2 -> index '2'). It is
# captured rather than discarded because it is the only thing that makes the
# "images but no video" diagnosis reachable, and because an operator who typed
# it meant it (FR-020, ADR-0001).
_POST_PATH = re.compile(
    r"^/(?:i/web|[^/]+)/status(?:es)?/(?P<id>\d+)"
    r"(?:/(?P<kind>photo|video)/(?P<index>\d+))?/?"
)

_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9_-]")

_MAX_HANDLE_LEN = 64


@dataclass(frozen=True)
class PostReference:
    """A validated X post. Its existence proves FR-002/FR-003 passed.

    media_index is None for a bare post URL, which is the ordinary case and
    keeps FR-017's every-video behaviour. When it is set, the URL named one
    specific media item and the whole pipeline honours that: the extractor is
    given the indexed URL, and the index lands in the filename (FR-020).
    """

    post_id: str
    canonical_url: str
    media_index: int | None = None


@dataclass(frozen=True)
class OutputTarget:
    """Where one video will land, and whether it is already there."""

    path: Path
    exists: bool


def parse_post_url(url: str) -> PostReference:
    """Validate an X post URL and extract its post ID.

    Makes no network request -- rejection happens before anything is fetched
    (FR-002). Shortened t.co links are rejected here rather than resolved,
    since resolving one would itself be the request FR-002 forbids.

    Raises ValueError naming the URL and the reason (FR-019).
    """
    # Imported here rather than at module scope purely for locality; urllib is
    # stdlib and free to import.
    from urllib.parse import urlsplit

    if not url or not url.strip():
        raise ValueError("no URL given")

    url = url.strip()

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ValueError(f"refusing {url!r} - not a parseable URL ({exc})") from exc

    if parts.scheme not in ("http", "https"):
        raise ValueError(
            f"refusing {url!r} - scheme must be http or https, got {parts.scheme!r}"
        )

    # hostname is already lowercased and port-stripped by urlsplit.
    host = parts.hostname or ""
    if host not in ACCEPTED_HOSTS:
        if host == "t.co":
            raise ValueError(
                f"refusing {url!r} - shortened links are not accepted. "
                "Open the link and pass the x.com URL it resolves to."
            )
        raise ValueError(
            f"refusing {url!r} - host {host!r} is not an X or Twitter post URL. "
            f"Accepted hosts: {', '.join(sorted(ACCEPTED_HOSTS))}"
        )

    # Query and fragment are discarded by looking at .path alone, which
    # satisfies the URL-noise edge case (?s=20&t=abc and trailing slashes).
    match = _POST_PATH.match(parts.path)
    if not match:
        raise ValueError(
            f"refusing {url!r} - not a post URL. "
            "Expected a path like /<author>/status/<id>"
        )

    post_id = match.group("id")
    raw_index = match.group("index")
    media_index = int(raw_index) if raw_index is not None else None

    # Rebuild rather than echo the input, so no query string, fragment, or
    # userinfo survives into what we hand to the extractor. The media index is
    # the one part of the path that is carried through, because dropping it
    # silently discards a request the operator made explicitly (FR-020).
    canonical_url = f"https://x.com/i/web/status/{post_id}"
    if media_index is not None:
        canonical_url = f"{canonical_url}/{match.group('kind')}/{media_index}"

    return PostReference(
        post_id=post_id, canonical_url=canonical_url, media_index=media_index
    )


def sanitize_handle(handle: str) -> str:
    """Reduce an author handle to something safe to put in a filename.

    Anything outside [A-Za-z0-9_-] becomes an underscore, so path separators
    and ".." are unrepresentable in the result (FR-011).
    """
    if not handle:
        return "unknown"
    cleaned = _UNSAFE_IN_FILENAME.sub("_", handle)[:_MAX_HANDLE_LEN]
    # A handle of only unsafe characters collapses to underscores, which is
    # legal but useless; treat it as absent.
    if not cleaned or set(cleaned) == {"_"}:
        return "unknown"
    return cleaned


def build_target(
    output_dir: Path,
    handle: str,
    post_id: str,
    ext: str,
    index: int | None = None,
) -> OutputTarget:
    """Compose the output path and prove it stays inside output_dir.

    `ext` is supplied by the caller from yt-dlp's own prepare_filename() and is
    never hardcoded: merge_output_format only applies when a merge actually
    happens, so a progressive rendition keeps its native container
    (research D3). Hardcoding .mp4 would make the FR-016 existence check test a
    path that never exists, re-downloading on every run.

    Raises ValueError if the composed path escapes output_dir (FR-011).
    """
    safe_handle = sanitize_handle(handle)
    suffix = ext.lstrip(".")
    stem = f"{safe_handle}-{post_id}" if index is None else f"{safe_handle}-{post_id}-{index}"
    name = f"{stem}.{suffix}" if suffix else stem

    root = Path(output_dir).resolve()
    candidate = (root / name).resolve()

    # Belt and braces: the character filter above already makes separators and
    # ".." unrepresentable. This is the Principle V boundary and the check is
    # cheap, so both stay.
    if not candidate.is_relative_to(root):
        raise ValueError(
            f"refusing to write outside the output directory: {candidate} is not inside {root}"
        )

    return OutputTarget(path=candidate, exists=candidate.exists())
