"""Actual CalendarEntity methods, authenticated runtime and a simulated Kiosk peer."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components import calendar
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, callback
from homeassistant.helpers import entity_registry as er


class EditableCalendar(calendar.CalendarEntity):
    _attr_name = "Lab Family Calendar"
    _attr_unique_id = "lab_family_calendar"
    _attr_should_poll = False
    _attr_supported_features = calendar.CalendarEntityFeature(7)

    def __init__(self):
        self.events = []
        self.operations = []

    @property
    def event(self):
        return self.events[0] if self.events else None

    async def async_get_events(self, hass, start_date, end_date):
        return [
            e
            for e in self.events
            if e.start_datetime_local < end_date and e.end_datetime_local > start_date
        ]

    async def async_create_event(self, **kwargs):
        self.operations.append(("add", kwargs))
        self.events.append(
            calendar.CalendarEvent(
                kwargs["dtstart"], kwargs["dtend"], kwargs["summary"], uid=uuid4().hex
            )
        )
        self.async_write_ha_state()

    async def async_update_event(self, uid, event, **kwargs):
        self.operations.append(("change", event))
        original = next(e for e in self.events if e.uid == uid)
        self.events[self.events.index(original)] = replace(
            original, start=event["dtstart"], end=event["dtend"], summary=event["summary"]
        )
        self.async_write_ha_state()

    async def async_delete_event(self, uid, **kwargs):
        self.operations.append(("cancel", uid))
        self.events = [e for e in self.events if e.uid != uid]
        self.async_write_ha_state()


async def exercise_calendar(hass, entry, fixture, client, report, case):
    domain = "polish_context_voice"
    runtime = hass.data[domain][entry.entry_id]
    diary = EditableCalendar()
    await hass.data["calendar"].async_add_entities([diary])
    registry = er.async_get(hass)
    registry.async_update_entity(diary.entity_id, aliases={"rodzinny test", "testowy"})
    exposed_entities.async_expose_entity(hass, "conversation", diary.entity_id, True)
    original = dict(runtime.calendar_settings)
    runtime.calendar_settings = {
        **original,
        "calendar_default": diary.entity_id,
        "calendar_write_enabled": True,
    }
    await hass.async_block_till_done()

    async def command(text, **kwargs):
        result = await client.call(domain + "/process", text=text, dry_run=False, **kwargs)
        await hass.async_block_till_done()
        return result

    async def confirm(first, text="tak", **kwargs):
        assert first["status"] == "confirm", first
        return await command(text, conversation_id=first["conversation_id"], **kwargs)

    try:
        with case(report, "calendar_preview_is_pure_no_provider_or_kiosk_actions"):
            before = list(diary.operations)
            result = await client.call(
                domain + "/process", text="dodaj Dentystę jutro o 15 na godzinę", dry_run=True
            )
            assert result["status"] == "dry_run" and not diary.events, result
            assert result["risk"] == "medium", result
            assert diary.operations == before

        with case(report, "calendar_polish_add_confirm_and_read_native_api"):
            first = await command("dodaj Dentystę na jutro o piętnastej, na godzinę")
            assert "na godzinę" in first["speech"] and not diary.events, first
            assert "jutro o piętnastej" in first["speech"] and "15:00" not in first["speech"]
            done = await confirm(first, "dobrze")
            assert done["status"] == "executed" and len(diary.events) == 1, done
            assert done["speech"] == "Wydarzenie jest już w kalendarzu.", done
            assert diary.events[0].summary == "Dentystę"
            assert diary.events[0].start.hour == 15
            result = await command("co mamy jutro w kalendarzu")
            assert result["status"] == "answered" and "Dentystę" in result["speech"], result

        with case(report, "calendar_confirmation_is_single_use_and_source_bound"):
            before = len(diary.events)
            await command("tak", conversation_id=first["conversation_id"])
            assert len(diary.events) == before
            first = await command("dodaj Trening jutro o 17", source_area="kitchen")
            wrong = await command(
                "tak", conversation_id=first["conversation_id"], source_area="living_room"
            )
            assert wrong["status"] != "executed" and len(diary.events) == before, wrong
            done = await confirm(first, source_area="kitchen")
            assert done["status"] == "executed", done

        with case(report, "calendar_move_keeps_duration_and_cancels_only_selected_event"):
            first = await command("przełóż jutrzejszego dentystę na szesnastą")
            done = await confirm(first, "potwierdzam")
            assert done["status"] == "executed", done
            assert done["speech"] == "Termin wydarzenia został zmieniony.", done
            event = next(e for e in diary.events if e.summary == "Dentystę")
            assert event.start.hour == 16 and event.end - event.start == timedelta(hours=1)
            first = await command("odwołaj dentystę jutro")
            done = await confirm(first, "okej")
            assert done["status"] == "executed", done
            assert done["speech"] == "Wydarzenie zostało odwołane.", done
            assert [e.summary for e in diary.events] == ["Trening"]

        with case(report, "calendar_missing_hour_followup_then_rejection_does_not_write"):
            before = len(diary.events)
            question = await command("dodaj Badanie jutro")
            assert question["continue_conversation"] and "godzinie" in question["speech"], question
            first = await command("o piętnastej", conversation_id=question["conversation_id"])
            assert first["status"] == "confirm", first
            result = await command("nie", conversation_id=first["conversation_id"])
            assert result["status"] == "cancelled" and len(diary.events) == before, result

        with case(report, "calendar_permission_exposure_and_write_switch_rechecked_on_confirm"):
            before = len(diary.events)
            first = await command("dodaj Ukryte jutro o 14")
            exposed_entities.async_expose_entity(hass, "conversation", diary.entity_id, False)
            await hass.async_block_till_done()
            done = await confirm(first)
            assert done["status"] == "blocked" and len(diary.events) == before, done
            exposed_entities.async_expose_entity(hass, "conversation", diary.entity_id, True)
            await hass.async_block_till_done()
            first = await command("dodaj Wyłączone jutro o 14")
            runtime.calendar_settings["calendar_write_enabled"] = False
            done = await confirm(first)
            assert done["status"] == "blocked" and len(diary.events) == before, done
            runtime.calendar_settings["calendar_write_enabled"] = True

        with case(report, "calendar_stale_event_is_not_moved_after_confirmation"):
            first = await command("przełóż trening jutro na jutro o 18")
            old = diary.events[0]
            diary.events[0] = replace(old, summary="Zmieniony trening")
            done = await confirm(first)
            assert done["reason"] == "calendar_changed" and diary.events[0].start.hour == 17, done
            diary.events[0] = old

        with case(report, "calendar_ambiguity_requires_selection_not_first_match"):
            old = diary.events[0]
            diary.events.append(
                replace(
                    old,
                    uid=uuid4().hex,
                    start=old.start + timedelta(hours=2),
                    end=old.end + timedelta(hours=2),
                )
            )
            question = await command("odwołaj trening jutro")
            assert question["continue_conversation"] and question["status"] == "clarify", question
            cid = question["conversation_id"]
            pending = next(turn for key, turn in runtime.pending_calendars.items() if key[1] == cid)
            invalid = await command("drugie да", conversation_id=cid)
            assert invalid["status"] == "clarify" and invalid["continue_conversation"], invalid
            assert pending is next(
                turn for key, turn in runtime.pending_calendars.items() if key[1] == cid
            )
            first = await command("Nie, to drugie", conversation_id=cid)
            assert first["status"] == "confirm" and len(diary.events) == 2, first
            done = await confirm(first)
            assert done["status"] == "executed" and len(diary.events) == 1, done
            assert diary.events[0].start.hour == 17

        with case(report, "calendar_expiry_and_changed_default_never_commit"):
            before = len(diary.events)
            first = await command("dodaj Wygasłe jutro o 14")
            next(
                turn for turn in runtime.pending_calendars.values() if turn.stage == "confirm"
            ).expires_at = 0
            result = await command("tak", conversation_id=first["conversation_id"])
            assert result["status"] != "executed" and len(diary.events) == before, result
            first = await command("dodaj Inny kalendarz jutro o 14")
            runtime.calendar_settings["calendar_default"] = "calendar.lab_calendar"
            result = await confirm(first)
            assert result["reason"] == "calendar_changed" and len(diary.events) == before, result
            runtime.calendar_settings["calendar_default"] = diary.entity_id

        with case(report, "calendar_read_only_user_can_read_but_cannot_modify"):
            from custom_components.polish_context_voice.core.types import Request

            reader = await hass.auth.async_create_user(
                "Calendar reader", group_ids=[GROUP_ID_READ_ONLY]
            )
            context = Context(user_id=reader.id)
            before = list(diary.operations)
            result = await runtime.process(
                Request("co mamy jutro w kalendarzu", user_id=reader.id), context
            )
            assert result.status == "answered", result
            result = await runtime.process(
                Request("dodaj Ukryty zapis jutro o 14", user_id=reader.id), context
            )
            assert result.status == "blocked" and diary.operations == before, result

        with case(report, "calendar_all_day_and_recurring_master_are_explicit"):
            first = await command("dodaj Wyjazd jutro cały dzień")
            done = await confirm(first)
            assert done["status"] == "executed", done
            event = next(e for e in diary.events if e.summary == "Wyjazd")
            assert not isinstance(event.start, datetime) and event.end - event.start == timedelta(
                days=1
            )
            old = diary.events[0]
            diary.events[0] = replace(old, rrule="FREQ=DAILY")
            result = await command("odwołaj trening jutro")
            assert result["reason"] == "calendar_recurrence", result
            diary.events[0] = old

        with case(report, "calendar_default_selection_and_kiosk_forms_have_native_back"):
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            assert "calendar" in flow["menu_options"] and "calendar_kiosk" in flow["menu_options"]
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], user_input={"next_step_id": "calendar"}
            )
            assert flow["type"] == "form" and "pcv_navigation" in flow["description_placeholders"]
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], user_input={"back": True}
            )
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], user_input={"next_step_id": "calendar_kiosk"}
            )
            assert flow["type"] == "form" and "mappings" in flow["description_placeholders"]
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], user_input={"back": True}
            )
            assert flow["type"] == "menu"

        with case(report, "calendar_form_saves_preferences_and_preserves_other_options"):
            # Invoke the public form step on a handler, avoiding a live entry reload mid-scenario.
            from custom_components.polish_context_voice.config_flow import OptionsFlow

            handler = OptionsFlow()
            handler.hass = hass
            handler.handler = entry.entry_id
            flow = await handler.async_step_calendar(
                {
                    "calendar_default": diary.entity_id,
                    "calendar_write_enabled": True,
                    "calendar_default_duration": 45,
                }
            )
            assert flow["data"]["calendar_default"] == diary.entity_id
            assert flow["data"]["calendar_default_duration"] == 45

        with case(report, "calendar_kiosk_only_named_show_event_no_calendar_write_broadcast"):
            satellite = fixture["satellite"]
            exposed_entities.async_expose_entity(hass, "conversation", satellite, True)
            runtime.calendar_settings["calendar_kiosks"] = {satellite: "Unique Lab Kiosk"}
            await hass.async_block_till_done()
            received = []

            @callback
            def kiosk_peer(event):
                received.append(dict(event.data))
                hass.bus.async_fire(
                    "kiosk_satellite_calendar_result",
                    {
                        "id": event.data["id"],
                        "ok": True,
                        "kiosk": "Unique Lab Kiosk",
                        "result": "shown",
                    },
                )

            off = hass.bus.async_listen("kiosk_satellite_calendar", kiosk_peer)
            try:
                before = list(diary.operations)
                result = await command(
                    "pokaż kalendarz na przyszły tydzień", satellite_id=satellite
                )
                assert result["status"] == "executed" and len(received) == 1, result
                assert (
                    received[0]["action"] == "show" and received[0]["kiosk"] == "Unique Lab Kiosk"
                )
                assert received[0]["view"] == "week" and diary.operations == before
                result = await client.call(
                    domain + "/process",
                    text="pokaż kalendarz",
                    satellite_id=satellite,
                    dry_run=True,
                )
                assert result["status"] == "dry_run" and len(received) == 1, result
            finally:
                off()

        report["calendar_scope"] = (
            "Native HA CalendarEntity and config flows. Kiosk event peer is simulated; "
            "no Android device, external calendar provider, audio or N5105 measurement."
        )
    finally:
        runtime.pending_calendars.clear()
        runtime.calendar_settings = original
