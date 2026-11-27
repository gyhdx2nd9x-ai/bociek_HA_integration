"""Read Daily Stoic from HA, keeping permission-checked conversation snapshots."""

import logging
from time import monotonic, perf_counter

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.util import dt as dt_util

from ..core.stoic import (
    DEFAULT_ENTITY_ID,
    MAX_SNAPSHOTS,
    MAX_SPEECH_CHARS,
    SNAPSHOT_TTL,
    StoicSnapshot,
    entry_from_attributes,
    reading,
)
from ..core.types import Result

LOGGER = logging.getLogger(__name__)


def _result(status, speech, *, reason="", entity_id=None, data=None):
    return Result(
        status,
        speech,
        targets=(entity_id,) if entity_id else (),
        confidence=0.99,
        confidence_class="high",
        route="stoic",
        reason=reason,
        response_data=data,
    )


async def process(runtime, request, context, command, user=None):
    """No service calls and no copied JSON; authorisation is checked on every turn."""
    started = perf_counter()
    try:
        result = await _process(runtime, request, command)
    except Exception:
        LOGGER.exception("Daily Stoic reading failed")
        result = _result("error", "Nie udało się odczytać Daily Stoic.", reason="stoic_read_failed")
    result.conversation_id = request.conversation_id
    result.latency_ms = round((perf_counter() - started) * 1000, 3)
    return result


async def _process(runtime, request, command):
    now = monotonic()
    snapshots = runtime.stoic_snapshots
    for key, snapshot in list(snapshots.items()):
        if snapshot.expires_at <= now:
            del snapshots[key]
    user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
    if request.user_id and (user is None or not user.is_active):
        snapshots.pop(request.session_key, None)
        return _result("blocked", "Brak dostępu do Daily Stoic.", reason="user_unavailable")
    model = runtime.visible_model(user)
    configured = runtime.stoic_settings.get("stoic_entity_id")
    identifier = configured or (DEFAULT_ENTITY_ID if DEFAULT_ENTITY_ID in model.entities else None)
    if not identifier:
        candidates = [
            entity.id
            for entity in model.entities.values()
            if entity.domain == "sensor"
            and entity.platform == "daily_stoic"
            and entity.exposed
            and "voice_block" not in entity.labels
        ]
        if len(candidates) != 1:
            return _result(
                "clarify",
                "Wybierz sensor Daily Stoic w ustawieniach Boćka i udostępnij go Assist.",
                reason="stoic_sensor_ambiguous" if candidates else "stoic_sensor_missing",
            )
        identifier = candidates[0]
    entity = model.entities.get(identifier)
    if (
        entity is None
        or entity.domain != "sensor"
        or not entity.exposed
        or "voice_block" in entity.labels
        or not exposed_entities.async_should_expose(runtime.hass, "conversation", identifier)
        or user is not None
        and not user.permissions.check_entity(identifier, POLICY_READ)
    ):
        snapshots.pop(request.session_key, None)
        return _result(
            "blocked",
            "Brak dostępu do Daily Stoic. Sprawdź wybrany sensor i udostępnienie w Assist.",
            reason="stoic_not_exposed",
        )
    state = runtime.hass.states.get(identifier)
    if not entity.available or state is None or state.state in {"unknown", "unavailable"}:
        snapshots.pop(request.session_key, None)
        return _result(
            "unsupported", "Daily Stoic jest teraz niedostępne.", reason="stoic_unavailable"
        )
    previous = snapshots.get(request.session_key)
    if command.followup and (previous is None or previous.entity_id != identifier):
        return _result(
            "clarify",
            "Poproś najpierw o cytat lub dzisiejszą medytację.",
            reason="stoic_no_reading_context",
        )
    use_snapshot = (
        command.section in {"title", "commentary"}
        and not command.today
        and previous is not None
        and previous.entity_id == identifier
    )
    if not use_snapshot:
        snapshots.pop(request.session_key, None)
    entry = (
        previous.entry
        if use_snapshot
        else entry_from_attributes(state.attributes, dt_util.now().date())
    )
    if not use_snapshot and entry.date_status in {"stale", "invalid"}:
        snapshots.pop(request.session_key, None)
        return _result(
            "unsupported",
            "Daily Stoic nie udostępnia aktualnego wpisu na dziś.",
            reason="stoic_stale" if entry.date_status == "stale" else "stoic_invalid_date",
        )
    speech, reason, missing = reading(entry, command.section)
    data = {
        "section": command.section,
        "snapshot": use_snapshot,
        "date_status": entry.date_status,
        "missing_sections": list(missing),
    }
    if not reason.startswith("stoic_missing_") and reason != "stoic_text_too_long":
        # Bound retained text too, including sections that were not read this turn.
        total = sum(
            len(value) for value in (entry.quote, entry.author, entry.title, entry.commentary)
        )
        if total <= MAX_SPEECH_CHARS:
            if request.session_key not in snapshots and len(snapshots) >= MAX_SNAPSHOTS:
                snapshots.pop(next(iter(snapshots)))
            snapshots[request.session_key] = StoicSnapshot(
                identifier, entry, previous.expires_at if use_snapshot else now + SNAPSHOT_TTL
            )
    return _result(
        "answered" if reason in {"", "stoic_partial"} else "unsupported",
        speech,
        reason=reason,
        entity_id=identifier,
        data=data,
    )
