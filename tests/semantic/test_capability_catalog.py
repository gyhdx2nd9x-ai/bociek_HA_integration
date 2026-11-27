"""Contract and compatibility checks for the deliberately small E3 catalog slice."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from custom_components.polish_context_voice.core.capability_catalog import (
    catalog_snapshot,
    definition_for,
    prepare_entity,
)
from custom_components.polish_context_voice.core.home_model import Entity
from custom_components.polish_context_voice.core.types import Action, Proposal


def light(**changes):
    return replace(
        Entity(
            "light.table",
            "Nad stołem",
            "dining_room",
            exposed=True,
            attributes={"supported_color_modes": ["brightness"]},
        ),
        **changes,
    )


def sensor(**changes):
    return replace(
        Entity(
            "sensor.temperature",
            "Temperatura",
            "dining_room",
            exposed=True,
            device_class="temperature",
            state="17.7",
            attributes={"unit_of_measurement": "°C"},
        ),
        **changes,
    )


def brightness(value, entity=None):
    return prepare_entity(
        definition_for(Action.BRIGHTNESS),
        Proposal(Action.BRIGHTNESS, "light", value=value),
        entity or light(),
    )


def temperature(entity=None):
    return prepare_entity(
        definition_for(Action.TEMPERATURE),
        Proposal(Action.TEMPERATURE, "sensor"),
        entity or sensor(),
    )


def test_catalog_is_an_explicit_partial_slice_not_an_authorization_catalog():
    snapshot = catalog_snapshot()
    assert snapshot["schema_version"] == 1
    assert snapshot["partial"] is True
    assert {item["id"] for item in snapshot["capabilities"]} == {
        "light.set_brightness",
        "sensor.read_temperature",
    }
    assert all(
        item["authorization"] == "external_resolver_policy_and_ha_preflight"
        and item["scope_priority"] == ("explicit_location", "source_area")
        for item in snapshot["capabilities"]
    )
    assert json.loads(json.dumps(snapshot, allow_nan=False))["partial"] is True
    for action in {Action.ON, Action.OFF, Action.OPEN, Action.CLOSE, Action.SET_TEMPERATURE}:
        assert definition_for(action) is None


def test_snapshot_is_detached_and_contains_no_household_data():
    snapshot = catalog_snapshot()
    snapshot["capabilities"][0]["domain"] = "lock"
    snapshot["capabilities"].clear()
    assert len(catalog_snapshot()["capabilities"]) == 2
    serialized = json.dumps(catalog_snapshot())
    assert "entity_ids" not in serialized
    assert "dining_room" not in serialized
    assert "Nad sto" not in serialized
    assert definition_for(Action.BRIGHTNESS).domain == "light"


def test_definition_operation_and_facts_cannot_be_mutated():
    definition = definition_for(Action.BRIGHTNESS)
    prepared = brightness(45)
    with pytest.raises(FrozenInstanceError):
        definition.value_max = 1000
    with pytest.raises(FrozenInstanceError):
        prepared.status = "answered"
    with pytest.raises(FrozenInstanceError):
        prepared.facts.value = 100
    with pytest.raises(FrozenInstanceError):
        prepared.operation.service = "turn_off"
    first_call = prepared.call
    first_call.data["brightness_pct"] = 100
    assert prepared.call is not first_call
    assert prepared.call.data == {"brightness_pct": 45}
    facts = prepared.facts.to_dict()
    facts["value"] = 100
    assert prepared.facts.value == 45


@pytest.mark.parametrize("value", [0, 100, 37.5, True, False])
def test_brightness_preserves_legacy_inclusive_range_and_boolean_contract(value):
    prepared = brightness(value)
    assert prepared.status == "ready"
    assert prepared.reason == ""
    assert prepared.call.domain == "light"
    assert prepared.call.service == "turn_on"
    assert prepared.call.entity_ids == ("light.table",)
    assert prepared.call.data == {"brightness_pct": value}
    assert prepared.facts.to_dict() == {
        "capability_id": "light.set_brightness",
        "operation_kind": "command",
        "value_role": "requested",
        "source": "validated_parameter",
        "value": value,
        "unit": "%",
        "original_unit": "%",
    }
    assert "completed" not in prepared.facts.to_dict()


@pytest.mark.parametrize("mode", ["brightness", "color_temp", "rgb", "white", "legacy_mode"])
def test_brightness_preserves_supported_mode_rule_without_new_allowlist(mode):
    assert brightness(45, light(attributes={"supported_color_modes": [mode]})).status == "ready"


@pytest.mark.parametrize("modes", [[], ["onoff"], ["unknown"], ["onoff", "unknown"]])
def test_brightness_refuses_no_dimming_capability(modes):
    prepared = brightness(45, light(attributes={"supported_color_modes": modes}))
    assert prepared.status == "blocked"
    assert prepared.reason == "capability"
    assert prepared.speech == "To światło nie obsługuje jasności."
    assert prepared.call is None


def test_brightness_rejects_wrong_domain_even_with_supported_color_modes():
    prepared = brightness(45, light(id="switch.table"))
    assert (prepared.status, prepared.reason) == ("blocked", "capability")
    assert prepared.call is None


@pytest.mark.parametrize("value", [None, -0.1, 100.1, float("nan"), float("inf"), -float("inf")])
def test_brightness_invalid_values_never_produce_calls_or_nonfinite_facts(value):
    prepared = brightness(value)
    assert prepared.status == "blocked"
    assert prepared.reason == "range"
    assert prepared.speech == "Jasność musi wynosić od 0 do 100 procent."
    assert prepared.call is None
    assert prepared.facts.value is None
    json.dumps(prepared.facts.to_dict(), allow_nan=False)


def test_preparation_cannot_be_switched_to_another_capability_or_forged_definition():
    wrong = prepare_entity(
        definition_for(Action.TEMPERATURE), Proposal(Action.BRIGHTNESS, "light", value=50), light()
    )
    altered = prepare_entity(
        replace(definition_for(Action.BRIGHTNESS), value_max=1000),
        Proposal(Action.BRIGHTNESS, "light", value=500),
        light(),
    )
    for prepared in (wrong, altered):
        assert (prepared.status, prepared.reason) == ("blocked", "capability")
        assert prepared.call is None


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "n/a", "NaN", "inf", "-inf"])
def test_temperature_invalid_state_is_a_query_clarification(state):
    prepared = temperature(sensor(state=state))
    assert (prepared.status, prepared.reason) == ("clarify", "invalid_state")
    assert prepared.speech == "Czujnik nie ma poprawnego odczytu."
    assert prepared.call is None
    assert prepared.facts.value is None
    assert prepared.facts.value_role == "observed"
    json.dumps(prepared.facts.to_dict(), allow_nan=False)


@pytest.mark.parametrize(
    "changes", [{"device_class": None}, {"device_class": "humidity"}, {"id": "number.temp"}]
)
def test_temperature_requires_sensor_domain_and_temperature_device_class(changes):
    prepared = temperature(sensor(**changes))
    assert (prepared.status, prepared.reason) == ("blocked", "capability")
    assert prepared.speech == "To nie jest czujnik temperatury."
    assert prepared.call is None


@pytest.mark.parametrize(
    ("state", "unit", "canonical", "speech"),
    [
        ("17.7", "°C", "°C", "Jest około 18 stopni."),
        ("-1.5", "°C", "°C", "Jest około -2 stopni."),
        ("1.0", "°C", "°C", "Jest około 1 stopnia."),
        ("77.2", "°F", "°F", "Jest około 77 stopni Fahrenheita."),
        ("294.4", "K", "K", "Jest około 294 K."),
        ("17.7", "C", "°C", "Jest około 18 C."),
        ("17.7", "Celsius", "°C", "Jest około 18 Celsius."),
        ("77.2", "Fahrenheit", "°F", "Jest około 77 Fahrenheit."),
        ("294.4", "Kelvin", "K", "Jest około 294 Kelvin."),
        ("17.7", "custom", "custom", "Jest około 18 custom."),
        ("17.7", "", "", "Jest około 18."),
    ],
)
def test_temperature_facts_normalize_units_without_changing_values_or_legacy_speech(
    state, unit, canonical, speech
):
    prepared = temperature(sensor(state=state, attributes={"unit_of_measurement": unit}))
    assert prepared.status == "answered"
    assert prepared.speech == speech
    assert prepared.call is None
    assert prepared.facts.to_dict() == {
        "capability_id": "sensor.read_temperature",
        "operation_kind": "query",
        "value_role": "observed",
        "source": "ha_state_cache",
        "value": float(state),
        "unit": canonical,
        "original_unit": unit,
    }
    assert "entity_ids" not in prepared.facts.to_dict()


def test_catalog_only_prepares_semantics_and_does_not_replace_external_safety_checks():
    entity = light(exposed=False, available=False, labels=frozenset({"voice_block"}))
    prepared = brightness(45, entity)
    assert prepared.status == "ready"
    assert definition_for(Action.BRIGHTNESS).authorization == (
        "external_resolver_policy_and_ha_preflight"
    )
    assert not hasattr(prepared, "authorized")
