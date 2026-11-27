"""Request-local content routing alongside existing dialogs and named shortcuts."""

import sys
from time import monotonic
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import test_runtime_resources as resources
from test_runtime_resources import req

from custom_components.polish_context_voice.core.media_search import MediaCandidate
from custom_components.polish_context_voice.core.types import Result

PREFIX = "custom_components.polish_context_voice.ha."


@pytest.fixture
def runtime(monkeypatch, home):
    return resources.runtime.__wrapped__(monkeypatch, home)


async def test_stoic_request_abandons_calendar_draft(runtime, monkeypatch):
    request = req("Przeczytaj dzisiejszą medytację")
    runtime.pending_calendars[request.session_key] = object()
    handler = AsyncMock(return_value=Result("answered", "Cytat", route="stoic"))
    monkeypatch.setitem(sys.modules, PREFIX + "stoic", NS(process=handler))
    result = await runtime.process(request, None)
    assert result.speech == "Cytat"
    assert request.session_key not in runtime.pending_calendars
    assert runtime.diagnostics.snapshot()["recent_requests"][0]["route"] == "stoic"
    assert not runtime.recorded_calls


async def test_explicit_title_abandons_calendar_draft(runtime, monkeypatch):
    request = req("Puść utwór Gwiezdna droga")
    runtime.pending_calendars[request.session_key] = object()
    handler = AsyncMock(return_value=Result("executed", "Muzyka", route="media_search"))
    monkeypatch.setitem(sys.modules, PREFIX + "media_search", NS(process=handler))
    result = await runtime.process(request, None)
    assert result.speech == "Muzyka"
    assert request.session_key not in runtime.pending_calendars
    assert runtime.diagnostics.snapshot()["recent_requests"][0]["route"] == "media_search"


async def test_named_shortcut_retains_precedence_over_bare_title(runtime, monkeypatch):
    runtime.music_settings = {
        "music_shortcuts": {"favorite": {"name": "Gwiezdna droga", "aliases": []}}
    }
    handler = AsyncMock(return_value=Result("executed", "Skrót", route="music"))
    search = AsyncMock()
    monkeypatch.setitem(sys.modules, PREFIX + "music", NS(process=handler))
    monkeypatch.setitem(sys.modules, PREFIX + "media_search", NS(process=search))
    result = await runtime.process(req("Puść Gwiezdna droga"), None)
    assert result.speech == "Skrót"
    assert handler.call_args.args[3].shortcut == "favorite"
    search.assert_not_called()


def turn():
    return NS(
        expires_at=monotonic() + 45,
        candidates=(MediaCandidate("library://track/1", "Gwiezdna droga", "track"),),
    )


async def test_choice_reply_is_delivered_only_to_its_source(runtime, monkeypatch):
    original = req("pierwszy", satellite_id="a")
    runtime.media_search_pending[original.session_key] = turn()
    handler = AsyncMock(return_value=Result("executed", "Wybrana", route="media_search"))
    monkeypatch.setitem(sys.modules, PREFIX + "media_search", NS(process=handler))
    await runtime.process(req("pierwszy", satellite_id="b"), None)
    handler.assert_not_called()
    assert original.session_key in runtime.media_search_pending
    result = await runtime.process(original, None)
    assert result.speech == "Wybrana"
    handler.assert_awaited_once()


async def test_contextual_next_waits_for_current_content_choice(runtime):
    request = req("dalej")
    runtime.media_search_pending[request.session_key] = turn()
    result = await runtime.process(request, None)
    assert result.reason == "pending_question"
    assert result.continue_conversation
    assert request.session_key in runtime.media_search_pending
    assert not runtime.recorded_calls


@pytest.mark.parametrize(
    ("text", "deadline", "expected_route"),
    [("dalej", 100.1, "calendar"), ("dalej", 100, "music"), ("tak", 100, "calendar")],
)
async def test_calendar_deadline_only_releases_contextual_music(
    runtime, monkeypatch, text, deadline, expected_route
):
    request = req(text, satellite_id="same")
    other = req(text, satellite_id="other")
    runtime.pending_calendars[request.session_key] = NS(expires_at=deadline)
    runtime.pending_calendars[other.session_key] = NS(expires_at=130)
    monkeypatch.setattr(sys.modules[type(runtime).__module__], "monotonic", lambda: 100)
    handlers = {
        route: AsyncMock(return_value=Result("answered", route, route=route))
        for route in ("calendar", "music")
    }
    for route, handler in handlers.items():
        monkeypatch.setitem(sys.modules, PREFIX + route, NS(process=handler))

    result = await runtime.process(request, None)

    assert result.route == expected_route
    assert handlers[expected_route].await_count == 1
    assert sum(handler.await_count for handler in handlers.values()) == 1
    assert other.session_key in runtime.pending_calendars
    assert (request.session_key in runtime.pending_calendars) == (expected_route == "calendar")


async def test_new_light_command_discards_its_content_choice(runtime):
    request = req("zapal światła")
    runtime.media_search_pending[request.session_key] = turn()
    result = await runtime.process(request, None)
    assert result.status == "executed"
    assert request.session_key not in runtime.media_search_pending
    assert result.targets == ("light.living_room_main",)


@pytest.mark.parametrize("pending_domain", ["calendar", "catalog"])
async def test_new_exclusive_light_command_takes_precedence_over_content_reply(
    runtime, monkeypatch, pending_domain
):
    pending = (
        runtime.pending_calendars if pending_domain == "calendar" else runtime.media_search_pending
    )
    request = req("Zapal tylko nastrojowe oświetlenie w salonie")
    other = req("pierwszy", satellite_id="separate-room")
    pending[request.session_key] = turn()
    pending[other.session_key] = turn()
    handler = AsyncMock(side_effect=AssertionError("Light command took a content route"))
    adapter = "calendar" if pending_domain == "calendar" else "media_search"
    monkeypatch.setitem(sys.modules, PREFIX + adapter, NS(process=handler))

    result = await runtime.process(request, None)

    handler.assert_not_called()
    assert result.status == "executed"
    assert result.targets == ("light.living_room_ambient",)
    assert [(call.service, call.entity_ids) for _, _, call in runtime.recorded_calls] == [
        ("turn_on", ("light.living_room_ambient",))
    ]
    assert request.session_key not in pending
    assert other.session_key in pending
