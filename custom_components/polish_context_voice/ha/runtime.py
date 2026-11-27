"""N5105-friendly runtime: shared metadata, request-local identity and bounded work."""

from asyncio import CancelledError
from dataclasses import asdict, replace
from time import monotonic
from uuid import uuid4

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..core.calendar import parse_calendar
from ..core.communication import ACTION_FAILED_SPEECH
from ..core.context.admission import Admission, RequestBusy
from ..core.context.dialogue import Dialogue, classify_reply
from ..core.diagnostics import RequestDiagnostics
from ..core.engine import Engine
from ..core.home_model.cache import ModelCache
from ..core.home_model.health import check
from ..core.llm.limits import LimitedFallback
from ..core.llm.router import OpenAICompatibleFallback
from ..core.maintenance import DEFAULT_BATTERY_THRESHOLD
from ..core.media_search import follows as follows_media_search
from ..core.media_search import parse_media_search
from ..core.music import music_health, parse_music
from ..core.music_session import MusicSession
from ..core.nlu.capabilities import parse_capability
from ..core.nlu.timers import is_timer_followup, is_timer_request, is_timer_response_echo
from ..core.stoic import parse_stoic
from ..core.types import Call, Result, Risk
from ..core.vacuum import mapped_rooms
from ..core.vacuum import match_command as match_vacuum
from ..core.vacuum import resolve_command as resolve_vacuum
from .intents import HAExecutor
from .registry_sync import snapshot, update_state


class Runtime:
    def __init__(self, hass, entry):
        self.hass = hass
        self.cache = ModelCache(lambda: snapshot(hass))
        self.admission = Admission(limit=8)
        self.diagnostics = RequestDiagnostics()
        self.pending = {}
        self.pending_timers = {}
        self.pending_capabilities = {}
        self.pending_calendars = {}
        self.calendar_locks = {}
        self.dialogue = Dialogue()
        self.baseline_ids = None
        settings = {**entry.data, **entry.options}
        self.music_settings = settings
        self.calendar_settings = settings
        self.stoic_settings = settings
        self.stoic_snapshots = {}
        self.media_search_settings = settings
        self.media_search_pending = {}
        self.music = MusicSession()
        self.maintenance_battery_threshold = settings.get(
            "maintenance_battery_threshold", DEFAULT_BATTERY_THRESHOLD
        )
        self.fallback = None
        if settings.get("llm_endpoint") and settings.get("llm_model"):
            self.fallback = LimitedFallback(
                OpenAICompatibleFallback(
                    async_get_clientsession(hass), settings["llm_endpoint"], settings["llm_model"]
                )
            )

    def visible_model(self, user):
        model = self.cache.get()
        if user is None:
            return model
        return model.visible_to(
            lambda entity_id: user.permissions.check_entity(entity_id, POLICY_READ)
        )

    def _discard_control_turns(self, session_key):
        self.pending.pop(session_key, None)
        self.pending_timers.pop(session_key, None)
        self.pending_capabilities.pop(session_key, None)
        self.dialogue.discard(session_key)
        self.music.pending.pop(session_key, None)
        self.music.questions.pop(session_key, None)

    async def process(self, request, context):
        trace = self.diagnostics.begin(
            dry_run=request.dry_run,
            has_source_area=request.source_area is not None,
            has_satellite=request.satellite_id is not None,
            has_device=request.device_id is not None,
        )
        try:
            with trace.activate():
                result = await self._process(request, context, trace)
        except CancelledError:
            trace.finish(interrupted=True)
            raise
        except Exception:
            trace.finish()
            raise
        result.request_id = trace.identifier
        trace.finish(result)
        return result

    async def _process(self, request, context, trace):
        with trace.stage("source_context"):
            request = replace(request, conversation_id=request.conversation_id or str(uuid4()))
            if request.source_area is None and not request.satellite_id and request.device_id:
                device = dr.async_get(self.hass).async_get(request.device_id)
                if device:
                    request = replace(
                        request, source_area=dr.async_get_effective_area_id(self.hass, device)
                    )
        try:
            with self.admission.enter(request.session_key):
                with trace.stage("authorization"):
                    user = None
                    if request.user_id:
                        user = await self.hass.auth.async_get_user(request.user_id)
                        if user is None or not user.is_active:
                            return Result(
                                "blocked",
                                "Brak dostępu.",
                                reason="user_unavailable",
                                conversation_id=request.conversation_id,
                            )
                with trace.stage("routing"):
                    timer_echo = is_timer_response_echo(request.text)
                if timer_echo:
                    return Result(
                        "ignored",
                        "",
                        reason="timer_response_echo",
                        route="native_timer",
                        conversation_id=request.conversation_id,
                    )
                with trace.stage("routing"):
                    vacuum_match = match_vacuum(request.text)
                if vacuum_match:
                    self._discard_control_turns(request.session_key)
                    self.pending_calendars.pop(request.session_key, None)
                    self.media_search_pending.pop(request.session_key, None)
                    return await trace.run(
                        _process_vacuum(self, request, context, user, vacuum_match)
                    )
                with trace.stage("routing"):
                    stoic_command = parse_stoic(request.text)
                    music = parse_music(request.text, self.music_settings)
                    # Configured exact shortcuts retain precedence over catalog search.
                    media_command = (
                        None
                        if music and music.operation == "start"
                        else parse_media_search(request.text, self.media_search_settings)
                    )
                    media_followup = follows_media_search(self.media_search_pending, request)
                if stoic_command or media_command or media_followup:
                    self._discard_control_turns(request.session_key)
                    self.pending_calendars.pop(request.session_key, None)
                    if stoic_command:
                        from .stoic import process as process_stoic

                        self.media_search_pending.pop(request.session_key, None)
                        return await trace.run(
                            process_stoic(self, request, context, stoic_command, user)
                        )
                    from .media_search import process as process_media_search

                    return await trace.run(
                        process_media_search(self, request, context, media_command, user)
                    )
                with trace.stage("routing"):
                    calendar_command = parse_calendar(request.text)
                    calendar_turn = self.pending_calendars.get(request.session_key)
                    calendar_followup = (
                        calendar_turn is not None
                        # Let a short playback command through once the question expires.
                        and (
                            not (music and music.contextual)
                            or calendar_turn.expires_at > monotonic()
                        )
                        and nlu_calendar_followup(request.text)
                    )
                if calendar_command or calendar_followup:
                    from .calendar import process as process_calendar

                    self._discard_control_turns(request.session_key)
                    self.media_search_pending.pop(request.session_key, None)
                    return await trace.run(
                        process_calendar(self, request, context, calendar_command)
                    )
                self.pending_calendars.pop(request.session_key, None)
                if music and music.contextual:
                    now = monotonic()
                    questions = (
                        self.pending,
                        self.pending_timers,
                        self.pending_capabilities,
                        self.dialogue.questions,
                        self.music.pending,
                        self.music.questions,
                        self.media_search_pending,
                    )
                    if any(
                        (pending := mapping.get(request.session_key)) is not None
                        and pending.expires_at > now
                        for mapping in questions
                    ):
                        return Result(
                            "clarify",
                            "Czekam na odpowiedź na poprzednie pytanie.",
                            reason="pending_question",
                            continue_conversation=True,
                            conversation_id=request.conversation_id,
                        )
                self.media_search_pending.pop(request.session_key, None)
                with trace.stage("routing"):
                    music_configured = bool(self.music_settings.get("music_rooms"))
                    music_route = (
                        music
                        and (
                            music_configured
                            or music.contextual
                            or music.operation
                            in {"start", "unknown_shortcut", "ambiguous_shortcut"}
                        )
                    ) or self.music.follows(
                        request, self.visible_model(user) if self.music.questions else None
                    )
                if music_route:
                    from .music import process as process_music

                    self.pending.pop(request.session_key, None)
                    self.pending_timers.pop(request.session_key, None)
                    self.pending_capabilities.pop(request.session_key, None)
                    self.dialogue.discard(request.session_key)
                    return await trace.run(process_music(self, request, context, music, user))
                self.music.pending.pop(request.session_key, None)
                self.music.questions.pop(request.session_key, None)
                with trace.stage("routing"):
                    capability = parse_capability(request.text)
                    capability_route = capability or (
                        request.session_key in self.pending_capabilities
                        and classify_reply(request.text) is not None
                    )
                if capability_route:
                    from .capabilities import process as process_capability

                    self.pending.pop(request.session_key, None)
                    self.pending_timers.pop(request.session_key, None)
                    self.dialogue.discard(request.session_key)
                    return await trace.run(process_capability(self, request, context, capability))
                self.pending_capabilities.pop(request.session_key, None)
                with trace.stage("routing"):
                    timer_route = is_timer_request(request.text) or (
                        request.session_key in self.pending_timers
                        and is_timer_followup(request.text)
                    )
                if timer_route:
                    from .timers import process

                    self.pending.pop(request.session_key, None)
                    self.dialogue.discard(request.session_key)
                    return await trace.run(
                        process(self.hass, request, context, pending=self.pending_timers)
                    )
                self.pending_timers.pop(request.session_key, None)
                executor = HAExecutor(self.hass, context)
                engine = Engine(
                    lambda: self.visible_model(user),
                    executor,
                    self.fallback,
                    pending=self.pending,
                    dialogue=self.dialogue,
                )
                return await trace.run(engine.process(request))
        except RequestBusy:
            return Result(
                "busy",
                "Trwa już polecenie. Spróbuj ponownie za chwilę.",
                reason="request_busy",
                conversation_id=request.conversation_id,
            )

    def health(self):
        model = self.cache.get()
        if self.baseline_ids is None:
            self.baseline_ids = frozenset(model.entities)
        return check(model, self.baseline_ids) + [
            asdict(finding) for finding in music_health(model, self.music_settings)
        ]

    def subscribe(self):
        @callback
        def invalidate(*_):
            self.cache.invalidate()

        @callback
        def state_changed(event):
            update_state(self.hass, self.cache, event)

        unsubscribers = [
            self.hass.bus.async_listen(event, invalidate)
            for event in (
                "entity_registry_updated",
                "device_registry_updated",
                "area_registry_updated",
                "floor_registry_updated",
                "label_registry_updated",
            )
        ]
        unsubscribers.append(self.hass.bus.async_listen("state_changed", state_changed))
        unsubscribers.append(
            exposed_entities.async_listen_entity_updates(
                self.hass,
                "conversation",
                invalidate,
            )
        )

        @callback
        def unsubscribe():
            for unsub in unsubscribers:
                unsub()
            self.pending.clear()
            self.pending_timers.clear()
            self.pending_capabilities.clear()
            self.pending_calendars.clear()
            self.calendar_locks.clear()
            self.stoic_snapshots.clear()
            self.media_search_pending.clear()
            self.dialogue.clear()
            self.music.clear()

        return unsubscribe


def nlu_calendar_followup(text):
    """A new control command abandons the calendar draft; short answers stay local."""
    from ..core import nlu

    return (
        nlu.parse(text) is None
        and not is_timer_request(text)
        and not (
            (music := parse_music(text, {})) and not music.contextual or parse_capability(text)
        )
        and parse_stoic(text) is None
        and parse_media_search(text, {}) is None
    )


async def _process_vacuum(runtime, request, context, user, match):
    """Resolve and execute a whole-map or mapped-room Dreame clean request."""
    model = runtime.visible_model(user)
    candidates = [
        entity
        for entity in model.entities.values()
        if entity.domain == "vacuum"
        and entity.platform == "dreame_vacuum"
        and entity.exposed
        and "voice_block" not in entity.labels
    ]
    vacuums = [entity for entity in candidates if entity.available]
    if not vacuums and candidates:
        return Result(
            "unsupported", ACTION_FAILED_SPEECH, reason="vacuum_unavailable", route="vacuum",
        )
    if not vacuums:
        return Result(
            "unsupported",
            "Nie widzę dostępnego odkurzacza Dreame.",
            reason="vacuum_missing",
            route="vacuum",
            conversation_id=request.conversation_id,
        )
    if len(vacuums) != 1:
        return Result(
            "clarify",
            "Wybierz odkurzacz Dreame.",
            reason="vacuum_ambiguous",
            route="vacuum",
            conversation_id=request.conversation_id,
        )
    vacuum = vacuums[0]
    state = runtime.hass.states.get(vacuum.id)
    command = resolve_vacuum(match, mapped_rooms(state))
    if command.room == "":
        return Result(
            "clarify",
            "Tego pomieszczenia nie ma na aktywnej mapie odkurzacza.",
            reason="vacuum_room_not_mapped",
            route="vacuum",
        )
    attributes = state.attributes if state is not None else {}
    mode = str(attributes.get("cleangenius_mode", "")).casefold()
    if not attributes.get("cleangenius") or mode not in {
        "vacuum and mop",
        "odkurzanie i mopowanie",
        "odkurzanie oraz mopowanie",
    }:
        return Result(
            "blocked",
            "Domyślny tryb GeniusClean z mopowaniem nie jest teraz ustawiony.",
            reason="vacuum_genius_mode_not_ready",
            route="vacuum",
        )
    if command.room is None:
        call = Call("vacuum", "start", (vacuum.id,))
        speech = "Rozpoczynam domyślne sprzątanie całej mapy."
    else:
        call = Call(
            "dreame_vacuum",
            "vacuum_clean_segment",
            (vacuum.id,),
            {"segments": [int(command.room)]},
        )
        speech = "Rozpoczynam domyślne sprzątanie w wybranym pomieszczeniu."
    result = Result(
        "dry_run" if request.dry_run else "executed", speech,
        targets=(vacuum.id,), calls=(call,), confidence=0.98,
        confidence_class="high", risk=Risk.MEDIUM, route="vacuum",
        area_ids=(vacuum.area_id,) if vacuum.area_id else (),
        conversation_id=request.conversation_id,
    )
    if request.dry_run:
        result.speech = "Plan sprzątania gotowy; odkurzacz nie został uruchomiony."
        return result
    executor = HAExecutor(runtime.hass, context)
    try:
        await executor.preflight(result.calls, request)
        if command.room is not None and command.room not in mapped_rooms(
            runtime.hass.states.get(vacuum.id)
        ):
            return Result(
                "blocked",
                "Mapa odkurzacza się zmieniła. Powtórz polecenie.",
                reason="vacuum_map_changed",
                route="vacuum",
            )
        await executor.execute(call, request)
    except HomeAssistantError:
        result.status = "error"
        result.speech = ACTION_FAILED_SPEECH
        result.reason = "execution_failed"
        return result
    result.completed = (vacuum.id,)
    return result
