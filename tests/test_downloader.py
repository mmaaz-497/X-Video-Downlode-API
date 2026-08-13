"""Tests for the extractor wrapper -- the seam between yt-dlp and our filenames.

Everything here feeds **literal info dicts** to the pure helpers in
`backend.downloader`. No mocking framework, no monkeypatching of yt-dlp
internals, no network, no ffmpeg. The one place a real `YoutubeDL` is
constructed (`test_extension_*`) uses only `prepare_filename`, which is a pure
string operation over the dict it is handed.

What this file deliberately does NOT cover, because covering it would require
the network or a mock: `download_post`'s own download loop. See the T008 record
in tasks.md -- the `playlist_items` path in particular has never executed.
"""

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from backend.downloader import (
    _base_options,
    _diagnose,
    _diagnose_format,
    _entries_of,
    _extension_for_format,
    _extension_of,
    _filename_index,
    _format_options_of,
    _handle_of,
    _promote,
    _remove_temp_dir,
)
from backend.validation import PostReference, build_target, parse_post_url

POST_ID = "1234567890123456789"


# --------------------------------------------------------------------------
# The Principle V regression guard
# --------------------------------------------------------------------------


def test_base_options_always_restricts_the_extractor():
    """SECURITY-CRITICAL, and free to check (research D8).

    Without `allowed_extractors`, a post that contains no video but carries a
    link makes the extractor hand an author-chosen third-party URL back to
    yt-dlp for extraction -- *after* our allowlist gate has already passed. This
    is the one line that closes that hole, so it gets a test that needs no
    network to run.
    """
    assert _base_options(None)["allowed_extractors"] == ["twitter"]


def test_extractor_restriction_survives_a_progress_hook():
    """The hook branch is the other construction path; it must not diverge."""
    assert _base_options(lambda status: None)["allowed_extractors"] == ["twitter"]


def test_base_options_returns_a_fresh_dict_each_call():
    """download_post mutates the result (paths, outtmpl, playlist_items).

    A shared dict would leak per-entry settings -- notably playlist_items --
    into the next entry's download.
    """
    first = _base_options(None)
    first["paths"] = {"home": "/tmp/leak"}
    assert "paths" not in _base_options(None)


def test_progress_hook_is_only_registered_when_supplied():
    assert "progress_hooks" not in _base_options(None)
    hook = lambda status: None
    assert _base_options(hook)["progress_hooks"] == [hook]


# --------------------------------------------------------------------------
# Entry branching (research D5)
# --------------------------------------------------------------------------


def test_flat_info_dict_is_one_entry():
    info = {"id": "20", "ext": "mp4", "uploader_id": "someone"}
    assert _entries_of(info) == [info]


def test_playlist_info_dict_yields_its_entries():
    entries = [{"id": "20-1", "ext": "mp4"}, {"id": "20-2", "ext": "mp4"}]
    assert _entries_of({"_type": "playlist", "entries": entries}) == entries


def test_playlist_drops_none_entries():
    """yt-dlp emits None for an entry it could not extract."""
    kept = {"id": "20-2", "ext": "mp4"}
    assert _entries_of({"_type": "playlist", "entries": [None, kept, None]}) == [kept]


@pytest.mark.parametrize("entries", [[], None])
def test_playlist_with_no_usable_entries_is_empty(entries):
    """download_post turns this into "no video in it" rather than crashing."""
    assert _entries_of({"_type": "playlist", "entries": entries}) == []


def test_multi_video_post_produces_indexed_targets(tmp_path):
    """Two entries yield -1 and -2 suffixes; the composition T008 never ran.

    This reproduces download_post's target-building loop over literal metadata.
    It covers the naming half of the multi-video path only -- the download call
    underneath it, which sets playlist_items, is still unexercised.
    """
    info = {
        "_type": "playlist",
        "uploader_id": "someone",
        "entries": [
            {"id": "20-1", "ext": "mp4", "uploader_id": "someone"},
            {"id": "20-2", "ext": "mp4", "uploader_id": "someone"},
        ],
    }
    entries = _entries_of(info)
    multiple = len(entries) > 1

    names = [
        build_target(
            tmp_path,
            _handle_of(entry) or _handle_of(info),
            POST_ID,
            ".mp4",
            position if multiple else None,
        ).path.name
        for position, entry in enumerate(entries, 1)
    ]

    assert names == [f"someone-{POST_ID}-1.mp4", f"someone-{POST_ID}-2.mp4"]


def test_single_video_post_produces_an_unsuffixed_target(tmp_path):
    info = {"id": "20", "ext": "mp4", "uploader_id": "someone"}
    entries = _entries_of(info)
    multiple = len(entries) > 1

    target = build_target(
        tmp_path, _handle_of(entries[0]), POST_ID, ".mp4", 1 if multiple else None
    )
    assert target.path.name == f"someone-{POST_ID}.mp4"


# --------------------------------------------------------------------------
# Handle resolution precedence (research D3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected", "why"),
    [
        ({"uploader_id": "handle", "uploader": "Display Name"}, "handle", "uploader_id wins"),
        ({"uploader": "Display Name"}, "Display Name", "falls back to uploader"),
        ({}, "unknown", "neither present -- i/web/status/<id> carries no handle"),
        ({"uploader_id": None, "uploader": "Display Name"}, "Display Name", "explicit None falls through"),
        ({"uploader_id": "", "uploader": "Display Name"}, "Display Name", "empty string falls through"),
        ({"uploader_id": None, "uploader": None}, "unknown", "both None"),
    ],
)
def test_handle_resolution_precedence(info, expected, why):
    assert _handle_of(info) == expected, why


def test_handle_is_not_taken_from_the_url(tmp_path):
    """The whole reason _handle_of exists (research D3).

    A display name with spaces and separators reaches the filename only through
    sanitize_handle, never raw.
    """
    target = build_target(tmp_path, _handle_of({"uploader": "Some One/../x"}), POST_ID, ".mp4")
    assert target.path.name == f"Some_One____x-{POST_ID}.mp4"
    assert target.path.parent == tmp_path.resolve()


# --------------------------------------------------------------------------
# Extension resolution -- "never hardcode .mp4" (research D3, corrected)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ext", "expected"),
    [("mp4", ".mp4"), ("webm", ".webm"), ("mkv", ".mkv"), ("m4a", ".m4a")],
)
def test_extension_comes_from_the_entry_not_a_constant(ext, expected):
    """A progressive rendition from the /best fallback keeps its container.

    prepare_filename is a pure string operation over the dict handed to it, so
    this constructs a real YoutubeDL without touching the network.
    """
    with YoutubeDL(_base_options(None)) as ydl:
        assert _extension_of(ydl, {"id": "20", "ext": ext, "title": "t"}) == expected


def test_entry_without_ext_is_refused_rather_than_guessed():
    """No container reported means we do not know, so we do not invent one.

    prepare_filename substitutes the literal "NA" for absent fields, so an entry
    with no `ext` yields `t [20].NA`. That is not an extension. Guessing .mp4
    here would not produce an mp4 -- _promote renames whatever actually landed
    to this name -- so it would produce a webm called .mp4.
    """
    with YoutubeDL(_base_options(None)) as ydl:
        with pytest.raises(ValueError, match="no container format"):
            _extension_of(ydl, {"id": "20", "title": "t"})


def test_refusal_names_the_entry_it_could_not_resolve():
    """FR-019 again: the error has to say which video it is about."""
    with YoutubeDL(_base_options(None)) as ydl:
        with pytest.raises(ValueError, match="video 20-2"):
            _extension_of(ydl, {"id": "20-2", "title": "t"})


# --------------------------------------------------------------------------
# Error mapping (research D6)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected", "source"),
    [
        (
            "ERROR: [twitter] 20: No video could be found in this tweet",
            "this post has no video in it.",
            "twitter.py:1377 via raise_no_formats",
        ),
        (
            "ERROR: [twitter] 20: Media #2 is not a video",
            "this post contains media, but it is not a video.",
            "twitter.py:1359",
        ),
        (
            "ERROR: [twitter] 20: You are not authorized to view this protected tweet",
            "this post belongs to a protected account and is not publicly accessible. "
            "This tool does not authenticate.",
            "twitter.py:1092",
        ),
        (
            "ERROR: [twitter] 20: NSFW tweet requires authentication",
            "this post is age-restricted and is not publicly accessible. "
            "This tool does not authenticate.",
            "twitter.py:1090",
        ),
        (
            "ERROR: [twitter] 20: Twitter API says: _Missing: No status found with that ID",
            "this post could not be found. It may have been deleted.",
            "twitter.py:1086",
        ),
        (
            "ERROR: [twitter] 20: Requested tweet is unavailable",
            "this post could not be found. It may have been deleted.",
            "twitter.py:1093",
        ),
    ],
)
def test_each_verified_yt_dlp_string_maps_to_its_diagnosis(message, expected, source):
    assert _diagnose(DownloadError(message)) == expected, source


@pytest.mark.parametrize(
    "message",
    [
        "no video could be found in this tweet",
        "NO VIDEO COULD BE FOUND IN THIS TWEET",
        "No Video Could Be Found In This Tweet",
    ],
)
def test_diagnosis_matching_is_case_insensitive(message):
    """These strings live in a third-party library; casing is not a contract."""
    assert _diagnose(DownloadError(message)) == "this post has no video in it."


def test_unrecognised_error_falls_through_carrying_yt_dlps_own_text():
    """The path a real HTTP 522 took during T008.

    A string change upstream must degrade a specific diagnosis into a still-
    truthful generic one, never crash and never invent a cause.
    """
    raw = "ERROR: unable to download video data: HTTP Error 522: Origin Connection Time-out"
    result = _diagnose(DownloadError(raw))
    assert result.startswith("could not extract video from this post.")
    assert raw in result


def test_generic_fallback_survives_an_empty_error_message():
    """FR-019: never emit an explanation that names nothing.

    A DownloadError carrying no text still has to produce a sentence. This is
    the _diagnose half of the empty-message defect T008 found; the
    KeyboardInterrupt half lives in download_post's except clause and cannot be
    reached without a download -- see the module docstring.
    """
    result = _diagnose(DownloadError(""))
    assert result.strip().endswith("yt-dlp said:") is False or "could not extract" in result
    assert "could not extract video from this post." in result


def test_diagnosis_order_is_stable_for_an_overlapping_message():
    """Ordered match: the first needle in the table wins, deterministically."""
    both = "No video could be found in this tweet. Requested tweet is unavailable"
    assert _diagnose(DownloadError(both)) == "this post has no video in it."


# --------------------------------------------------------------------------
# FR-004 categories stay distinct (US2)
# --------------------------------------------------------------------------


def test_the_four_fr004_categories_are_mutually_distinct():
    """US2's whole point: four causes, four different explanations.

    Asserted as a set so that collapsing any two into one wording fails here,
    which is the regression US2 exists to prevent.
    """
    categories = {
        "no media": "No video could be found in this tweet",
        "images but no video": "Media #2 is not a video",
        "not found": "Requested tweet is unavailable",
        "not accessible": "You are not authorized to view this protected tweet",
    }
    produced = {label: _diagnose(DownloadError(msg)) for label, msg in categories.items()}
    assert len(set(produced.values())) == 4, produced


def test_filename_index_prefers_the_media_index_over_position():
    """FR-020: an indexed URL resolves to one entry, so `multiple` is false.

    Falling back to the positional rule here is exactly the silent wrong-file
    bug ADR-0001 records.
    """
    indexed = parse_post_url("https://x.com/u/status/20/video/2")
    assert _filename_index(indexed, position=1, multiple=False) == 2


def test_filename_index_keeps_positional_numbering_for_bare_urls():
    """FR-017 is untouched for the ordinary case."""
    bare = PostReference(post_id="20", canonical_url="https://x.com/i/web/status/20")
    assert _filename_index(bare, position=1, multiple=True) == 1
    assert _filename_index(bare, position=2, multiple=True) == 2
    assert _filename_index(bare, position=1, multiple=False) is None


# --------------------------------------------------------------------------
# Format listing (US3) -- literal info dicts, research D4
# --------------------------------------------------------------------------


def test_format_options_carry_the_five_fields_verbatim():
    """FR-007: the identifier must be passable straight back to --format."""
    info = {
        "formats": [
            {"format_id": "http-950", "resolution": "1280x720", "ext": "mp4",
             "filesize": 95400000, "tbr": 950.0},
            {"format_id": "http-256", "resolution": "640x360", "ext": "mp4",
             "filesize_approx": 25600000, "tbr": 256.0},
        ]
    }
    options = _format_options_of(info)

    assert len(options) == 2
    assert options[0].format_id == "http-950"
    assert options[0].resolution == "1280x720"
    assert options[0].ext == "mp4"
    assert options[0].filesize_approx == 95400000
    assert options[0].tbr == 950.0
    # filesize_approx is accepted under either of yt-dlp's two spellings.
    assert options[1].filesize_approx == 25600000


def test_missing_size_and_tbr_stay_absent_rather_than_zero():
    """Substituting 0 would read as "empty file", not "not reported"."""
    option = _format_options_of({"formats": [{"format_id": "hls-audio", "ext": "m4a"}]})[0]
    assert option.filesize_approx is None
    assert option.tbr is None
    assert option.resolution is None
    assert option.ext == "m4a"


def test_single_format_post_yields_one_option():
    """US3 acceptance scenario 2."""
    info = {"formats": [{"format_id": "http-640", "resolution": "640x360", "ext": "mp4"}]}
    assert len(_format_options_of(info)) == 1


def test_formats_without_an_id_are_not_offered():
    """An option the operator cannot pass back is not an option."""
    info = {"formats": [{"resolution": "1280x720", "ext": "mp4"}, {"format_id": "ok"}]}
    assert [o.format_id for o in _format_options_of(info)] == ["ok"]


@pytest.mark.parametrize("info", [{}, {"formats": None}, {"formats": []}])
def test_absent_formats_list_is_empty_not_an_error(info):
    assert _format_options_of(info) == ()


# --------------------------------------------------------------------------
# Format selection (US4) -- FR-008
# --------------------------------------------------------------------------


def test_default_format_is_unchanged_when_no_format_is_requested():
    """US1 must not shift because US4 exists."""
    assert _base_options(None)["format"] == "bestvideo+bestaudio/best"


_MIXED_CONTAINERS = {
    "id": "20",
    # What yt-dlp's default selector chose. Under --format this is the wrong
    # answer, and taking it is the defect this group covers.
    "ext": "mp4",
    "formats": [
        {"format_id": "http-950", "ext": "mp4", "resolution": "1280x720"},
        {"format_id": "http-256", "ext": "webm", "resolution": "640x360"},
        {"format_id": "hls-audio", "ext": "m4a"},
    ],
}


@pytest.mark.parametrize(
    ("format_id", "expected"),
    [("http-950", ".mp4"), ("http-256", ".webm"), ("hls-audio", ".m4a")],
)
def test_extension_follows_the_requested_format_not_the_default(format_id, expected):
    """The deviation-2 defect.

    `http-256` is webm while the entry's own `ext` says mp4. Taking the entry's
    value would write a webm named .mp4, and _promote renames whatever landed to
    that name -- so the file would misdescribe itself and FR-016 would keep
    agreeing with the wrong name forever (ADR-0001's lying-filename class).
    """
    assert _extension_for_format(_MIXED_CONTAINERS, format_id) == expected


def test_requested_format_extension_disagrees_with_the_default_on_purpose():
    """Guards the specific pair that makes the bug observable."""
    with YoutubeDL(_base_options(None)) as ydl:
        default = _extension_of(ydl, _MIXED_CONTAINERS)
    chosen = _extension_for_format(_MIXED_CONTAINERS, "http-256")
    assert default == ".mp4"
    assert chosen == ".webm"
    assert chosen != default


def test_unknown_format_id_returns_none_rather_than_guessing():
    """None is the FR-008 unavailable case; the caller turns it into a message.

    Returning ".mp4" here would be exactly the guess the fix exists to remove.
    """
    assert _extension_for_format(_MIXED_CONTAINERS, "http-9999") is None


@pytest.mark.parametrize("entry", [{"id": "20"}, {"id": "20", "formats": []}, {"id": "20", "formats": None}])
def test_entry_without_formats_cannot_resolve_a_requested_format(entry):
    assert _extension_for_format(entry, "http-950") is None


@pytest.mark.parametrize("ext", [None, "", "NA"])
def test_requested_format_without_a_container_is_refused(ext):
    """Same refusal as _extension_of: no container reported means we do not know."""
    entry = {"id": "20", "formats": [{"format_id": "http-950", "ext": ext}]}
    with pytest.raises(ValueError, match="reports no container"):
        _extension_for_format(entry, "http-950")


def test_format_id_matching_is_exact_not_a_prefix():
    """`http-95` must not select `http-950`."""
    assert _extension_for_format(_MIXED_CONTAINERS, "http-95") is None


def test_numeric_format_ids_match_as_strings():
    """yt-dlp reports some ids as ints; the CLI always hands over a string."""
    entry = {"id": "20", "formats": [{"format_id": 137, "ext": "webm"}]}
    assert _extension_for_format(entry, "137") == ".webm"


def test_unavailable_format_names_both_what_was_asked_and_what_exists():
    """US4 acceptance scenario 2, FR-008."""
    info = {
        "formats": [
            {"format_id": "http-950", "ext": "mp4"},
            {"format_id": "http-256", "ext": "mp4"},
        ]
    }
    error = DownloadError(
        "ERROR: [twitter] 20: Requested format is not available. "
        "Use --list-formats for a list of available formats"
    )
    message = _diagnose_format(error, "http-9999", info)

    assert "'http-9999'" in message, "must name what was requested"
    assert "http-950" in message and "http-256" in message, "must name what exists"


def test_unavailable_format_reads_formats_from_playlist_entries_too():
    """A multi-video post carries formats per entry, not on the container."""
    info = {
        "_type": "playlist",
        "entries": [{"id": "a", "formats": [{"format_id": "http-640", "ext": "mp4"}]}],
    }
    error = DownloadError("Requested format is not available")
    assert "http-640" in _diagnose_format(error, "bogus", info)


def test_a_post_reporting_no_formats_says_so_rather_than_listing_nothing():
    error = DownloadError("Requested format is not available")
    assert "reports no formats" in _diagnose_format(error, "bogus", {"formats": []})


def test_format_diagnosis_defers_to_the_normal_table_for_other_errors():
    """A format override must not swallow the US2 diagnoses.

    A deleted post is still a deleted post, even when --format was passed.
    """
    error = DownloadError("ERROR: [twitter] 20: Requested tweet is unavailable")
    assert _diagnose_format(error, "http-950", {}) == (
        "this post could not be found. It may have been deleted."
    )


def test_format_diagnosis_preserves_the_generic_fallback():
    """The HTTP 522 path stays intact under a format override too."""
    raw = "ERROR: unable to download video data: HTTP Error 522: Origin Connection Time-out"
    result = _diagnose_format(DownloadError(raw), "http-950", {})
    assert result.startswith("could not extract video from this post.")
    assert raw in result


# --------------------------------------------------------------------------
# Promotion and cleanup -- filesystem only, no yt-dlp involved
# --------------------------------------------------------------------------


def test_promote_moves_the_single_finished_file(tmp_path):
    temp_dir = tmp_path / ".tmp-xvd-test"
    temp_dir.mkdir()
    (temp_dir / "20.mp4").write_bytes(b"video bytes")
    target = tmp_path / f"someone-{POST_ID}.mp4"

    _promote(temp_dir, target)

    assert target.read_bytes() == b"video bytes"
    assert list(temp_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("files", "why"),
    [
        ([], "nothing landed -- an assumption broke"),
        (["a.mp4", "b.mp4"], "two files -- promoting either one would be a guess"),
    ],
)
def test_promote_refuses_to_guess(tmp_path, files, why):
    """A silent wrong-file promotion is worse than an error."""
    temp_dir = tmp_path / ".tmp-xvd-test"
    temp_dir.mkdir()
    for name in files:
        (temp_dir / name).write_bytes(b"x")

    with pytest.raises(RuntimeError, match="expected exactly one finished file"):
        _promote(temp_dir, tmp_path / "out.mp4")


def test_cleanup_removes_the_temp_directory(tmp_path, capsys):
    """FR-015 on the ordinary path: gone, and silent about it."""
    temp_dir = tmp_path / ".tmp-xvd-test"
    temp_dir.mkdir()
    (temp_dir / "partial.mp4.part").write_bytes(b"x" * 1024)
    warnings = []

    _remove_temp_dir(temp_dir, warnings.append)

    assert not temp_dir.exists()
    assert warnings == []
    assert capsys.readouterr().err == "", "the core module writes nothing itself"


def test_cleanup_is_silent_when_the_directory_is_already_gone(tmp_path):
    """The finally block runs even when the body never created anything."""
    warnings = []
    _remove_temp_dir(tmp_path / "never-existed", warnings.append)
    assert warnings == []


def test_cleanup_warns_through_the_callback_naming_the_directory(tmp_path, capsys):
    """The T008 regression: a visible leftover is acceptable, an invisible one is not.

    An open write handle is what Ctrl+C leaves behind on Windows, where the OS
    refuses to unlink the file. POSIX unlinks it happily, so this asserts the
    *contract* rather than the platform: whatever the cause, if the directory
    survives, the caller is handed its exact path.
    """
    temp_dir = tmp_path / ".tmp-xvd-held"
    temp_dir.mkdir()
    handle = open(temp_dir / "partial.mp4.part", "wb")
    handle.write(b"x" * 4096)
    handle.flush()
    warnings = []
    try:
        _remove_temp_dir(temp_dir, warnings.append)
        if temp_dir.exists():
            assert len(warnings) == 1
            assert "could not remove" in warnings[0]
            assert str(temp_dir) in warnings[0], "the caller must be given the exact path"
        else:
            # POSIX: the unlink succeeded despite the open handle, which is the
            # correct outcome there. Nothing to warn about.
            assert warnings == []
        assert capsys.readouterr().err == "", "still nothing written by the core module"
    finally:
        handle.close()


def test_cleanup_without_a_callback_drops_the_warning_silently(tmp_path, capsys):
    """No callback means the caller chose not to hear about it.

    The alternative -- falling back to stderr when on_warning is None -- would
    put terminal output back in this module by another name.
    """
    temp_dir = tmp_path / ".tmp-xvd-held"
    temp_dir.mkdir()
    handle = open(temp_dir / "partial.mp4.part", "wb")
    handle.write(b"x" * 4096)
    handle.flush()
    try:
        _remove_temp_dir(temp_dir)  # no callback
        assert capsys.readouterr().err == ""
    finally:
        handle.close()


def test_warning_callback_default_keeps_cleanup_callable_with_one_argument(tmp_path):
    """on_warning is optional, matching the progress hook's shape."""
    temp_dir = tmp_path / ".tmp-xvd-test"
    temp_dir.mkdir()
    _remove_temp_dir(temp_dir)
    assert not temp_dir.exists()
