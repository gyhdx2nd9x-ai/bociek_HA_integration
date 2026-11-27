"""Music Assistant search through public HA services, followed by exact URI playback."""

import asyncio
import re
from dataclasses import replace
from time import monotonic, perf_counter

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import entity_registry as er

from ..core.context.admission import RequestBusy
from ..core.context.dialogue import classify_reply
from ..core.diagnostics import measure_service_call
from ..core.media_search import (
    CHOICE_TTL,
    MAX_CHOICES,
    MAX_RESULTS,
    PENDING_LIMIT,
    MediaSearchTurn,
    candidates_from_response,
    exact_candidates,
    selected_index,
    source_candidates,
    with_location,
)
from ..core.music import MusicCommand, plan_music
from ..core.music_session import MusicMemory, music_fingerprint
from ..core.nlu.normalize import normalize
from ..core.types import Call, Result, Risk
from .intents import HAExecutor

SEARCH_TIMEOUT = 8
PLAY_TIMEOUT = 15
SEARCH_ID = "__bociek_dynamic_search__"


def _result(request, status, speech, reason="", **kwargs):
    return Result(
        status,
        speech,
        reason=reason,
        route="media_search",
        conversation_id=request.conversation_id,
        **kwargs,
    )


def _settings(runtime):
    return {**runtime.music_settings, **runtime.media_search_settings}


def _plan(runtime, request, user, command, item=None):
    """Reuse the existing player/group/risk planner with an ephemeral exact shortcut."""
    model, settings = runtime.visible_model(user), _settings(runtime)
    shortcuts = dict(settings.get("music_shortcuts", {}))
    shortcuts[SEARCH_ID] = {
        "name": "Wyszukana treść",
        "backend": "home_assistant",
        "media": {
            "media_content_id": item.uri if item else f"library://{command.media_type}/pending",
            "media_content_type": command.media_type,
        },
    }
    plan = plan_music(
        MusicCommand(
            "start",
            SEARCH_ID,
            location=command.location,
            player_name=command.player_name,
            backend="music_assistant",
            config_entry_id=settings.get("media_search_config_entry_id"),
        ),
        request,
        model,
        {**settings, "music_shortcuts": shortcuts},
    )
    if plan.status not in {"ready", "confirm"}:
        return plan, None
    entity_id = plan.calls[0].entity_ids[0]
    entity = model.entities.get(entity_id)
    registry = er.async_get(runtime.hass).async_get(entity_id)
    configured = settings.get("media_search_config_entry_id")
    if (
        entity is None
        or entity.platform != "music_assistant"
        or not entity.config_entry_id
        or registry is None
        or registry.platform != "music_assistant"
        or registry.config_entry_id != entity.config_entry_id
        or configured
        and configured != entity.config_entry_id
    ):
        return _result(
            request,
            "blocked",
            "Wybierz w ustawieniach pokoju odtwarzacz Music Assistant z właściwej instancji.",
            "media_search_backend_mismatch",
        ), None
    data = {
        "media_id": item.uri if item else f"library://{command.media_type}/pending",
        "media_type": command.media_type,
        "enqueue": "replace",
    }
    if settings.get("media_search_username"):
        data["username"] = settings["media_search_username"]
    plan.calls = (
        Call(
            "music_assistant",
            "play_media",
            plan.calls[0].entity_ids,
            data,
            plan.calls[0].guard_entity_ids,
        ),
    )
    plan.response_data = {
        **plan.response_data,
        "backend": "music_assistant",
        "config_entry_id": entity.config_entry_id,
        "media_type": command.media_type,
    }
    signature = (
        plan.targets,
        plan.area_ids,
        entity.id,
        entity.config_entry_id,
        settings.get("media_search_username", ""),
        tuple((i, tuple(sorted(model.entities[i].labels))) for i in plan.targets),
    )
    return plan, signature


def _put(runtime, request, turn):
    pending = runtime.media_search_pending
    if len(pending) >= PENDING_LIMIT:
        pending.pop(next(iter(pending)))
    pending[request.session_key] = turn


async def _read_instance(runtime, request, user):
    """Catalog questions need read access to MA, not control of a room player."""
    if request.user_id:
        user = await runtime.hass.auth.async_get_user(request.user_id)
        if user is None or not user.is_active:
            return _result(
                request,
                "blocked",
                "Brak dostępu do katalogu Music Assistant.",
                "media_search_catalog_denied",
            ), None
    model, settings = runtime.visible_model(user), _settings(runtime)
    configured = settings.get("media_search_config_entry_id")
    instances = set()
    for entity in model.entities.values():
        if (
            entity.domain != "media_player"
            or entity.platform != "music_assistant"
            or not entity.exposed
            or not exposed_entities.async_should_expose(runtime.hass, "conversation", entity.id)
            or not entity.available
            or "voice_block" in entity.labels
            or not entity.config_entry_id
            or user
            and not user.permissions.check_entity(entity.id, POLICY_READ)
        ):
            continue
        registry = er.async_get(runtime.hass).async_get(entity.id)
        if (
            registry
            and registry.platform == "music_assistant"
            and registry.config_entry_id == entity.config_entry_id
        ):
            instances.add(entity.config_entry_id)
    if configured:
        instances &= {configured}
    if len(instances) != 1:
        return _result(
            request,
            "clarify",
            "Wybierz dostępną instancję Music Assistant w ustawieniach wyszukiwania.",
            "media_search_catalog_instance",
        ), None
    instance = next(iter(instances))
    return Result("ready", "", response_data={"config_entry_id": instance}), (
        instance,
        settings.get("media_search_username", ""),
    )


async def _catalog_list(runtime, request, context, command, user):
    plan, signature = await _read_instance(runtime, request, user)
    if signature is None:
        return plan
    candidates = await _search(runtime, request, context, command, plan)
    current, current_signature = await _read_instance(runtime, request, user)
    if current_signature is None:
        return current
    if current_signature != signature:
        return _result(
            request,
            "clarify",
            "Zmieniła się instancja lub profil Music Assistant. Powtórz pytanie.",
            "media_search_target_changed",
        )
    if candidates is None:
        return _result(
            request,
            "blocked",
            "Akcja wyszukiwania Music Assistant jest niedostępna.",
            "media_search_unavailable",
        )
    if not candidates:
        return _result(
            request,
            "answered",
            "Nie ma pasujących pozycji w dostępnych katalogach Music Assistant.",
            "media_search_no_results",
        )
    titles = "; ".join(item.description for item in candidates)
    unverified = not all(item.authors for item in candidates)
    prefix = (
        "Music Assistant nie podaje tu autora. Wyniki wyszukiwania: "
        if unverified
        else "Znalezione tytuły: "
    )
    return _result(
        request,
        "answered",
        prefix + titles + ".",
        "media_search_author_unverified" if unverified else "",
        response_data={
            "candidates": [
                {"name": item.name, "media_type": item.media_type} for item in candidates
            ]
        },
    )


async def _search(runtime, request, context, command, plan):
    settings = _settings(runtime)
    data = {
        "config_entry_id": plan.response_data["config_entry_id"],
        "name": command.query,
        "media_type": [command.media_type],
        "limit": MAX_RESULTS,
        "library_only": bool(
            command.library_only
            or not command.source
            and settings.get("media_search_library_first")
        ),
    }
    if command.creator and command.media_type in {"track", "album"}:
        data["artist"] = command.creator
    if settings.get("media_search_username"):
        data["username"] = settings["media_search_username"]
    if not runtime.hass.services.has_service("music_assistant", "search"):
        return None

    async def call():
        async with asyncio.timeout(SEARCH_TIMEOUT):
            with measure_service_call():
                response = await runtime.hass.services.async_call(
                    "music_assistant",
                    "search",
                    data,
                    blocking=True,
                    return_response=True,
                    context=context,
                )
            return candidates_from_response(response, command)

    candidates = await call()
    if (
        data["library_only"]
        and not command.library_only
        and not exact_candidates(candidates, command)
    ):
        data["library_only"] = False
        candidates = await call()
    return candidates


def _prompt(request, turn, *, author_unverified=False):
    if turn.stage == "title":
        if turn.command.media_type == "audiobook":
            speech = "Jaki audiobook mam wyszukać?"
        elif turn.command.media_type == "artist":
            speech = "Jakiego wykonawcę mam wyszukać?"
        elif turn.command.media_type == "album":
            speech = "Jaki album mam wyszukać?"
        else:
            speech = "Jaki utwór mam wyszukać?"
    elif turn.stage == "confirm":
        speech = turn.confirmation_prompt or f"Uruchomić „{turn.candidates[0].description}”?"
    elif len(turn.candidates) == 1:
        speech = f"Czy chodzi o „{turn.candidates[0].description}”?"
    else:
        labels = ("Pierwsza", "Druga", "Trzecia")
        speech = (
            " ".join(f"{labels[i]}: {item.description}." for i, item in enumerate(turn.candidates))
            + " Którą pozycję wybrać?"
        )
    if author_unverified:
        speech = "Music Assistant nie podaje tu autora. " + speech
    return _result(
        request,
        "clarify" if turn.stage != "confirm" else "confirm",
        speech,
        "media_search_title_required"
        if turn.stage == "title"
        else "media_search_choice"
        if turn.stage != "confirm"
        else "confirmation_required",
        continue_conversation=True,
        risk=Risk.MEDIUM if turn.stage == "confirm" else Risk.LOW,
        response_data={
            "candidates": [
                {"name": item.name, "version": item.version, "media_type": item.media_type}
                for item in turn.candidates
            ]
        },
    )


async def _commit(
    runtime, request, context, command, item, user, signature, *, confirmed=False, expected=None
):
    plan, current_signature = _plan(runtime, request, user, command, item)
    if current_signature is None:
        return plan
    if (
        current_signature != signature
        or expected is not None
        and music_fingerprint(plan) != expected
    ):
        return _result(
            request,
            "clarify",
            "Zmienił się odtwarzacz lub konfiguracja. Powtórz polecenie.",
            "media_search_target_changed",
        )
    executor = HAExecutor(runtime.hass, context)
    # Share admission keys with ordinary pause/next and named-shortcut playback.
    with runtime.music.admission.enter(plan.response_data["playback_key"]):
        await executor.preflight(plan.calls, request)
        fresh, fresh_signature = _plan(runtime, request, user, command, item)
        if fresh_signature != signature or music_fingerprint(fresh) != music_fingerprint(plan):
            return _result(
                request,
                "clarify",
                "Zmienił się stan głośnika. Powtórz polecenie.",
                "media_search_target_changed",
            )
        if request.dry_run:
            fresh.status, fresh.speech = (
                "dry_run",
                "Plan odtwarzania gotowy; muzyka nie została zmieniona.",
            )
            fresh.route = "media_search"
            return fresh
        if plan.status == "confirm" and not confirmed:
            prompt = (
                f"Zastąpić obecne odtwarzanie pozycją „{item.description}”?"
                if plan.response_data.get("current_content") is not None
                else f"Uruchomić „{item.description}”?"
            )
            turn = MediaSearchTurn(
                command,
                (item,),
                signature,
                monotonic() + CHOICE_TTL,
                "confirm",
                music_fingerprint(plan),
                prompt,
            )
            _put(runtime, request, turn)
            return _prompt(request, turn)
        async with asyncio.timeout(PLAY_TIMEOUT):
            await executor.execute(fresh.calls[0], request)
        runtime.media_search_pending.pop(request.session_key, None)
        fresh.status, fresh.reason, fresh.route = "executed", "", "media_search"
        medium = "audiobook " if command.media_type == "audiobook" else ""
        fresh.speech = (
            f"Odtwarzam muzykę wykonawcy {item.name}."
            if command.media_type == "artist"
            else f"Odtwarzam {medium}„{item.name}”."
        )
        fresh.completed = fresh.targets
        memory_key = runtime.music.memory_key(request, runtime.visible_model(user))
        if (
            memory_key
            and fresh.response_data["selected_player"] in runtime.visible_model(user).entities
        ):
            selected = runtime.visible_model(user).entities[fresh.response_data["selected_player"]]
            if selected.area_id == memory_key[-1]:
                runtime.music.put(
                    runtime.music.memory, memory_key, MusicMemory(selected.id, monotonic() + 60)
                )
        return fresh


async def process(runtime, request, context, command, user):
    started = perf_counter()
    try:
        result = await _process(runtime, request, context, command, user)
    except TimeoutError:
        runtime.media_search_pending.pop(request.session_key, None)
        result = _result(
            request,
            "error",
            "Music Assistant nie odpowiedział na czas. Sprawdź odtwarzanie przed ponowieniem.",
            "media_search_timeout",
        )
    except RequestBusy:
        result = _result(
            request,
            "busy",
            "Ten głośnik obsługuje już polecenie. Spróbuj za chwilę.",
            "music_player_busy",
        )
    except Exception:
        # Provider exceptions can contain signed URLs: never echo their payload.
        runtime.media_search_pending.pop(request.session_key, None)
        result = _result(
            request,
            "error",
            "Nie udało się wyszukać lub uruchomić tej pozycji. Sprawdź Music Assistant.",
            "media_search_failed",
        )
    result.route = "media_search"
    result.conversation_id = request.conversation_id
    result.latency_ms = round((perf_counter() - started) * 1000, 3)
    return result


async def _process(runtime, request, context, command, user):
    pending = runtime.media_search_pending
    for key, value in list(pending.items()):
        if value.expires_at <= monotonic():
            pending.pop(key, None)
    if not runtime.media_search_settings.get("media_search_enabled", True):
        pending.pop(request.session_key, None)
        return _result(
            request,
            "blocked",
            "Wyszukiwanie mediów jest wyłączone w ustawieniach Boćka.",
            "media_search_disabled",
        )
    previous = pending.pop(request.session_key, None)
    if command is None:
        if previous is None:
            return _result(
                request,
                "clarify",
                "Nie ma aktualnego wyboru muzyki. Podaj tytuł ponownie.",
                "media_search_no_pending",
            )
        reply = classify_reply(request.text)
        if reply is False:
            speech = (
                "Nie uruchamiam audiobooka."
                if previous.stage == "title" and previous.command.media_type == "audiobook"
                else "Nie uruchamiam odtwarzania."
                if previous.stage == "title"
                else "Odtwarzanie nie zostało zmienione."
            )
            return _result(request, "cancelled", speech)
        if previous.stage == "title":
            if reply is True:
                _put(runtime, request, previous)
                return _prompt(request, previous)
            query = request.text.strip().strip('„”"\'').strip()
            if not query:
                _put(runtime, request, previous)
                return _prompt(request, previous)
            creator = None
            by_pattern = (
                r"\s+(?:autora|autorki|autorstwa)\s+(.+)$"
                if previous.command.media_type == "audiobook"
                else r"\s+(?:wykonawcy|zespołu|artysty|artystki)\s+(.+)$"
            )
            if by := re.search(by_pattern, query, re.I):
                creator = query[by.start(1) :].strip()
                query = query[: by.start()].strip().strip('„”"\'').strip()
            return await _process(
                runtime,
                request,
                context,
                replace(previous.command, query=query, creator=creator or previous.command.creator),
                user,
            )
        index = selected_index(request.text, previous.candidates)
        if previous.stage == "confirm" and reply is not True:
            index = None
        if index is None:
            _put(runtime, request, previous)
            confirming = previous.stage == "confirm"
            return _result(
                request,
                "confirm" if confirming else "clarify",
                "Potwierdź poprzednie pytanie albo powiedz „nie”."
                if confirming
                else "Wybierz jedną z podanych pozycji albo podaj jej nazwę.",
                "confirmation_required" if confirming else "media_search_choice",
                continue_conversation=True,
                risk=Risk.MEDIUM if confirming else Risk.LOW,
            )
        return await _commit(
            runtime,
            request,
            context,
            previous.command,
            previous.candidates[index],
            user,
            previous.target_signature,
            confirmed=previous.stage == "confirm",
            expected=previous.plan_fingerprint,
        )
    if command.list_only:
        return await _catalog_list(runtime, request, context, command, user)
    if not command.query:
        if request.dry_run:
            title_turn = MediaSearchTurn(command, (), (), monotonic() + CHOICE_TTL, "title")
            return _result(
                request,
                "clarify",
                _prompt(request, title_turn).speech,
                "media_search_title_required",
            )
        turn = MediaSearchTurn(command, (), (), monotonic() + CHOICE_TTL, "title")
        _put(runtime, request, turn)
        return _prompt(request, turn)
    original = command
    command = with_location(command, runtime.visible_model(user))
    plan, signature = _plan(runtime, request, user, command)
    if signature is None:
        return plan
    await HAExecutor(runtime.hass, context).preflight(plan.calls, request)
    candidates = None
    if (
        command.query != original.query
        and command.location
        and not command.explicit_location
        and not original.quoted
    ):
        # A full catalog title may itself contain an Area alias. Neither parsing
        # may silently redirect playback when both interpretations are plausible.
        original_plan, original_signature = _plan(runtime, request, user, original)
        if original_signature is not None:
            await HAExecutor(runtime.hass, context).preflight(original_plan.calls, request)
            full = await _search(runtime, request, context, original, original_plan)
            if full and exact_candidates(full, original):
                return _result(
                    request,
                    "clarify",
                    f"„{original.query}” może oznaczać tytuł albo miejsce odtwarzania. "
                    "Podaj tytuł w cudzysłowie lub wskaż odtwarzacz po nazwie.",
                    "media_search_title_location_ambiguous",
                )
    if candidates is None:
        candidates = await _search(runtime, request, context, command, plan)
    fresh, fresh_signature = _plan(runtime, request, user, command)
    if fresh_signature is None:
        return fresh
    if fresh_signature != signature:
        return _result(
            request,
            "clarify",
            "Zmienił się odtwarzacz lub konfiguracja. Powtórz polecenie.",
            "media_search_target_changed",
        )
    await HAExecutor(runtime.hass, context).preflight(fresh.calls, request)
    if candidates is None:
        return _result(
            request,
            "blocked",
            "Akcja wyszukiwania Music Assistant jest niedostępna.",
            "media_search_unavailable",
        )
    if command.source:
        candidates = source_candidates(candidates, command.source)
        if not candidates:
            return _result(
                request,
                "clarify",
                "Nie mogę potwierdzić tego źródła w wynikach Music Assistant. "
                "Podaj sam tytuł albo wybierz zapisany skrót do tej pozycji.",
                "media_search_source_unverified",
            )
    if not candidates:
        return _result(
            request,
            "answered",
            "Nie ma pasujących pozycji w dostępnych katalogach Music Assistant.",
            "media_search_no_results",
        )
    if command.creator:
        candidates = tuple(
            item
            for item in candidates
            if not (names := item.authors if command.media_type == "audiobook" else item.creators)
            or normalize(command.creator) in {normalize(name) for name in names}
        )
        if not candidates:
            return _result(
                request,
                "answered",
                "Nie ma potwierdzonego dopasowania dla tego tytułu i autora lub wykonawcy.",
                "media_search_no_results",
            )
    exact = exact_candidates(candidates, command)
    choices = exact or candidates
    author_unverified = (
        command.media_type == "audiobook"
        and bool(command.creator or command.list_only)
        and not all(item.authors for item in choices)
    )
    if len(exact) == 1 and not command.list_only and not author_unverified:
        return await _commit(runtime, request, context, command, exact[0], user, signature)
    if len(choices) > MAX_CHOICES:
        return _result(
            request,
            "clarify",
            "Pasuje kilka pozycji. Podaj pełniejszy tytuł lub wykonawcę.",
            "media_search_too_many",
        )
    turn = MediaSearchTurn(command, choices, signature, monotonic() + CHOICE_TTL)
    if request.dry_run:
        result = _prompt(request, turn, author_unverified=author_unverified)
        result.continue_conversation = False
        result.speech = (
            "Podgląd wymaga wyboru pozycji; odtwarzanie nie zostało zmienione. " + result.speech
        )
        return result
    _put(runtime, request, turn)
    return _prompt(request, turn, author_unverified=author_unverified)
