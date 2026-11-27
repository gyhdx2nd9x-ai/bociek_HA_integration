"""Presentation policy only: never changes target resolution or execution."""

from datetime import time

from .types import Risk

UNRECOGNIZED_COMMAND_SPEECH = "Nie rozumiem."
ACTION_FAILED_SPEECH = "Nie mogę ci z tym pomóc."


def quiet_hours(now: time, start: str = "20:00", end: str = "08:00") -> bool:
    """Local wall-clock window, start inclusive, end exclusive; equal disables it."""
    try:
        begin, finish = time.fromisoformat(start), time.fromisoformat(end)
    except (ValueError, TypeError):
        return False
    if begin.tzinfo or finish.tzinfo or begin == finish:
        return False
    current = now.replace(tzinfo=None)
    if begin < finish:
        return begin <= current < finish
    return current >= begin or current < finish


def response_speech(result, request, model, settings, local_time, quiet_signal=False):
    """Silence only complete, low-risk light actions visible in the satellite room."""
    if not settings.get("quiet_ack_enabled", False):
        return result.speech
    if not quiet_signal and not quiet_hours(
        local_time, settings.get("quiet_start", "20:00"), settings.get("quiet_end", "08:00")
    ):
        return result.speech
    source = model.satellites.get(request.satellite_id)
    allowed_areas = settings.get("quiet_areas", [])
    if not source or (allowed_areas and source not in allowed_areas):
        return result.speech
    if request.device_id and model.devices.get(request.device_id) != source:
        return result.speech
    if (
        request.dry_run
        or result.status != "executed"
        or result.route != "fast"
        or result.risk != Risk.LOW
        or (result.response_data or {}).get("light_state", {}).get("unknown")
        or not result.targets
        or set(result.targets) != set(result.completed)
        or not result.calls
        or any(
            c.domain != "light" or c.service not in {"turn_on", "turn_off"} for c in result.calls
        )
    ):
        return result.speech
    for identifier in result.targets:
        entity = model.entities.get(identifier)
        if (
            entity is None
            or entity.domain != "light"
            or entity.area_id != source
            or not entity.available
            or not entity.exposed
            or entity.labels & {"voice_confirm", "voice_high_risk", "voice_block"}
        ):
            return result.speech
    return ""
