"""Named media requests preserve user titles and leave existing controls intact."""

from dataclasses import replace
from time import monotonic

import pytest

from custom_components.polish_context_voice.core.home_model import Area, HomeModel
from custom_components.polish_context_voice.core.media_search import (
    MediaCandidate,
    MediaSearchCommand,
    MediaSearchTurn,
    candidates_from_response,
    exact_candidates,
    follows,
    parse_media_search,
    selected_index,
    source_candidates,
    with_location,
)
from custom_components.polish_context_voice.core.types import Request


@pytest.mark.parametrize(
    "text,kind,query,creator",
    [
        ("Puść utwór Łąka i deszcz", "track", "Łąka i deszcz", None),
        ("Puszcz utwór Łąka i deszcz", "track", "Łąka i deszcz", None),
        ("Odpal mi piosenkę Hotel California", "track", "Hotel California", None),
        (
            "Odtwórz piosenkę We Are The Champions wykonawcy Queen",
            "track",
            "We Are The Champions",
            "Queen",
        ),
        ("Puść album Abbey Road zespołu The Beatles", "album", "Abbey Road", "The Beatles"),
        ("Puść muzykę wykonawcy Dżem", "artist", "Dżem", None),
        ("Włącz mi muzykę Queen", "artist", "Queen", None),
        ("Odpal mi piosenki zespołu The Beatles", "artist", "The Beatles", None),
        ("Puść audiobook Mały Książę", "audiobook", "Mały Książę", None),
        ("Włącz mi audiobooka Diuna", "audiobook", "Diuna", None),
        ("Czy możesz mi włączyć audiobooka Dune?", "audiobook", "Dune", None),
        ("Odtwórz mi książkę audio Dune", "audiobook", "Dune", None),
        ("Wznów audiobook Solaris autora Stanisław Lem", "audiobook", "Solaris", "Stanisław Lem"),
        ("Puść piosenkę Don't Stop Me Now", "track", "Don't Stop Me Now", None),
        ("Puść piosenkę La bohème", "track", "La bohème", None),
        ("Proszę zagraj Hotel California.", "track", "Hotel California", None),
    ],
)
def test_content_forms(text, kind, query, creator):
    command = parse_media_search(text, {})
    assert command.media_type == kind
    assert command.query == query
    assert command.creator == creator


@pytest.mark.parametrize(
    "text",
    [
        "Włącz światła",
        "Wyłącz muzykę",
        "Puść muzykę",
        "następny",
        "dalej",
        "graj dalej",
        "nie puść utworu X",
        "odtwórz playlistę",
        "Jaka jest pogoda?",
        "Puść utwór X i wyłącz światła",
    ],
)
def test_other_routes_keep_priority(text):
    assert parse_media_search(text, {}) is None


def test_explicit_media_without_title_requests_one_instead_of_searching_everything():
    command = parse_media_search("Włącz mi audiobooka")
    assert command.media_type == "audiobook" and command.query == ""
    assert parse_media_search("Włącz mi muzykę") is None


def test_missing_title_followup_stays_in_its_conversation_and_does_not_capture_commands():
    request = Request(
        "",
        user_id="alice",
        source_area="living_room",
        satellite_id="assist_satellite.living_room",
        conversation_id="conversation-1",
    )
    turn = MediaSearchTurn(
        MediaSearchCommand("audiobook", ""), (), (), monotonic() + 20, "title"
    )
    pending = {request.session_key: turn}
    assert follows(pending, replace(request, text="Diuna"))
    assert follows(pending, replace(request, text="Tak"))
    assert not follows(pending, replace(request, text="Włącz światła"))
    assert not follows(pending, replace(request, text="Jaka jest pogoda?"))
    assert not follows(
        pending,
        replace(request, text="Diuna", satellite_id="assist_satellite.other"),
    )


def test_named_shortcuts_win_over_search():
    settings = {"music_shortcuts": {"favorite": {"name": "Moja muzyka", "aliases": ["Solaris"]}}}
    assert parse_media_search("Puść Solaris", settings) is None


def test_room_suffix_and_literal_title():
    model = HomeModel(
        areas=[Area("living", "Living Room", ("salon", "salonie")), Area("attic", "Poddasze")]
    )
    command = with_location(parse_media_search("Puść utwór X wykonawcy Queen w salonie"), model)
    assert (command.query, command.creator, command.location) == ("X", "Queen", "salonie")
    literal = with_location(parse_media_search("Puść utwór „Noc w salonie”"), model)
    assert literal.query == "Noc w salonie" and literal.location is None
    scoped = with_location(parse_media_search("Puść utwór „Noc w salonie” na poddaszu"), model)
    assert scoped.query == "Noc w salonie" and scoped.location == "poddaszu"


def test_sources_and_room_order():
    model = HomeModel(areas=[Area("living", "Salon", ("salonie",))])
    command = with_location(parse_media_search("Puść utwór X z Plex w salonie"), model)
    assert (command.query, command.source, command.location) == ("X", "plex", "salonie")
    assert parse_media_search("Puść utwór X z mojej biblioteki").library_only
    assert parse_media_search("Puść utwór X z Monochrome").source == "monochrome"


def test_raw_titles_are_not_rewritten_as_uris_or_stripped():
    command = parse_media_search("Puść piosenkę „AC/DC - Hells Bells”")
    assert command.query == "AC/DC - Hells Bells"
    candidate = MediaCandidate("library://track/1", "Łąka", "track")
    assert exact_candidates((candidate,), MediaSearchCommand("track", "łąka")) == (candidate,)


@pytest.mark.parametrize(
    "uri",
    [
        "https://secret/x",
        "javascript:alert(1)",
        "file://track/a",
        "library://artist/1",
        "library://track/",
        "library://u:p@track/a",
        "library://track/a b",
    ],
)
def test_malformed_catalog_uri_never_becomes_playback(uri):
    assert not candidates_from_response(
        {"tracks": [{"name": "X", "uri": uri}]}, MediaSearchCommand("track", "X")
    )


def test_metadata_bounded_and_no_source_guess_from_library():
    command = MediaSearchCommand("track", "X")
    rows = [
        {"name": "X", "uri": f"library://track/{i}", "artists": [{"name": "Queen"}]}
        for i in range(20)
    ]
    items = candidates_from_response({"tracks": rows}, command)
    assert len(items) == 5 and items[0].creators == ("Queen",)
    assert source_candidates(items, "plex") == ()
    direct = MediaCandidate("plex://track/abc", "X", "track")
    assert source_candidates((direct,), "plex") == (direct,)


def test_choices_are_session_scoped_and_expire():
    request = Request("tak", user_id="alice", source_area="living", conversation_id="c")
    item = MediaCandidate("library://track/1", "X", "track")
    turn = MediaSearchTurn(MediaSearchCommand("track", "X"), (item,), (), monotonic() + 20)
    pending = {request.session_key: turn}
    assert follows(pending, request)
    assert not follows(pending, replace(request, user_id="bob"))
    assert not follows(pending, replace(request, dry_run=True))
    assert not follows(pending, replace(request, source_area="bedroom"))
    pending[request.session_key] = replace(turn, expires_at=0)
    assert not follows(pending, request) and not pending


@pytest.mark.parametrize("text,index", [("pierwszy", 0), ("druga", 1), ("numer 3", 2), ("Y", 1)])
def test_short_selection_replies(text, index):
    items = tuple(
        MediaCandidate(f"library://track/{i}", name, "track")
        for i, name in enumerate(("X", "Y", "Z"))
    )
    assert selected_index(text, items) == index


def test_artist_restriction_requires_returned_metadata():
    command = MediaSearchCommand("track", "X", creator="Queen")
    items = (MediaCandidate("library://track/1", "X", "track", creators=("Other",)),)
    assert not exact_candidates(items, command)


def test_author_listing_is_not_artist_filter():
    command = parse_media_search("Jakie mam audiobooki autora Stanisław Lem?")
    assert command.media_type == "audiobook" and command.query == "Stanisław Lem"
    assert command.list_only and command.creator is None


def test_ordinary_home_commands_skip_the_music_shortcut_index(monkeypatch):
    from custom_components.polish_context_voice.core import media_search

    def unexpected(*args):
        raise AssertionError("ordinary command must not build the shortcut index twice")

    monkeypatch.setattr(media_search, "parse_music", unexpected)
    assert media_search.parse_media_search("Włącz światła w kuchni") is None
    assert media_search.parse_media_search("Jaka jest pogoda?") is None
