"""Native HA reads and exact service calls, with bounded, single-use confirmation."""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic, perf_counter

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.util import dt as dt_util

from ..core.capabilities import READ_OPERATIONS, plan_capability, selected_entities
from ..core.contacts import contact_speech
from ..core.context.dialogue import classify_reply
from ..core.engine import fingerprint
from ..core.maintenance import maintenance_report
from ..core.speech import temperature_speech
from ..core.state_queries import light_state_speech
from ..core.types import Call, Result
from ..core.weather import (
    DRY_CONDITIONS,
    current_weather,
    daily_forecast,
    precipitation_comment,
    probability,
    rest_of_day,
    unavailable_weather_speech,
)
from .intents import HAExecutor

LOGGER = logging.getLogger(__name__)
WEATHER_FORECAST_TIMEOUT = 2.0


@dataclass
class PendingCapability:
    capability: object
    fingerprint: tuple
    expires_at: float


def read_calls(cap, plan, model):
    if cap.operation == "weather":
        if cap.kind == "current":
            return ()
        if cap.kind == "rain_end" and model.entities[plan.targets[0]].state in DRY_CONDITIONS:
            return ()
        features = model.entities[plan.targets[0]].attributes.get("supported_features", 0)
        kind = "hourly" if features & 2 else "daily" if features & 1 else None
        if kind:
            return (Call("weather", "get_forecasts", plan.targets, {"type": kind}),)
    if cap.operation == "todo_read":
        return (Call("todo", "get_items", plan.targets, {"status": ["needs_action"]}),)
    if cap.operation == "forecast":
        return (Call("weather", "get_forecasts", plan.targets, {"type": "daily"}),)
    if cap.operation == "calendar":
        start = dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=cap.day
        )
        return (
            Call(
                "calendar",
                "get_events",
                plan.targets,
                {
                    "start_date_time": start.isoformat(),
                    "end_date_time": (start + timedelta(days=1)).isoformat(),
                },
            ),
        )
    return ()


async def process(runtime, request, context, cap):
    started = perf_counter()
    try:
        result = await _process(runtime, request, context, cap)
    except Unauthorized:
        result = Result(
            "blocked",
            unavailable_weather_speech(cap.operation, cap.kind)
            if cap is not None and cap.domain == "weather"
            else (
                "Home Assistant nie zezwala temu użytkownikowi na tę akcję. "
                "Samo prawo odczytu stanu encji może nie wystarczać."
            ),
            reason="ha_permission_denied",
        )
    except Exception:
        LOGGER.exception("Native capability failed")
        result = Result(
            "error",
            unavailable_weather_speech(cap.operation, cap.kind)
            if cap is not None and cap.domain == "weather"
            else "Nie udało się obsłużyć tej funkcji HA. Sprawdź jej integrację.",
            reason="native_capability_failed",
        )
    result.route = "ha_native"
    result.conversation_id = request.conversation_id
    result.latency_ms = round((perf_counter() - started) * 1000, 3)
    return result


async def _process(runtime, request, context, cap):
    pending = runtime.pending_capabilities
    for key, item in list(pending.items()):
        if item.expires_at <= monotonic():
            del pending[key]
    previous = pending.pop(request.session_key, None)
    confirmed = cap is None and classify_reply(request.text) is True
    if confirmed:
        if previous is None:
            return Result(
                "clarify", "Nie ma aktualnej operacji do potwierdzenia.", reason="no_pending"
            )
        cap = previous.capability
    if cap is None:
        return Result("cancelled", "Anulowano.")
    user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
    model = runtime.visible_model(user)
    plan = plan_capability(cap, request, model)
    if plan.status not in {"ready", "confirm"}:
        return plan
    if confirmed and fingerprint(plan) != previous.fingerprint:
        return Result(
            "clarify", "Zmienił się plan lub uprawnienia. Powtórz polecenie.", reason="plan_changed"
        )
    plan.calls = read_calls(cap, plan, model) if cap.operation in READ_OPERATIONS else plan.calls
    if request.dry_run:
        plan.status = "dry_run"
        plan.speech = "Plan gotowy; nie wywołano żadnej akcji HA."
        return plan
    if plan.status == "confirm" and not confirmed:
        if len(pending) >= 512:
            pending.pop(next(iter(pending)))
        pending[request.session_key] = PendingCapability(cap, fingerprint(plan), monotonic() + 30)
        return plan
    if cap.operation in READ_OPERATIONS:
        await _preflight_read(runtime.hass, plan.targets, request)
        if (
            cap.operation == "weather"
            and cap.kind == "rain_end"
            and model.entities[plan.targets[0]].state in DRY_CONDITIONS
        ):
            # Current HA observation answers this question; no forecast call needed.
            plan.calls = ()
            plan.status = "answered"
            plan.speech = "Przecież teraz nie pada."
            plan.response_data = {
                "forecast_status": "not_needed",
                "condition": model.entities[plan.targets[0]].state,
            }
            return plan
        if plan.calls:
            call = plan.calls[0]
            response = None
            optional_forecast = cap.operation == "weather"
            try:
                async with asyncio.timeout(WEATHER_FORECAST_TIMEOUT if optional_forecast else 15):
                    response = await runtime.hass.services.async_call(
                        call.domain,
                        call.service,
                        {**call.data, "entity_id": list(call.entity_ids)},
                        blocking=True,
                        return_response=True,
                        context=context,
                    )
            except (HomeAssistantError, TimeoutError):
                if not optional_forecast:
                    raise
                # Current weather remains useful when optional forecast access fails.
                LOGGER.debug("Optional weather forecast unavailable", exc_info=True)
            # Exposure/permissions may have changed while a provider was responding.
            await _preflight_read(runtime.hass, plan.targets, request)
            current_user = (
                await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
            )
            current = plan_capability(cap, request, runtime.visible_model(current_user))
            if current.status != "ready" or current.targets != plan.targets:
                return Result(
                    "clarify", "Zmieniła się konfiguracja. Powtórz pytanie.", reason="plan_changed"
                )
            entity = runtime.visible_model(current_user).entities[plan.targets[0]]
            if optional_forecast:
                plan.speech = current_weather(entity.state, entity.attributes)
                data = response.get(entity.id) if isinstance(response, dict) else None
                forecasts = data.get("forecast") if isinstance(data, dict) else None
                available = isinstance(forecasts, list) and bool(forecasts)
                comment = (
                    precipitation_comment(
                        forecasts,
                        call.data["type"],
                        dt_util.now(),
                        entity.state,
                        focus_end=cap.kind == "rain_end",
                    )
                    if available and (cap.kind == "rain_end" or call.data["type"] != "hourly")
                    else rest_of_day(forecasts, dt_util.now(), entity.state)
                    if available
                    else ""
                )
                if comment:
                    plan.speech += " " + comment
                elif not available:
                    plan.speech += " Prognoza opadów jest teraz niedostępna."
                plan.response_data = {
                    "forecast_status": "available" if available else "unavailable",
                    "forecast_type": call.data["type"],
                }
            else:
                plan.speech, plan.response_data = describe_response(
                    cap, response, entity.id, entity.attributes
                )
        else:
            if cap.operation == "maintenance":
                plan.speech, plan.response_data = maintenance_report(
                    cap.kind,
                    selected_entities(plan, model),
                    threshold=runtime.maintenance_battery_threshold,
                    area_ids=plan.area_ids,
                    area_names={identifier: area.name for identifier, area in model.areas.items()},
                )
            else:
                plan.speech = describe_states(cap, selected_entities(plan, model))
            if cap.operation == "weather":
                plan.response_data = {
                    "forecast_status": "not_requested" if cap.kind == "current" else "unsupported"
                }
                if cap.kind == "rain_end":
                    plan.speech += " Nie mam prognozy godzinowej, żeby określić koniec opadów."
        plan.status = "answered"
        return plan
    executor = HAExecutor(runtime.hass, context)
    await executor.preflight(plan.calls, request)
    current = plan_capability(cap, request, runtime.visible_model(user))
    if fingerprint(current) != fingerprint(plan):
        return Result("clarify", "Zmienił się plan. Powtórz polecenie.", reason="plan_changed")
    call = plan.calls[0]
    if cap.operation == "ask":
        async with asyncio.timeout(45):
            response = await runtime.hass.services.async_call(
                call.domain,
                call.service,
                {**call.data, "entity_id": call.entity_ids[0]},
                blocking=True,
                return_response=True,
                context=context,
            )
        await _preflight_read(runtime.hass, plan.targets, request)
        if not isinstance(response, dict) or not isinstance(response.get("sentence"), str):
            raise HomeAssistantError("Invalid question response")
        sentence = response["sentence"][:300]
        plan.speech = (
            f"Odpowiedź z {model.entities[plan.targets[0]].name}: {sentence or 'brak odpowiedzi'}."
        )
        plan.response_data = {"id": response.get("id"), "sentence": sentence}
        plan.status = "answered"
    else:
        async with asyncio.timeout(20):
            await executor.execute(call, request)
        plan.status = "executed"
        plan.speech = "Uruchomiono rutynę." if cap.operation == "routine" else "Gotowe."
    plan.completed = plan.targets
    plan.reason = ""
    return plan


async def _preflight_read(hass, identifiers, request):
    user = await hass.auth.async_get_user(request.user_id) if request.user_id else None
    if request.user_id and (user is None or not user.is_active):
        raise HomeAssistantError("User unavailable")
    for entity_id in identifiers:
        if not exposed_entities.async_should_expose(hass, "conversation", entity_id):
            raise HomeAssistantError("Entity not exposed")
        if user and not user.permissions.check_entity(entity_id, POLICY_READ):
            raise HomeAssistantError("Read not permitted")


def describe_states(cap, entities):
    now = dt_util.now()
    if cap.operation == "time":
        return f"Jest {now:%H:%M}."
    if cap.operation == "date":
        weekday = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")[
            now.weekday()
        ]
        return f"Dzisiaj {weekday}, {now:%d.%m.%Y}."
    if cap.operation == "weather":
        entity = entities[0]
        return current_weather(entity.state, entity.attributes)
    if cap.operation == "sensor":
        entity = entities[0]
        try:
            if not entity.available or not math.isfinite(float(entity.state)):
                raise ValueError
        except (ValueError, TypeError):
            return "Czujnik nie ma aktualnego odczytu."
        unit = entity.attributes.get("unit_of_measurement", "")
        unit = "procent" if unit == "%" else unit
        value = str(entity.state).replace(".", ",")
        return f"Wilgotność wynosi {value} {unit}."
    if cap.kind in {"window", "door"}:
        return contact_speech(cap.kind, cap.query, entities)
    if cap.kind == "on":
        if cap.domain == "light" and cap.query:
            return light_state_speech(cap.query, entities)
        active = [e.name for e in entities if e.available and e.state == "on"]
        unknown = [e.name for e in entities if not e.available or e.state not in {"on", "off"}]
        label = "Włączone"
        speech = (
            f"{label}: {', '.join(active[:8])}."
            if active
            else "Brak aktywnych wśród sprawdzonych, udostępnionych encji."
        )
        if len(active) > 8:
            speech += f" Oraz {len(active) - 8} innych."
        if unknown:
            speech += f" Brak aktualnego stanu dla {len(unknown)} encji: {', '.join(unknown[:5])}."
        return speech
    entity = entities[0]
    if entity.device_class == "temperature" and entity.available:
        try:
            spoken = temperature_speech(
                float(entity.state), entity.attributes.get("unit_of_measurement", "")
            )
        except (TypeError, ValueError):
            spoken = ""
        return f"{entity.name}: {spoken or 'brak aktualnego odczytu'}."
    state = {
        "on": "włączone",
        "off": "wyłączone",
        "open": "otwarte",
        "closed": "zamknięte",
        "playing": "odtwarza",
        "paused": "wstrzymane",
    }.get(entity.state, entity.state)
    value = state if entity.available else "brak aktualnego stanu"
    return f"{entity.name}: {value} {entity.attributes.get('unit_of_measurement', '')}."


def describe_response(cap, response, entity_id, attributes):
    if not isinstance(response, dict) or not isinstance(response.get(entity_id), dict):
        raise HomeAssistantError("Invalid provider response")
    data = response[entity_id]
    if cap.operation == "todo_read":
        items = data.get("items")
        if not isinstance(items, list):
            raise HomeAssistantError("Missing todo items")
        summaries = [str(item.get("summary", ""))[:120] for item in items if isinstance(item, dict)]
        speech = (
            "Lista jest pusta." if not summaries else "Na liście: " + ", ".join(summaries[:8]) + "."
        )
        if len(summaries) > 8:
            speech += f" Oraz {len(summaries) - 8} kolejnych pozycji."
        return speech, {"items": summaries[:8], "total": len(summaries)}
    if cap.operation == "calendar":
        events = data.get("events")
        if not isinstance(events, list):
            raise HomeAssistantError("Missing calendar events")
        ordered = sorted(
            (e for e in events if isinstance(e, dict)), key=lambda e: str(e.get("start", ""))
        )
        summaries = []
        for event in ordered[:5]:
            start = str(event.get("start", ""))
            parsed = dt_util.parse_datetime(start) if "T" in start else None
            at = dt_util.as_local(parsed).strftime("%H:%M") if parsed else "cały dzień"
            summaries.append(f"{at}: {str(event.get('summary', 'wydarzenie'))[:120]}")
        speech = "Brak wydarzeń w wybranym dniu." if not ordered else "; ".join(summaries) + "."
        if len(ordered) > 5:
            speech += f" Oraz {len(ordered) - 5} kolejnych wydarzeń."
        return speech, {"events": summaries, "total": len(ordered)}
    forecasts = data.get("forecast")
    if not isinstance(forecasts, list):
        raise HomeAssistantError("Missing forecasts")
    date = dt_util.now().date() + timedelta(days=cap.day)
    for forecast in forecasts:
        if not isinstance(forecast, dict):
            continue
        parsed = dt_util.parse_datetime(str(forecast.get("datetime", "")))
        if not isinstance(parsed, datetime) or dt_util.as_local(parsed).date() != date:
            continue
        speech = daily_forecast(forecast, attributes, date, today=dt_util.now().date())
        temperature = forecast.get("temperature")
        if not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
            temperature = None
        chance = probability(forecast)
        return speech, {
            "date": date.isoformat(),
            "condition": forecast.get("condition"),
            "temperature": temperature,
            "temperature_low": forecast.get("templow"),
            "precipitation_probability": chance,
        }
    return "Brak prognozy na wybrany dzień.", {"date": date.isoformat(), "forecast": None}
