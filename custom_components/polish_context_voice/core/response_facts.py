"""Small Polish responses from typed facts, never from plans or public JSON.

An executor return acknowledges the requested setting, not an observed light
state. Queries describe the cached HA reading or the absence of a usable
reading. Other outcomes keep their existing question, warning or error;
rendering cannot authorize an operation.
"""

from decimal import Decimal

from .capability_catalog import CapabilityFacts
from .capability_contracts import CapabilityReport
from .speech import number, rounded


def _complete_brightness(result, report, facts):
    if (
        result.status != "executed"
        or report.stage != "executed"
        or report.evidence != "executor_returned"
        or report.operation_kind != "command"
        or facts.value_role != "requested"
        or facts.source != "validated_parameter"
        or facts.unit != "%"
        or not 0 <= facts.value <= 100
        or not result.targets
        or len(set(result.targets)) != len(result.targets)
        or len(set(result.completed)) != len(result.completed)
        or set(result.targets) != set(result.completed)
        or report.completed_target_count != len(result.completed)
        or not result.calls
    ):
        return False
    called = []
    for call in result.calls:
        if (
            call.domain != "light"
            or call.service != "turn_on"
            or set(call.data) != {"brightness_pct"}
            or not number(call.data["brightness_pct"])
            or call.data["brightness_pct"] != facts.value
            or not call.entity_ids
            or any(not identifier.startswith("light.") for identifier in call.entity_ids)
        ):
            return False
        called.extend(call.entity_ids)
    return len(called) == len(result.targets) and set(called) == set(result.targets)


def _percentage(value):
    # Preserve the requested decimal; never round an action parameter for speech.
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if len(text) > 32:
        return None
    return ("0" if value == 0 else text).replace(".", ",")


def _temperature(facts):
    whole = rounded(facts.value)
    value = f"minus {abs(whole)}" if whole < 0 else str(whole)
    if facts.unit in {"°C", "°F"}:
        noun = "stopnia" if abs(whole) == 1 else "stopni"
        scale = " Fahrenheita" if facts.unit == "°F" else ""
    elif facts.unit == "K":
        noun = "kelwina" if abs(whole) == 1 else "kelwinów"
        scale = ""
    else:
        return None
    return f"około {value} {noun}{scale}"


def _unavailable_temperature(result, report):
    if (
        report.schema_version != 1
        or report.id != "sensor.read_temperature"
        or report.operation_kind != "query"
        or result.status != "clarify"
        or report.stage != "clarification_required"
        or report.evidence != "none"
        or result.calls
        or result.completed
        or report.completed_target_count != 0
        or result.continue_conversation
    ):
        return None
    facts = report.facts
    if facts is not None and (
        not isinstance(facts, CapabilityFacts)
        or facts.capability_id != report.id
        or facts.operation_kind != "query"
        or facts.value_role != "observed"
        or facts.source != "ha_state_cache"
        or facts.value is not None
    ):
        return None
    return {
        "no_targets": "Nie mogę teraz sprawdzić temperatury.",
        "ineligible_target": "Nie mogę teraz sprawdzić temperatury.",
        "invalid_state": "Nie mam teraz prawidłowego odczytu temperatury.",
    }.get(result.reason)


def capability_speech(result, variant=0):
    """Return a supported factual sentence, or None to preserve existing speech.

    Only our immutable private report is a source of facts. Serialized response
    metadata is intentionally ignored. No home lookup, provider or I/O occurs.
    """
    report = getattr(result, "_capability_report", None)
    if not isinstance(report, CapabilityReport):
        return None
    if speech := _unavailable_temperature(result, report):
        return speech
    if not isinstance(report.facts, CapabilityFacts):
        return None
    facts = report.facts
    if (
        report.schema_version != 1
        or facts.capability_id != report.id
        or facts.operation_kind != report.operation_kind
        or not number(facts.value)
    ):
        return None
    if report.id == "light.set_brightness":
        if not _complete_brightness(result, report, facts):
            return None
        value = _percentage(facts.value)
        if value is None:
            return None
        noun = "procent" if facts.value == int(facts.value) else "procenta"
        return (
            f"OK, jasność na {value} {noun}.",
            f"Jasność na {value} {noun}.",
            f"Dobrze, jasność na {value} {noun}.",
        )[variant % 3]
    if (
        report.id != "sensor.read_temperature"
        or report.operation_kind != "query"
        or result.status != "answered"
        or report.stage != "answered"
        or report.evidence != "ha_state_cache"
        or facts.value_role != "observed"
        or facts.source != "ha_state_cache"
        or len(result.targets) != 1
        or not result.targets[0].startswith("sensor.")
        or result.calls
        or result.completed
        or report.completed_target_count != 0
    ):
        return None
    spoken = _temperature(facts)
    if spoken is None:
        return None
    return (
        f"Jest {spoken}.",
        f"Temperatura to {spoken}.",
        f"{spoken[0].upper() + spoken[1:]}.",
    )[variant % 3]
