"""Bounded D02 light proposals through native HA; synthetic lights, no audio."""

from dataclasses import replace

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, callback
from homeassistant.helpers import entity_registry as er

DOMAIN = "polish_context_voice"
MAIN = "light.lab_main_lights"
TABLE = "light.lab_table_light"
AMBIENT = "light.lab_ambient_light"
KITCHEN = "light.lab_kitchen_light"
LIGHTS = (MAIN, TABLE, AMBIENT, KITCHEN)
ON_BACKGROUND = "Włącz światła wow jaka figura"


async def exercise_light_background(hass, entry, fixture, client, session, agent, report, case):
    from homeassistant.components.intent import async_register_timer_handler
    from homeassistant.components.intent.const import TIMER_DATA

    from custom_components.polish_context_voice.core.types import Request

    runtime = hass.data[DOMAIN][entry.entry_id]
    registry = er.async_get(hass)
    original_registry = {
        identifier: (registry.async_get(identifier).labels, registry.async_get(identifier).aliases)
        for identifier in LIGHTS
    }
    original_states = {identifier: hass.states.get(identifier).state for identifier in LIGHTS}
    calls, users = [], []
    group = None

    @callback
    def capture(event):
        if event.data["domain"] == "light":
            calls.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    async def voice(text, *, connection=client, **kwargs):
        result = await connection.call(
            "conversation/process",
            text=text,
            language="pl",
            agent_id=agent,
            **({"satellite_id": fixture["satellite"]} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    async def command(text, **kwargs):
        result = await client.call(
            DOMAIN + "/process",
            text=text,
            **({"satellite_id": fixture["satellite"], "dry_run": True} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    def targets(result):
        return {item["id"] for item in result["response"]["data"].get("success", [])}

    def states():
        return {
            state.entity_id: (state.state, tuple(state.attributes.get("entity_id", ())))
            for state in hass.states.async_all()
            if state.domain == "light"
        }

    def pending_for(result):
        matches = [
            (key, pending)
            for key, pending in runtime.pending.items()
            if key[1] == result["conversation_id"]
        ]
        assert len(matches) == 1, matches
        key, pending = matches[0]
        assert pending.background, pending
        return key, pending

    def assert_question(result):
        assert result["continue_conversation"], result
        assert result["response"]["response_type"] == "query_answer", result
        assert not targets(result), result

    async def reset():
        nonlocal group
        runtime.dialogue.clear()
        runtime.pending.clear()
        if group is not None:
            registry.async_remove(group.entity_id)
            hass.states.async_remove(group.entity_id)
            group = None
        for identifier, (labels, aliases) in original_registry.items():
            registry.async_update_entity(identifier, labels=labels, aliases=aliases)
            exposed_entities.async_expose_entity(hass, "conversation", identifier, True)
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": list(LIGHTS)}, blocking=True
        )
        await hass.async_block_till_done()
        calls.clear()

    try:
        with case(report, "d02_background_non_timer_utterance_never_starts_timer_or_light"):
            await reset()
            manager = hass.data[TIMER_DATA]
            timer_events = []
            device_id = registry.async_get(fixture["satellite"]).device_id
            unsubscribe_timer = async_register_timer_handler(
                hass, device_id, lambda event, timer: timer_events.append((event, timer.id))
            )
            try:
                before, timers_before = states(), dict(manager.timers)
                # This one non-timer sample was formerly in the timer rejection
                # loop. D02 intentionally changes its oracle to an OFF question,
                # followed here by refusal. Every timer correction stays rejected.
                question = await voice("wyłącz światła Usiądź normalnie")
                assert_question(question)
                assert not runtime.pending_timers and manager.timers == timers_before
                assert not timer_events and states() == before and not calls
                reply = await voice("Nie", conversation_id=question["conversation_id"])
                assert not reply["continue_conversation"] and not targets(reply)
                assert not runtime.pending_timers and manager.timers == timers_before
                assert not timer_events and states() == before and not calls
            finally:
                unsubscribe_timer()

        with case(report, "d02_background_native_question_then_affirmative_executes_once"):
            await reset()
            before = states()
            question = await voice(ON_BACKGROUND)
            assert_question(question)
            key, _ = pending_for(question)
            assert states() == before and not calls
            result = await voice("Tak. Tak", conversation_id=question["conversation_id"])
            assert targets(result) == {MAIN} and not result["continue_conversation"], result
            assert hass.states.is_state(MAIN, "on") and hass.states.is_state(TABLE, "off")
            assert key not in runtime.pending and len(calls) == 1
            await voice("Potwierdzam", conversation_id=question["conversation_id"])
            assert len(calls) == 1, calls

        with case(report, "d02_background_explicit_kitchen_off_keeps_priority_and_asymmetry"):
            await reset()
            await hass.services.async_call(
                "light", "turn_on", {"entity_id": list(LIGHTS)}, blocking=True
            )
            await hass.async_block_till_done()
            calls.clear()
            before = states()
            question = await voice("Wyłącz światła w kuchni wow jaka figura")
            assert_question(question)
            assert states() == before and not calls
            result = await voice("Dobrze", conversation_id=question["conversation_id"])
            assert targets(result) == {KITCHEN}, result
            assert hass.states.is_state(KITCHEN, "off") and hass.states.is_state(MAIN, "on")
            assert len(calls) == 1 and calls[0].data["service"] == "turn_off"

        with case(report, "d02_background_preview_cannot_arm_or_consume_spoken_pending"):
            await reset()
            before = states()
            preview = await command(ON_BACKGROUND)
            assert preview["status"] == "confirm", preview
            assert preview["reason"] == "background_confirmation" and preview["route"] == "fast"
            assert preview["response_data"]["background"]["kind"] == "light_prefix"
            assert not runtime.pending and states() == before and not calls
            result = await voice("Tak", conversation_id=preview["conversation_id"])
            assert not targets(result) and not runtime.pending
            question = await voice(ON_BACKGROUND)
            key, original = pending_for(question)
            await command("Tak", conversation_id=question["conversation_id"])
            assert runtime.pending[key] == original and not calls and states() == before
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert targets(result) == {MAIN} and len(calls) == 1, result

        with case(report, "d02_background_rejection_and_fixed_expiry_never_execute"):
            await reset()
            before = states()
            question = await voice(ON_BACKGROUND)
            key, _ = pending_for(question)
            reply = await voice("Nie", conversation_id=question["conversation_id"])
            assert not reply["continue_conversation"] and key not in runtime.pending
            await voice("Tak", conversation_id=question["conversation_id"])
            assert states() == before and not calls
            question = await voice(ON_BACKGROUND)
            key, original = pending_for(question)
            repeat = await voice("Chyba", conversation_id=question["conversation_id"])
            assert_question(repeat)
            assert runtime.pending[key].expires_at == original.expires_at
            runtime.pending[key] = replace(original, expires_at=0)
            reply = await voice("Potwierdzam", conversation_id=question["conversation_id"])
            assert not targets(reply) and key not in runtime.pending
            assert states() == before and not calls

        with case(report, "d02_background_source_and_conversation_cannot_consume_pending"):
            await reset()
            before = states()
            question = await voice(ON_BACKGROUND)
            key, original = pending_for(question)
            for changed in (
                {"satellite_id": None},
                {"satellite_id": None, "device_id": fixture["devices"]["living_room"][1]},
                {"conversation_id": None},
            ):
                reply = await voice(
                    "Tak", **({"conversation_id": question["conversation_id"]} | changed)
                )
                assert not targets(reply), reply
                assert runtime.pending[key] == original and not calls and states() == before
            result = await voice("Potwierdzam", conversation_id=question["conversation_id"])
            assert targets(result) == {MAIN} and len(calls) == 1, result

        with case(report, "d02_background_other_user_cannot_consume_pending"):
            await reset()
            before = states()
            question = await voice(ON_BACKGROUND)
            key, original = pending_for(question)
            other = await hass.auth.async_create_user(
                "D02 synthetic admin", group_ids=[GROUP_ID_ADMIN]
            )
            users.append(other)
            refresh = await hass.auth.async_create_refresh_token(
                other, client_id="http://127.0.0.1"
            )
            access_token = hass.auth.async_create_access_token(refresh)
            async with session.ws_connect("http://127.0.0.1:18123/api/websocket") as socket:
                assert (await socket.receive_json())["type"] == "auth_required"
                await socket.send_json({"type": "auth", "access_token": access_token})
                assert (await socket.receive_json())["type"] == "auth_ok"
                reply = await voice(
                    "Tak",
                    connection=type(client)(socket),
                    conversation_id=question["conversation_id"],
                )
                assert not targets(reply), reply
            assert runtime.pending[key] == original and states() == before and not calls
            result = await voice("Potwierdzam", conversation_id=question["conversation_id"])
            assert targets(result) == {MAIN} and len(calls) == 1, result

        with case(report, "d02_background_fresh_roles_and_exposure_reject_stale_plan"):
            for mutation in ("role", "exposure"):
                await reset()
                question = await voice(ON_BACKGROUND)
                assert_question(question)
                if mutation == "role":
                    registry.async_update_entity(
                        MAIN, labels=original_registry[MAIN][0] - {"voice_default_on"}
                    )
                else:
                    exposed_entities.async_expose_entity(hass, "conversation", MAIN, False)
                await hass.async_block_till_done()
                before = states()
                reply = await voice("Tak", conversation_id=question["conversation_id"])
                assert not targets(reply), reply
                assert states() == before and not calls

        with case(report, "d02_background_changed_group_membership_rejects_stale_plan"):
            await reset()
            registry.async_update_entity(
                MAIN, labels=original_registry[MAIN][0] - {"voice_default_on"}
            )
            group = registry.async_get_or_create(
                "light", DOMAIN, "d02_synthetic_group", suggested_object_id="lab_d02_group"
            )
            registry.async_update_entity(
                group.entity_id, area_id="living_room", labels={"voice_default_on", "voice_all_off"}
            )
            hass.states.async_set(group.entity_id, "off", {"entity_id": [TABLE]})
            exposed_entities.async_expose_entity(hass, "conversation", group.entity_id, True)
            await hass.async_block_till_done()
            question = await voice(ON_BACKGROUND)
            assert_question(question)
            hass.states.async_set(group.entity_id, "off", {"entity_id": [TABLE, AMBIENT]})
            await hass.async_block_till_done()
            before = states()
            reply = await voice("Tak", conversation_id=question["conversation_id"])
            assert not targets(reply), reply
            assert states() == before and not calls

        with case(report, "d02_background_ha_read_only_permission_is_checked_after_yes"):
            await reset()
            reader = await hass.auth.async_create_user(
                "D02 synthetic reader", group_ids=[GROUP_ID_READ_ONLY]
            )
            users.append(reader)
            request = Request(
                ON_BACKGROUND,
                satellite_id=fixture["satellite"],
                conversation_id="d02-read-only",
                user_id=reader.id,
            )
            before = states()
            question = await runtime.process(request, Context(user_id=reader.id))
            assert question.status == "confirm" and question.reason == "background_confirmation"
            assert not calls and states() == before
            result = await runtime.process(replace(request, text="Tak"), Context(user_id=reader.id))
            assert result.status == "error" and result.reason == "execution_failed", result
            assert not result.completed and not calls and states() == before

        with case(report, "d02_background_compound_correction_unknown_area_are_not_salvaged"):
            for text in (
                "Włącz światła i wyłącz światła w kuchni",
                "Włącz światła nie włączaj jednak",
                "Włącz światła w garażu wow jaka figura",
                "Włącz światła na parterze wow jaka figura",
                "Ustaw światła na 35 procent wow jaka figura",
            ):
                await reset()
                before = states()
                result = await command(text, dry_run=False)
                assert result["status"] not in {"executed", "confirm"}, result
                assert result["reason"] != "background_confirmation", result
                assert not runtime.pending and not result["completed"]
                assert states() == before and not calls

        with case(report, "d02_background_valid_complete_alias_keeps_existing_fast_path"):
            await reset()
            registry.async_update_entity(
                TABLE, aliases=set(original_registry[TABLE][1]) | {"światła wow jaka figura"}
            )
            await hass.async_block_till_done()
            result = await voice(ON_BACKGROUND)
            assert targets(result) == {TABLE}, result
            assert not result["continue_conversation"] and not runtime.pending
            assert hass.states.is_state(TABLE, "on") and hass.states.is_state(MAIN, "off")
            assert len(calls) == 1, calls

        report["light_background_confirmation"] = {
            "native_conversation_websocket_registry_and_services": True,
            "synthetic_lights_only": True,
            "bounded_single_area_low_risk_on_off_only": True,
            "question_precedes_every_background_execution": True,
            "whole_affirmative_single_use_and_fixed_expiry": True,
            "source_user_conversation_preview_isolation": True,
            "fresh_roles_exposure_group_membership_and_ha_permissions": True,
            "complete_alias_has_priority": True,
            "compound_correction_unknown_area_and_non_on_off_rejected": True,
            "physical_devices_audio_background_speaker_separation_and_n5105_tested": False,
        }
    finally:
        unsubscribe()
        if group is not None:
            registry.async_remove(group.entity_id)
            hass.states.async_remove(group.entity_id)
        for identifier, (labels, aliases) in original_registry.items():
            registry.async_update_entity(identifier, labels=labels, aliases=aliases)
            exposed_entities.async_expose_entity(hass, "conversation", identifier, True)
        for identifier, state in original_states.items():
            if state in {"on", "off"}:
                await hass.services.async_call(
                    "light", "turn_" + state, {"entity_id": identifier}, blocking=True
                )
        for user in users:
            await hass.auth.async_remove_user(user)
        runtime.pending.clear()
        runtime.dialogue.clear()
        await hass.async_block_till_done()
