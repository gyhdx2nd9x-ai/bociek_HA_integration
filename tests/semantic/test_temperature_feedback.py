"""An unreadable temperature gets useful feedback without a guessed reading."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.polish_context_voice.core.capability_catalog import CapabilityFacts
from custom_components.polish_context_voice.core.capability_contracts import CapabilityReport
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Entity
from custom_components.polish_context_voice.core.presentation import Presentation
from custom_components.polish_context_voice.core.response_facts import capability_speech
from custom_components.polish_context_voice.core.types import Call, Request, Result

UNREADABLE = "Nie mogę teraz sprawdzić temperatury."
INVALID_READING = "Nie mam teraz prawidłowego odczytu temperatury."
ORIGINAL = "Existing detailed question or error."
SENSOR = "sensor.living_room_temperature"


def request(text="jaka jest temperatura", **changes):
    return Request(
        text,
        **({"source_area": "living_room", "conversation_id": "temperature-feedback"} | changes),
    )


def failure(reason="no_targets", *, facts=None):
    result = Result("clarify", ORIGINAL, reason=reason)
    result._capability_report = CapabilityReport(
        "sensor.read_temperature", "query", "clarification_required", "none", facts
    )
    return result


def absent_facts(**changes):
    return replace(
        CapabilityFacts("sensor.read_temperature", "query", "observed", "ha_state_cache"),
        **changes,
    )


def change_sensor(home, **changes):
    sensor = replace(home.entities[SENSOR], **changes)
    return home.with_entities(
        sensor if entity.id == SENSOR else entity for entity in home.entities.values()
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("no_targets", UNREADABLE),
        ("ineligible_target", UNREADABLE),
        ("invalid_state", INVALID_READING),
    ],
)
@pytest.mark.parametrize("has_empty_facts", [False, True])
def test_specific_missing_observation_has_one_neutral_factual_message(
    reason, expected, has_empty_facts
):
    result = failure(reason, facts=absent_facts() if has_empty_facts else None)
    assert capability_speech(result) == expected
    assert result.speech == ORIGINAL and not result.calls and not result.completed


@pytest.mark.parametrize(
    "reason",
    [
        "ambiguous_target",
        "missing_area",
        "unknown_location",
        "ambiguous_location",
        "confidence",
        "plan_changed",
        "capability",
        "user_unavailable",
        "group_cycle",
        "",
    ],
)
def test_other_failures_keep_their_more_specific_existing_question_or_error(reason):
    assert capability_speech(failure(reason)) is None


@pytest.mark.parametrize(
    "status",
    [
        "answered",
        "executed",
        "partial",
        "confirm",
        "error",
        "blocked",
        "dry_run",
        "cancelled",
        "unsupported",
    ],
)
def test_unreadable_feedback_never_rewrites_other_result_statuses(status):
    result = failure()
    result.status = status
    assert capability_speech(result) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "light.set_brightness"},
        {"operation_kind": "command"},
        {"stage": "answered"},
        {"stage": "failed"},
        {"evidence": "ha_state_cache"},
        {"evidence": "validated_plan"},
        {"schema_version": 2},
        {"completed_target_count": 1},
    ],
)
def test_private_query_report_must_match_the_current_incomplete_result(changes):
    result = failure()
    result._capability_report = replace(result._capability_report, **changes)
    assert capability_speech(result) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"capability_id": "light.set_brightness"},
        {"operation_kind": "command"},
        {"value_role": "requested"},
        {"source": "validated_parameter"},
        {"value": 21.5},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": False},
        {"value": "unknown"},
    ],
)
def test_feedback_rejects_an_actual_or_malformed_value_in_the_absent_fact(changes):
    assert capability_speech(failure("invalid_state", facts=absent_facts(**changes))) is None


@pytest.mark.parametrize("facts", [{}, SimpleNamespace(value=None), "missing reading"])
def test_dict_and_other_non_typed_facts_cannot_enter_the_feedback_path(facts):
    assert capability_speech(failure(facts=facts)) is None


@pytest.mark.parametrize("field", ["calls", "completed", "continue_conversation"])
def test_existing_calls_completions_or_follow_up_questions_are_never_suppressed(field):
    result = failure()
    setattr(
        result,
        field,
        {
            "calls": (Call("light", "turn_off", ("light.a",)),),
            "completed": ("sensor.a",),
            "continue_conversation": True,
        }[field],
    )
    assert capability_speech(result) is None


def test_public_json_cannot_forge_an_unreadable_temperature_report():
    result = Result("clarify", ORIGINAL, reason="invalid_state")
    result.response_data = {"capability": failure()._capability_report.to_dict()}
    assert capability_speech(result) is None
    result._capability_report = SimpleNamespace(**result.response_data["capability"])
    assert capability_speech(result) is None


def test_public_json_cannot_turn_a_typed_failure_into_an_observed_answer():
    result = failure("invalid_state", facts=absent_facts())
    result.response_data = {"capability": result._capability_report.to_dict()}
    result.response_data["capability"]["facts"]["value"] = 999
    result.response_data["capability"]["evidence"] = "ha_state_cache"
    assert capability_speech(result) == INVALID_READING
    assert result._capability_report.facts.value is None


@pytest.mark.parametrize(
    "mode", ["missing", "hidden", "unavailable", "blocked", "wrong_device_class"]
)
async def test_engine_does_not_disclose_missing_or_ineligible_sensor_details(home, executor, mode):
    if mode == "missing":
        model = home.with_entities(
            entity for entity in home.entities.values() if entity.id != SENSOR
        )
    else:
        changes = {
            "hidden": {"exposed": False},
            "unavailable": {"available": False, "state": "unavailable"},
            "blocked": {"labels": frozenset({"voice_block"})},
            "wrong_device_class": {"device_class": "humidity"},
        }[mode]
        model = change_sensor(
            home, **({"name": "Prywatny czujnik sypialni", "state": "999"} | changes)
        )
    reads = []

    def provider():
        reads.append(True)
        return model

    result = await Engine(provider, executor).process(request())
    assert result.status == "clarify" and result.reason in {"no_targets", "ineligible_target"}
    assert result.speech == UNREADABLE and len(reads) == 1
    assert not result.calls and not result.completed and not executor.calls and not executor.checked
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "Prywatny czujnik" not in serialized and "999" not in serialized
    assert SENSOR not in serialized


@pytest.mark.parametrize("state", [None, "unknown", "NaN", "nan", "inf", "-inf", "n/a", ""])
async def test_invalid_cached_reading_uses_feedback_without_services_or_extra_lookup(
    home, executor, state
):
    model = change_sensor(home, state=state)
    reads = []

    def provider():
        reads.append(True)
        return model

    result = await Engine(provider, executor).process(request())
    assert (result.status, result.reason, result.speech) == (
        "clarify",
        "invalid_state",
        INVALID_READING,
    )
    facts = result.response_data["capability"]["facts"]
    assert facts["value"] is None and facts["source"] == "ha_state_cache"
    assert len(reads) == 1 and not executor.calls and not executor.checked
    assert not result.calls and not result.completed and not result.continue_conversation


async def test_next_valid_reading_recovers_immediately_without_remembering_a_failure(
    home, executor
):
    current = [change_sensor(home, state="NaN")]
    engine = Engine(lambda: current[0], executor)
    bad = await engine.process(request())
    current[0] = change_sensor(home, state="19.6")
    good = await engine.process(request())
    current[0] = change_sensor(home, available=False, state="unavailable")
    unavailable = await engine.process(request())
    assert bad.speech == INVALID_READING
    assert good.status == "answered" and good.speech == "Jest około 20 stopni."
    assert good.response_data["capability"]["facts"]["value"] == 19.6
    assert unavailable.speech == UNREADABLE and not executor.calls
    assert not engine.pending and not engine.dialogue.questions


async def test_explicit_room_wins_even_when_the_satellite_room_has_a_valid_reading(home, executor):
    kitchen = Entity(
        "sensor.kitchen_temperature",
        "Temperatura kuchenna",
        "kitchen",
        exposed=True,
        device_class="temperature",
        state="NaN",
        attributes={"unit_of_measurement": "°C"},
    )
    current = [home.with_entities([*home.entities.values(), kitchen])]
    engine = Engine(lambda: current[0], executor)
    local = await engine.process(request())
    explicit = await engine.process(request("jaka jest temperatura w kuchni"))
    assert local.status == "answered" and local.speech == "Jest około 22 stopni."
    assert explicit.speech == INVALID_READING and explicit.area_ids == ("kitchen",)
    current[0] = home.with_entities([*home.entities.values(), replace(kitchen, state="18.4")])
    recovered = await engine.process(request("jaka jest temperatura w kuchni"))
    assert recovered.status == "answered" and recovered.speech == "Jest około 18 stopni."
    assert recovered.targets == (kitchen.id,) and not executor.calls


async def test_satellite_area_is_used_without_an_explicit_location(home, executor):
    result = await Engine(lambda: home, executor).process(
        request(source_area=None, satellite_id="assist_satellite.kitchen")
    )
    assert result.speech == UNREADABLE and result.area_ids == ("kitchen",)
    assert not result.calls and not result.completed and not executor.calls


async def test_two_readable_sensors_still_ask_the_existing_specific_question(home, executor):
    first = replace(
        home.entities[SENSOR],
        labels=frozenset(),
        name="Czujnik przy drzwiach",
        aliases=("przy drzwiach",),
    )
    second = replace(
        first, id="sensor.second_temperature", name="Czujnik przy oknie", aliases=("przy oknie",)
    )
    model = home.with_entities(
        [first if entity.id == SENSOR else entity for entity in home.entities.values()] + [second]
    )
    result = await Engine(lambda: model, executor).process(request())
    assert result.status == "clarify" and result.reason == "ambiguous_target"
    assert result.continue_conversation and result.speech.startswith(
        "Które urządzenie masz na myśli:"
    )
    assert "przy drzwiach" in result.speech and "przy oknie" in result.speech
    assert capability_speech(result) is None and not executor.calls


async def test_missing_area_keeps_the_room_question(home, executor):
    result = await Engine(lambda: home, executor).process(request(source_area=None))
    assert result.status == "clarify" and result.reason == "missing_area"
    assert result.speech == "W którym pomieszczeniu?" and result.continue_conversation
    assert capability_speech(result) is None and not executor.calls


async def test_unknown_room_and_non_temperature_failure_keep_existing_feedback(home, executor):
    engine = Engine(lambda: home, executor)
    unknown = await engine.process(request("jaka jest temperatura w piwnicy"))
    light = await engine.process(request("włącz światła", source_area="office"))
    assert unknown.reason == "unknown_location" and unknown.speech not in {
        UNREADABLE,
        INVALID_READING,
    }
    assert capability_speech(unknown) is None
    assert light.speech not in {UNREADABLE, INVALID_READING}
    assert capability_speech(light) is None


@pytest.mark.parametrize("dry_run", [False, True])
async def test_feedback_is_read_only_for_real_and_preview_requests(home, executor, dry_run):
    model = change_sensor(home, state="NaN")
    result = await Engine(lambda: model, executor).process(request(dry_run=dry_run))
    assert result.status == "clarify" and result.speech == INVALID_READING
    assert not executor.calls and not executor.checked and not result.calls
    presenter = Presentation()
    assert (
        presenter.acknowledgement(result.speech, result, request(dry_run=dry_run))
        == INVALID_READING
    )
    assert not presenter.acks and not presenter.fact_replies


async def test_unreadable_feedback_does_not_advance_the_next_audible_valid_temperature(
    home, executor
):
    presenter = Presentation()
    req = request(satellite_id="assist_satellite.living_room")
    current = [change_sensor(home, state="NaN")]
    engine = Engine(lambda: current[0], executor)
    for _ in range(3):
        failure_result = await engine.process(req)
        assert (
            presenter.acknowledgement(failure_result.speech, failure_result, req) == INVALID_READING
        )
    assert not presenter.fact_replies
    current[0] = home
    good = await engine.process(req)
    assert presenter.acknowledgement(good.speech, good, req) == "Jest około 22 stopni."
    assert presenter.acknowledgement(good.speech, good, req) == "Temperatura to około 22 stopni."
    assert not executor.calls
