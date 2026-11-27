"""Calendar dialogue branch tests; provider execution remains a separate boundary."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_native_boundary import load

from custom_components.polish_context_voice.core.calendar import CalendarCommand
from custom_components.polish_context_voice.core.types import Request, Result


@pytest.fixture
def confirmation(monkeypatch):
    module = load(
        monkeypatch,
        "calendar",
        {
            "homeassistant.auth.permissions.const": {
                "POLICY_CONTROL": "control",
                "POLICY_READ": "read",
            },
            "homeassistant.components.calendar": {
                "WEBSOCKET_EVENT_SCHEMA": lambda event: event,
                "CalendarEntity": type("CalendarEntity", (), {}),
            },
            "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
            "homeassistant.util.dt": {"now": lambda: datetime(2026, 10, 21, tzinfo=UTC)},
        },
    )
    monkeypatch.setattr(module, "monotonic", lambda: 100.0)
    metadata = NS(
        id="calendar.family",
        labels=set(),
        platform="local_calendar",
        config_entry_id="synthetic",
        unique_id="family",
    )
    runtime = NS(
        pending_calendars={},
        calendar_settings={"calendar_write_enabled": True},
    )
    command = CalendarCommand("add", title="Próba", when="jutro o 15")
    data = {
        "entity_id": metadata.id,
        "guard": module._guard(metadata, runtime.calendar_settings),
        "description": "Dodać „Próbę” jutro o piętnastej?",
    }
    request = Request(
        "tak",
        conversation_id="confirmation",
        user_id="alice",
        source_area="kitchen",
        satellite_id="assist_satellite.kitchen",
        device_id="kitchen-device",
    )
    turn = module.CalendarTurn(command, "confirm", 130.0, data)
    runtime.pending_calendars[request.session_key] = turn
    commit = AsyncMock(return_value=Result("executed", "Zapisano."))
    entity = AsyncMock(return_value=(NS(), metadata))
    monkeypatch.setattr(module, "_commit", commit)
    monkeypatch.setattr(module, "_entity", entity)
    return NS(
        module=module,
        runtime=runtime,
        request=request,
        turn=turn,
        commit=commit,
        entity=entity,
    )


@pytest.mark.parametrize("reply", ["Tak. Tak", "tak tak", "Potwierdzam, potwierdzam", "Dobrze"])
async def test_repeated_affirmative_consumes_the_original_proposal_once(confirmation, reply):
    c = confirmation
    request = replace(c.request, text=reply)
    result = await c.module._process(c.runtime, request, None, None)
    assert result.status == "executed"
    c.commit.assert_awaited_once_with(c.runtime, request, None, c.turn.command, c.turn.data)
    assert request.session_key not in c.runtime.pending_calendars
    again = await c.module._process(c.runtime, request, None, None)
    assert again.reason == "calendar_no_pending"
    c.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "reply", ["Так", "tak да", "tak nie", "tak ale zmień godzinę", "tak tak tak tak"]
)
async def test_unclear_reply_keeps_original_deadline_without_writing(confirmation, reply):
    c = confirmation
    result = await c.module._process(c.runtime, replace(c.request, text=reply), None, None)
    assert result.reason == "calendar_confirmation_unclear"
    assert result.speech == (
        "Potwierdzenie jest niejednoznaczne. Dodać „Próbę” jutro o piętnastej?"
    )
    assert result.continue_conversation
    assert c.runtime.pending_calendars[c.request.session_key] is c.turn
    assert c.turn.expires_at == 130.0
    c.entity.assert_awaited_once()
    c.commit.assert_not_awaited()


async def test_neutral_confirmation_message_keeps_the_users_event_title(confirmation):
    c = confirmation
    title = "Nie odwołałem próby"
    c.turn.command = replace(c.turn.command, title=title)
    c.turn.data["description"] = f"Dodać „{title}” jutro o piętnastej?"
    result = await c.module._process(c.runtime, replace(c.request, text="Так"), None, None)
    assert result.speech == (
        "Potwierdzenie jest niejednoznaczne. "
        "Dodać „Nie odwołałem próby” jutro o piętnastej?"
    )
    assert c.runtime.pending_calendars[c.request.session_key] is c.turn
    assert c.turn.command.title == title
    assert c.turn.expires_at == 130.0
    c.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "bob"},
        {"conversation_id": "other-conversation"},
        {"source_area": "living_room"},
        {"satellite_id": "assist_satellite.living_room"},
        {"device_id": "living-room-device"},
        {"dry_run": True},
    ],
)
async def test_repeated_affirmative_cannot_confirm_another_source(confirmation, change):
    c = confirmation
    request = replace(c.request, text="Tak. Tak", **change)
    result = await c.module._process(c.runtime, request, None, None)
    assert result.reason == "calendar_no_pending"
    assert c.runtime.pending_calendars[c.request.session_key] is c.turn
    c.commit.assert_not_awaited()


async def test_expired_confirmation_explains_that_no_calendar_change_was_made(confirmation):
    c = confirmation
    c.turn.expires_at = 100.0
    result = await c.module._process(c.runtime, c.request, None, None)
    assert result.reason == "calendar_no_pending"
    assert "Minął czas na potwierdzenie" in result.speech
    assert "Kalendarz pozostaje bez zmian" in result.speech
    assert "Powtórz pełne polecenie" in result.speech
    assert not result.continue_conversation
    assert not c.runtime.pending_calendars
    c.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "operation,expected",
    [
        ("add", "Wydarzenie nie zostało dodane."),
        ("cancel", "Wydarzenie zostaje w kalendarzu."),
        ("change", "Termin wydarzenia pozostaje bez zmian."),
    ],
)
async def test_rejection_describes_abandoning_the_command_not_deleting_the_event(
    confirmation, operation, expected
):
    c = confirmation
    c.turn.command = replace(c.turn.command, operation=operation)
    result = await c.module._process(c.runtime, replace(c.request, text="Nie"), None, None)
    assert result.status == "cancelled"
    assert result.speech == expected
    assert not c.runtime.pending_calendars
    c.commit.assert_not_awaited()


async def test_rejection_without_pending_command_does_not_imply_event_deletion(confirmation):
    c = confirmation
    c.runtime.pending_calendars.clear()
    result = await c.module._process(c.runtime, replace(c.request, text="Nie"), None, None)
    assert result.status == "cancelled"
    assert result.speech == (
        "Nie ma oczekującego polecenia kalendarza. Kalendarz pozostaje bez zmian."
    )
    c.commit.assert_not_awaited()


async def test_unclear_reply_does_not_repeat_title_after_permission_revocation(confirmation):
    c = confirmation
    c.entity.side_effect = PermissionError
    result = await c.module.process(c.runtime, replace(c.request, text="Так"), None, None)
    assert result.reason == "calendar_access"
    assert "Próbę" not in result.speech
    assert not result.continue_conversation
    assert not c.runtime.pending_calendars
    c.commit.assert_not_awaited()
