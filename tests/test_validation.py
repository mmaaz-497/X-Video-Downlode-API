"""Tests for the Constitution Principle V surface.

This file exists because `validation.py` is the only place a bad URL can be
stopped, and because a filename is the only place a post's author gets to
influence the filesystem. Both are worth locking down; the rest of the project
is covered by the T008 manual gate, not by tests (Principle II).

Everything here is a pure function over literal strings. No network, no ffmpeg,
no yt-dlp.
"""

import pytest

from backend.validation import (
    ACCEPTED_HOSTS,
    build_target,
    parse_post_url,
    sanitize_handle,
)

POST_ID = "1234567890123456789"


# --------------------------------------------------------------------------
# Accepted URLs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("host", sorted(ACCEPTED_HOSTS))
def test_every_accepted_host_is_accepted(host):
    """All eight hosts, driven off the frozenset itself.

    Parametrising over ACCEPTED_HOSTS rather than a copied literal list means
    that deleting a host makes this test lose a case rather than fail -- so the
    count is asserted separately below. The two together catch both directions.
    """
    reference = parse_post_url(f"https://{host}/someone/status/{POST_ID}")
    assert reference.post_id == POST_ID


def test_accepted_host_list_is_exactly_the_eight_from_the_spec():
    """Guards against the five-host draft list reappearing (T007's concern)."""
    assert ACCEPTED_HOSTS == frozenset(
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


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (f"https://x.com/i/web/status/{POST_ID}", "handle-free canonical form"),
        (f"https://x.com/someone/statuses/{POST_ID}", "legacy /statuses/ plural"),
        (f"https://x.com/someone/status/{POST_ID}/", "trailing slash"),
        (f"https://x.com/someone/status/{POST_ID}?s=20&t=abc", "share-link query noise"),
        (f"https://x.com/someone/status/{POST_ID}#anchor", "fragment"),
        (f"http://x.com/someone/status/{POST_ID}", "plain http is allowed"),
        (f"https://x.com:443/someone/status/{POST_ID}", "explicit port is stripped"),
        (f"https://X.CoM/someone/status/{POST_ID}", "host case is normalised"),
        (f"  https://x.com/someone/status/{POST_ID}  ", "surrounding whitespace"),
        (f"https://x.com/someone/status/{POST_ID}/video/2", "indexed media path"),
    ],
)
def test_accepted_url_shapes(url, why):
    assert parse_post_url(url).post_id == POST_ID, why


def test_canonical_url_discards_everything_but_the_id():
    """What we hand yt-dlp is rebuilt, not echoed.

    No query string, fragment, or userinfo from the input survives into the URL
    the extractor receives.
    """
    reference = parse_post_url(
        f"https://user:pw@mobile.twitter.com/someone/status/{POST_ID}?s=20#x"
    )
    assert reference.canonical_url == f"https://x.com/i/web/status/{POST_ID}"


# --------------------------------------------------------------------------
# Media index (FR-020, ADR-0001)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_index", "expected_suffix"),
    [
        (f"https://x.com/u/status/{POST_ID}/photo/2", 2, f"/photo/2"),
        (f"https://x.com/u/status/{POST_ID}/video/1", 1, f"/video/1"),
        (f"https://x.com/u/status/{POST_ID}/video/12", 12, f"/video/12"),
        (f"https://x.com/i/web/status/{POST_ID}/video/3", 3, f"/video/3"),
        (f"https://x.com/u/status/{POST_ID}/photo/2/", 2, f"/photo/2"),
        (f"https://x.com/u/status/{POST_ID}/photo/2?s=20", 2, f"/photo/2"),
    ],
)
def test_media_index_is_captured_and_preserved(url, expected_index, expected_suffix):
    """The index is the operator's stated request, not URL noise (FR-020).

    Dropping it made `Media #<n> is not a video` unreachable, which is what left
    FR-004's second category unsatisfiable.
    """
    reference = parse_post_url(url)
    assert reference.media_index == expected_index
    assert reference.canonical_url == f"https://x.com/i/web/status/{POST_ID}{expected_suffix}"


@pytest.mark.parametrize(
    "url",
    [
        f"https://x.com/u/status/{POST_ID}",
        f"https://x.com/i/web/status/{POST_ID}",
        f"https://x.com/u/status/{POST_ID}/",
        f"https://x.com/u/status/{POST_ID}?s=20&t=abc",
    ],
)
def test_bare_urls_have_no_media_index(url):
    """FR-017 is scoped to bare URLs, so they must stay exactly as they were."""
    reference = parse_post_url(url)
    assert reference.media_index is None
    assert reference.canonical_url == f"https://x.com/i/web/status/{POST_ID}"


def test_media_index_is_an_int_not_a_string():
    """It reaches build_target as the filename index, which composes it directly."""
    assert parse_post_url(f"https://x.com/u/status/{POST_ID}/video/2").media_index == 2


@pytest.mark.parametrize(
    "url",
    [
        f"https://x.com/u/status/{POST_ID}/audio/2",
        f"https://x.com/u/status/{POST_ID}/photo/abc",
        f"https://x.com/u/status/{POST_ID}/photo",
    ],
)
def test_unrecognised_trailing_path_is_ignored_not_captured(url):
    """Only photo/video with a numeric index counts.

    Anything else leaves a bare reference rather than being coerced into one --
    the post ID still parsed, so the URL is still valid.
    """
    reference = parse_post_url(url)
    assert reference.media_index is None
    assert reference.canonical_url == f"https://x.com/i/web/status/{POST_ID}"


def test_indexed_urls_of_one_post_produce_different_filenames(tmp_path):
    """The collision ADR-0001 was written about.

    Without the index in the name, /video/1 and /video/2 both resolve to
    <handle>-<post-id>.mp4, and FR-016 then reports the second as "Already
    downloaded" while handing over the first.
    """
    one = parse_post_url(f"https://x.com/u/status/{POST_ID}/video/1")
    two = parse_post_url(f"https://x.com/u/status/{POST_ID}/video/2")

    name_one = build_target(tmp_path, "someone", one.post_id, ".mp4", one.media_index).path.name
    name_two = build_target(tmp_path, "someone", two.post_id, ".mp4", two.media_index).path.name

    assert name_one == f"someone-{POST_ID}-1.mp4"
    assert name_two == f"someone-{POST_ID}-2.mp4"
    assert name_one != name_two


# --------------------------------------------------------------------------
# Rejected URLs -- the FR-003 bypasses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "why"),
    [
        # The look-alike family. Each one is accepted by a substring or
        # startswith check and rejected by the exact-match one, which is the
        # entire reason FR-003 specifies exact match.
        (f"https://x.com.evil.net/someone/status/{POST_ID}", "suffix look-alike"),
        (f"https://notx.com/someone/status/{POST_ID}", "prefix look-alike"),
        (f"https://evil.net/x.com/status/{POST_ID}", "host name appearing in the path"),
        (f"https://twitter.com.evil.net/a/status/{POST_ID}", "suffix look-alike, twitter"),
        (f"https://xx.com/someone/status/{POST_ID}", "near miss"),
        (f"https://x.co/someone/status/{POST_ID}", "TLD truncation"),
        (f"https://sub.x.com/someone/status/{POST_ID}", "undeclared subdomain"),
        # Deliberately excluded hosts.
        (f"https://t.co/{POST_ID}", "shortener -- resolving it would be the request FR-002 forbids"),
        (f"https://twitter3e4tixl4xyajtrzo62zg5vztmjuricljdp2c5kshju4avyoid.onion/a/status/{POST_ID}", "onion mirror, research D1"),
        # Non-HTTP schemes.
        (f"file:///etc/passwd", "file scheme"),
        (f"ftp://x.com/someone/status/{POST_ID}", "ftp scheme"),
        (f"javascript:alert(1)", "javascript scheme"),
        (f"//x.com/someone/status/{POST_ID}", "protocol-relative, no scheme"),
        (f"x.com/someone/status/{POST_ID}", "bare host, no scheme"),
        # Right host, wrong path.
        ("https://x.com/", "root"),
        ("https://x.com/someone", "profile, not a post"),
        ("https://x.com/home", "timeline"),
        ("https://x.com/i/spaces/abc123", "Spaces, not a post"),
        (f"https://x.com/someone/photo/{POST_ID}", "not a status path"),
        # Right shape, wrong ID.
        ("https://x.com/someone/status/notanumber", "non-numeric ID"),
        ("https://x.com/someone/status/", "missing ID"),
        ("https://x.com/someone/status/abc123", "alphanumeric ID"),
        # Empty input.
        ("", "empty string"),
        ("   ", "whitespace only"),
    ],
)
def test_rejected_url_shapes(url, why):
    with pytest.raises(ValueError):
        parse_post_url(url)


def test_rejection_message_names_the_url_and_the_reason():
    """FR-019: an error must say what was wrong, not just that something was."""
    bad = f"https://x.com.evil.net/someone/status/{POST_ID}"
    with pytest.raises(ValueError) as caught:
        parse_post_url(bad)
    message = str(caught.value)
    assert bad in message
    assert "x.com.evil.net" in message


def test_shortener_rejection_says_what_to_do_instead():
    with pytest.raises(ValueError, match="shortened links"):
        parse_post_url("https://t.co/abc123")


# --------------------------------------------------------------------------
# sanitize_handle -- the filename half of FR-011
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected", "why"),
    [
        ("someone", "someone", "ordinary handle survives untouched"),
        ("Some_One-123", "Some_One-123", "underscore and hyphen are allowed"),
        ("../../etc/passwd", "______etc_passwd", "traversal is unrepresentable"),
        ("a/b", "a_b", "posix separator"),
        ("a\\b", "a_b", "windows separator"),
        ("..", "unknown", "bare traversal collapses to underscores, treated as absent"),
        (".", "unknown", "single dot likewise"),
        ("日本語ユーザー", "unknown", "all-unicode collapses to underscores"),
        ("user日本", "user__", "mixed keeps the ascii part"),
        ("a:b*c?d", "a_b_c_d", "windows-illegal characters"),
        ("", "unknown", "empty"),
        ("___", "unknown", "underscores only is legal but useless"),
    ],
)
def test_sanitize_handle(raw, expected, why):
    assert sanitize_handle(raw) == expected, why


def test_sanitize_handle_truncates_at_64():
    assert sanitize_handle("a" * 200) == "a" * 64


def test_sanitize_handle_truncates_after_substitution_not_before():
    """A 200-char unicode handle must not survive as 64 chars of garbage."""
    result = sanitize_handle("é" * 200)
    assert len(result) <= 64
    assert result == "unknown"


# --------------------------------------------------------------------------
# build_target -- the path half of FR-011
# --------------------------------------------------------------------------


def test_build_target_without_index(tmp_path):
    target = build_target(tmp_path, "someone", POST_ID, ".mp4")
    assert target.path.name == f"someone-{POST_ID}.mp4"
    assert target.path.parent == tmp_path.resolve()
    assert target.exists is False


def test_build_target_with_index(tmp_path):
    target = build_target(tmp_path, "someone", POST_ID, ".mp4", 2)
    assert target.path.name == f"someone-{POST_ID}-2.mp4"


@pytest.mark.parametrize("ext", [".webm", "webm", ".mkv", ".m4a"])
def test_build_target_passes_any_extension_through(tmp_path, ext):
    """Never hardcoded .mp4 (research D3).

    A progressive rendition from the /best fallback keeps its native container.
    Hardcoding .mp4 would make the FR-016 existence check test a path that never
    exists, re-downloading on every run.
    """
    target = build_target(tmp_path, "someone", POST_ID, ext)
    assert target.path.suffix == f".{ext.lstrip('.')}"


def test_build_target_reports_an_existing_file(tmp_path):
    """The FR-016 idempotent-skip signal."""
    (tmp_path / f"someone-{POST_ID}.mp4").write_bytes(b"already here")
    assert build_target(tmp_path, "someone", POST_ID, ".mp4").exists is True


def test_build_target_uses_unknown_for_a_missing_handle(tmp_path):
    """i/web/status/<id> carries no handle at all (research D3)."""
    assert build_target(tmp_path, "", POST_ID, ".mp4").path.name == f"unknown-{POST_ID}.mp4"


@pytest.mark.parametrize(
    "handle",
    ["../evil", "../../evil", "/etc/passwd", "..\\..\\evil", "a/../../b"],
)
def test_build_target_never_escapes_the_output_directory(tmp_path, handle):
    """Containment holds for every traversal attempt (FR-011).

    sanitize_handle makes separators unrepresentable, so these are contained
    rather than raising -- the assertion is on where the file lands, which is
    the property FR-011 actually states. The explicit ValueError guard in
    build_target is the second line of defence and is exercised below.
    """
    target = build_target(tmp_path, handle, POST_ID, ".mp4")
    assert target.path.parent == tmp_path.resolve()
    assert target.path.is_relative_to(tmp_path.resolve())


def test_handle_traversal_is_neutralised_rather_than_rejected(tmp_path):
    """Worth stating explicitly: a `..` handle does not raise, it gets filtered.

    `sanitize_handle` runs first and turns separators into underscores, so the
    containment guard never sees a traversal from this direction. Asserting the
    ValueError here instead would encode the wrong mechanism and would start
    failing the day sanitising got stricter.
    """
    target = build_target(tmp_path, "../../evil", POST_ID, ".mp4")
    assert target.path.parent == tmp_path.resolve()
    assert target.path.name.startswith("_")


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        (
            {"handle": "someone", "post_id": "../../../../..", "ext": "mp4"},
            "post_id is inserted unsanitised -- safe only because parse_post_url "
            "guarantees it is digits, which is exactly why the guard stays",
        ),
        (
            {"handle": "someone", "post_id": "1", "ext": "./../../../../evil"},
            "ext comes from yt-dlp's prepare_filename and is never filtered here",
        ),
    ],
)
def test_build_target_raises_when_the_path_would_escape(tmp_path, kwargs, why):
    """FR-011's second line of defence, reached through real arguments.

    Neither vector is reachable in production -- post_id is `\\d+` by the time
    validation is done with it, and ext is a real suffix. That is the point: the
    guard is what makes those upstream guarantees load-bearing rather than
    assumed, so it is tested through the arguments that could actually carry a
    traversal, not by disabling the sanitiser.
    """
    with pytest.raises(ValueError, match="outside the output directory"):
        build_target(tmp_path, **kwargs), why
