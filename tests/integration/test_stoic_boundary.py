"""Daily Stoic boundary doubles; no HA installation or physical device."""

import importlib.util
import sys
import types
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.stoic import (
    DEFAULT_ENTITY_ID,
    MAX_SNAPSHOTS,
    SNAPSHOT_TTL,
    parse_stoic,
)
from custom_components.polish_context_voice.core.types import Request


def load_boundary(monkeypatch, allowed):
    exports = {
        "homeassistant.auth.permissions.const": {"POLICY_READ": "read"},
        "homeassistant.components.homeassistant.exposed_entities": {
            "async_should_expose": lambda h, a, e: allowed["exposed"]
        },
        "homeassistant.util.dt": {"now": lambda: datetime(2026, 10, 29, 23, 59)},
    }
    for name, values in exports.items():
        parts = name.split(".")
        for end in range(1, len(parts) + 1):
            module_name = ".".join(parts[:end])
            if module_name not in sys.modules:
                module = types.ModuleType(module_name)
                module.__path__ = []
                monkeypatch.setitem(sys.modules, module_name, module)
                if end > 1:
                    monkeypatch.setattr(
                        sys.modules[".".join(parts[: end - 1])],
                        parts[end - 1],
                        module,
                        raising=False,
                    )
        for key, value in values.items():
            monkeypatch.setattr(sys.modules[name], key, value, raising=False)
    name = "custom_components.polish_context_voice.ha.stoic"
    spec = importlib.util.spec_from_file_location(
        name, Path("custom_components/polish_context_voice/ha/stoic.py")
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def boundary(monkeypatch):
    allowed = {"exposed": True, "read": True}
    module = load_boundary(monkeypatch, allowed)
    clock = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    user = NS(is_active=True, permissions=NS(check_entity=lambda e, p: allowed["read"]))
    attributes = {
        "quote": "Cytat pierwszy.",
        "author_and_source": "Autor",
        "title": "Tytuł pierwszy",
        "commentary": "Komentarz pierwszy.",
        "dateKey": "10-29",
    }
    state = NS(state="2026-10-29", attributes=attributes)
    entity = Entity(
        DEFAULT_ENTITY_ID,
        "Stoic",
        None,
        exposed=True,
        state="2026-10-29",
        platform="daily_stoic",
    )
    model = HomeModel(entities=[entity])
    runtime = NS(
        hass=NS(
            auth=NS(async_get_user=AsyncMock(return_value=user)),
            states=NS(get=lambda e: state if e in model.entities else None),
        ),
        visible_model=lambda u: model,
        stoic_settings={},
        stoic_snapshots={},
    )
    request = Request("Przeczytaj cytat", user_id="user", conversation_id="conversation")
    return module, runtime, request, model, state, allowed, clock


async def read(boundary, text="Przeczytaj cytat", request=None):
    module, runtime, default, *_ = boundary
    request = replace(request or default, text=text)
    return await module.process(runtime, request, None, parse_stoic(text))


async def test_read_only_sensor_no_service_and_live_attributes(boundary):
    result = await read(boundary, "Przeczytaj dzisiejszą medytację")
    assert result.status == "answered" and result.route == "stoic"
    assert "Cytat pierwszy." in result.speech and "Komentarz pierwszy." in result.speech
    assert not result.calls and not result.completed
    assert result.targets == (DEFAULT_ENTITY_ID,)
    assert result.response_data["date_status"] == "current"


@pytest.mark.parametrize("denied", ["read", "exposed", "guard", "model_exposure", "inactive"])
async def test_access_denied_never_discloses_text_and_discards_snapshot(boundary, denied):
    await read(boundary)
    _, runtime, _, model, _, allowed, _ = boundary
    if denied in {"read", "exposed"}:
        allowed[denied] = False
    elif denied == "guard":
        model.entities[DEFAULT_ENTITY_ID] = replace(
            model.entities[DEFAULT_ENTITY_ID], labels=frozenset({"voice_block"})
        )
    elif denied == "model_exposure":
        model.entities[DEFAULT_ENTITY_ID] = replace(
            model.entities[DEFAULT_ENTITY_ID], exposed=False
        )
    else:
        runtime.hass.auth.async_get_user.return_value.is_active = False
    result = await read(boundary, "Przeczytaj omówienie")
    assert result.status == "blocked" and "pierwszy" not in result.speech
    assert not result.targets and not runtime.stoic_snapshots


async def test_unavailable_and_missing_section(boundary):
    state = boundary[4]
    state.attributes.pop("commentary")
    result = await read(boundary, "Przeczytaj omówienie")
    assert result.reason == "stoic_missing_commentary"
    assert "Omówienie nie jest dostępne" in result.speech
    state.state = "unavailable"
    result = await read(boundary)
    assert result.reason == "stoic_unavailable" and not result.calls


async def test_same_session_followup_keeps_old_snapshot_but_today_reads_fresh(boundary):
    await read(boundary)
    state = boundary[4]
    state.attributes.update(commentary="Komentarz drugi.", quote="Cytat drugi.")
    result = await read(boundary, "Przeczytaj omówienie")
    assert result.speech == "Komentarz pierwszy." and result.response_data["snapshot"]
    result = await read(boundary, "Przeczytaj dzisiejsze omówienie")
    assert result.speech == "Komentarz drugi." and not result.response_data["snapshot"]


async def test_short_followup_needs_same_source_reading_context(boundary):
    result = await read(boundary, "A omówienie?")
    assert result.reason == "stoic_no_reading_context" and "pierwszy" not in result.speech
    await read(boundary)
    result = await read(boundary, "A omówienie?")
    assert result.speech == "Komentarz pierwszy."


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "other"},
        {"satellite_id": "assist_satellite.other"},
        {"dry_run": True},
        {"conversation_id": "other"},
        {"device_id": "other"},
    ],
)
async def test_snapshot_does_not_cross_users_sources_or_transport(boundary, change):
    await read(boundary)
    boundary[4].attributes["commentary"] = "Komentarz drugi."
    result = await read(boundary, "Przeczytaj omówienie", replace(boundary[2], **change))
    assert result.speech == "Komentarz drugi." and not result.response_data["snapshot"]


async def test_expiry_and_stale_metadata(boundary):
    await read(boundary)
    boundary[6][0] += SNAPSHOT_TTL + 1
    boundary[4].attributes["dateKey"] = "10-28"
    result = await read(boundary, "Przeczytaj omówienie")
    assert result.reason == "stoic_stale" and not boundary[1].stoic_snapshots


async def test_snapshot_count_is_bounded(boundary):
    for index in range(MAX_SNAPSHOTS + 2):
        result = await read(boundary, request=replace(boundary[2], conversation_id=str(index)))
        assert result.status == "answered"
    assert len(boundary[1].stoic_snapshots) == MAX_SNAPSHOTS


async def test_configured_missing_sensor_never_silently_uses_another(boundary):
    boundary[1].stoic_settings["stoic_entity_id"] = "sensor.no_such_sensor"
    result = await read(boundary)
    assert result.status == "blocked" and "pierwszy" not in result.speech


async def test_auto_detect_single_platform_sensor_when_standard_id_absent(boundary):
    _, _, _, model, *_ = boundary
    entity = model.entities.pop(DEFAULT_ENTITY_ID)
    model.entities["sensor.moja_medytacja"] = replace(entity, id="sensor.moja_medytacja")
    result = await read(boundary)
    assert result.status == "answered" and result.targets == ("sensor.moja_medytacja",)


async def test_autodetection_asks_when_multiple_sources(boundary):
    _, _, _, model, *_ = boundary
    entity = model.entities.pop(DEFAULT_ENTITY_ID)
    for suffix in ("a", "b"):
        model.entities["sensor." + suffix] = replace(entity, id="sensor." + suffix)
    result = await read(boundary)
    assert result.reason == "stoic_sensor_ambiguous" and not result.targets


async def test_user_sensor_contract_commentary_is_answer_on_originating_request(boundary):
    _, runtime, request, _, state, *_ = boundary
    state.attributes = {
        "quote": "Pełny cytat.",
        "author": "Autor",
        "source": "Źródło",
        "author_and_source": "Autor, Źródło",
        "title": "Medytacja",
        "date": "29 października",
        "day_of_year": 302,
        "is_leap_year": False,
        "commentary": "Cały komentarz z książki.",
    }
    request = replace(request, satellite_id="assist_satellite.portal", device_id="portal")
    result = await read(boundary, "Przeczytaj omówienie stoica", request=request)
    assert result.status == "answered" and result.speech == state.attributes["commentary"]
    assert result.response_data["date_status"] == "current"
    assert not result.calls  # Response in the existing Assist pipeline, no TTS redirect/service.
    assert request.session_key in runtime.stoic_snapshots
