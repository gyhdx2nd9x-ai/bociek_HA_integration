"""Factual speech follows typed evidence; it never supplies execution evidence."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.polish_context_voice.core.capability_catalog import CapabilityFacts
from custom_components.polish_context_voice.core.capability_contracts import CapabilityReport
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.response_facts import capability_speech
from custom_components.polish_context_voice.core.types import (
    Action,
    Call,
    Proposal,
    Request,
    Result,
)


def brightness_result(value=35, targets=("light.a",)):
    result = Result(
        "executed",
        "Gotowe.",
        targets=targets,
        calls=tuple(
            Call("light", "turn_on", (target,), {"brightness_pct": value}) for target in targets
        ),
        completed=targets,
    )
    result._capability_report = CapabilityReport(
        "light.set_brightness",
        "command",
        "executed",
        "executor_returned",
        CapabilityFacts(
            "light.set_brightness", "command", "requested", "validated_parameter", value, "%"
        ),
        len(targets),
    )
    return result


def temperature_result(value=21.5, unit="°C"):
    result = Result("answered", "Legacy temperature response.", targets=("sensor.temperature",))
    result._capability_report = CapabilityReport(
        "sensor.read_temperature",
        "query",
        "answered",
        "ha_state_cache",
        CapabilityFacts(
            "sensor.read_temperature", "query", "observed", "ha_state_cache", value, unit
        ),
    )
    return result


def changed_report(result, **changes):
    result._capability_report = replace(result._capability_report, **changes)
    return result


def changed_facts(result, **changes):
    return changed_report(result, facts=replace(result._capability_report.facts, **changes))


def request(text, **changes):
    return Request(text, **({"source_area": "living_room", "conversation_id": "facts"} | changes))


def model_with(home, entity):
    entities = dict(home.entities)
    entities[entity.id] = entity
    return HomeModel(
        home.areas.values(),
        home.floors.values(),
        entities.values(),
        satellites=home.satellites,
        devices=home.devices,
    )


@pytest.mark.parametrize(
    ("value", "spoken", "noun"),
    [
        (0, "0", "procent"),
        (35, "35", "procent"),
        (35.0, "35", "procent"),
        (35.25, "35,25", "procenta"),
        (0.125, "0,125", "procenta"),
        (100, "100", "procent"),
    ],
)
def test_successful_brightness_retains_the_requested_precision(value, spoken, noun):
    result = brightness_result(value)
    assert capability_speech(result) == f"OK, jasność na {spoken} {noun}."
    assert result.calls[0].data == {"brightness_pct": value}
    assert result._capability_report.facts.value_role == "requested"


def test_multiple_completed_light_calls_can_share_one_requested_brightness():
    result = brightness_result(35, ("light.a", "light.b"))
    assert capability_speech(result) == "OK, jasność na 35 procent."


@pytest.mark.parametrize(
    "status",
    [
        "ready",
        "dry_run",
        "confirm",
        "partial",
        "error",
        "blocked",
        "clarify",
        "answered",
        "cancelled",
    ],
)
def test_plans_and_incomplete_results_never_sound_like_a_completed_brightness_action(status):
    result = brightness_result()
    result.status = status
    assert capability_speech(result) is None


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "planned"},
        {"stage": "partial"},
        {"evidence": "validated_plan"},
        {"evidence": "physical_state"},
        {"operation_kind": "query"},
        {"id": "unimplemented.operation"},
        {"completed_target_count": 0},
        {"completed_target_count": 2},
        {"facts": None},
        {"schema_version": 999},
    ],
)
def test_brightness_requires_a_consistent_supported_typed_report(change):
    assert capability_speech(changed_report(brightness_result(), **change)) is None


@pytest.mark.parametrize(
    "change",
    [
        {"capability_id": "sensor.read_temperature"},
        {"operation_kind": "query"},
        {"value_role": "observed"},
        {"source": "ha_state_cache"},
        {"unit": None},
        {"unit": "percent"},
        {"value": None},
        {"value": True},
        {"value": "35"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": -1},
        {"value": 101},
    ],
)
def test_brightness_rejects_unproven_or_non_numeric_facts(change):
    assert capability_speech(changed_facts(brightness_result(), **change)) is None


@pytest.mark.parametrize(
    "calls",
    [
        (),
        (Call("switch", "turn_on", ("light.a",), {"brightness_pct": 35}),),
        (Call("light", "turn_off", ("light.a",), {"brightness_pct": 35}),),
        (Call("light", "turn_on", ("light.b",), {"brightness_pct": 35}),),
        (Call("light", "turn_on", ("light.a",), {"brightness_pct": 34}),),
        (
            Call(
                "light", "turn_on", ("light.a",), {"brightness_pct": 35, "color_temp_kelvin": 2700}
            ),
        ),
        (Call("light", "turn_on", ("light.a", "light.a"), {"brightness_pct": 35}),),
        (Call("light", "turn_on", ("light.a",), {"brightness_pct": 35}),) * 2,
    ],
)
def test_brightness_does_not_summarize_other_calls_or_extra_parameters(calls):
    result = brightness_result()
    result.calls = calls
    assert capability_speech(result) is None


@pytest.mark.parametrize(
    ("targets", "completed"),
    [
        ((), ()),
        (("light.a",), ()),
        (("light.a",), ("light.b",)),
        (("light.a", "light.a"), ("light.a",)),
        (("light.a",), ("light.a", "light.a")),
    ],
)
def test_brightness_requires_exact_non_duplicate_completion(targets, completed):
    result = brightness_result()
    result.targets, result.completed = targets, completed
    assert capability_speech(result) is None


def test_extremely_small_brightness_cannot_expand_into_unbounded_spoken_digits():
    assert capability_speech(brightness_result(1e-300)) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(21.49, "21"), (21.5, "22"), (-1.5, "minus 2"), (-0.5, "minus 1"), (-0.49, "0")],
)
def test_temperature_rounding_uses_halves_away_from_zero_and_spoken_minus(value, expected):
    speech = capability_speech(temperature_result(value))
    assert speech is not None and f"około {expected} stopni" in speech
    assert "Celsjusza" not in speech and "-" not in speech


@pytest.mark.parametrize(
    ("unit", "suffix"), [("°C", "stopni."), ("°F", "stopni Fahrenheita."), ("K", "kelwinów.")]
)
def test_temperature_keeps_non_celsius_scales_explicit(unit, suffix):
    speech = capability_speech(temperature_result(21.5, unit))
    assert speech is not None and speech.endswith(suffix)


@pytest.mark.parametrize("variant", [0, 1, 2, 3, 4, 5])
def test_all_temperature_variants_preserve_fahrenheit_name_and_unconverted_value(variant):
    speech = capability_speech(temperature_result(71.6, "°F"), variant)
    assert speech is not None and "72 stopni Fahrenheita." in speech
    assert "fahrenheita" not in speech


@pytest.mark.parametrize(
    ("value", "noun"), [(1.49, "kelwina"), (1.5, "kelwinów"), (294.5, "kelwinów")]
)
def test_kelvin_rounding_and_declension_do_not_convert_to_celsius(value, noun):
    speech = capability_speech(temperature_result(value, "K"))
    assert speech is not None and speech.endswith(f"{noun}.") and "stopni" not in speech


@pytest.mark.parametrize("unit", [None, "", "unknown", "°Ré", "<script>", "Celsius<script>"])
def test_unknown_units_keep_existing_speech_instead_of_inventing_a_scale(unit):
    assert capability_speech(temperature_result(unit=unit)) is None


@pytest.mark.parametrize("value", [None, True, "21.5", float("nan"), float("inf"), -float("inf")])
def test_invalid_temperature_facts_do_not_produce_a_new_answer(value):
    assert capability_speech(temperature_result(value)) is None


@pytest.mark.parametrize(
    "change",
    [
        {"id": "light.set_brightness"},
        {"operation_kind": "command"},
        {"stage": "executed"},
        {"evidence": "validated_plan"},
        {"completed_target_count": 1},
        {"facts": None},
    ],
)
def test_temperature_requires_one_observation_not_a_plan(change):
    assert capability_speech(changed_report(temperature_result(), **change)) is None


@pytest.mark.parametrize(
    "change",
    [
        {"value_role": "requested"},
        {"source": "validated_parameter"},
        {"capability_id": "light.set_brightness"},
        {"operation_kind": "command"},
    ],
)
def test_temperature_requires_matching_observed_cache_facts(change):
    assert capability_speech(changed_facts(temperature_result(), **change)) is None


@pytest.mark.parametrize("field", ["status", "targets", "calls", "completed"])
def test_temperature_does_not_present_executions_or_ambiguous_target_sets(field):
    result = temperature_result()
    setattr(
        result,
        field,
        {
            "status": "dry_run",
            "targets": ("sensor.a", "sensor.b"),
            "calls": (Call("sensor", "read", ("sensor.temperature",)),),
            "completed": ("sensor.temperature",),
        }[field],
    )
    assert capability_speech(result) is None


def test_public_json_can_neither_create_nor_rewrite_trusted_fact_evidence():
    forged = Result("executed", "Gotowe.", targets=("light.a",), completed=("light.a",))
    forged.response_data = {"capability": brightness_result()._capability_report.to_dict()}
    assert capability_speech(forged) is None
    trusted = brightness_result()
    trusted.response_data = {"capability": trusted._capability_report.to_dict()}
    trusted.response_data["capability"]["facts"]["value"] = 99
    trusted.response_data["capability"]["evidence"] = "physical_state"
    assert capability_speech(trusted) == "OK, jasność na 35 procent."


@pytest.mark.parametrize(
    "invalid", [None, {}, SimpleNamespace(id="light.set_brightness"), "report"]
)
def test_malformed_private_report_falls_back_without_an_exception(invalid):
    result = brightness_result()
    result._capability_report = invalid
    assert capability_speech(result) is None


def test_malformed_typed_facts_fall_back_without_an_exception():
    result = changed_report(brightness_result(), facts={"value": 35, "unit": "%"})
    assert capability_speech(result) is None


async def test_engine_spoken_brightness_is_a_requested_value_not_an_observed_state(home, executor):
    old_light = home.entities["light.living_room_main"]
    model = model_with(
        home, replace(old_light, attributes={**old_light.attributes, "brightness": 1})
    )
    reads = []

    def provider():
        reads.append(True)
        return model

    result = await Engine(provider, executor).process(request("ustaw światła na 35%"))
    assert result.speech == "OK, jasność na 35 procent."
    assert executor.calls[0].data == {"brightness_pct": 35}
    assert result.response_data["capability"]["facts"]["value_role"] == "requested"
    assert len(reads) == 2


async def test_temperature_facts_are_fresh_for_each_engine_request(home, executor):
    current = [home]
    engine = Engine(lambda: current[0], executor)
    first = await engine.process(request("jaka jest temperatura"))
    sensor = home.entities["sensor.living_room_temperature"]
    current[0] = model_with(home, replace(sensor, state="24.6"))
    second = await engine.process(request("jaka jest temperatura"))
    assert first.speech == "Jest około 22 stopni."
    assert second.speech == "Jest około 25 stopni."
    assert not executor.calls


@pytest.mark.parametrize(
    ("unit", "value", "expected"),
    [
        ("celsius", "21.5", "Jest około 22 stopni."),
        ("fahrenheit", "71.6", "Jest około 72 stopni Fahrenheita."),
        ("kelvin", "294.5", "Jest około 295 kelwinów."),
    ],
)
async def test_engine_uses_the_normalized_unit_from_the_fresh_typed_cache_fact(
    home, executor, unit, value, expected
):
    sensor = home.entities["sensor.living_room_temperature"]
    model = model_with(home, replace(sensor, state=value, attributes={"unit_of_measurement": unit}))
    result = await Engine(lambda: model, executor).process(request("jaka jest temperatura"))
    assert result.speech == expected and not executor.calls


async def test_unknown_temperature_scale_preserves_the_legacy_answer(home, executor):
    sensor = home.entities["sensor.living_room_temperature"]
    model = model_with(home, replace(sensor, attributes={"unit_of_measurement": "unknown"}))
    result = await Engine(lambda: model, executor).process(request("jaka jest temperatura"))
    assert result.speech == "Jest około 22 unknown."
    assert capability_speech(result) is None and not executor.calls


async def test_confirmation_only_speaks_brightness_after_executor_returns(home, executor):
    lamp = home.entities["light.living_room_main"]
    model = model_with(home, replace(lamp, labels=lamp.labels | {"voice_confirm"}))
    engine = Engine(lambda: model, executor)
    question = await engine.process(request("ustaw światła na 35%"))
    assert question.status == "confirm" and capability_speech(question) is None
    assert not executor.calls
    done = await engine.process(request("potwierdzam"))
    assert done.status == "executed" and done.speech == "OK, jasność na 35 procent."
    assert len(executor.calls) == 1


async def test_fresh_policy_failure_after_confirmation_never_speaks_a_requested_setting(
    home, executor
):
    lamp = home.entities["light.living_room_main"]
    current = [model_with(home, replace(lamp, labels=lamp.labels | {"voice_confirm"}))]
    engine = Engine(lambda: current[0], executor)
    question = await engine.process(request("ustaw światła na 35%"))
    assert question.status == "confirm"
    current[0] = model_with(current[0], replace(current[0].entities[lamp.id], exposed=False))
    result = await engine.process(request("potwierdzam"))
    assert result.status != "executed" and not executor.calls
    assert capability_speech(result) is None and "jasność na" not in result.speech


@pytest.mark.parametrize("failure", ["preflight", "first_call", "second_call"])
async def test_engine_failure_or_partial_execution_keeps_the_existing_failure_speech(
    home, executor, failure
):
    extra = replace(home.entities["light.living_room_main"], id="light.extra")
    model = model_with(home, extra)
    executor.deny = failure == "preflight"
    executor.fail_at = {"preflight": None, "first_call": 0, "second_call": 1}[failure]
    result = await Engine(lambda: model, executor).process(request("ustaw światła na 35%"))
    assert result.status == ("partial" if failure == "second_call" else "error")
    assert capability_speech(result) is None
    assert "jasność na" not in result.speech


async def test_preview_is_still_a_plan_and_temperature_preview_is_still_read_only(home, executor):
    engine = Engine(lambda: home, executor)
    plan = await engine.process(request("ustaw światła na 35%", dry_run=True))
    answer = await engine.process(request("jaka jest temperatura", dry_run=True))
    assert (
        plan.status == "dry_run" and plan.speech == "Plan gotowy; urządzenia nie zostały zmienione."
    )
    assert capability_speech(plan) is None and not executor.calls
    assert answer.status == "answered" and answer.speech == "Jest około 22 stopni."


async def test_renderer_does_not_expand_parser_acceptance_or_add_service_operations(home, executor):
    engine = Engine(lambda: home, executor)
    result = await engine.process(request("ustaw światła na 35% i zamknij roletę"))
    assert result.status not in {"executed", "partial"}
    assert not executor.calls and capability_speech(result) is None
    planned = engine.plan(Proposal(Action.BRIGHTNESS, "light", value=35.25), request("typed input"))
    assert planned.status == "ready" and capability_speech(planned) is None
