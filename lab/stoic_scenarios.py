"""S01 against synthetic native HA state, exposure, permissions and Assist text."""

from calendar import isleap
from datetime import timedelta

from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

DOMAIN = "polish_context_voice"


async def exercise_stoic(hass, entry, client, agent, user, report, case):
    from custom_components.polish_context_voice.core.types import Request

    runtime = hass.data[DOMAIN][entry.entry_id]
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create(
        "sensor", "daily_stoic", "lab_stoic_reading", suggested_object_id="lab_stoic_reading"
    )
    original_settings = runtime.stoic_settings
    runtime.stoic_settings = {**original_settings, "stoic_entity_id": sensor.entity_id}
    attributes = {
        "quote": "Laboratoryjny cytat po polsku.",
        "author_and_source": "Autor laboratoryjny",
        "title": "Tytuł laboratoryjny",
        "commentary": "Pełne laboratoryjne omówienie.",
        "date": dt_util.now().date().isoformat(),
    }
    hass.states.async_set(sensor.entity_id, "medytacja", attributes)
    exposed_entities.async_expose_entity(hass, "conversation", sensor.entity_id, True)
    restricted = None
    calls = []

    @callback
    def capture(event):
        calls.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    async def command(text, *, conversation_id="stoic_native", dry_run=False):
        result = await client.call(
            DOMAIN + "/process", text=text, conversation_id=conversation_id, dry_run=dry_run
        )
        await hass.async_block_till_done()
        assert not result["calls"] and not result["completed"] and not calls, result
        return result

    async def assist(text):
        result = await client.call(
            "conversation/process", agent_id=agent, language="pl", text=text
        )
        await hass.async_block_till_done()
        assert result["response"]["response_type"] == "query_answer", result
        assert not result["continue_conversation"] and not calls, result
        return result["response"]["speech"]["plain"]["speech"]

    try:
        await hass.async_block_till_done()
        calls.clear()
        with case(report, "stoic_native_sensor_reads_all_existing_sections_without_actions"):
            result = await command("Przeczytaj dzisiejszą medytację")
            assert result["status"] == "answered" and result["route"] == "stoic", result
            for key in ("quote", "author_and_source", "title", "commentary"):
                assert attributes[key] in result["speech"], result
            assert result["response_data"]["date_status"] == "current", result
            assert result["targets"] == [sensor.entity_id], result

        with case(report, "stoic_native_assist_accepts_today_meditation_and_quote_variants"):
            for utterance in (
                "Jaka jest dzisiejsza medytacja",
                "Jaka jest dzisiejsza medytacja?",
                "medytacja na dziś",
            ):
                speech = await assist(utterance)
                assert all(attributes[key] in speech for key in ("quote", "title", "commentary"))
            for utterance in ("dzisiejszy cytat", "cytat na dziś"):
                speech = await assist(utterance)
                assert attributes["quote"] in speech and attributes["author_and_source"] in speech
                assert attributes["commentary"] not in speech

        with case(report, "stoic_native_followup_snapshot_and_explicit_today_use_correct_entry"):
            await command("Przeczytaj cytat")
            changed = {**attributes, "commentary": "Nowe laboratoryjne omówienie."}
            hass.states.async_set(sensor.entity_id, "medytacja", changed)
            await hass.async_block_till_done()
            result = await command("A omówienie?")
            assert result["speech"] == attributes["commentary"], result
            assert result["response_data"]["snapshot"], result
            fresh = await command("Przeczytaj dzisiejsze omówienie")
            assert fresh["speech"] == changed["commentary"], fresh
            assert not fresh["response_data"]["snapshot"], fresh
            other = await command("A omówienie?", conversation_id="stoic_other")
            assert other["reason"] == "stoic_no_reading_context", other

        with case(report, "stoic_native_missing_commentary_reports_exact_missing_section"):
            missing = {key: value for key, value in attributes.items() if key != "commentary"}
            hass.states.async_set(sensor.entity_id, "medytacja", missing)
            await hass.async_block_till_done()
            result = await command("Przeczytaj dzisiejsze omówienie")
            assert result["reason"] == "stoic_missing_commentary", result
            assert "Omówienie nie jest dostępne" in result["speech"], result

        with case(report, "stoic_native_unavailable_stale_and_hidden_source_do_not_disclose_text"):
            hass.states.async_set(sensor.entity_id, "unavailable", attributes)
            await hass.async_block_till_done()
            result = await command("Przeczytaj cytat")
            assert result["reason"] == "stoic_unavailable", result
            stale = {
                **attributes,
                "date": (dt_util.now().date() - timedelta(days=1)).isoformat(),
            }
            hass.states.async_set(sensor.entity_id, "medytacja", stale)
            await hass.async_block_till_done()
            result = await command("Przeczytaj cytat na dziś")
            assert result["reason"] == "stoic_stale", result
            hass.states.async_set(sensor.entity_id, "medytacja", attributes)
            exposed_entities.async_expose_entity(hass, "conversation", sensor.entity_id, False)
            await hass.async_block_till_done()
            result = await command("Przeczytaj cytat")
            assert result["status"] == "blocked" and not result["targets"], result
            assert attributes["quote"] not in result["speech"], result
            exposed_entities.async_expose_entity(hass, "conversation", sensor.entity_id, True)
            await hass.async_block_till_done()

        with case(report, "stoic_native_restricted_ha_reader_cannot_read_exposed_private_sensor"):
            restricted = await hass.auth.async_create_user("Stoic restricted reader")
            restricted.groups = [Group("No Stoic read permission", {"entities": {}})]
            assert not restricted.permissions.check_entity(sensor.entity_id, POLICY_READ)
            result = await runtime.process(
                Request(
                    "Przeczytaj cytat", user_id=restricted.id, conversation_id="stoic_restricted"
                ),
                Context(user_id=restricted.id),
            )
            assert result.status == "blocked" and not result.targets and not result.calls, result
            assert attributes["quote"] not in result.speech and not calls, result

        with case(report, "stoic_native_conversation_reports_plain_query_without_generated_text"):
            result = await client.call(
                "conversation/process", agent_id=agent, language="pl", text="Przeczytaj cytat"
            )
            await hass.async_block_till_done()
            response = result["response"]
            assert response["response_type"] == "query_answer", result
            assert response["speech"]["plain"]["speech"] == (
                attributes["quote"] + " " + attributes["author_and_source"] + "."
            ), result
            assert not result["continue_conversation"] and not calls, result

        with case(report, "stoic_native_user_ordinal_contract_returns_full_commentary_in_assist"):
            today = dt_util.now().date()
            user_attributes = {
                **attributes,
                "date": "Data po polsku",
                "day_of_year": today.timetuple().tm_yday,
                "is_leap_year": isleap(today.year),
                "author": "Autor",
                "source": "Źródło",
            }
            hass.states.async_set(sensor.entity_id, "medytacja", user_attributes)
            await hass.async_block_till_done()
            result = await command("Przeczytaj dzisiejsze omówienie stoica")
            assert result["speech"] == attributes["commentary"], result
            assert result["response_data"]["date_status"] == "current", result
            assert not calls
            hass.states.async_set(
                sensor.entity_id,
                "medytacja",
                {
                    **user_attributes,
                    "day_of_year": (today - timedelta(days=1)).timetuple().tm_yday,
                },
            )
            await hass.async_block_till_done()
            result = await command("Przeczytaj dzisiejsze omówienie stoica")
            assert result["reason"] == "stoic_stale" and not calls, result

        report["stoic_reading"] = {
            "synthetic_native_ha_states_only": True,
            "sensor_exposure_read_permissions_and_availability_checked": True,
            "no_service_or_llm_calls": True,
            "existing_quote_title_and_commentary_used": True,
            "assist_today_meditation_and_quote_variants_checked": True,
            "same_source_conversation_snapshot_and_fresh_today_checked": True,
            "missing_commentary_and_stale_date_reported": True,
            "physical_sensor_audio_stop_screen_and_n5105_not_tested": True,
        }
    finally:
        unsubscribe()
        runtime.stoic_settings = original_settings
        runtime.stoic_snapshots.clear()
        hass.states.async_remove(sensor.entity_id)
        if registry.async_get(sensor.entity_id):
            registry.async_remove(sensor.entity_id)
        if restricted:
            await hass.auth.async_remove_user(restricted)
        await hass.async_block_till_done()
