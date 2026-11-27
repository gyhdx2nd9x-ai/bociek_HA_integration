"""P6 unreadable-temperature feedback on synthetic native HA state and permissions."""

import json

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

DOMAIN = "polish_context_voice"
UNREADABLE = "Nie mogę teraz sprawdzić temperatury."
INVALID_READING = "Nie mam teraz prawidłowego odczytu temperatury."


async def exercise_temperature_feedback(hass, entry, client, agent, report, case):
    # The native lab installs its copied integration before loading core contracts.
    from custom_components.polish_context_voice.core.types import Request

    runtime = hass.data[DOMAIN][entry.entry_id]
    registry, areas = er.async_get(hass), ar.async_get(hass)
    room = areas.async_create("Laboratorium odczytu", aliases={"laboratorium odczytu"})
    satellite = registry.async_get_or_create(
        "assist_satellite",
        DOMAIN,
        "lab_temperature_feedback_satellite",
        suggested_object_id="lab_temperature_feedback_satellite",
    )
    registry.async_update_entity(satellite.entity_id, area_id=room.id)
    hass.states.async_set(satellite.entity_id, "idle")
    reader = await hass.auth.async_create_user(
        "Lab temperature feedback reader", group_ids=[GROUP_ID_READ_ONLY]
    )
    sensors, calls = [], []
    attributes = {"device_class": "temperature", "unit_of_measurement": "°C"}

    @callback
    def capture(event):
        calls.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    async def command(text="jaka jest temperatura"):
        result = await client.call(
            DOMAIN + "/process", text=text, source_area=room.id, dry_run=False
        )
        await hass.async_block_till_done()
        return result

    async def read_as_reader():
        result = await runtime.process(
            Request(
                "jaka jest temperatura",
                source_area=room.id,
                user_id=reader.id,
                conversation_id="temperature_feedback_reader",
            ),
            Context(user_id=reader.id),
        )
        await hass.async_block_till_done()
        return result.to_dict()

    def no_service(result):
        # Native Result.to_dict preserves tuples; WebSocket JSON uses lists.
        assert not result["calls"] and not result["completed"] and not calls, result
        row = next(
            row
            for row in runtime.diagnostics.snapshot()["recent_requests"]
            if row["request_id"] == result["request_id"]
        )
        assert row["ha_service_calls"] == {
            "attempted": 0,
            "returned": 0,
            "failed": 0,
            "interrupted": 0,
        }, row

    try:
        await hass.async_block_till_done()
        calls.clear()
        with case(report, "p6_temperature_missing_sensor_has_useful_feedback_without_a_service"):
            result = await command()
            assert (result["status"], result["reason"], result["speech"]) == (
                "clarify",
                "no_targets",
                UNREADABLE,
            ), result
            assert not result["continue_conversation"], result
            assert result["response_data"]["capability"]["evidence"] == "none", result
            no_service(result)

        with case(report, "p6_temperature_hidden_sensor_is_not_disclosed_to_admin_or_reader"):
            for suffix, alias in (("a", "czujnik przy drzwiach"), ("b", "czujnik przy oknie")):
                sensor = registry.async_get_or_create(
                    "sensor",
                    DOMAIN,
                    "lab_temperature_feedback_" + suffix,
                    suggested_object_id="lab_temperature_feedback_" + suffix,
                )
                registry.async_update_entity(
                    sensor.entity_id,
                    area_id=room.id,
                    aliases={alias},
                    name="Prywatny odczyt " + suffix,
                )
                hass.states.async_set(sensor.entity_id, "777", attributes)
                exposed_entities.async_expose_entity(hass, "conversation", sensor.entity_id, False)
                sensors.append(sensor.entity_id)
            await hass.async_block_till_done()
            calls.clear()
            for result in (await command(), await read_as_reader()):
                assert result["status"] == "clarify" and result["speech"] == UNREADABLE, result
                serialized = json.dumps(result, ensure_ascii=False)
                assert all(identifier not in serialized for identifier in sensors), result
                assert "Prywatny odczyt" not in serialized and "777" not in result["speech"], result
                assert result["response_data"]["capability"]["facts"] is None, result
                no_service(result)

        with case(report, "p6_temperature_invalid_and_unavailable_cached_reads_recover_for_reader"):
            exposed_entities.async_expose_entity(hass, "conversation", sensors[0], True)
            hass.states.async_set(sensors[0], "17.7", attributes)
            await hass.async_block_till_done()
            calls.clear()
            first = await read_as_reader()
            assert first["status"] == "answered" and first["speech"] == "Jest około 18 stopni.", (
                first
            )
            no_service(first)
            rebuilds = runtime.cache.metrics()["rebuilds"]
            for state, reason, expected in (
                ("NaN", "invalid_state", INVALID_READING),
                ("inf", "invalid_state", INVALID_READING),
                ("unavailable", "ineligible_target", UNREADABLE),
            ):
                hass.states.async_set(sensors[0], state, attributes)
                await hass.async_block_till_done()
                result = await read_as_reader()
                assert (result["status"], result["reason"], result["speech"]) == (
                    "clarify",
                    reason,
                    expected,
                ), result
                assert not result["continue_conversation"], result
                assert result["response_data"]["capability"]["evidence"] == "none", result
                assert runtime.cache.metrics()["rebuilds"] == rebuilds
                no_service(result)
            hass.states.async_set(sensors[0], "19.6", attributes)
            await hass.async_block_till_done()
            recovered = await read_as_reader()
            assert (
                recovered["status"] == "answered" and recovered["speech"] == "Jest około 20 stopni."
            ), recovered
            assert recovered["response_data"]["capability"]["facts"]["value"] == 19.6
            assert runtime.cache.metrics()["rebuilds"] == rebuilds
            no_service(recovered)

        with case(report, "p6_temperature_ambiguity_keeps_native_conversation_follow_up_question"):
            exposed_entities.async_expose_entity(hass, "conversation", sensors[1], True)
            hass.states.async_set(sensors[1], "18.6", attributes)
            await hass.async_block_till_done()
            runtime.dialogue.clear()
            calls.clear()
            result = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="jaka jest temperatura",
                satellite_id=satellite.entity_id,
            )
            await hass.async_block_till_done()
            speech = result["response"]["speech"]["plain"]["speech"]
            assert result["continue_conversation"], result
            assert speech.startswith("Które urządzenie masz na myśli:"), result
            assert "czujnik przy drzwiach" in speech and "czujnik przy oknie" in speech, result
            assert speech not in {UNREADABLE, INVALID_READING}, result
            assert result["response"]["response_type"] == "query_answer", result
            assert not calls

        report["p6_temperature_feedback"] = {
            "synthetic_native_ha_states_only": True,
            "websocket_conversation_and_read_only_user_checked": True,
            "hidden_sensor_id_name_and_reading_not_disclosed": True,
            "missing_invalid_and_unavailable_readings_do_not_execute_services": True,
            "fresh_state_recovery_without_registry_rebuild": True,
            "existing_ambiguity_follow_up_preserved": True,
            "physical_devices_audio_and_n5105_not_tested": True,
        }
    finally:
        unsubscribe()
        runtime.dialogue.clear()
        runtime.pending.clear()
        for identifier in sensors:
            hass.states.async_remove(identifier)
            if registry.async_get(identifier):
                registry.async_remove(identifier)
        hass.states.async_remove(satellite.entity_id)
        if registry.async_get(satellite.entity_id):
            registry.async_remove(satellite.entity_id)
        areas.async_delete(room.id)
        await hass.auth.async_remove_user(reader)
        await hass.async_block_till_done()
