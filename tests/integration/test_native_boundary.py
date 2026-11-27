"""Contract tests using doubles, deliberately not a running/test-installed HA."""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest


def load(monkeypatch, name, exports):
    for full_name, attributes in exports.items():
        parts = full_name.split(".")
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
        for key, value in attributes.items():
            monkeypatch.setattr(sys.modules[full_name], key, value, raising=False)
    module_name = "custom_components.polish_context_voice.ha." + name
    spec = importlib.util.spec_from_file_location(
        module_name, Path("custom_components/polish_context_voice/ha") / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def entry(identifier, **kwargs):
    return NS(
        entity_id=identifier,
        name="Lampa",
        original_name=None,
        device_class=None,
        original_device_class=None,
        disabled_by=kwargs.get("disabled_by"),
        labels=kwargs.get("labels", {"label_uuid"}),
        aliases={"sufit"},
        device_id="device",
        area_id=kwargs.get("area_id"),
    )


def test_native_registry_effective_area_exposure_labels(monkeypatch):
    entities = {
        "light.main": entry("light.main"),
        "assist_satellite.voice": entry("assist_satellite.voice", area_id="kitchen"),
    }
    states = {
        key: NS(entity_id=key, state="off", attributes={"friendly_name": key}) for key in entities
    }
    exposed_calls = []

    def exposed(hass, assistant, identifier):
        exposed_calls.append((assistant, identifier))
        return identifier == "light.main"

    area = NS(id="living_room", name="Salon", aliases={"pokój"}, floor_id="floor", labels=set())
    device = NS(
        id="device",
        area_id="living_room",
        labels={"guard_uuid"},
        name="Device",
        name_by_user="Urządzenie w salonie",
    )
    exports = {
        "homeassistant.components.homeassistant.exposed_entities": {"async_should_expose": exposed},
        "homeassistant.helpers.entity_registry": {
            "async_get": lambda h: NS(entities=entities),
            "async_get_effective_area_id": lambda h, e: e.area_id or device.area_id,
            "async_get_entity_aliases": lambda h, e: ["sufit", "Nazwa obliczona przez HA"],
        },
        "homeassistant.helpers.device_registry": {
            "async_get": lambda h: NS(async_get=lambda i: device),
            "async_get_effective_area_id": lambda h, d: d.area_id,
        },
        "homeassistant.helpers.area_registry": {
            "async_get": lambda h: NS(async_list_areas=lambda: [area])
        },
        "homeassistant.helpers.floor_registry": {
            "async_get": lambda h: NS(
                async_list_floors=lambda: [NS(floor_id="floor", name="Parter", aliases=set())]
            )
        },
        "homeassistant.helpers.label_registry": {
            "async_get": lambda h: NS(
                async_list_labels=lambda: [
                    NS(label_id="label_uuid", name="voice_default_on"),
                    NS(label_id="guard_uuid", name="voice_confirm"),
                ]
            )
        },
    }
    module = load(monkeypatch, "registry_sync", exports)
    hass = NS(
        states=NS(get=states.get, async_all=lambda: list(states.values())),
        config=NS(units=NS(temperature_unit="°C")),
    )
    model = module.snapshot(hass)
    assert model.entities["light.main"].area_id == "living_room"
    assert model.satellites["assist_satellite.voice"] == "kitchen"
    assert model.entities["light.main"].labels == {"voice_default_on", "voice_confirm"}
    assert model.entities["light.main"].exposed
    assert model.entities["light.main"].aliases == ("sufit", "Nazwa obliczona przez HA")
    assert not model.entities["assist_satellite.voice"].exposed
    assert ("conversation", "light.main") in exposed_calls


@pytest.mark.parametrize("mode", ["allowed", "denied", "unexposed", "inactive", "unavailable"])
async def test_executor_permissions_exposure_and_context(monkeypatch, mode):
    from custom_components.polish_context_voice.core.types import Call, Request

    module = load(
        monkeypatch,
        "intents",
        {
            "homeassistant.auth.permissions.const": {"POLICY_CONTROL": "control"},
            "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
            "homeassistant.components.homeassistant.exposed_entities": {
                "async_should_expose": lambda *a: mode != "unexposed"
            },
        },
    )
    user = NS(
        is_active=mode != "inactive", permissions=NS(check_entity=lambda *a: mode != "denied")
    )
    hass = NS(
        auth=NS(async_get_user=AsyncMock(return_value=user)),
        states=NS(get=lambda e: NS(state="unavailable" if mode == "unavailable" else "off")),
        services=NS(has_service=lambda *a: True, async_call=AsyncMock()),
    )
    executor = module.HAExecutor(hass)
    executor.context = object()
    call, request = Call("light", "turn_on", ("light.main",)), Request("x", user_id="u")
    if mode == "allowed":
        await executor.execute(call, request)
        assert hass.services.async_call.call_args.kwargs["context"] is executor.context
        assert hass.services.async_call.call_args.args[2] == {"entity_id": ["light.main"]}
    else:
        with pytest.raises(RuntimeError):
            await executor.execute(call, request)
        hass.services.async_call.assert_not_called()


@pytest.fixture
def state_sync(monkeypatch):
    return load(
        monkeypatch,
        "registry_sync",
        {
            "homeassistant.components.homeassistant.exposed_entities": {},
            **{
                "homeassistant.helpers." + name + "_registry": {}
                for name in ("entity", "device", "area", "floor", "label")
            },
        },
    )


def test_state_refresh_retains_relevant_values_without_reindexing(state_sync, home):
    from dataclasses import replace

    from custom_components.polish_context_voice.core.home_model.cache import ModelCache

    cache = ModelCache(lambda: home)
    indexes = cache.get().entity_index
    identifier = "light.living_room_main"
    original = home.entities[identifier]
    # Registry device_class override may legitimately differ from the state attribute.
    home.entities[identifier] = replace(
        original, device_class="override", attributes={"device_class": "light"}
    )
    state = NS(
        state="unavailable",
        attributes={
            "device_class": "light",
            "supported_features": 42,
            "entity_id": ["light.member"],
            "irrelevant_payload": "x" * 100_000,
        },
    )
    state_sync.update_state(NS(), cache, NS(data={"entity_id": identifier, "new_state": state}))
    refreshed = cache.get().entities[identifier]
    assert not refreshed.available
    assert refreshed.members == ("light.member",)
    assert refreshed.attributes["supported_features"] == 42
    assert "irrelevant_payload" not in refreshed.attributes
    assert refreshed.device_class == "override"
    assert cache.rebuilds == 1
    assert cache.state_updates == 1
    assert home.entity_index is indexes


@pytest.mark.parametrize("change", ["new", "removed", "renamed", "class_removed"])
def test_state_metadata_changes_invalidate(state_sync, home, change):
    from dataclasses import replace

    from custom_components.polish_context_voice.core.home_model.cache import ModelCache

    cache = ModelCache(lambda: home)
    cache.get()
    identifier = "light.living_room_main"
    state = NS(state="off", attributes={})
    if change == "new":
        identifier = "light.new"
    elif change == "removed":
        state = None
    elif change == "renamed":
        state.attributes["friendly_name"] = "Changed"
    else:
        original = home.entities[identifier]
        home.entities[identifier] = replace(original, attributes={"device_class": "old"})
    state_sync.update_state(NS(), cache, NS(data={"entity_id": identifier, "new_state": state}))
    assert cache.dirty


def test_climate_state_units_use_ha_configuration(state_sync):
    attrs = state_sync.state_attributes(
        NS(config=NS(units=NS(temperature_unit="°F"))),
        "climate.room",
        NS(attributes={"min_temp": 45, "max_temp": 80}),
    )
    assert attrs == {"min_temp": 45, "max_temp": 80, "temperature_unit": "°F"}


def test_weather_wind_attributes_survive_filter_without_provider_payload(state_sync):
    attrs = {
        "temperature": 17.7,
        "temperature_unit": "°C",
        "wind_speed": 25,
        "wind_gust_speed": 45,
        "wind_speed_unit": "km/h",
    }
    result = state_sync.state_attributes(
        NS(), "weather.home", NS(attributes={**attrs, "unused_payload": "large"})
    )
    assert result == attrs


def test_live_media_context_retains_queue_identity_and_filters_unrelated_payload(state_sync):
    attrs = {
        "active_queue": "shared",
        "media_content_type": "music",
        "media_content_id": "library://track/1",
        "media_title": "Title",
        "media_artist": "Artist",
        "group_members": ["media_player.a", "media_player.b"],
    }
    assert (
        state_sync.state_attributes(
            NS(),
            "media_player.a",
            NS(
                attributes={
                    **attrs,
                    "provider_payload": "large",
                }
            ),
        )
        == attrs
    )


@pytest.mark.parametrize("mode", ["allowed", "denied_member", "unexposed_member"])
async def test_coordinator_action_rechecks_all_indirect_group_targets(monkeypatch, mode):
    from custom_components.polish_context_voice.core.types import Call, Request

    module = load(
        monkeypatch,
        "intents",
        {
            "homeassistant.auth.permissions.const": {"POLICY_CONTROL": "control"},
            "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
            "homeassistant.components.homeassistant.exposed_entities": {
                "async_should_expose": lambda h, assistant, i: (
                    not (mode == "unexposed_member" and i == "media_player.child")
                ),
            },
        },
    )
    checked = []

    def permitted(identifier, policy):
        checked.append(identifier)
        return not (mode == "denied_member" and identifier == "media_player.child")

    user = NS(is_active=True, permissions=NS(check_entity=permitted))
    hass = NS(
        auth=NS(async_get_user=AsyncMock(return_value=user)),
        states=NS(get=lambda i: NS(state="playing")),
        services=NS(has_service=lambda *args: True, async_call=AsyncMock()),
    )
    call = Call(
        "media_player",
        "media_next_track",
        ("media_player.leader",),
        guard_entity_ids=("media_player.leader", "media_player.child"),
    )
    executor = module.HAExecutor(hass)
    if mode == "allowed":
        await executor.execute(call, Request("następny", user_id="u"))
        assert checked == ["media_player.leader", "media_player.child"]
        assert hass.services.async_call.call_args.args[2] == {"entity_id": ["media_player.leader"]}
    else:
        with pytest.raises(RuntimeError):
            await executor.execute(call, Request("następny", user_id="u"))
        hass.services.async_call.assert_not_called()
