"""Allowlisted native Assist timers, never a generic native-agent fallback.

Recognition does not run sentence triggers. Execution uses HA's intent API with
the original device/context. HA owns countdowns and satellite timer events.
"""

import logging
from dataclasses import dataclass, replace
from time import monotonic, perf_counter

from homeassistant.components import conversation
from homeassistant.components.intent import async_device_supports_timers
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent, template

from ..core.nlu.timers import (
    TIMER_NO_MATCH_SPEECH,
    canonical_timer_text,
    spoken_duration,
    timer_prefix_candidates,
    timer_reply,
)
from ..core.types import Result, Risk

_LOGGER = logging.getLogger(__name__)
ALLOWED_INTENTS = frozenset(
    {
        "HassStartTimer",
        "HassCancelTimer",
        "HassCancelAllTimers",
        "HassPauseTimer",
        "HassUnpauseTimer",
        "HassIncreaseTimer",
        "HassDecreaseTimer",
        "HassTimerStatus",
    }
)
ALLOWED_SLOTS = frozenset(
    {
        "hours",
        "minutes",
        "seconds",
        "name",
        "area",
        "start_hours",
        "start_minutes",
        "start_seconds",
    }
)
ERRORS = {
    "timer_not_found": "Nie ma takiego minutnika.",
    "multiple_timers_matched": "Pasuje kilka minutników. Podaj nazwę lub czas minutnika.",
    "no_timer_support": "To urządzenie nie zgłosiło obsługi minutników w Home Assistant.",
}


@dataclass(frozen=True)
class PendingTimer:
    text: str
    native_intent: dict
    expires_at: float


async def process(hass, request, context, *, pending=None, expected_intent=None):
    started = perf_counter()
    result = Result(
        "unsupported",
        TIMER_NO_MATCH_SPEECH,
        route="native_timer",
        reason="timer_no_match",
        conversation_id=request.conversation_id,
    )
    try:
        previous = None
        if pending is not None:
            for key, item in list(pending.items()):
                if item.expires_at <= monotonic():
                    del pending[key]
            previous = pending.pop(request.session_key, None)
        reply = timer_reply(request.text)
        if reply is True:
            result = await _confirm(hass, request, context, previous)
        elif reply is False:
            result = Result(
                "cancelled" if previous else "clarify",
                "Nie uruchamiam minutnika." if previous else "Nie ma minutnika do potwierdzenia.",
                reason="timer_proposal_cancelled" if previous else "no_pending_timer",
                route="native_timer",
                conversation_id=request.conversation_id,
            )
        else:
            await _process(hass, request, context, result, expected_intent)
            if pending is not None and result.reason == "timer_no_match":
                result = await _suggest(hass, request, context, pending, result)
    except Exception:
        # A native API failure must not fall through to a model or claim success.
        _LOGGER.exception("Native Assist timer processing failed")
        result.status = "error"
        result.speech = "Nie udało się obsłużyć minutnika. Sprawdź log integracji."
        result.reason = "timer_handler_error"
    result.latency_ms = round((perf_counter() - started) * 1000, 3)
    return result


async def _confirm(hass, request, context, previous):
    if previous is None:
        return Result(
            "clarify",
            "Nie ma aktualnego minutnika do potwierdzenia. Powtórz polecenie.",
            reason="no_pending_timer",
            route="native_timer",
            conversation_id=request.conversation_id,
        )
    # Single use: the pending record was removed before any await. Re-run parsing
    # and device checks, and compare the exact plan before native execution.
    return await process(
        hass,
        replace(request, text=previous.text),
        context,
        expected_intent=previous.native_intent,
    )


async def _suggest(hass, request, context, pending, original):
    for prefix in timer_prefix_candidates(request.text):
        preview = await process(hass, replace(request, text=prefix, dry_run=True), context)
        plan = preview.native_intent
        if preview.status != "dry_run" or not plan or plan["name"] != "HassStartTimer":
            continue
        if not plan["slots"] or not plan["slots"].keys() <= {"hours", "minutes", "seconds"}:
            continue
        if not request.dry_run:
            if len(pending) >= 512:
                pending.pop(next(iter(pending)))
            pending[request.session_key] = PendingTimer(prefix, plan, monotonic() + 30)
        preview.status = "confirm"
        preview.reason = "additional_speech"
        preview.risk = Risk.MEDIUM
        preview.confidence, preview.confidence_class = 0.8, "medium"
        preview.speech = f"Odliczać {spoken_duration(plan['slots'], words=True)}?"
        return preview
    return original


async def _process(hass, request, context, result, expected_intent=None):
    if not 0 < len(request.text) <= 2000:
        result.reason = "invalid_text_length"
        return
    agent = conversation.async_get_agent(hass)
    if agent is None:
        result.status = "error"
        result.speech = "Natywny agent Assist jest niedostępny."
        result.reason = "native_agent_unavailable"
        return
    device_id = request.device_id
    if request.satellite_id:
        satellite = er.async_get(hass).async_get(request.satellite_id)
        if satellite is None or not satellite.device_id:
            result.status = "blocked"
            result.speech = "Nie ma urządzenia tego satelity w Home Assistant."
            result.reason = "timer_satellite_unknown"
            return
        if device_id and satellite.device_id != device_id:
            result.status = "blocked"
            result.speech = "Identyfikatory satelity i urządzenia są niezgodne."
            result.reason = "timer_device_mismatch"
            return
        device_id = satellite.device_id
    user_input = conversation.ConversationInput(
        text=canonical_timer_text(request.text),
        context=context,
        conversation_id=request.conversation_id,
        device_id=device_id,
        satellite_id=request.satellite_id,
        language="pl",
        agent_id=agent.entity_id,
    )
    recognized = await agent.async_recognize_intent(user_input)
    if recognized is None or recognized.unmatched_entities:
        return
    name = recognized.intent.name
    values = {key: entity.value for key, entity in recognized.entities.items()}
    if name not in ALLOWED_INTENTS or not values.keys() <= ALLOWED_SLOTS:
        # In particular, conversation_command would schedule arbitrary device control.
        result.reason = "timer_intent_not_allowed"
        return
    result.native_intent = {"name": name, "slots": values, "device_id": device_id}
    result.confidence, result.confidence_class = 0.99, "high"
    if expected_intent is not None and expected_intent != result.native_intent:
        result.status = "clarify"
        result.speech = "Zmienił się plan minutnika lub urządzenie. Powtórz polecenie."
        result.reason = "timer_plan_changed"
        return
    if not device_id:
        result.status = "clarify"
        result.speech = "Uruchom minutnik z satelity obsługującego minutniki. Brakuje urządzenia."
        result.reason = "timer_device_required"
        return
    if not async_device_supports_timers(hass, device_id):
        result.status = "blocked"
        result.speech = ERRORS["no_timer_support"]
        result.reason = "no_timer_support"
        return
    if request.dry_run:
        result.status = "dry_run"
        result.speech = "Plan minutnika gotowy; minutnik nie został zmieniony."
        result.reason = ""
        return
    # Load and validate the response before execution. Never execute an intent twice
    # to obtain a response or run the native agent's trigger/LLM processing methods.
    language = await agent.async_get_or_load_intents("pl")
    response_source = language.intent_responses.get(name, {}).get(recognized.response or "default")
    response_template = template.Template(response_source, hass) if response_source else None
    if response_template:
        response_template.ensure_valid()
    try:
        response = await intent.async_handle(
            hass,
            "conversation",
            name,
            {key: {"value": value} for key, value in values.items()},
            request.text,
            context,
            "pl",
            assistant="conversation",
            device_id=device_id,
            satellite_id=request.satellite_id,
        )
    except intent.IntentError as error:
        result.status = "error"
        result.reason = getattr(error, "response_key", None) or "timer_handler_error"
        result.speech = ERRORS.get(result.reason, "Nie udało się obsłużyć minutnika.")
        return
    if response.response_type == intent.IntentResponseType.ERROR:
        result.status = "error"
        result.speech = response.speech.get("plain", {}).get("speech", "Błąd minutnika.")
        result.reason = "timer_handler_error"
        return
    result.status = "answered" if name == "HassTimerStatus" else "executed"
    result.reason = ""
    result.speech = response.speech.get("plain", {}).get("speech", "")
    if name == "HassStartTimer" and (duration := spoken_duration(values, words=True)):
        # Only announce after a successful native start; use the accepted duration,
        # including a confirmed proposal, never reparse the user's free text.
        result.speech = f"Odliczam {duration}."
    if not result.speech and response_template:
        try:
            result.speech = " ".join(
                str(
                    response_template.async_render(
                        {"slots": values | response.speech_slots},
                        parse_result=False,
                    )
                ).split()
            )
        except Exception:
            _LOGGER.exception("Timer succeeded but its response template failed")
    if not result.speech:
        result.speech = "Polecenie minutnika zostało obsłużone."
