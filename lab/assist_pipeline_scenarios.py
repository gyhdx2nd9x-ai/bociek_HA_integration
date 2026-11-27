"""Text through the native Assist pipeline, including its HA-managed chat log."""

import asyncio
from inspect import signature

from homeassistant.components.assist_pipeline import async_get_pipeline
from homeassistant.components.assist_pipeline.pipeline import async_update_pipeline
from homeassistant.components.conversation.chat_log import async_get_chat_log
from homeassistant.core import Context, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.chat_session import async_get_chat_session


def open_chat_session(hass, conversation_id, user):
    # Since HA 2026.11 the chat belongs to the authenticated user.
    kwargs = {"context": Context(user_id=user.id)} if (
        "context" in signature(async_get_chat_session).parameters
    ) else {}
    return async_get_chat_session(hass, conversation_id, **kwargs)


async def exercise_assist_pipeline(hass, fixture, client, agent, user, report, case):
    original = async_get_pipeline(hass)
    local_device = fixture["devices"]["living_room"][1]
    other_device = fixture["devices"]["kitchen"][1]
    switch = "switch.lab_test_switch"
    actions = []

    @callback
    def capture(event):
        if event.data["domain"] == "switch" and event.data["service"] == "turn_on":
            actions.append(event)

    async def turn(text, conversation_id=None, device_id=local_device, *, processed_locally=False):
        await client.call(
            "assist_pipeline/run",
            pipeline=original.id,
            start_stage="intent",
            end_stage="intent",
            input={"text": text},
            device_id=device_id,
            conversation_id=conversation_id,
        )
        identifier = client.identifier
        events = {}
        async with asyncio.timeout(15):
            while "run-end" not in events:
                message = await client.ws.receive_json()
                if message.get("id") != identifier:
                    continue
                event = message["event"]
                assert event["type"] != "error", event
                events[event["type"]] = event.get("data")
        assert events["intent-start"]["engine"] == agent, events
        assert events["intent-end"]["processed_locally"] is processed_locally, events
        result = events["intent-end"]["intent_output"]
        if conversation_id is not None:
            assert result["conversation_id"] == conversation_id, result
        await hass.async_block_till_done()
        return result

    await async_update_pipeline(
        hass,
        original,
        conversation_engine=agent,
        conversation_language="pl",
        language="pl",
        prefer_local_intents=False,
    )
    unsubscribe = hass.bus.async_listen("call_service", capture)
    initially_on = hass.states.is_state(switch, "on")
    try:
        with case(report, "assist_pipeline_satellite_room_yields_to_spoken_room"):
            salon = "light.lab_main_lights"
            kitchen = "light.lab_kitchen_light"
            lights = [salon, kitchen]
            initial = [target for target in lights if hass.states.is_state(target, "on")]
            try:
                await hass.services.async_call(
                    "light", "turn_off", {"entity_id": lights}, blocking=True,
                )
                for utterance, expected in (
                    ("Włącz światło", salon), ("Włącz światło w kuchni", kitchen),
                ):
                    result = await turn(utterance)
                    ids = {item["id"] for item in result["response"]["data"].get("success", [])}
                    assert ids == {expected}, result
                    assert hass.states.is_state(expected, "on")
                    other = next(target for target in lights if target != expected)
                    assert hass.states.is_state(other, "off")
                    await hass.services.async_call(
                        "light", "turn_off", {"entity_id": expected}, blocking=True,
                    )
            finally:
                for target in initial:
                    await hass.services.async_call(
                        "light", "turn_on", {"entity_id": target}, blocking=True,
                    )
        with case(report, "assist_pipeline_room_correction_prepares_single_source_confirmation"):
            initial = [target for target in lights if hass.states.is_state(target, "on")]
            try:
                await hass.services.async_call(
                    "light", "turn_off", {"entity_id": lights}, blocking=True,
                )
                first = await turn("Zapal światła")
                assert hass.states.is_state(salon, "on") and hass.states.is_state(kitchen, "off")
                cid = first["conversation_id"]
                prompt = await turn("Nie, w kuchni", cid)
                assert prompt["continue_conversation"]
                assert prompt["response"]["speech"]["plain"]["speech"] == (
                    "Gotowe. Włączyć też światła w kuchni?"
                )
                assert prompt["response"]["data"]["success"] == []
                assert hass.states.is_state(salon, "on") and hass.states.is_state(kitchen, "off")
                other = await turn("Tak", cid, device_id=other_device)
                assert other["response"]["response_type"] == "error"
                assert hass.states.is_state(kitchen, "off")
                result = await turn("Tak", cid)
                assert [item["id"] for item in result["response"]["data"]["success"]] == [kitchen]
                assert hass.states.is_state(salon, "on") and hass.states.is_state(kitchen, "on")
                assert (await turn("Tak", cid))["response"]["response_type"] == "error"
            finally:
                for target in lights:
                    service = "turn_on" if target in initial else "turn_off"
                    await hass.services.async_call(
                        "light", service, {"entity_id": target}, blocking=True,
                    )
        with case(report, "assist_pipeline_room_correction_replaces_unexecuted_plan"):
            registry = er.async_get(hass)
            label_registry = lr.async_get(hass)
            label = label_registry.async_get_label_by_name("voice_confirm")
            if label is None:
                label = label_registry.async_create("voice_confirm")
            old_labels = registry.async_get(salon).labels
            initial = [target for target in lights if hass.states.is_state(target, "on")]
            try:
                registry.async_update_entity(salon, labels=old_labels | {label.label_id})
                await hass.services.async_call(
                    "light", "turn_off", {"entity_id": lights}, blocking=True,
                )
                await hass.async_block_till_done()
                first = await turn("Zapal światła")
                assert first["continue_conversation"] and first["response"]["data"]["success"] == []
                cid = first["conversation_id"]
                corrected = await turn("Nie, w kuchni", cid)
                assert corrected["continue_conversation"]
                assert corrected["response"]["speech"]["plain"]["speech"] == (
                    "Włączyć światła w kuchni?"
                )
                assert all(hass.states.is_state(target, "off") for target in lights)
                confirmed = await turn("Tak", cid)
                assert [item["id"] for item in confirmed["response"]["data"]["success"]] == [
                    kitchen
                ]
                assert hass.states.is_state(kitchen, "on") and hass.states.is_state(salon, "off")
            finally:
                registry.async_update_entity(salon, labels=old_labels)
                for target in lights:
                    service = "turn_on" if target in initial else "turn_off"
                    await hass.services.async_call(
                        "light", service, {"entity_id": target}, blocking=True,
                    )
        with case(report, "assist_pipeline_reuses_announced_lights_for_choice_correction"):
            options = [salon, "light.lab_table_light", "light.lab_ambient_light"]
            initial = {target for target in options if hass.states.is_state(target, "on")}
            try:
                await hass.services.async_call(
                    "light", "turn_off", {"entity_id": options}, blocking=True,
                )
                question = await turn("Włącz lampę")
                assert question["continue_conversation"]
                cid = question["conversation_id"]
                selected = await turn("pierwsza", cid)
                first = selected["response"]["data"]["success"][0]["id"]
                assert first in options and hass.states.is_state(first, "on")
                corrected = await turn("Nie, tę drugą", cid)
                assert corrected["continue_conversation"]
                assert corrected["response"]["speech"]["plain"]["speech"].startswith(
                    "Gotowe. Włączyć też "
                )
                assert [key for key in options if hass.states.is_state(key, "on")] == [first]
                other = await turn("Tak", cid, device_id=other_device)
                assert other["response"]["response_type"] == "error"
                assert [key for key in options if hass.states.is_state(key, "on")] == [first]
                confirmed = await turn("Tak", cid)
                second = confirmed["response"]["data"]["success"][0]["id"]
                assert second in options and second != first
                on = {key for key in options if hass.states.is_state(key, "on")}
                assert on == {first, second}
                assert (await turn("Tak", cid))["response"]["response_type"] == "error"
            finally:
                for target in options:
                    service = "turn_on" if target in initial else "turn_off"
                    await hass.services.async_call(
                        "light", service, {"entity_id": target}, blocking=True,
                    )
        with case(report, "assist_pipeline_and_preview_select_preferred_lamp_directly"):
            table = "light.lab_table_light"
            label = label_registry.async_get_label_by_name("voice_primary")
            table_labels = registry.async_get(table).labels
            was_on = hass.states.is_state(table, "on")
            try:
                registry.async_update_entity(table, labels=table_labels | {label.label_id})
                await hass.services.async_call(
                    "light", "turn_off", {"entity_id": table}, blocking=True,
                )
                await hass.async_block_till_done()
                trial = await client.call(
                    "polish_context_voice/trial", text="Włącz lampę", area="living_room",
                )
                assert trial["status"] == "dry_run" and trial["actions"] == ["light.turn_on"]
                assert any("Preferowany odczyt / cel" in line for line in trial["explanation"])
                assert hass.states.is_state(table, "off")
                result = await turn("Włącz lampę")
                assert not result["continue_conversation"]
                assert [item["id"] for item in result["response"]["data"]["success"]] == [table]
                assert hass.states.is_state(table, "on")
            finally:
                registry.async_update_entity(table, labels=table_labels)
                await hass.services.async_call(
                    "light", "turn_on" if was_on else "turn_off", {"entity_id": table},
                    blocking=True,
                )
        await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
        with case(report, "assist_pipeline_keeps_one_chat_turn_per_input_including_error"):
            prompt = await turn("Włącz gniazdo testowe")
            assert prompt["continue_conversation"], prompt
            cid = prompt["conversation_id"]
            invalid = await turn("tak да", cid)
            assert invalid["response"]["response_type"] == "error", invalid
            assert not invalid["continue_conversation"]
            assert not actions and hass.states.is_state(switch, "off")
            with (
                open_chat_session(hass, cid, user) as session,
                async_get_chat_log(hass, session) as log,
            ):
                messages = [item for item in log.content if item.role in {"user", "assistant"}]
                assert [item.role for item in messages] == ["user", "assistant"] * 2
                assert [item.content for item in messages[::2]] == [
                    "Włącz gniazdo testowe", "tak да"
                ]
                assert [item.content for item in messages[1::2]] == [
                    item["response"]["speech"]["plain"]["speech"] for item in (prompt, invalid)
                ]

        with case(report, "assist_pipeline_repeated_confirmation_uses_original_device_once"):
            prompt = await turn("Włącz gniazdo testowe")
            assert prompt["continue_conversation"], prompt
            cid = prompt["conversation_id"]
            elsewhere = await turn("Tak. Tak", cid, device_id=other_device)
            assert elsewhere["response"]["response_type"] == "error", elsewhere
            assert not actions and hass.states.is_state(switch, "off")
            confirmed = await turn("Tak. Tak", cid)
            assert confirmed["response"]["response_type"] == "action_done", confirmed
            assert hass.states.is_state(switch, "on")
            assert len(actions) == 1 and actions[0].context.user_id == user.id
            duplicate = await turn("Tak. Tak", cid)
            assert duplicate["response"]["response_type"] == "error", duplicate
            assert len(actions) == 1

        with case(report, "assist_pipeline_prefer_local_can_handle_command_before_bociek"):
            pipeline = async_get_pipeline(hass, pipeline_id=original.id)
            await async_update_pipeline(hass, pipeline, prefer_local_intents=True)
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": switch}, blocking=True
            )
            result = await turn("Włącz gniazdo testowe", processed_locally=True)
            assert result["response"]["response_type"] == "action_done", result
            assert not result["continue_conversation"]
            assert hass.states.is_state(switch, "on") and len(actions) == 2
    finally:
        unsubscribe()
        service = "turn_on" if initially_on else "turn_off"
        await hass.services.async_call("switch", service, {"entity_id": switch}, blocking=True)
        await async_update_pipeline(
            hass,
            original,
            conversation_engine=original.conversation_engine,
            conversation_language=original.conversation_language,
            language=original.language,
            prefer_local_intents=original.prefer_local_intents,
        )
