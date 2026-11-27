"""Frozen b5 behavior for the first E3 capability extraction.

The fixture was captured before replacing the policy branches. It deliberately
checks old speech, permissions, thresholds, target order and service payloads;
additive ``capability`` metadata and the explicit P6 speech update may differ.
The original frozen fixture and every action-plan field remain unchanged.
"""

import json
import math
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.policy import evaluate
from custom_components.polish_context_voice.core.resolver import Resolution
from custom_components.polish_context_voice.core.types import Action, Proposal, Request

GOLDEN_PATH = Path(__file__).parents[1] / "fixtures" / "e3_legacy_plans.json"
SPEECH_UPDATE_PATH = GOLDEN_PATH.with_name("e3_speech_update_v1.json")
SPEECH_UPDATE = json.loads(SPEECH_UPDATE_PATH.read_text())


def test_p6_speech_update_is_explicit_and_original_fixture_is_unchanged():
    assert sha256(GOLDEN_PATH.read_bytes()).hexdigest() == (
        "b130f0c177cfcfb1a55ab746679c95df16d8f01430620bdac8675b3c83fe5317"
    )
    assert SPEECH_UPDATE["original_fixture_sha256"] == sha256(GOLDEN_PATH.read_bytes()).hexdigest()
    assert SPEECH_UPDATE["schema_version"] == 1
    assert SPEECH_UPDATE["engine"] == {
        "temperature_other_area": {
            "field": "speech",
            "before": (
                "Doprecyzuj pomieszczenie lub nazwę urządzenia. "
                "Sprawdź też role głosowe i dostępność encji."
            ),
            "after": "Nie mogę teraz sprawdzić temperatury.",
        }
    }


def _json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _legacy_result(result):
    fields = result.to_dict()
    for key in ("latency_ms", "conversation_id", "request_id"):
        fields.pop(key)
    metadata = fields.get("response_data")
    if metadata is not None:
        metadata = dict(metadata)
        metadata.pop("capability", None)
        fields["response_data"] = metadata or None
    return _json_value(fields)


def _light(**changes):
    return replace(
        Entity(
            "light.test",
            "Światło testowe",
            "test_room",
            exposed=True,
            state="off",
            attributes={"supported_color_modes": ["brightness"]},
        ),
        **changes,
    )


def policy_cases():
    """Synthetic combinations; all expected outcomes come from the frozen file."""
    modes = (
        (),
        ("onoff",),
        ("unknown",),
        ("brightness",),
        ("color_temp",),
        ("rgb",),
        ("onoff", "rgb"),
        ("unknown", "brightness"),
    )
    values = (None, -1.0, 0.0, 30.5, 100.0, 101.0, math.nan, math.inf)
    for mode_index, supported in enumerate(modes):
        entity = _light(attributes={"supported_color_modes": list(supported)})
        for value_index, value in enumerate(values):
            yield (
                f"brightness_mode_{mode_index}_value_{value_index}",
                Proposal(Action.BRIGHTNESS, "named", name="światło testowe", value=value),
                Resolution((entity,), ("test_room",)),
            )
    for confidence_index, confidence in enumerate((0.84, 0.85, 0.96, 0.97, 0.99, math.nan)):
        for origin in ("fast", "llm"):
            for action in (Action.BRIGHTNESS, Action.TEMPERATURE):
                entity = (
                    _light()
                    if action == Action.BRIGHTNESS
                    else Entity(
                        "sensor.test",
                        "Temperatura testowa",
                        "test_room",
                        exposed=True,
                        state="21.5",
                        device_class="temperature",
                        attributes={"unit_of_measurement": "°C"},
                    )
                )
                yield (
                    f"{action.value}_{origin}_confidence_{confidence_index}",
                    Proposal(
                        action,
                        "named",
                        name=entity.name,
                        value=35.0,
                        confidence=confidence,
                        origin=origin,
                    ),
                    Resolution((entity,), ("test_room",)),
                )
    for action in (Action.BRIGHTNESS, Action.TEMPERATURE):
        for domain in ("light", "switch", "sensor", "climate", "lock"):
            entity = _light(id=f"{domain}.test", device_class="temperature", state="21.5")
            yield (
                f"{action.value}_domain_{domain}",
                Proposal(action, "named", name=entity.name, value=35.0),
                Resolution((entity,), ("test_room",)),
            )
        for restriction in ("voice_confirm", "voice_high_risk"):
            entity = _light(
                labels=frozenset({restriction}), device_class="temperature", state="21.5"
            )
            if action == Action.TEMPERATURE:
                entity = replace(entity, id="sensor.test")
            yield (
                f"{action.value}_restriction_{restriction}",
                Proposal(action, "named", name=entity.name, value=35.0),
                Resolution((entity,), ("test_room",)),
            )
    for state_index, state in enumerate(
        ("0", "-1.5", "21.5", "0.0", "nan", "inf", "-inf", "unknown", "unavailable", None)
    ):
        for unit_index, unit in enumerate(("°C", "°F", "K", "")):
            entity = Entity(
                "sensor.test",
                "Temperatura testowa",
                "test_room",
                exposed=True,
                device_class="temperature",
                state=state,
                attributes={"unit_of_measurement": unit},
            )
            yield (
                f"temperature_state_{state_index}_unit_{unit_index}",
                Proposal(Action.TEMPERATURE, "sensor"),
                Resolution((entity,), ("test_room",)),
            )
    for error in (
        "no_targets",
        "ambiguous_target",
        "unknown_location",
        "missing_area",
        "ineligible_target",
        "cross_area_group",
        "selection_changed",
    ):
        for action in (Action.BRIGHTNESS, Action.TEMPERATURE):
            yield (
                f"{action.value}_resolution_{error}",
                Proposal(action, "light", value=35.0),
                Resolution(area_ids=("test_room",), error=error),
            )
    for confirm in (False, True):
        for compatible in (False, True):
            entities = (
                _light(id="light.first"),
                _light(
                    id="light.second",
                    labels=frozenset({"voice_confirm"}) if confirm else frozenset(),
                    attributes={"supported_color_modes": ["rgb"] if compatible else ["onoff"]},
                ),
            )
            yield (
                f"brightness_multiple_confirm_{confirm}_compatible_{compatible}",
                Proposal(Action.BRIGHTNESS, "light", value=35.0),
                Resolution(entities, ("test_room",), aggregate=True),
            )


POLICY_CASES = list(policy_cases())
GOLDEN = json.loads(GOLDEN_PATH.read_text()) if GOLDEN_PATH.exists() else {}


@pytest.mark.parametrize(
    "case_id,proposal,resolution", POLICY_CASES, ids=[case[0] for case in POLICY_CASES]
)
def test_policy_matches_pre_extraction_b5(case_id, proposal, resolution):
    assert _legacy_result(evaluate(proposal, resolution)) == GOLDEN["policy"][case_id]


def _model_with(home, *changed):
    entities = dict(home.entities)
    entities.update({entity.id: entity for entity in changed})
    return HomeModel(
        home.areas.values(),
        home.floors.values(),
        entities.values(),
        satellites=home.satellites,
        devices=home.devices,
    )


ENGINE_CASES = (
    ("brightness_default", "ustaw światła na 35%", "living_room", None),
    ("brightness_named", "ustaw lampę przy stole na 35%", "living_room", None),
    ("brightness_explicit_area", "ustaw światła w kuchni na 35%", "living_room", None),
    ("brightness_zero", "ustaw światła na 0%", "living_room", None),
    ("brightness_hundred", "ustaw światła na 100%", "living_room", None),
    ("brightness_range", "ustaw światła na 101%", "living_room", None),
    ("brightness_unsupported", "ustaw światła na 35%", "office", None),
    ("brightness_unknown_area", "ustaw światła w piwnicy na 35%", "living_room", None),
    ("brightness_no_area", "ustaw światła na 35%", None, None),
    ("brightness_satellite", "ustaw światła na 35%", None, "assist_satellite.living_room"),
    ("temperature_primary", "jaka jest temperatura", "living_room", None),
    ("temperature_other_area", "jaka jest temperatura w kuchni", "living_room", None),
    ("temperature_no_area", "jaka jest temperatura", None, None),
    ("temperature_unknown_area", "jaka jest temperatura w piwnicy", "living_room", None),
    ("temperature_satellite", "jaka jest temperatura", None, "assist_satellite.living_room"),
)


@pytest.mark.parametrize(
    "case_id,text,area,satellite", ENGINE_CASES, ids=[case[0] for case in ENGINE_CASES]
)
async def test_engine_matches_pre_extraction_b5(home, executor, case_id, text, area, satellite):
    engine = Engine(lambda: home, executor)
    result = await engine.process(
        Request(
            text,
            source_area=area,
            satellite_id=satellite,
            conversation_id="e3_compatibility",
            dry_run=True,
        )
    )
    expected = dict(GOLDEN["engine"][case_id])
    if update := SPEECH_UPDATE["engine"].get(case_id):
        assert expected["speech"] == update["before"]
        expected["speech"] = update["after"]
    assert _legacy_result(result) == expected
    assert executor.calls == executor.checked == []


async def test_brightness_group_preserves_leaf_calls_and_inherited_confirmation(home, executor):
    group = _light(
        id="light.test_group",
        name="Grupa próbna",
        area_id="living_room",
        aliases=("zestaw testowy",),
        labels=frozenset({"voice_confirm"}),
        attributes={"entity_id": ["light.living_room_main", "light.living_room_table"]},
    )
    home = _model_with(home, group)
    engine = Engine(lambda: home, executor)
    request = Request(
        "ustaw zestaw testowy na 35%",
        source_area="living_room",
        conversation_id="e3_group",
        satellite_id="assist_satellite.living_room",
    )
    question = await engine.process(request)
    assert question.status == "confirm"
    assert question.targets == ("light.living_room_main", "light.living_room_table")
    assert executor.calls == []
    completed = await engine.process(replace(request, text="potwierdzam"))
    assert completed.status == "executed"
    assert [(call.domain, call.service, call.entity_ids, call.data) for call in executor.calls] == [
        ("light", "turn_on", ("light.living_room_main",), {"brightness_pct": 35.0}),
        ("light", "turn_on", ("light.living_room_table",), {"brightness_pct": 35.0}),
    ]
    assert len(executor.checked) == 2
    assert (await engine.process(replace(request, text="potwierdzam"))).reason == "no_pending"


@pytest.mark.parametrize("change", ("exposure", "capability", "high_risk", "available"))
async def test_brightness_confirmation_rechecks_current_authorization(home, executor, change):
    target = home.entities["light.living_room_main"]
    confirmed = replace(target, labels=target.labels | {"voice_confirm"})
    holder = [_model_with(home, confirmed)]
    engine = Engine(lambda: holder[0], executor)
    request = Request(
        "ustaw światła na 35%",
        source_area="living_room",
        conversation_id="e3_change",
        satellite_id="assist_satellite.living_room",
    )
    assert (await engine.process(request)).status == "confirm"
    changed = {
        "exposure": replace(confirmed, exposed=False),
        "capability": replace(confirmed, attributes={"supported_color_modes": ["onoff"]}),
        "high_risk": replace(confirmed, labels=confirmed.labels | {"voice_high_risk"}),
        "available": replace(confirmed, available=False),
    }[change]
    holder[0] = _model_with(home, changed)
    result = await engine.process(replace(request, text="potwierdzam"))
    assert result.status == "clarify"
    assert result.reason == "plan_changed"
    assert executor.calls == executor.checked == []


async def test_temperature_ambiguity_retry_selects_exposed_sensor_only(home, executor):
    original = home.entities["sensor.living_room_temperature"]
    first = replace(original, labels=frozenset(), aliases=("przy oknie",))
    second = replace(
        original,
        id="sensor.test_second",
        name="Drugi pomiar",
        labels=frozenset(),
        aliases=("przy biurku",),
        state="19.4",
    )
    hidden = replace(
        original,
        id="sensor.test_hidden",
        name="Ukryty pomiar",
        exposed=False,
        aliases=("ukryty",),
        state="31",
    )
    home = _model_with(home, first, second, hidden)
    engine = Engine(lambda: home, executor)
    request = Request(
        "jaka jest temperatura",
        source_area="living_room",
        conversation_id="e3_temperature",
        satellite_id="assist_satellite.living_room",
    )
    initial = await engine.process(request)
    assert initial.status == "clarify" and initial.reason == "ambiguous_target"
    assert initial.targets == ()
    answer = await engine.process(replace(request, text="przy biurku"))
    assert answer.status == "answered"
    assert answer.targets == ("sensor.test_second",)
    assert answer.speech == "Jest około 19 stopni."
    assert answer.calls == ()
    assert executor.calls == executor.checked == []


@pytest.mark.parametrize(
    "exposed,available,reason",
    (
        (False, True, "no_targets"),
        (True, False, "ineligible_target"),
    ),
)
async def test_temperature_never_substitutes_hidden_or_unavailable_sensor(
    home,
    executor,
    exposed,
    available,
    reason,
):
    target = home.entities["sensor.living_room_temperature"]
    home = _model_with(home, replace(target, exposed=exposed, available=available))
    engine = Engine(lambda: home, executor)
    result = await engine.process(Request("jaka jest temperatura", source_area="living_room"))
    assert result.status == "clarify" and result.reason == reason
    assert result.calls == ()
    assert executor.calls == executor.checked == []
