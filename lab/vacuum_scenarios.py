"""Check the vacuum route using native HA registries and a virtual segment service."""

import voluptuous as vol
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er


async def exercise_vacuum(hass, client, agent, user, report, case, satellite_id):
    registry = er.async_get(hass)
    robot = registry.async_get_or_create("vacuum", "dreame_vacuum", "lab_vacuum")
    entity_id = robot.entity_id
    domain, service = "dreame_vacuum", "vacuum_clean_segment"
    calls = []

    @callback
    def clean(call):
        calls.append((call.context.user_id, dict(call.data)))

    hass.services.async_register(
        domain, service, clean,
        schema=vol.Schema({vol.Required("entity_id"): [str], vol.Required("segments"): [int]}),
    )
    hass.states.async_set(
        entity_id,
        "docked",
        {
            "selected_map": "Testowa mapa",
            "rooms": {"Testowa mapa": [{"id": 7, "name": "Gabinet"}]},
            "cleangenius": "Routine cleaning",
            "cleangenius_mode": "Vacuum and mop",
        },
    )
    exposed_entities.async_expose_entity(hass, "conversation", entity_id, True)
    await hass.async_block_till_done()
    try:
        with case(report, "vacuum_native_preview_is_read_only_and_scoped_to_one_segment"):
            result = await client.call(
                "call_service",
                domain="polish_context_voice",
                service="preview",
                service_data={"text": "odkurz gabinet"},
                return_response=True,
            )
            plan = result["response"]
            assert plan["status"] == "dry_run", plan
            assert plan["targets"] == [entity_id], plan
            assert plan["calls"][0]["data"] == {"segments": [7]}, plan
            assert not calls

        with case(report, "vacuum_native_conversation_returns_success_after_one_virtual_call"):
            result = await client.call(
                "conversation/process", text="odkurz gabinet", agent_id=agent, language="pl"
            )
            assert result["response"]["response_type"] == "action_done", result
            assert calls == [(user.id, {"entity_id": [entity_id], "segments": [7]})], calls

        with case(report, "vacuum_native_unmapped_room_does_not_expand_to_full_clean"):
            result = await client.call(
                "polish_context_voice/process", text="odkurz szklarnię", dry_run=False
            )
            assert result["reason"] == "vacuum_room_not_mapped", result
            assert not result["calls"] and len(calls) == 1

        with case(report, "vacuum_native_replaces_an_older_device_confirmation"):
            switch_id = "switch.lab_test_switch"
            assert hass.states.get(switch_id).state == "off"
            options = {"agent_id": agent, "language": "pl", "satellite_id": satellite_id}
            prompt = await client.call(
                "conversation/process", text="włącz gniazdo testowe", **options
            )
            assert prompt["continue_conversation"], prompt
            options["conversation_id"] = prompt["conversation_id"]

            result = await client.call("conversation/process", text="odkurz gabinet", **options)
            assert result["response"]["response_type"] == "action_done", result
            assert calls == [(user.id, {"entity_id": [entity_id], "segments": [7]})] * 2, calls

            reply = await client.call("conversation/process", text="tak", **options)
            assert reply["response"]["response_type"] == "error", reply
            assert not reply["continue_conversation"]
            assert hass.states.get(switch_id).state == "off"
        with case(report, "vacuum_native_unavailable_or_failed_device_uses_short_reply"):
            options = {"agent_id": agent, "language": "pl", "text": "odkurz gabinet"}
            before = hass.states.get(entity_id)
            hass.services.async_remove(domain, service)
            result = await client.call("conversation/process", **options)
            assert result["response"]["speech"]["plain"]["speech"] == "Nie mogę ci z tym pomóc."
            hass.states.async_set(entity_id, "unavailable", dict(before.attributes))
            await hass.async_block_till_done()
            result = await client.call("conversation/process", **options)
            assert result["response"]["speech"]["plain"]["speech"] == "Nie mogę ci z tym pomóc."
            assert len(calls) == 2
    finally:
        exposed_entities.async_expose_entity(hass, "conversation", entity_id, False)
        hass.services.async_remove(domain, service)
        hass.states.async_remove(entity_id)
        registry.async_remove(entity_id)
        await hass.async_block_till_done()
