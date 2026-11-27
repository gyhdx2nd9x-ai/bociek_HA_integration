"""Named media on unrelated HA layouts, with strict scope and exact aliases."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.music import (
    MusicCommand,
    music_health,
    parse_music,
    plan_music,
    validate_shortcut,
)
from custom_components.polish_context_voice.core.music_session import MusicSession
from custom_components.polish_context_voice.core.types import Request


@pytest.fixture
def settings():
    return {
        "music_shortcuts": {
            "youtube": {
                "name": "Moja playlista YouTube",
                "aliases": ["moja muzyka YouTube", "YouTube na wieczór"],
                "backend": "home_assistant",
                "media": {
                    "media_content_id": "media-source://test/curated-playlist",
                    "media_content_type": "playlist",
                },
            },
            "plex": {
                "name": "Biblioteka Plex",
                "aliases": ["moje HiRes", "muzyka z Plex"],
                "backend": "music_assistant",
                "config_entry_id": "ma",
                "media": {
                    "media_content_id": "library://playlist/42",
                    "media_content_type": "playlist",
                },
            },
        },
        "music_rooms": {
            "attic_92": {"player": "media_player.attic", "default_shortcut": "plex"},
            "garden_91": {"player": "media_player.garden"},
        },
    }


@pytest.fixture
def music_home():
    return HomeModel(
        areas=[
            Area("attic_92", "Poddasze"),
            Area("garden_91", "Ogród"),
            Area("workshop_93", "Workshop", aliases=("pracownia Ani", "pracowni Ani")),
        ],
        entities=[
            Entity(
                "media_player.attic",
                "Kolumny na poddaszu",
                "attic_92",
                exposed=True,
                state="idle",
                attributes={"supported_features": 65535},
            ),
            Entity(
                "media_player.garden",
                "Głośnik ogrodowy",
                "garden_91",
                exposed=True,
                state="idle",
                attributes={"supported_features": 65535},
            ),
        ],
        satellites={"assist_satellite.custom": "attic_92"},
    )


def request(text, **kwargs):
    return Request(text, satellite_id="assist_satellite.custom", conversation_id="c", **kwargs)


@pytest.mark.parametrize(
    "text",
    [
        "Odpal moją playlistę YouTube",
        "Puść moją muzykę YouTube",
        "Włącz playlistę YouTube",
        "Odtwórz YouTube na wieczór",
        "Proszę odpal moją muzykę YouTube.",
    ],
)
def test_aliases_and_polish_case(text, settings, music_home):
    cmd = parse_music(text, settings)
    assert cmd.shortcut == "youtube"
    plan = plan_music(cmd, request(text), music_home, settings)
    assert plan.status == "ready"
    assert plan.targets == ("media_player.attic",)
    assert plan.calls[0].data["media_content_id"] == "media-source://test/curated-playlist"


def test_new_house_context_and_explicit_room(settings, music_home):
    cmd = parse_music("odpal moją muzykę YouTube w ogrodzie", settings)
    result = plan_music(cmd, request(""), music_home, settings)
    assert result.targets == ("media_player.garden",)
    assert result.area_ids == ("garden_91",)
    assert (
        plan_music(MusicCommand("start"), request(""), music_home, settings).response_data[
            "shortcut"
        ]
        == "plex"
    )


@pytest.mark.parametrize(
    "text",
    [
        "odpal moją playlistę YouTube w nieznanym pokoju",
        "odpal moją playlistę YouTube i wyłącz światła",
        "odpal moją playlistę YouTube ale nie teraz",
        "odpal moją playlistę YouTube Wow jaka figura",
        "puść muzykę wszędzie",
        "przenieś muzykę tutaj",
    ],
)
def test_unresolved_suffix_never_executes(text, settings, music_home):
    result = plan_music(parse_music(text, settings), request(text), music_home, settings)
    assert not result.calls


@pytest.mark.parametrize(
    "text", ["zapal światła", "jaka jest pogoda", "nie odpal playlisty YouTube"]
)
def test_music_does_not_steal_other_commands(text, settings):
    assert parse_music(text, settings) is None


def test_complete_alias_with_room_is_not_split(settings, music_home):
    settings["music_shortcuts"]["plex"]["aliases"] += ["relaks w ogrodzie"]
    cmd = parse_music("puść relaks w ogrodzie", settings)
    assert cmd.shortcut == "plex" and cmd.location is None
    assert plan_music(cmd, request(""), music_home, settings).targets == ("media_player.attic",)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"exposed": False}, "music_player_hidden"),
        ({"available": False}, "music_player_unavailable"),
        ({"labels": frozenset({"voice_block"})}, "music_player_hidden"),
        ({"labels": frozenset({"voice_high_risk"})}, "high_risk_disabled"),
        ({"area_id": "garden_91"}, "music_player_moved"),
        ({"attributes": {"supported_features": 1}}, "music_capability"),
        (
            {
                "attributes": {
                    "supported_features": 65535,
                    "group_members": ["media_player.attic", "media_player.garden"],
                }
            },
            "music_group_unsupported",
        ),
    ],
)
def test_player_guards(changes, reason, settings, music_home):
    old = music_home.entities["media_player.attic"]
    music_home.entities[old.id] = replace(old, **changes)
    result = plan_music(MusicCommand("start"), request(""), music_home, settings)
    assert result.reason == reason and not result.calls


def test_alias_collision_is_rejected(settings):
    candidate = deepcopy(settings["music_shortcuts"]["plex"])
    candidate["aliases"] = ["moją muzykę YouTube"]
    assert (
        validate_shortcut(candidate, settings["music_shortcuts"], "plex") == "duplicate_music_alias"
    )


@pytest.mark.parametrize("uri", ["https://youtube.com/playlist?list=123", "https://youtu.be/abc"])
def test_youtube_page_is_not_claimed_playable(uri, settings):
    value = deepcopy(settings["music_shortcuts"]["youtube"])
    value["media"]["media_content_id"] = uri
    assert validate_shortcut(value, {}, None) == "youtube_page"


def test_music_health_uses_arbitrary_room_ids(settings, music_home):
    assert not music_health(music_home, settings)
    music_home.entities.pop("media_player.attic")
    assert music_health(music_home, settings)[0].code == "music_room_mapping"


@pytest.mark.parametrize(
    "name,spoken",
    [
        ("Jadalnia", "jadalni"),
        ("Gabinet", "gabinecie"),
        ("Pralnia", "pralni"),
        ("Spiżarnia", "spiżarni"),
        ("Garderoba", "garderobie"),
        ("Piwnica", "piwnicy"),
        ("Poddasze", "poddaszu"),
        ("Strych", "strychu"),
        ("Wiatrołap", "wiatrołapie"),
        ("Przedpokój", "przedpokoju"),
        ("Taras", "tarasie"),
        ("Balkon", "balkonie"),
        ("Ogród", "ogrodzie"),
        ("Warsztat", "warsztacie"),
        ("Kotłownia", "kotłowni"),
        ("Pracownia", "pracowni"),
        ("Garaż", "garażu"),
    ],
)
def test_common_room_inflections_without_hardcoded_area_ids(name, spoken, settings):
    model = HomeModel(
        areas=[Area("uuid_a17", name)],
        entities=[
            Entity(
                "media_player.other",
                "Głośnik",
                "uuid_a17",
                exposed=True,
                state="idle",
                attributes={"supported_features": 512},
            )
        ],
    )
    settings["music_rooms"] = {"uuid_a17": {"player": "media_player.other"}}
    cmd = parse_music(f"odpal moją muzykę YouTube w {spoken}", settings)
    plan = plan_music(cmd, Request("", source_area="irrelevant"), model, settings)
    assert plan.status == "ready" and plan.targets == ("media_player.other",)


async def test_rejection_permission_and_provider_failures_never_claim_success(
    settings,
    music_home,
    executor,
):
    session = MusicSession()
    executor.deny = True
    result = await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    assert result.status == "error" and not executor.calls
    executor.deny = False
    executor.fail_at = 0
    result = await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    assert result.status == "error" and not result.completed
    old = music_home.entities["media_player.attic"]
    music_home.entities[old.id] = replace(old, state="playing")
    await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    cancelled = await session.process(
        None, request("nie"), lambda: music_home, settings, executor, lambda p: None
    )
    assert cancelled.status == "cancelled" and not executor.calls


async def test_preview_has_no_service_calls(settings, music_home, executor):
    result = await MusicSession().process(
        MusicCommand("start"),
        request("", dry_run=True),
        lambda: music_home,
        settings,
        executor,
        lambda p: None,
    )
    assert result.status == "dry_run"
    assert not executor.calls and not executor.checked


async def test_start_and_stop_are_exact(settings, music_home, executor):
    session = MusicSession()
    start = await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    assert start.status == "executed" and start.completed == ("media_player.attic",)
    stop = await session.process(
        parse_music("wyłącz muzykę", settings),
        request(""),
        lambda: music_home,
        settings,
        executor,
        lambda p: None,
    )
    assert stop.status == "executed"
    assert executor.calls[-1].service == "media_stop"
    assert executor.calls[-1].entity_ids == ("media_player.attic",)


@pytest.mark.parametrize("answer", ["Tak", "Potwierdzam", "Dobrze", "Okej"])
async def test_replace_playing_confirmation(answer, settings, music_home, executor):
    old = music_home.entities["media_player.attic"]
    music_home.entities[old.id] = replace(old, state="playing")
    session = MusicSession()
    first = await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    assert first.status == "confirm" and not executor.calls
    result = await session.process(
        None, request(answer), lambda: music_home, settings, executor, lambda p: None
    )
    assert result.status == "executed" and len(executor.calls) == 1
    assert not session.follows(request(answer))


async def test_confirmation_isolated_and_expires(settings, music_home, executor):
    old = music_home.entities["media_player.attic"]
    music_home.entities[old.id] = replace(old, labels=frozenset({"voice_confirm"}))
    now = [0]
    session = MusicSession(clock=lambda: now[0])
    await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    assert not session.follows(replace(request("tak"), satellite_id="another"))
    assert not session.follows(replace(request("tak"), user_id="another"))
    assert not session.follows(request("tak", dry_run=True))
    now[0] = 30
    assert not session.follows(request("tak"))


async def test_changed_content_cannot_confirm(settings, music_home, executor):
    old = music_home.entities["media_player.attic"]
    music_home.entities[old.id] = replace(old, state="playing")
    session = MusicSession()
    await session.process(
        MusicCommand("start"), request(""), lambda: music_home, settings, executor, lambda p: None
    )
    music_home.entities[old.id] = replace(
        music_home.entities[old.id], attributes={**old.attributes, "media_content_id": "new"}
    )
    result = await session.process(
        None, request("tak"), lambda: music_home, settings, executor, lambda p: None
    )
    assert result.reason == "music_plan_changed" and not executor.calls


async def test_same_player_concurrency_does_not_block_other_room(settings, music_home, executor):
    entered, release = asyncio.Event(), asyncio.Event()
    original = executor.execute

    async def execute(call, req):
        if call.entity_ids == ("media_player.attic",):
            entered.set()
            await release.wait()
        await original(call, req)

    executor.execute = execute
    session = MusicSession()
    cmd = MusicCommand("start", "youtube")
    first = asyncio.create_task(
        session.process(cmd, request(""), lambda: music_home, settings, executor, lambda p: None)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        busy = await session.process(
            cmd,
            replace(request(""), conversation_id="other"),
            lambda: music_home,
            settings,
            executor,
            lambda p: None,
        )
        assert busy.reason == "music_player_busy"
        other = await session.process(
            replace(cmd, location="w ogrodzie".removeprefix("w ")),
            request(""),
            lambda: music_home,
            settings,
            executor,
            lambda p: None,
        )
        assert other.status == "executed"
    finally:
        release.set()
        await first
    assert not session.admission.active
