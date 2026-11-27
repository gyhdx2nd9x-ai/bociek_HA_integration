from dataclasses import replace
from datetime import time
from types import SimpleNamespace as NS

import pytest

from custom_components.polish_context_voice.core.communication import quiet_hours, response_speech
from custom_components.polish_context_voice.core.types import Call, Request, Result, Risk


@pytest.mark.parametrize(
    "clock,expected",
    [
        ("19:59", False),
        ("20:00", True),
        ("23:59", True),
        ("00:00", True),
        ("07:59", True),
        ("08:00", False),
        ("12:00", False),
    ],
)
def test_midnight_schedule(clock, expected):
    assert quiet_hours(time.fromisoformat(clock)) is expected


def test_day_window_equal_hours_and_invalid_settings():
    assert quiet_hours(time(12), "10:00", "14:00")
    assert not quiet_hours(time(14), "10:00", "14:00")
    assert not quiet_hours(time(12), "10:00", "10:00")
    assert not quiet_hours(time(12), "invalid", "08:00")
    assert not quiet_hours(time(12), "20:00+02:00", "08:00")


def sample():
    entity = NS(domain="light", area_id="living_room", available=True, exposed=True, labels=set())
    model = NS(
        satellites={"assist_satellite.echo": "living_room"},
        devices={"echo": "living_room"},
        entities={"light.main": entity},
    )
    request = Request("zapal światła", satellite_id="assist_satellite.echo", device_id="echo")
    result = Result(
        "executed",
        "Gotowe.",
        targets=("light.main",),
        completed=("light.main",),
        calls=(Call("light", "turn_on", ("light.main",)),),
    )
    return result, request, model


def speech(result, request, model, **settings):
    return response_speech(
        result, request, model, {"quiet_ack_enabled": True, **settings}, time(22)
    )


def test_silence_changes_only_presentation_not_execution_result():
    result, request, model = sample()
    assert speech(result, request, model) == ""
    assert result.speech == "Gotowe." and result.completed == ("light.main",)
    assert speech(result, request, model, quiet_ack_enabled=False) == "Gotowe."
    assert (
        response_speech(result, request, model, {"quiet_ack_enabled": True}, time(12)) == "Gotowe."
    )
    assert (
        response_speech(result, request, model, {"quiet_ack_enabled": True}, time(12), True) == ""
    )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "error"},
        {"status": "partial"},
        {"status": "confirm"},
        {"status": "answered"},
        {"status": "dry_run"},
        {"status": "blocked"},
        {"status": "clarify"},
        {"status": "cancelled"},
        {"completed": ()},
        {"completed": ("light.other",)},
        {"route": "llm"},
        {"route": "native_timer"},
        {"risk": Risk.MEDIUM},
        {"calls": ()},
        {"calls": (Call("switch", "turn_on", ("switch.x",)),)},
    ],
)
def test_results_that_must_remain_audible(change):
    result, request, model = sample()
    assert speech(replace(result, **change), request, model) == "Gotowe."


@pytest.mark.parametrize(
    "change",
    [
        {"satellite_id": None},
        {"satellite_id": "assist_satellite.unknown"},
        {"device_id": "other"},
        {"dry_run": True},
    ],
)
def test_mobile_missing_and_conflicting_source_keep_ack(change):
    result, request, model = sample()
    assert speech(result, replace(request, **change), model) == "Gotowe."


@pytest.mark.parametrize(
    "change",
    [
        {"area_id": "kitchen"},
        {"available": False},
        {"exposed": False},
        {"labels": {"voice_confirm"}},
        {"labels": {"voice_block"}},
        {"labels": {"voice_high_risk"}},
    ],
)
def test_cross_room_or_changed_entity_must_not_be_silent(change):
    result, request, model = sample()
    for key, value in change.items():
        setattr(model.entities["light.main"], key, value)
    assert speech(result, request, model) == "Gotowe."


def test_area_opt_in_and_missing_target():
    result, request, model = sample()
    assert speech(result, request, model, quiet_areas=["kitchen"]) == "Gotowe."
    assert speech(result, request, model, quiet_areas=["living_room"]) == ""
    del model.entities["light.main"]
    assert speech(result, request, model) == "Gotowe."
