"""Natural variants remain factual, bounded and local to the listener's source."""

from dataclasses import replace
from datetime import time

import pytest

from custom_components.polish_context_voice.core.capability_catalog import CapabilityFacts
from custom_components.polish_context_voice.core.capability_contracts import CapabilityReport
from custom_components.polish_context_voice.core.communication import response_speech
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.presentation import ACKNOWLEDGEMENTS, Presentation
from custom_components.polish_context_voice.core.response_facts import capability_speech
from custom_components.polish_context_voice.core.types import Call, Request, Result


def request(**changes):
    return Request(
        "synthetic request",
        **(
            {
                "satellite_id": "assist_satellite.living_room",
                "user_id": "user-a",
                "conversation_id": "wake-1",
            }
            | changes
        ),
    )


def fact_result(kind="brightness", value=35):
    brightness = kind == "brightness"
    identifier = "light.set_brightness" if brightness else "sensor.read_temperature"
    result = Result(
        "executed" if brightness else "answered",
        "",
        targets=("light.living_room_main",) if brightness else ("sensor.living_room_temperature",),
        completed=("light.living_room_main",) if brightness else (),
        calls=(Call("light", "turn_on", ("light.living_room_main",), {"brightness_pct": value}),)
        if brightness
        else (),
    )
    result._capability_report = CapabilityReport(
        identifier,
        "command" if brightness else "query",
        result.status,
        "executor_returned" if brightness else "ha_state_cache",
        CapabilityFacts(
            identifier,
            "command" if brightness else "query",
            "requested" if brightness else "observed",
            "validated_parameter" if brightness else "ha_state_cache",
            value,
            "%" if brightness else "°C",
        ),
        1 if brightness else 0,
    )
    result.speech = capability_speech(result)
    return result


def say(presenter, result, req=None, speech=None):
    return presenter.acknowledgement(
        result.speech if speech is None else speech, result, req or request()
    )


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (
            "brightness",
            35,
            [
                "OK, jasność na 35 procent.",
                "Jasność na 35 procent.",
                "Dobrze, jasność na 35 procent.",
            ],
        ),
        (
            "temperature",
            21.5,
            ["Jest około 22 stopni.", "Temperatura to około 22 stopni.", "Około 22 stopni."],
        ),
    ],
)
def test_consecutive_wake_sessions_rotate_short_factual_replies(kind, value, expected):
    presenter = Presentation()
    result = fact_result(kind, value)
    replies = [say(presenter, result, request(conversation_id=f"wake-{i}")) for i in range(7)]
    assert replies[:3] == expected
    assert replies[3:6] == expected
    assert all(left != right for left, right in zip(replies, replies[1:], strict=False))


def test_value_changes_do_not_reset_the_source_capability_sequence():
    presenter = Presentation()
    assert say(presenter, fact_result(value=35)) == "OK, jasność na 35 procent."
    assert say(presenter, fact_result(value=45)) == "Jasność na 45 procent."
    assert say(presenter, fact_result(value=12.5)) == "Dobrze, jasność na 12,5 procenta."


@pytest.mark.parametrize(
    "other_request",
    [
        request(satellite_id="assist_satellite.kitchen"),
        request(user_id="user-b"),
        request(satellite_id=None, device_id="device-a"),
        request(satellite_id=None, device_id=None, conversation_id="other-text-chat"),
    ],
)
def test_other_source_or_user_cannot_advance_this_sources_factual_response(other_request):
    presenter = Presentation()
    result = fact_result()
    assert say(presenter, result) == "OK, jasność na 35 procent."
    assert say(presenter, result, other_request) == "OK, jasność na 35 procent."
    assert say(presenter, result) == "Jasność na 35 procent."


def test_brightness_temperature_and_generic_acknowledgements_have_independent_sequences():
    presenter = Presentation()
    brightness, temperature = fact_result(), fact_result("temperature", 21.5)
    generic = Result("executed", "Gotowe.", targets=("light.a",), completed=("light.a",))
    assert say(presenter, brightness) == "OK, jasność na 35 procent."
    assert say(presenter, temperature) == "Jest około 22 stopni."
    assert say(presenter, generic) == ACKNOWLEDGEMENTS[0]
    assert say(presenter, brightness) == "Jasność na 35 procent."
    assert say(presenter, temperature) == "Temperatura to około 22 stopni."
    assert say(presenter, generic) == ACKNOWLEDGEMENTS[1]


def test_quiet_action_is_silent_and_does_not_consume_a_factual_variant(home):
    presenter = Presentation()
    result = fact_result()
    req = request()
    quiet = response_speech(result, req, home, {"quiet_ack_enabled": True}, time(23, 0))
    assert quiet == "" and say(presenter, result, req, speech=quiet) == ""
    assert not presenter.fact_replies
    assert say(presenter, result, req) == "OK, jasność na 35 procent."


@pytest.mark.parametrize("kind", ["brightness", "temperature"])
def test_preview_does_not_consume_a_variant_even_if_an_answer_would_be_audible(kind):
    presenter = Presentation()
    result = fact_result(kind, 35 if kind == "brightness" else 21.5)
    assert say(presenter, result, request(dry_run=True)) == result.speech
    assert not presenter.fact_replies
    assert say(presenter, result) == result.speech


@pytest.mark.parametrize(
    "speech", ["", "Odliczam dwie minuty.", "Czy potwierdzasz?", "Niestandardowa odpowiedź."]
)
def test_presentation_never_replaces_meaningful_or_suppressed_speech(speech):
    presenter = Presentation()
    assert say(presenter, fact_result(), speech=speech) == speech
    assert not presenter.fact_replies


@pytest.mark.parametrize("status", ["partial", "error", "confirm", "dry_run", "clarify", "blocked"])
def test_incomplete_action_cannot_consume_factual_variation(status):
    presenter = Presentation()
    result = fact_result()
    result.status = status
    assert say(presenter, result) == "OK, jasność na 35 procent."
    assert not presenter.fact_replies


def test_public_fact_json_cannot_enable_the_variation_path():
    presenter = Presentation()
    actual = fact_result()
    forged = replace(actual, response_data={"capability": actual._capability_report.to_dict()})
    assert not hasattr(forged, "_capability_report")
    assert say(presenter, forged) == "OK, jasność na 35 procent."
    assert not presenter.fact_replies


def test_source_without_any_identity_gets_a_factual_answer_without_stored_history():
    presenter = Presentation()
    result = fact_result()
    assert say(presenter, result, request(satellite_id=None, conversation_id=None)) == result.speech
    assert not presenter.fact_replies


def test_factual_reply_memory_is_bounded_and_expires_without_an_execution():
    now = [0.0]
    presenter = Presentation(clock=lambda: now[0])
    result = fact_result()
    for index in range(600):
        say(presenter, result, request(satellite_id=f"satellite-{index}"))
    assert len(presenter.fact_replies) == 512
    now[0] = 601
    assert say(presenter, result, speech="") == ""
    assert not presenter.fact_replies
    assert say(presenter, result) == result.speech


async def test_engine_response_then_quiet_presentation_never_calls_the_executor_twice(
    home, executor
):
    engine = Engine(lambda: home, executor)
    req = replace(request(), text="ustaw światła na 35%")
    result = await engine.process(req)
    presenter = Presentation()
    assert result.status == "executed" and len(executor.calls) == 1
    quiet = response_speech(result, req, home, {"quiet_ack_enabled": True}, time(23, 0))
    assert say(presenter, result, req, speech=quiet) == ""
    assert len(executor.calls) == 1 and not presenter.fact_replies
    day = response_speech(result, req, home, {"quiet_ack_enabled": True}, time(12, 0))
    assert say(presenter, result, req, speech=day) == "OK, jasność na 35 procent."
    assert len(executor.calls) == 1
