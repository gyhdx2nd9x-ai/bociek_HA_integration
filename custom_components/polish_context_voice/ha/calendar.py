"""Calendar entity API with fresh permissions, single-use dialogue and Kiosk display.

Only HA calendar entities mutate data. The Kiosk protocol is used exclusively for
the non-mutating `show` operation, always with an explicitly configured target.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, perf_counter
from uuid import uuid4

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.calendar import WEBSOCKET_EVENT_SCHEMA, CalendarEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ..core.calendar import (
    answer_slot,
    event_overview,
    event_when,
    fold,
    kiosk_conflict,
    kiosk_key,
    moment,
    period,
)
from ..core.context.dialogue import classify_reply, ordinal_reply
from ..core.nlu.timers import spoken_duration
from ..core.types import Result, Risk

LOGGER = logging.getLogger(__name__)
WRITES = {"add": 1, "cancel": 2, "change": 4}


@dataclass
class CalendarTurn:
    command: object
    stage: str
    expires_at: float
    data: object = None


def _result(status, speech, **kwargs):
    return Result(
        status, speech, route="ha_calendar", confidence=0.99, confidence_class="high", **kwargs
    )


def _ask(runtime, request, command, stage, speech, data=None):
    turns = runtime.pending_calendars
    if len(turns) >= 512:
        turns.pop(next(iter(turns)))
    turns[request.session_key] = CalendarTurn(command, stage, monotonic() + 30, data)
    return _result("clarify", speech, continue_conversation=True)


async def _entity(runtime, request, identifier, *, write=False):
    user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
    if request.user_id and (user is None or not user.is_active):
        raise PermissionError
    model = runtime.visible_model(user)
    metadata = model.entities.get(identifier)
    if (
        metadata is None
        or not metadata.exposed
        or not metadata.available
        or "voice_block" in metadata.labels
        or (write and "voice_high_risk" in metadata.labels)
    ):
        raise PermissionError
    if user and not user.permissions.check_entity(
        identifier, POLICY_CONTROL if write else POLICY_READ
    ):
        raise PermissionError
    component = runtime.hass.data.get("calendar")
    entity = component.get_entity(identifier) if component is not None else None
    if not isinstance(entity, CalendarEntity) or not entity.available:
        raise HomeAssistantError("Calendar unavailable")
    return entity, metadata


def _choose_calendar(command, model, settings):
    candidates = [
        e
        for e in model.entities.values()
        if e.domain == "calendar" and e.exposed and e.available and "voice_block" not in e.labels
    ]
    if command.calendar:
        matches = model.entity_index.get(model.key(command.calendar), set())
        candidates = [e for e in candidates if e.id in matches]
    elif configured := settings.get("calendar_default"):
        candidates = [e for e in candidates if e.id == configured]
    elif len(candidates) > 1:
        primary = [e for e in candidates if "voice_primary" in e.labels]
        if len(primary) == 1:
            candidates = primary
    return candidates


async def _find(runtime, request, command, now):
    """Read the household calendars; a default is a write destination, not a read filter."""
    user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
    model = runtime.visible_model(user)
    candidates = [
        e
        for e in model.entities.values()
        if e.domain == "calendar" and e.exposed and "voice_block" not in e.labels
    ]
    if command.calendar:
        matches = model.entity_index.get(model.key(command.calendar), set())
        candidates = [e for e in candidates if e.id in matches]
        if len(candidates) > 1:
            return _ask(
                runtime,
                request,
                command,
                "calendar",
                "Który kalendarz: " + ", ".join(e.name for e in candidates[:5]) + "?",
            )
    candidates.sort(key=lambda e: e.id)
    if not candidates:
        return _result(
            "clarify",
            "Nie mam dostępnego kalendarza dla tego polecenia. "
            "Udostępnij kalendarze Assist lub podaj nazwę kalendarza.",
            reason="calendar_no_targets",
        )
    if command.when and period(command.when, now) is None:
        return _ask(
            runtime, request, command, "when", "Podaj dzień, na przykład: jutro albo w piątek."
        )
    if request.dry_run:
        return _result(
            "dry_run",
            "Plan gotowy; nie odczytano ani nie zmieniono wydarzeń.",
            targets=tuple(e.id for e in candidates),
            response_data={
                "operation": "find",
                "title": command.title,
                "when": command.when,
                "needs_lookup": True,
            },
        )

    # Bound concurrent provider calls and the whole overview. A broken provider
    # must not hide successful calendars or leave orphaned background tasks.
    semaphore = asyncio.Semaphore(4)

    async def read(identifier):
        async with semaphore:
            return await _events(runtime, request, identifier, command, now)

    tasks = {asyncio.create_task(read(e.id)): e for e in candidates}
    try:
        await asyncio.wait(tasks, timeout=12)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    records, successful = [], []
    failed = 0
    for metadata, events in zip(tasks.values(), results, strict=True):
        if isinstance(events, BaseException):
            failed += 1
            continue
        try:
            # Recheck access after waiting for the other providers as well.
            _, metadata = await _entity(runtime, request, metadata.id)
        except (PermissionError, HomeAssistantError):
            failed += 1
            continue
        successful.append(metadata.id)
        records.extend((event, metadata) for event in events)
    if not successful:
        return _result(
            "error",
            "Nie mogę teraz odczytać kalendarzy. Sprawdź ich dostępność i uprawnienia w HA.",
            reason="calendar_provider",
        )
    records.sort(
        key=lambda row: (row[0].start_datetime_local.timestamp(), row[0].summary, row[1].id)
    )
    rows = [
        {
            "title": e.summary,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "calendar_id": metadata.id,
            "calendar_name": metadata.name,
        }
        for e, metadata in records[:10]
    ]
    speech = event_overview(
        (
            (e.summary, e.start_datetime_local if isinstance(e.start, datetime) else e.start)
            for e, _ in records[:5]
        ),
        now,
    )
    if records:
        if len(records) > 5:
            speech += f" Oraz {len(records) - 5} kolejnych wydarzeń."
    else:
        speech = (
            "Nie ma pasujących wydarzeń."
            if not failed
            else ("Nie ma wydarzeń w odczytanych kalendarzach.")
        )
    if failed:
        speech += " Nie udało się odczytać wszystkich kalendarzy; wynik jest niepełny."
    return _result(
        "answered",
        speech,
        targets=tuple(successful),
        reason="calendar_partial" if failed else "",
        response_data={
            "events": rows,
            "total": len(records),
            "calendars_read": successful,
            "failed_calendars": failed,
            "partial": bool(failed),
        },
    )


def _key(title):
    words = {
        "dentyste": "dentysta",
        "dentysty": "dentysta",
        "wizyte": "wizyta",
        "wizyty": "wizyta",
        "treningu": "trening",
    }
    return tuple(words.get(word, word) for word in fold(title).split())


def _stamp(event):
    return (
        event.uid,
        event.start.isoformat(),
        event.end.isoformat(),
        event.summary,
        event.description,
        event.location,
        event.rrule,
        event.recurrence_id,
    )


def _date_value(value):
    return value.isoformat()


def _guard(metadata, settings):
    return (
        metadata.id,
        tuple(sorted(metadata.labels)),
        metadata.platform,
        metadata.config_entry_id,
        metadata.unique_id,
        settings.get("calendar_default"),
        settings.get("calendar_default_duration", 60),
    )


async def _events(runtime, request, identifier, command, now):
    entity, _ = await _entity(runtime, request, identifier)
    span = period(command.when, now) if command.when else None
    if command.when and span is None:
        raise ValueError("date")
    start, end = span or (
        now.replace(hour=0, minute=0, second=0, microsecond=0),
        now + timedelta(days=180),
    )
    async with asyncio.timeout(10):
        events = await entity.async_get_events(runtime.hass, start, end)
    if not isinstance(events, list) or len(events) > 2000:
        raise HomeAssistantError("Calendar returned too many events")
    await _entity(runtime, request, identifier)
    query = set(_key(command.title))
    candidates = [e for e in events if not query or query <= set(_key(e.summary))]
    if command.when and (at := moment(command.when, now)) and at[1]:
        candidates = [e for e in candidates if e.start_datetime_local == at[0]]
    return sorted(candidates, key=lambda e: (e.start_datetime_local.timestamp(), e.summary))


async def _show(runtime, request, command, now):
    """No empty Kiosk target: concurrent voice turns must never fan out."""
    mapping = runtime.calendar_settings.get("calendar_kiosks", {})
    source = request.satellite_id
    model = runtime.visible_model(None)
    if not source and request.device_id:
        sources = [
            e.id
            for e in model.entities.values()
            if e.domain == "assist_satellite" and e.device_id == request.device_id
        ]
        source = sources[0] if len(sources) == 1 else None
    name = mapping.get(source, "")
    if not name:
        return _result(
            "clarify",
            "Przypisz tego satelitę do ekranu w ustawieniach Boćka: Kalendarz — ekrany Kiosk.",
            reason="calendar_kiosk_missing",
        )
    if kiosk_conflict(name, [v for k, v in mapping.items() if k != source]):
        return _result(
            "blocked",
            "Nazwy ekranów Kiosk są niejednoznaczne. Zmień przypisania w ustawieniach Boćka.",
            reason="calendar_kiosk_ambiguous",
        )
    target = model.entities.get(source)
    user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
    if (
        target is None
        or not target.exposed
        or not target.available
        or "voice_block" in target.labels
        or (request.user_id and (user is None or not user.is_active))
        or (user and not user.permissions.check_entity(source, POLICY_CONTROL))
    ):
        raise PermissionError
    span = period(command.when, now)
    if span is None:
        return _result(
            "clarify",
            "Podaj dzień albo powiedz: pokaż kalendarz na przyszły tydzień.",
            reason="calendar_date",
        )
    payload = {
        "id": uuid4().hex,
        "action": "show",
        "kiosk": name,
        "start": span[0].date().isoformat(),
        "view": command.view,
    }
    if request.dry_run:
        return _result(
            "dry_run",
            "Plan gotowy; ekran nie został zmieniony.",
            targets=(source,),
            native_intent=payload,
        )
    future = asyncio.get_running_loop().create_future()

    def received(event):
        data = event.data
        if (
            data.get("id") == payload["id"]
            and not future.done()
            and isinstance(data.get("kiosk"), str)
            and kiosk_key(data["kiosk"]) == kiosk_key(name)
        ):
            future.set_result(data)

    unsubscribe = runtime.hass.bus.async_listen("kiosk_satellite_calendar_result", received)
    try:
        runtime.hass.bus.async_fire("kiosk_satellite_calendar", payload)
        async with asyncio.timeout(5):
            answer = await future
        if answer.get("ok") is not True:
            raise HomeAssistantError("Kiosk refused calendar display")
        return _result("executed", "Pokazuję kalendarz.", targets=(source,), completed=(source,))
    except TimeoutError:
        return _result(
            "error",
            "Ekran nie potwierdził otwarcia kalendarza. Sprawdź nazwę "
            "Kiosk i jego połączenie z HA.",
            reason="calendar_kiosk_timeout",
        )
    finally:
        unsubscribe()


async def process(runtime, request, context, command):
    started = perf_counter()
    try:
        async with asyncio.timeout(20):
            result = await _process(runtime, request, context, command)
    except PermissionError:
        result = _result(
            "blocked",
            "Nie mam dostępu do tego kalendarza lub ekranu. "
            "Sprawdź udostępnienie Assist i uprawnienia.",
            reason="calendar_access",
        )
    except (HomeAssistantError, TimeoutError):
        LOGGER.warning("Calendar provider did not complete the request", exc_info=True)
        result = _result(
            "error",
            "Nie mogę teraz obsłużyć kalendarza. Sprawdź jego integrację. "
            "Przed ponowną próbą zapisu sprawdź, czy wydarzenie już powstało.",
            reason="calendar_provider",
        )
    except Exception:
        LOGGER.exception("Calendar request failed")
        result = _result("error", "Nie udało się obsłużyć kalendarza.", reason="calendar_error")
    result.conversation_id = request.conversation_id
    result.latency_ms = round((perf_counter() - started) * 1000, 3)
    return result


async def _process(runtime, request, context, command):
    now = dt_util.now()
    turns = runtime.pending_calendars
    deadline = monotonic()
    current = turns.get(request.session_key)
    expired = current is not None and current.expires_at <= deadline
    for key, item in list(turns.items()):
        if item.expires_at <= deadline:
            turns.pop(key, None)
    previous = turns.pop(request.session_key, None)
    text = fold(request.text.strip().rstrip("?.!"))
    picked = None
    if command is None:
        reply = classify_reply(request.text)
        if reply is False:
            speech = (
                {
                    "add": "Wydarzenie nie zostało dodane.",
                    "cancel": "Wydarzenie zostaje w kalendarzu.",
                    "change": "Termin wydarzenia pozostaje bez zmian.",
                }.get(previous.command.operation, "Kalendarz pozostaje bez zmian.")
                if previous is not None
                else "Nie ma oczekującego polecenia kalendarza. Kalendarz pozostaje bez zmian."
            )
            return _result("cancelled", speech)
        if previous is None:
            return _result(
                "clarify",
                (
                    "Minął czas na potwierdzenie. Kalendarz pozostaje bez zmian. "
                    "Powtórz pełne polecenie."
                    if expired
                    else "Nie ma aktualnej operacji kalendarza do potwierdzenia."
                ),
                reason="calendar_no_pending",
            )
        if previous.stage == "confirm":
            if reply is not True:
                # Do not turn a foreign-script STT error into write authorization.
                # Keep the original deadline, so repeated noise cannot extend it.
                if not runtime.calendar_settings.get("calendar_write_enabled", False):
                    raise PermissionError
                _, metadata = await _entity(
                    runtime, request, previous.data["entity_id"], write=True
                )
                if _guard(metadata, runtime.calendar_settings) != previous.data["guard"]:
                    return _result(
                        "clarify",
                        "Zmieniła się konfiguracja kalendarza. Powtórz polecenie.",
                        reason="calendar_changed",
                    )
                turns[request.session_key] = previous
                return _result(
                    "clarify",
                    "Potwierdzenie jest niejednoznaczne. " + previous.data["description"],
                    reason="calendar_confirmation_unclear",
                    continue_conversation=True,
                    risk=Risk.MEDIUM,
                    targets=(previous.data["entity_id"],),
                )
            return await _commit(runtime, request, context, previous.command, previous.data)
        if previous.stage == "event":
            options = previous.data
            ordinal, attempted = ordinal_reply(request.text, suffixes=("wydarzenie", "spotkanie"))
            matches = [e for e in options if fold(e.summary) == text]
            if ordinal is not None and ordinal < len(options):
                picked = options[ordinal]
            elif not attempted and len(matches) == 1:
                picked = matches[0]
            else:
                turns[request.session_key] = previous
                return _result(
                    "clarify", "Wybierz jedno z podanych wydarzeń albo podaj dokładny tytuł.",
                    continue_conversation=True,
                )
            command = previous.command
        else:
            command = answer_slot(previous.command, previous.stage, request.text)
            if command is None:
                return _ask(
                    runtime,
                    request,
                    previous.command,
                    previous.stage,
                    "Nie udało się doprecyzować. Podaj godzinę, datę lub nazwę kalendarza.",
                )
    if command.operation == "help":
        if command.error == "overview_syntax":
            return _result(
                "clarify",
                "Podaj jeden termin, na przykład: jaki jest plan na jutro "
                "albo co mamy w przyszłym tygodniu w kalendarzu?",
                reason="calendar_syntax",
            )
        return _result(
            "clarify",
            "Powiedz na przykład: dodaj dentystę na jutro o 15 "
            "na godzinę, albo: co mamy jutro w kalendarzu?",
            reason="calendar_syntax",
        )
    if command.operation == "show":
        return await _show(runtime, request, command, now)
    if command.operation == "find":
        return await _find(runtime, request, command, now)
    user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
    candidates = _choose_calendar(command, runtime.visible_model(user), runtime.calendar_settings)
    if not candidates:
        return _result(
            "clarify",
            "Nie mam dostępnego kalendarza dla tego polecenia. "
            "Wybierz go w ustawieniach Boćka i udostępnij Assist.",
            reason="calendar_no_targets",
        )
    if len(candidates) != 1:
        names = ", ".join(e.name for e in candidates[:5])
        return _ask(runtime, request, command, "calendar", f"Który kalendarz: {names}?")
    identifier = candidates[0].id
    write = command.operation in WRITES
    if write and not runtime.calendar_settings.get("calendar_write_enabled", False):
        return _result(
            "blocked",
            "Zapisywanie wydarzeń jest wyłączone. Włącz je w ustawieniach "
            "Boćka: Kalendarz — wydarzenia.",
            reason="calendar_write_disabled",
        )
    entity, metadata = await _entity(runtime, request, identifier, write=write)
    if write and not entity.supported_features & WRITES[command.operation]:
        action = {"add": "dodawania", "change": "przenoszenia", "cancel": "usuwania"}[
            command.operation
        ]
        return _result(
            "blocked",
            f"Integracja HA tego kalendarza nie obsługuje {action} wydarzeń. "
            "Wykonaj tę zmianę w aplikacji kalendarza lub wybierz inny kalendarz.",
            reason="calendar_operation_unsupported",
        )
    if command.operation == "add":
        parsed = moment(command.when, now)
        if parsed is None:
            return _ask(runtime, request, command, "when", "Na jaki dzień zapisać wydarzenie?")
        at, timed = parsed
        if not timed and not command.all_day:
            return _ask(
                runtime,
                request,
                command,
                "time",
                "O której godzinie? Dla wydarzenia całodniowego powiedz: cały dzień.",
            )
        if (timed and at <= now) or at.date() < now.date():
            return _ask(
                runtime,
                request,
                command,
                "when",
                "Ten termin już minął. Podaj nowy dzień i godzinę.",
            )
        duration = command.duration or int(
            runtime.calendar_settings.get("calendar_default_duration", 60)
        )
        if not command.title or len(command.title) > 200 or not 1 <= duration <= 1440:
            return _result(
                "clarify",
                "Podaj krótki tytuł i czas trwania do jednej doby.",
                reason="calendar_range",
            )
        start = at.date() if command.all_day else at
        end = (
            start + timedelta(days=1)
            if command.all_day
            else (at.astimezone(UTC) + timedelta(minutes=duration)).astimezone(at.tzinfo)
        )
        data = {"summary": command.title, "dtstart": _date_value(start), "dtend": _date_value(end)}
        description = f"Dodać „{command.title}” {event_when(start, now)}"
        if timed:
            description += ", na " + spoken_duration(
                {"hours": duration // 60, "minutes": duration % 60}, words=True
            )
        data = {"entity_id": identifier, "event": data, "description": description + "?"}
    else:
        if command.when and period(command.when, now) is None:
            return _ask(
                runtime, request, command, "when", "Podaj dzień, na przykład: jutro albo w piątek."
            )
        if request.dry_run:
            return _result(
                "dry_run",
                "Plan gotowy; nie odczytano ani nie zmieniono wydarzeń.",
                targets=(identifier,),
                risk=Risk.MEDIUM if write else Risk.LOW,
                response_data={
                    "operation": command.operation,
                    "title": command.title,
                    "when": command.when,
                    "needs_lookup": True,
                },
            )
        events = await _events(runtime, request, identifier, command, now)
        if not events:
            return _result("clarify", "Nie ma takiego wydarzenia.", reason="calendar_not_found")
        if picked is not None:
            events = [e for e in events if _stamp(e) == _stamp(picked)]
            if not events:
                return _result(
                    "clarify",
                    "Wydarzenie się zmieniło. Powtórz polecenie.",
                    reason="calendar_changed",
                )
        if len(events) > 1:
            names = "; ".join(
                f"{i + 1}: {e.summary}, "
                + event_when(
                    e.start_datetime_local if isinstance(e.start, datetime) else e.start, now
                )
                for i, e in enumerate(events[:3])
            )
            if len(events) > 3:
                return _ask(
                    runtime,
                    request,
                    command,
                    "when",
                    "Pasuje wiele wydarzeń. Podaj konkretny dzień.",
                )
            return _ask(
                runtime, request, command, "event", "Które wydarzenie: " + names + "?", events
            )
        event = events[0]
        if not event.uid or (event.rrule and not event.recurrence_id):
            return _result(
                "clarify",
                "To wydarzenie nie ma identyfikatora wystąpienia do bezpiecznej "
                "zmiany. Zmień je w aplikacji kalendarza. Całych serii jeszcze nie zmieniam.",
                reason="calendar_recurrence",
            )
        data = {
            "entity_id": identifier,
            "stamp": _stamp(event),
            "uid": event.uid,
            "recurrence_id": event.recurrence_id,
            "description": (
                f"Odwołać „{event.summary}” "
                + event_when(
                    event.start_datetime_local
                    if isinstance(event.start, datetime)
                    else event.start,
                    now,
                )
                + "?"
            ),
        }
        if command.operation == "change":
            parsed = moment(command.new_when, now, base=event.start_datetime_local)
            if parsed is None:
                return _result(
                    "clarify",
                    "Podaj nowy dzień lub godzinę, na przykład: przełóż dentystę na jutro o 16.",
                    reason="calendar_date",
                )
            at, timed = parsed
            if isinstance(event.start, datetime):
                if not timed:
                    at = at.replace(
                        hour=event.start_datetime_local.hour,
                        minute=event.start_datetime_local.minute,
                    )
                start = at
            else:
                start = at.date() if not timed else at
            if at < now and (isinstance(event.start, datetime) or at.date() < now.date()):
                return _result(
                    "clarify",
                    "Nowy termin już minął. Podaj przyszły termin.",
                    reason="calendar_past",
                )
            if isinstance(start, datetime):
                length = event.end_datetime_local.astimezone(
                    UTC
                ) - event.start_datetime_local.astimezone(UTC)
                end = (start.astimezone(UTC) + length).astimezone(start.tzinfo)
                if moment(f"{start.date()} o {start:%H:%M}", now) is None:
                    return _result(
                        "clarify",
                        "Ten termin wypada podczas zmiany czasu. Podaj jednoznaczną godzinę.",
                        reason="calendar_dst",
                    )
            else:
                end = start + (event.end - event.start)
            payload = {
                "summary": event.summary,
                "dtstart": start.isoformat(),
                "dtend": end.isoformat(),
            }
            for key in ("description", "location"):
                if getattr(event, key):
                    payload[key] = getattr(event, key)
            data["event"] = payload
            data["description"] = f"Przełożyć „{event.summary}” na {event_when(start, now)}?"
    if request.dry_run:
        return _result(
            "dry_run",
            "Plan gotowy; wydarzenie nie zostało zapisane.",
            targets=(identifier,),
            risk=Risk.MEDIUM,
            response_data={"operation": command.operation, **data},
        )
    data["guard"] = _guard(metadata, runtime.calendar_settings)
    result = _ask(runtime, request, command, "confirm", data["description"], data)
    result.status, result.risk, result.targets = "confirm", Risk.MEDIUM, (identifier,)
    return result


async def _commit(runtime, request, context, command, data):
    identifier = data["entity_id"]
    lock = runtime.calendar_locks.setdefault(identifier, asyncio.Lock())
    async with lock:
        if not runtime.calendar_settings.get("calendar_write_enabled", False):
            raise PermissionError
        entity, metadata = await _entity(runtime, request, identifier, write=True)
        user = await runtime.hass.auth.async_get_user(request.user_id) if request.user_id else None
        candidates = _choose_calendar(
            command, runtime.visible_model(user), runtime.calendar_settings
        )
        if (
            len(candidates) != 1
            or candidates[0].id != identifier
            or _guard(metadata, runtime.calendar_settings) != data["guard"]
        ):
            return _result(
                "clarify",
                "Zmieniła się konfiguracja kalendarza. Powtórz polecenie.",
                reason="calendar_changed",
            )
        if not entity.supported_features & WRITES[command.operation]:
            raise PermissionError
        if command.operation != "add":
            events = await _events(runtime, request, identifier, command, dt_util.now())
            if not any(_stamp(e) == data["stamp"] for e in events):
                return _result(
                    "clarify",
                    "Wydarzenie się zmieniło. Powtórz polecenie.",
                    reason="calendar_changed",
                )
        await _entity(runtime, request, identifier, write=True)
        if request.dry_run:
            return _result("dry_run", "Nie zapisano zmian.", targets=(identifier,))
        if hasattr(entity, "async_set_context"):
            entity.async_set_context(context)
        if command.operation in {"add", "change"}:
            at = WEBSOCKET_EVENT_SCHEMA(data["event"])["dtstart"]
            past = at <= dt_util.now() if isinstance(at, datetime) else at < dt_util.now().date()
            if past:
                return _result(
                    "clarify", "Ten termin już minął. Podaj nowy termin.", reason="calendar_past"
                )
        if command.operation == "add":
            await entity.async_create_event(**WEBSOCKET_EVENT_SCHEMA(data["event"]))
            speech = "Wydarzenie jest już w kalendarzu."
        elif command.operation == "change":
            await entity.async_update_event(
                data["uid"],
                WEBSOCKET_EVENT_SCHEMA(data["event"]),
                recurrence_id=data.get("recurrence_id"),
            )
            speech = "Termin wydarzenia został zmieniony."
        else:
            await entity.async_delete_event(data["uid"], recurrence_id=data.get("recurrence_id"))
            speech = "Wydarzenie zostało odwołane."
        entity.async_update_event_listeners()
        return _result(
            "executed",
            speech,
            targets=(identifier,),
            completed=(identifier,),
            risk=Risk.MEDIUM,
            response_data={"operation": command.operation},
        )
