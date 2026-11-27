"""Dreame vacuum routing stays bounded to the active map and configured mode."""

from dataclasses import replace
from time import monotonic
from types import SimpleNamespace as NS

import pytest
import test_runtime_resources as resources
from test_runtime_resources import req

from custom_components.polish_context_voice.core.home_model import Entity


@pytest.fixture
def runtime(monkeypatch, home):
    return resources.runtime.__wrapped__(monkeypatch, home)


def attach_vacuum(runtime, *, mapped=True, mode="Vacuum and mop", genius="Routine cleaning"):
    vacuum_id = "vacuum.lab_cleaner"
    runtime.cache.get().entities[vacuum_id] = Entity(
        id=vacuum_id,
        name="Robot testowy",
        area_id="living_room",
        exposed=True,
        available=True,
        state="sleeping",
        platform="dreame_vacuum",
    )
    active_rooms = [
        {"id": 1, "name": "Kuchnia"},
        {"id": 2, "name": "Korytarz"},
        {"id": 3, "name": "Salon"},
    ]
    if not mapped:
        active_rooms = [{"id": 1, "name": "Kuchnia"}]
    state = NS(
        attributes={
            "selected_map": "Mapa 1",
            "rooms": {"Mapa 1": active_rooms},
            "cleangenius": genius,
            "cleangenius_mode": mode,
        }
    )
    runtime.hass.states = NS(get=lambda entity_id: state if entity_id == vacuum_id else None)
    return vacuum_id


async def test_general_request_previews_full_map_default(runtime):
    vacuum_id = attach_vacuum(runtime)
    result = await runtime.process(req("Alexa, posprzątaj", dry_run=True), None)
    assert result.status == "dry_run"
    assert result.targets == (vacuum_id,)
    assert [(call.domain, call.service, call.entity_ids, call.data) for call in result.calls] == [
        ("vacuum", "start", (vacuum_id,), {})
    ]


async def test_named_room_previews_only_the_mapped_segment(runtime):
    vacuum_id = attach_vacuum(runtime)
    result = await runtime.process(req("odkurz kuchnię", dry_run=True), None)
    assert result.status == "dry_run"
    assert result.calls[0].domain == "dreame_vacuum"
    assert result.calls[0].service == "vacuum_clean_segment"
    assert result.calls[0].entity_ids == (vacuum_id,)
    assert result.calls[0].data == {"segments": [1]}


async def test_unmapped_room_and_non_genius_mode_never_plan_a_service(runtime):
    attach_vacuum(runtime, mapped=False)
    result = await runtime.process(req("odkurz sypialnię", dry_run=True), None)
    assert result.status == "clarify"
    assert not result.calls

    attach_vacuum(runtime, mode="Sweeping")
    result = await runtime.process(req("odkurz", dry_run=True), None)
    assert result.status == "blocked"
    assert not result.calls


async def test_successful_execution_records_one_vacuum_result(runtime):
    vacuum_id = attach_vacuum(runtime)
    result = await runtime.process(req("odkurz kuchnię", dry_run=False), None)
    assert result.status == "executed"
    assert result.completed == (vacuum_id,)
    assert len(runtime.recorded_calls) == 1
    row = runtime.diagnostics.snapshot()["recent_requests"][-1]
    assert row["route"] == "vacuum"
    assert row["status"] == "executed"
    assert row["request_id"] == result.request_id
    assert "handler" in row["stages_ms"]


async def test_vacuum_discards_previous_confirmation_only_in_its_session(runtime):
    vacuum_id = attach_vacuum(runtime)
    light_id = "light.living_room_main"
    entities = runtime.cache.get().entities
    entities[light_id] = replace(
        entities[light_id], labels=entities[light_id].labels | {"voice_confirm"}
    )
    prompts = [
        req("zapal światła", satellite_id="room-a"),
        req("zapal światła", satellite_id="room-b"),
    ]
    for prompt in prompts:
        assert (await runtime.process(prompt, None)).status == "confirm"

    preview = await runtime.process(
        req("odkurz kuchnię", satellite_id="room-a", dry_run=True), None
    )
    assert preview.status == "dry_run"
    assert all(prompt.session_key in runtime.pending for prompt in prompts)

    vacuum = await runtime.process(req("odkurz kuchnię", satellite_id="room-a"), None)
    assert vacuum.status == "executed"
    reply = await runtime.process(req("tak", satellite_id="room-a"), None)
    assert reply.reason == "no_pending"
    other = await runtime.process(req("tak", satellite_id="room-b"), None)
    assert other.status == "executed"
    assert [call.entity_ids for _, _, call in runtime.recorded_calls] == [(vacuum_id,), (light_id,)]


@pytest.mark.parametrize("pending_name", ["pending_calendars", "media_search_pending"])
async def test_unmapped_vacuum_request_abandons_older_content_question(runtime, pending_name):
    attach_vacuum(runtime, mapped=False)
    request = req("odkurz sypialnię", satellite_id="same")
    other = req("odkurz sypialnię", satellite_id="other")
    pending = getattr(runtime, pending_name)
    pending[request.session_key] = pending[other.session_key] = NS(expires_at=monotonic() + 30)

    result = await runtime.process(request, None)

    assert result.reason == "vacuum_room_not_mapped"
    assert request.session_key not in pending
    assert other.session_key in pending
    assert not runtime.recorded_calls


async def test_light_preview_skips_vacuum_adapter(runtime, monkeypatch):
    import sys
    from unittest.mock import AsyncMock

    adapter = AsyncMock(side_effect=AssertionError("Vacuum adapter must not run"))
    monkeypatch.setattr(sys.modules[type(runtime).__module__], "_process_vacuum", adapter)
    assert (await runtime.process(req("zapal światła", dry_run=True), None)).status == "dry_run"
    adapter.assert_not_awaited()
