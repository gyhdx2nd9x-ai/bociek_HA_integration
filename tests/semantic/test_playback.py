"""Conversational media commands, independent rooms and exactly one shared-queue action."""

import asyncio
from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.music import parse_music, plan_music
from custom_components.polish_context_voice.core.music_session import MusicSession
from custom_components.polish_context_voice.core.types import Request


def player(identifier, area="living", **kwargs):
    return Entity(
        identifier,
        identifier.split(".")[1],
        area,
        exposed=True,
        state="playing",
        attributes={"supported_features": 65535, "media_content_type": "music"},
        **kwargs,
    )


@pytest.fixture
def house():
    return HomeModel(
        areas=[Area("living", "Salon"), Area("kitchen", "Kuchnia")],
        entities=[player("media_player.main"), player("media_player.kitchen", "kitchen")],
        satellites={"assist_satellite.living": "living", "assist_satellite.kitchen": "kitchen"},
    )


def request(text, **kwargs):
    return Request(text, satellite_id="assist_satellite.living", conversation_id="c", **kwargs)


def settings():
    return {"music_rooms": {"living": {"player": "media_player.main"}}}


def plan(text, house, config=None, req=None):
    return plan_music(parse_music(text, config or {}), req or request(text), house, config or {})


@pytest.mark.parametrize(
    "text",
    [
        "następny",
        "następna",
        "dalej",
        "pomiń",
        "kolejny",
        "kolejna",
        "następna piosenka",
        "następny utwór",
        "kolejna piosenka",
        "kolejny utwór",
        "puść następną piosenkę",
        "włącz następny utwór",
        "Proszę następna!",
    ],
)
def test_next_track_natural_variants(text, house):
    result = plan(text, house)
    assert result.status == "ready" and result.calls[0].service == "media_next_track"
    assert result.targets == ("media_player.main",)


@pytest.mark.parametrize(
    "text", ["poprzedni", "poprzednia", "poprzednia piosenka", "poprzedni utwór"]
)
def test_previous_track_natural_variants(text, house):
    assert plan(text, house).calls[0].service == "media_previous_track"


@pytest.mark.parametrize(
    "text",
    [
        "nie następny",
        "dalej i wyłącz światła",
        "następna osoba mówi",
        "pomiń ale później",
        "jeśli skończy to następny",
        "następny utwór i otwórz drzwi",
    ],
)
def test_short_media_never_trims_negation_or_other_speech(text):
    assert parse_music(text, {}) is None


def test_continue_uses_live_playing_or_paused_state(house):
    assert plan("dalej", house).calls[0].service == "media_next_track"
    current = house.entities["media_player.main"]
    house.entities[current.id] = replace(current, state="paused")
    assert plan("dalej", house).calls[0].service == "media_play"
    assert plan("graj dalej", house).calls[0].service == "media_play"
    assert plan("następny", house).calls[0].service == "media_next_track"


def test_no_local_music_does_not_control_remote_room_or_tts(house):
    current = house.entities["media_player.main"]
    for state, content, kind in (
        ("idle", "", "music"),
        ("playing", "media-source://tts/message", "music"),
        ("playing", "", "movie"),
    ):
        house.entities[current.id] = replace(
            current,
            state=state,
            attributes={
                **current.attributes,
                "media_content_id": content,
                "media_content_type": kind,
            },
        )
        result = plan("dalej", house, settings())
        assert result.reason == "music_not_playing" and not result.calls


def test_explicit_room_overrides_source_and_matching_track_is_not_a_session(house):
    for identifier, current in list(house.entities.items()):
        house.entities[identifier] = replace(
            current,
            attributes={
                **current.attributes,
                "media_content_id": "library://track/42",
                "media_title": "Same song",
            },
        )
    assert plan("następny", house).targets == ("media_player.main",)
    assert plan("następny w kuchni", house).targets == ("media_player.kitchen",)


def group(house, ma=False):
    members = tuple(house.entities)
    for index, (identifier, current) in enumerate(list(house.entities.items())):
        house.entities[identifier] = replace(
            current,
            platform="music_assistant" if ma else "test",
            config_entry_id="ma-one" if ma else "test",
            unique_id="queue" if index == 0 else "child",
            attributes={
                **current.attributes,
                "group_members": members,
                **({"active_queue": "queue"} if ma else {}),
            },
        )


@pytest.mark.parametrize("ma", [False, True])
@pytest.mark.parametrize(
    "text,service",
    [
        ("następna", "media_next_track"),
        ("poprzednia piosenka", "media_previous_track"),
        ("pauza", "media_pause"),
        ("graj dalej", "media_play"),
        ("stop", "media_stop"),
    ],
)
def test_group_command_from_either_room_is_one_coordinator_action(house, ma, text, service):
    group(house, ma)
    for origin in ("living", "kitchen"):
        result = plan(
            text, house, req=replace(request(text), satellite_id=f"assist_satellite.{origin}")
        )
        assert result.status == "ready", result
        assert set(result.targets) == set(house.entities)
        assert len(result.calls) == 1 and result.calls[0].service == service
        assert result.calls[0].entity_ids == ("media_player.main",)
        assert result.calls[0].guard_entity_ids == result.targets


def test_ma_virtual_group_owner_and_shared_queue_without_native_group(house):
    for identifier, current in list(house.entities.items()):
        house.entities[identifier] = replace(
            current,
            platform="music_assistant",
            config_entry_id="ma-one",
            unique_id=identifier,
            attributes={**current.attributes, "active_queue": "queue"},
        )
    owner = player(
        "media_player.virtual_group",
        None,
        platform="music_assistant",
        config_entry_id="ma-one",
        unique_id="queue",
    )
    owner = replace(
        owner,
        attributes={
            **owner.attributes,
            "active_queue": "queue",
            "group_members": tuple(house.entities),
        },
    )
    house.entities[owner.id] = owner
    result = plan("następny", house)
    assert result.calls[0].entity_ids == (owner.id,) and len(result.targets) == 3


def test_ma_queue_identity_is_scoped_to_server_instance(house):
    for index, (identifier, current) in enumerate(list(house.entities.items())):
        house.entities[identifier] = replace(
            current,
            platform="music_assistant",
            config_entry_id=f"server-{index}",
            attributes={**current.attributes, "active_queue": "same-id"},
        )
    assert plan("następny", house).targets == ("media_player.main",)


def test_native_player_with_null_group_members_is_a_single_player(house):
    old = house.entities["media_player.main"]
    house.entities[old.id] = replace(old, attributes={**old.attributes, "group_members": None})
    result = plan("następny", house)
    assert result.status == "ready" and result.targets == (old.id,)


@pytest.mark.parametrize(
    "changes",
    [
        {"exposed": False},
        {"available": False},
        {"labels": frozenset({"voice_block"})},
        {"labels": frozenset({"voice_high_risk"})},
        {"labels": frozenset({"voice_exclude_all"})},
    ],
)
def test_group_guards_cover_other_rooms(house, changes):
    group(house)
    other = house.entities["media_player.kitchen"]
    house.entities[other.id] = replace(other, **changes)
    result = plan("następny", house)
    assert result.status == "blocked" and not result.calls


async def run(session, text, house, executor, config=None, req=None):
    return await session.process(
        parse_music(text, config or {}),
        req or request(text),
        lambda: house,
        config or {},
        executor,
        lambda result: None,
    )


async def test_source_bound_memory_survives_new_conversation_and_expires(house, executor):
    extra = player("media_player.second")
    house.entities[extra.id] = extra
    house = HomeModel(
        house.areas.values(), entities=house.entities.values(), satellites=house.satellites
    )
    now = [0]
    session = MusicSession(clock=lambda: now[0])
    first = await run(session, "następny utwór na odtwarzaczu second", house, executor, settings())
    assert first.targets == (extra.id,)
    new = replace(request("następny"), conversation_id="new")
    assert (await run(session, "następny", house, executor, settings(), new)).targets == (extra.id,)
    foreign = replace(new, user_id="someone-else")
    assert (await run(session, "następny", house, executor, settings(), foreign)).targets == (
        "media_player.main",
    )
    now[0] = 61
    assert (await run(session, "następny", house, executor, settings(), new)).targets == (
        "media_player.main",
    )


async def test_ambiguous_sessions_accept_source_bound_player_reply(house, executor):
    house.entities["media_player.second"] = player("media_player.second")
    house = HomeModel(
        house.areas.values(), entities=house.entities.values(), satellites=house.satellites
    )
    session = MusicSession()
    result = await run(session, "następny", house, executor)
    assert result.reason == "ambiguous_music_session" and result.continue_conversation
    reply = request("second")
    assert session.follows(reply, house)
    assert not session.follows(replace(reply, satellite_id="other"), house)
    result = await session.process(None, reply, lambda: house, {}, executor, lambda p: None)
    assert result.status == "executed" and result.targets == ("media_player.second",)


async def test_preview_never_remembers_player_or_arms_choice(house, executor):
    session = MusicSession()
    await run(session, "następny", house, executor, req=request("następny", dry_run=True))
    house.entities["media_player.second"] = player("media_player.second")
    await run(session, "następny", house, executor, req=request("następny", dry_run=True))
    assert (
        not session.pending and not session.questions and not session.memory and not executor.calls
    )


async def test_group_changed_during_preflight_never_executes(house, executor):
    group(house)
    original = executor.preflight

    async def changed(calls, req):
        await original(calls, req)
        old = house.entities["media_player.kitchen"]
        house.entities[old.id] = replace(old, exposed=False)

    executor.preflight = changed
    result = await run(MusicSession(), "następny", house, executor)
    assert result.reason == "music_plan_changed" and not executor.calls


async def test_same_queue_concurrency_is_shared_across_rooms(house, executor):
    group(house, ma=True)
    session = MusicSession()
    entered, release = asyncio.Event(), asyncio.Event()
    original = executor.execute

    async def execute(call, req):
        entered.set()
        await release.wait()
        await original(call, req)

    executor.execute = execute
    first = asyncio.create_task(run(session, "następny", house, executor))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        other = replace(request("następny"), satellite_id="assist_satellite.kitchen")
        result = await run(session, "następny", house, executor, req=other)
        assert result.reason == "music_player_busy"
    finally:
        release.set()
        await first
    assert len(executor.calls) == 1
