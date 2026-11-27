"""Native HA regressions from the b2 calendar trials, using synthetic events only."""

from dataclasses import replace
from datetime import timedelta

from homeassistant.auth.models import Group
from homeassistant.components import calendar
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .calendar_scenarios import EditableCalendar


class RegressionCalendar(EditableCalendar):
    """An editable calendar with counted reads and explicit provider failure modes."""

    def __init__(self, suffix):
        super().__init__()
        self._attr_name = f"Lab Calendar Regression {suffix}"
        self._attr_unique_id = f"lab_calendar_regression_{suffix}"
        self._attr_available = True
        self.reads = 0
        self.fail_reads = False

    async def async_get_events(self, hass, start_date, end_date):
        self.reads += 1
        if self.fail_reads:
            raise HomeAssistantError("Synthetic calendar provider failure")
        return await super().async_get_events(hass, start_date, end_date)


async def exercise_calendar_regressions(hass, entry, fixture, client, report, case):
    """Exercise aggregation, confirmations and provider capabilities on actual HA."""
    from custom_components.polish_context_voice.core.types import Request

    domain = "polish_context_voice"
    runtime = hass.data[domain][entry.entry_id]
    component = hass.data["calendar"]
    registry = er.async_get(hass)
    original_settings = dict(runtime.calendar_settings)
    original_pending = dict(runtime.pending_calendars)
    original_exposure = {
        entity_id: exposed_entities.async_should_expose(hass, "conversation", entity_id)
        for entity_id in hass.states.async_entity_ids("calendar")
    }
    diaries = [
        RegressionCalendar(suffix) for suffix in ("alpha", "beta", "gamma", "hidden", "blocked")
    ]
    alpha, beta, gamma, hidden, blocked = diaries
    users = []

    async def command(text, **kwargs):
        result = await client.call(domain + "/process", text=text, dry_run=False, **kwargs)
        await hass.async_block_till_done()
        return result

    async def confirm(first, text="tak", **kwargs):
        assert first["status"] == "confirm", first
        return await command(text, conversation_id=first["conversation_id"], **kwargs)

    def pending(conversation_id):
        return next(
            turn for key, turn in runtime.pending_calendars.items() if key[1] == conversation_id
        )

    try:
        # Isolate these cases from calendars left by earlier native scenarios.
        for entity_id in original_exposure:
            exposed_entities.async_expose_entity(hass, "conversation", entity_id, False)
        await component.async_add_entities(diaries)
        for diary in diaries:
            exposed_entities.async_expose_entity(
                hass, "conversation", diary.entity_id, diary is not hidden
            )
        registry.async_update_entity(alpha.entity_id, aliases={"próba alfa"})
        registry.async_update_entity(beta.entity_id, aliases={"próba beta", "O nas"})
        registry.async_update_entity(blocked.entity_id, labels={"voice_block"})
        runtime.calendar_settings = {
            **original_settings,
            "calendar_default": alpha.entity_id,
            "calendar_write_enabled": True,
        }
        await hass.async_block_till_done()

        tomorrow = (dt_util.now() + timedelta(days=1)).replace(
            hour=11, minute=0, second=0, microsecond=0
        )
        shared = calendar.CalendarEvent(
            tomorrow, tomorrow + timedelta(hours=1), "Próba wspólna", uid="shared-lab-event"
        )
        alpha.events = [shared]
        beta.events = [replace(shared)]
        gamma.events = [
            replace(
                shared,
                uid="earlier-lab-event",
                summary="Plan jutra",
                start=tomorrow - timedelta(hours=2),
                end=tomorrow - timedelta(hours=1),
            )
        ]
        hidden.events = [replace(shared, uid="hidden-lab-event", summary="Ukryta próba")]
        blocked.events = [replace(shared, uid="blocked-lab-event", summary="Zablokowana próba")]
        for diary in diaries:
            diary.async_write_ha_state()
        await hass.async_block_till_done()

        with case(report, "calendar_overview_reads_three_sources_without_deduplicating_events"):
            result = await command("co mamy jutro w kalendarzu")
            assert result["status"] == "answered" and result["risk"] == "low", result
            data = result["response_data"]
            rows = data["events"]
            assert data["total"] == 3 and len(rows) == 3, result
            assert set(data["calendars_read"]) == {
                alpha.entity_id,
                beta.entity_id,
                gamma.entity_id,
            }, result
            assert rows[0]["title"] == "Plan jutra", result
            assert {row["calendar_id"] for row in rows} == set(data["calendars_read"]), result
            assert all(row["calendar_name"] for row in rows), result
            assert sum(row["title"] == "Próba wspólna" for row in rows) == 2, result
            assert "Jutro o dziewiątej" in result["speech"], result
            assert result["speech"].count("Próba wspólna") == 2, result
            assert "kalendarz" not in result["speech"].lower(), result
            assert all(diary.name not in result["speech"] for diary in diaries), result
            assert data["failed_calendars"] == 0 and data["partial"] is False, result
            assert hidden.reads == 0 and blocked.reads == 0

        with case(report, "calendar_natural_plan_questions_select_local_today_or_tomorrow"):
            today_event = replace(
                shared,
                uid="today-overview-event",
                summary="Plan dzisiaj",
                start=tomorrow - timedelta(days=1),
                end=tomorrow - timedelta(days=1) + timedelta(hours=1),
            )
            alpha.events.append(today_event)
            before = [list(diary.operations) for diary in diaries]
            today_questions = (
                "Co jest w moim kalendarzu?",
                "Jakie mam dzisiaj plany?",
                "Jaki jest plan na dziś?",
                "Co jest w planie na dziś?",
                "Co mam zaplanowane?",
            )
            tomorrow_questions = (
                "Jakie mam jutro plany?",
                "Jaki jest plan na jutro?",
                "Co jest w moim kalendarzu jutro?",
                "Co mam zaplanowane na jutro?",
            )
            try:
                for text, expected in (
                    *((text, 1) for text in today_questions),
                    *((text, 3) for text in tomorrow_questions),
                ):
                    result = await command(text)
                    assert result["status"] == "answered" and result["route"] == "ha_calendar", (
                        text,
                        result,
                    )
                    data = result["response_data"]
                    assert data["total"] == expected, (text, result)
                    assert set(data["calendars_read"]) == {
                        alpha.entity_id,
                        beta.entity_id,
                        gamma.entity_id,
                    }, result
                    titles = [row["title"] for row in data["events"]]
                    assert ("Plan dzisiaj" in titles) is (expected == 1), (text, result)
                    assert [diary.operations for diary in diaries] == before
                voice = await hass.services.async_call(
                    "conversation",
                    "process",
                    {
                        "agent_id": "conversation.ha_polish_context_voice",
                        "language": "pl",
                        "text": "Jaki jest plan na jutro?",
                    },
                    blocking=True,
                    return_response=True,
                )
                assert voice["response"]["response_type"] == "query_answer", voice
                assert "Plan jutra" in voice["response"]["speech"]["plain"]["speech"], voice
                assert [diary.operations for diary in diaries] == before
            finally:
                alpha.events.remove(today_event)

        with case(report, "calendar_plan_question_honors_named_and_unknown_calendar"):
            before = [diary.reads for diary in diaries]
            result = await command("Jakie mam jutro plany w kalendarzu próba beta?")
            assert result["status"] == "answered", result
            assert result["response_data"]["calendars_read"] == [beta.entity_id], result
            assert [diary.reads for diary in diaries] == [before[0], before[1] + 1, *before[2:]]
            result = await command("Co jest w kalendarzu O nas?")
            assert result["status"] == "answered", result
            assert result["response_data"]["calendars_read"] == [beta.entity_id], result
            before = [diary.reads for diary in diaries]
            result = await command("Jakie mam jutro plany w kalendarzu Nieznany testowy?")
            assert result["reason"] == "calendar_no_targets", result
            assert [diary.reads for diary in diaries] == before

        with case(report, "calendar_plan_question_does_not_discard_a_different_source_proposal"):
            first = await command("dodaj Próba planu jutro o 15", source_area="kitchen")
            assert first["status"] == "confirm", first
            turn = pending(first["conversation_id"])
            before = list(alpha.operations)
            result = await command(
                "Jaki jest plan na jutro?",
                conversation_id=first["conversation_id"],
                source_area="living_room",
            )
            assert result["status"] == "answered", result
            assert pending(first["conversation_id"]) is turn
            assert alpha.operations == before
            result = await command(
                "Co jest w moim kalendarzu?",
                conversation_id=first["conversation_id"],
                source_area="kitchen",
            )
            assert result["status"] == "answered", result
            result = await confirm(first, "Potwierdzam", source_area="kitchen")
            assert result["status"] != "executed" and alpha.operations == before, result

        with case(report, "calendar_invalid_plan_period_never_defaults_to_today_or_executes_tail"):
            before_reads = [diary.reads for diary in diaries]
            before_operations = [list(diary.operations) for diary in diaries]
            for text in (
                "Jaki jest plan na dziś i wyłącz światła?",
                "Co jest w moim kalendarzu za dwa dni?",
                "Jaki jest plan na 31.02.2026?",
            ):
                result = await command(text)
                assert result["status"] == "clarify" and result["route"] == "ha_calendar", (
                    text,
                    result,
                )
                assert not result["calls"] and not result["completed"], result
                assert [diary.reads for diary in diaries] == before_reads
                assert [diary.operations for diary in diaries] == before_operations

        with case(report, "calendar_explicit_alias_limits_read_to_named_calendar"):
            before = [diary.reads for diary in diaries]
            result = await command("co mamy jutro w kalendarzu próba beta")
            assert result["status"] == "answered", result
            assert result["response_data"]["calendars_read"] == [beta.entity_id], result
            assert result["response_data"]["total"] == 1, result
            assert [diary.reads for diary in diaries] == [
                before[0],
                before[1] + 1,
                *before[2:],
            ]

        with case(report, "calendar_overview_respects_native_read_permissions_and_voice_access"):
            reader = await hass.auth.async_create_user("Calendar regression restricted reader")
            users.append(reader)
            reader.groups = [
                Group(
                    "Only alpha may be read",
                    {"entities": {"entity_ids": {alpha.entity_id: {"read": True}}}},
                )
            ]
            before = [diary.reads for diary in diaries]
            result = await runtime.process(
                Request("co mamy jutro w kalendarzu", user_id=reader.id),
                Context(user_id=reader.id),
            )
            assert result.status == "answered", result
            assert result.response_data["calendars_read"] == [alpha.entity_id], result
            assert result.response_data["total"] == 1, result
            for diary in (beta, gamma, hidden, blocked):
                assert diary.entity_id not in str(result.to_dict()), result
            assert [diary.reads for diary in diaries] == [before[0] + 1, *before[1:]]

        with case(report, "calendar_overview_reports_partial_provider_failure"):
            beta.fail_reads = True
            try:
                result = await command("co mamy jutro w kalendarzu")
                assert result["status"] == "answered", result
                data = result["response_data"]
                assert data["total"] == 2 and data["failed_calendars"] == 1, result
                assert data["partial"] is True, result
                assert set(data["calendars_read"]) == {alpha.entity_id, gamma.entity_id}, result
            finally:
                beta.fail_reads = False

        with case(report, "calendar_overview_reports_unavailable_source_without_calling_it"):
            beta._attr_available = False
            beta.async_write_ha_state()
            await hass.async_block_till_done()
            before = beta.reads
            try:
                result = await command("co mamy jutro w kalendarzu")
                assert result["status"] == "answered", result
                data = result["response_data"]
                assert data["total"] == 2 and data["failed_calendars"] == 1, result
                assert data["partial"] is True and beta.reads == before, result
            finally:
                beta._attr_available = True
                beta.async_write_ha_state()
                await hass.async_block_till_done()

        with case(report, "calendar_overview_all_providers_failed_never_claims_no_events"):
            for diary in (alpha, beta, gamma):
                diary.fail_reads = True
            try:
                result = await command("co mamy jutro w kalendarzu")
                assert result["status"] == "error" and result["reason"] == "calendar_provider", (
                    result
                )
                assert "Nie ma pasujących wydarzeń" not in result["speech"], result
            finally:
                for diary in (alpha, beta, gamma):
                    diary.fail_reads = False

        with case(report, "calendar_overview_preview_never_reads_or_mutates_providers"):
            before = [(diary.reads, list(diary.operations)) for diary in diaries]
            result = await client.call(
                domain + "/process", text="co mamy jutro w kalendarzu", dry_run=True
            )
            assert result["status"] == "dry_run" and result["risk"] == "low", result
            assert [(diary.reads, diary.operations) for diary in diaries] == before

        with case(report, "calendar_unknown_script_confirmation_retries_without_extending_expiry"):
            first = await command("dodaj Próba zapisu jutro o 15 na godzinę")
            turn = pending(first["conversation_id"])
            deadline = turn.expires_at
            before = list(alpha.operations)
            for text in ("Так", "tаk", "tak да"):
                result = await command(text, conversation_id=first["conversation_id"])
                assert result["status"] == "clarify", result
                assert result["reason"] == "calendar_confirmation_unclear", result
                assert result["continue_conversation"] is True, result
                assert pending(first["conversation_id"]) is turn
                assert turn.expires_at == deadline and alpha.operations == before
            done = await confirm(first, "Tak")
            assert done["status"] == "executed" and len(alpha.operations) == len(before) + 1, done
            await command("Tak", conversation_id=first["conversation_id"])
            assert len(alpha.operations) == len(before) + 1

        with case(report, "calendar_unclear_confirmation_rechecks_access_before_repeating_title"):
            before = list(alpha.operations)
            title = "Próba ponownego dostępu"
            for revoked in ("exposure", "write_switch", "default"):
                first = await command(f"dodaj {title} jutro o 15")
                assert first["status"] == "confirm", first
                if revoked == "exposure":
                    exposed_entities.async_expose_entity(
                        hass, "conversation", alpha.entity_id, False
                    )
                    await hass.async_block_till_done()
                elif revoked == "write_switch":
                    runtime.calendar_settings["calendar_write_enabled"] = False
                else:
                    runtime.calendar_settings["calendar_default"] = beta.entity_id
                try:
                    result = await command("Так", conversation_id=first["conversation_id"])
                    if revoked == "default":
                        assert result["reason"] == "calendar_changed", result
                    else:
                        assert result["status"] == "blocked", result
                    assert title not in result["speech"], result
                    assert result["continue_conversation"] is False, result
                    assert alpha.operations == before
                    assert not any(
                        key[1] == first["conversation_id"] for key in runtime.pending_calendars
                    )
                finally:
                    exposed_entities.async_expose_entity(
                        hass, "conversation", alpha.entity_id, True
                    )
                    runtime.calendar_settings["calendar_write_enabled"] = True
                    runtime.calendar_settings["calendar_default"] = alpha.entity_id
                    await hass.async_block_till_done()

        with case(report, "calendar_unclear_confirmation_cannot_cross_source_or_outlive_expiry"):
            before = list(alpha.operations)
            first = await command("dodaj Próba granic jutro o 15", source_area="kitchen")
            result = await command(
                "Так", conversation_id=first["conversation_id"], source_area="kitchen"
            )
            assert result["reason"] == "calendar_confirmation_unclear", result
            turn = pending(first["conversation_id"])
            result = await command(
                "Tak", conversation_id=first["conversation_id"], source_area="living_room"
            )
            assert result["status"] != "executed" and alpha.operations == before, result
            assert pending(first["conversation_id"]) is turn
            turn.expires_at = 0
            result = await confirm(first, "Tak", source_area="kitchen")
            assert result["status"] != "executed" and alpha.operations == before, result
            assert "Minął czas na potwierdzenie" in result["speech"], result

        with case(report, "calendar_unclear_confirmation_rejection_or_new_command_discards_draft"):
            before = list(alpha.operations)
            first = await command("dodaj Próba odmowy jutro o 15")
            await command("Так", conversation_id=first["conversation_id"])
            result = await command("nie", conversation_id=first["conversation_id"])
            assert result["status"] == "cancelled" and alpha.operations == before, result
            assert result["speech"] == "Wydarzenie nie zostało dodane.", result
            first = await command("dodaj Próba nowego pytania jutro o 15")
            await command("Так", conversation_id=first["conversation_id"])
            await command("co mamy jutro w kalendarzu", conversation_id=first["conversation_id"])
            result = await confirm(first)
            assert result["status"] != "executed" and alpha.operations == before, result

        with case(report, "calendar_missing_update_feature_still_allows_native_create_and_delete"):
            original_features = alpha._attr_supported_features
            alpha._attr_supported_features = (
                calendar.CalendarEntityFeature.CREATE_EVENT
                | calendar.CalendarEntityFeature.DELETE_EVENT
            )
            alpha.async_write_ha_state()
            await hass.async_block_till_done()
            before = list(alpha.operations)
            try:
                result = await command("przełóż Próbę wspólną jutro na jutro o 16")
                assert result["status"] == "blocked", result
                assert result["reason"] == "calendar_operation_unsupported", result
                assert alpha.operations == before
                first = await command("dodaj Próba funkcji jutro o 17")
                done = await confirm(first)
                assert done["status"] == "executed", done
                first = await command("odwołaj Próba funkcji jutro")
                done = await confirm(first)
                assert done["status"] == "executed", done
                assert [operation for operation, _ in alpha.operations[len(before) :]] == [
                    "add",
                    "cancel",
                ]
            finally:
                alpha._attr_supported_features = original_features
                alpha.async_write_ha_state()
                await hass.async_block_till_done()

        with case(report, "calendar_date_before_title_add_confirm_cancel_lifecycle"):
            first = await command("dodaj badanie jutro o piętnastej")
            done = await confirm(first)
            assert done["status"] == "executed", done
            event = next(e for e in alpha.events if e.summary == "badanie")
            first = await command("odwołaj jutro badanie")
            done = await confirm(first, "Dobrze")
            assert done["status"] == "executed", done
            assert all(e.uid != event.uid for e in alpha.events)
            assert all(operation != "change" for operation, _ in alpha.operations)

        with case(report, "calendar_repeated_affirmative_creates_one_event_and_is_single_use"):
            first = await command("dodaj Próba powtórzenia jutro o 15")
            before = list(alpha.operations)
            done = await confirm(first, "Tak. Tak")
            assert done["status"] == "executed", done
            assert len(alpha.operations) == len(before) + 1
            again = await command("Tak. Tak", conversation_id=first["conversation_id"])
            assert again["status"] != "executed", again
            assert len(alpha.operations) == len(before) + 1

        with case(report, "calendar_mixed_or_qualified_affirmation_never_authorizes_a_write"):
            first = await command("dodaj Próba niejasnej zgody jutro o 15")
            before = list(alpha.operations)
            turn = pending(first["conversation_id"])
            deadline = turn.expires_at
            for reply in ("tak, nie", "tak ale zmień godzinę", "tak tak tak tak"):
                result = await command(reply, conversation_id=first["conversation_id"])
                assert result["status"] != "executed", result
                assert alpha.operations == before
                assert pending(first["conversation_id"]).expires_at == deadline
            await command("nie", conversation_id=first["conversation_id"])

        with case(report, "calendar_declining_delete_explains_the_event_remains"):
            event = next(e for e in alpha.events if e.summary == "Próba powtórzenia")
            first = await command("odwołaj jutro Próba powtórzenia")
            assert "o piętnastej" in first["speech"] and "15:00" not in first["speech"]
            before = list(alpha.operations)
            result = await command("Nie", conversation_id=first["conversation_id"])
            assert result["status"] == "cancelled", result
            assert result["speech"] == "Wydarzenie zostaje w kalendarzu."
            assert any(e.uid == event.uid for e in alpha.events)
            assert alpha.operations == before

        with case(report, "calendar_declining_move_explains_that_the_time_was_not_changed"):
            first = await command("przełóż Próba powtórzenia jutro na jutro o 16")
            assert first["status"] == "confirm", first
            before = list(alpha.operations)
            result = await command("Nie", conversation_id=first["conversation_id"])
            assert result["speech"] == "Termin wydarzenia pozostaje bez zmian.", result
            assert alpha.operations == before

        with case(report, "calendar_spoken_duration_and_all_day_keep_exact_calendar_values"):
            first = await command("dodaj Próba długości jutro o 15:30 na dziewięćdziesiąt minut")
            assert first["status"] == "confirm", first
            assert "o piętnastej trzydzieści, na godzinę i trzydzieści minut" in first["speech"]
            done = await confirm(first)
            assert done["status"] == "executed", done
            event = next(e for e in alpha.events if e.summary == "Próba długości")
            assert event.start.hour == 15 and event.start.minute == 30
            assert event.end - event.start == timedelta(minutes=90)
            first = await command("dodaj Próba całodniowa jutro cały dzień")
            assert first["status"] == "confirm", first
            assert "jutro, przez cały dzień" in first["speech"]
            assert "północy" not in first["speech"]
            done = await confirm(first)
            assert done["status"] == "executed", done

        report["calendar_regression_scope"] = (
            "Synthetic native HA calendars: multi-calendar reads, real HA read permissions, "
            "partial provider errors, source-bound confirmations and operation feature flags. "
            "No external provider, physical Kiosk, audio or real household events."
        )
    finally:
        runtime.calendar_settings = original_settings
        runtime.pending_calendars.clear()
        runtime.pending_calendars.update(original_pending)
        for diary in diaries:
            if diary.entity_id:
                await component.async_remove_entity(diary.entity_id)
                if registry.async_get(diary.entity_id):
                    registry.async_remove(diary.entity_id)
        for entity_id, exposed in original_exposure.items():
            exposed_entities.async_expose_entity(hass, "conversation", entity_id, exposed)
        for user in users:
            await hass.auth.async_remove_user(user)
        await hass.async_block_till_done()
