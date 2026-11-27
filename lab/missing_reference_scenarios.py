"""Missing-reference follow-ups through native HA Conversation, synthetic lights only."""

from dataclasses import replace

from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

DOMAIN = "polish_context_voice"
MAIN = "light.lab_main_lights"
TABLE = "light.lab_table_light"
AMBIENT = "light.lab_ambient_light"


class ReferenceDimmer(LightEntity):
    """Real HA light provider without network, hardware, or replaced HA services."""

    _attr_name = "Lab D01 Dimmer"
    _attr_unique_id = "pcv_lab_d01_dimmer"
    _attr_should_poll = False
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_is_on = False
    _attr_brightness = 255

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


async def exercise_missing_references(hass, entry, fixture, client, session, agent, report, case):
    runtime = hass.data[DOMAIN][entry.entry_id]
    registry = er.async_get(hass)
    original_aliases = registry.async_get(TABLE).aliases
    registry.async_update_entity(TABLE, aliases=set(original_aliases) | {"nad stołem"})
    dimmer = ReferenceDimmer()
    await hass.data["light"].async_add_entities([dimmer])
    registry.async_update_entity(
        dimmer.entity_id, area_id="living_room", aliases={"ściemniacz próbny"}
    )
    exposed_entities.async_expose_entity(hass, "conversation", dimmer.entity_id, True)
    await hass.async_block_till_done()
    calls = []
    group = None
    other_user = None

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

    async def preview(text, **kwargs):
        result = await client.call(
            DOMAIN + "/process",
            text=text,
            dry_run=True,
            **({"satellite_id": fixture["satellite"]} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    def targets(result):
        return {item["id"] for item in result["response"]["data"].get("success", [])}

    def states():
        return {
            state.entity_id: (state.state, state.attributes.get("brightness"))
            for state in hass.states.async_all()
            if state.domain == "light"
        }

    async def reset():
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": [MAIN, TABLE, AMBIENT]}, blocking=True
        )
        await hass.async_block_till_done()
        calls.clear()

    def assert_question(result):
        assert result["continue_conversation"], result
        assert result["response"]["response_type"] == "query_answer", result
        assert not targets(result), result

    def turn_for(result):
        matches = [
            (key, turn)
            for key, turn in runtime.dialogue.questions.items()
            if key[1] == result["conversation_id"]
        ]
        assert len(matches) == 1, matches
        return matches[0]

    try:
        with case(report, "missing_reference_valid_memory_keeps_exact_main_set"):
            await reset()
            first = await voice("zapal światła")
            assert targets(first) == {MAIN}, first
            calls.clear()
            result = await voice("wyłącz je", conversation_id=first["conversation_id"])
            assert targets(result) == {MAIN}, result
            assert not result["continue_conversation"], result
            assert hass.states.is_state(MAIN, "off")
            assert hass.states.is_state(TABLE, "on")
            assert hass.states.is_state(AMBIENT, "on")
            assert len(calls) == 1, calls

        with case(report, "missing_reference_orphan_asks_then_alias_preserves_off"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            assert_question(question)
            assert states() == before and not calls
            result = await voice("nad stołem", conversation_id=question["conversation_id"])
            assert targets(result) == {TABLE}, result
            assert not result["continue_conversation"], result
            assert hass.states.is_state(TABLE, "off")
            assert hass.states.is_state(MAIN, "on")
            assert hass.states.is_state(AMBIENT, "on")
            assert len(calls) == 1, calls

        with case(report, "missing_reference_orphan_preserves_brightness_value"):
            await reset()
            before = states()
            question = await voice("ustaw ją na trzydzieści pięć procent")
            assert_question(question)
            assert states() == before and not calls
            result = await voice("ściemniacz próbny", conversation_id=question["conversation_id"])
            assert targets(result) == {dimmer.entity_id}, result
            assert not result["continue_conversation"], result
            brightness = hass.states.get(dimmer.entity_id).attributes["brightness"]
            assert abs(brightness - 255 * 0.35) <= 1, brightness
            assert len(calls) == 1, calls
            assert calls[0].data["service_data"]["brightness_pct"] == 35, calls[0]

        with case(report, "dialogue_short_brightness_keeps_the_exact_dimmable_light"):
            await reset()
            first = await voice("Włącz ściemniacz próbny")
            assert targets(first) == {dimmer.entity_id}, first
            calls.clear()
            result = await voice(
                "I jeszcze na 55 procent", conversation_id=first["conversation_id"]
            )
            assert targets(result) == {dimmer.entity_id}, result
            assert not result["continue_conversation"]
            brightness = hass.states.get(dimmer.entity_id).attributes["brightness"]
            assert abs(brightness - 255 * 0.55) <= 1, brightness
            assert len(calls) == 1, calls
            assert calls[0].data["service_data"]["brightness_pct"] == 55

        with case(report, "missing_reference_unknown_reply_reasks_without_extending_ttl"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            key, original = turn_for(question)
            for text in ("ta zupełnie inna", "lampka nieznana"):
                reply = await voice(text, conversation_id=question["conversation_id"])
                assert_question(reply)
                assert runtime.dialogue.questions[key].expires_at == original.expires_at
                assert states() == before and not calls
            result = await voice("nad stołem", conversation_id=question["conversation_id"])
            assert targets(result) == {TABLE}, result

        with case(report, "missing_reference_expired_question_cannot_execute_alias"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            key, turn = turn_for(question)
            # Expire the actual bounded turn; no sleep or production-clock replacement.
            runtime.dialogue.questions[key] = replace(turn, expires_at=0)
            result = await voice("nad stołem", conversation_id=question["conversation_id"])
            assert not targets(result), result
            assert key not in runtime.dialogue.questions
            assert states() == before and not calls

        with case(report, "missing_reference_rejection_and_new_command_clear_question"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            key, _ = turn_for(question)
            result = await voice("nie", conversation_id=question["conversation_id"])
            assert not result["continue_conversation"], result
            assert key not in runtime.dialogue.questions
            await voice("nad stołem", conversation_id=question["conversation_id"])
            assert states() == before and not calls
            question = await voice("wyłącz je")
            key, _ = turn_for(question)
            result = await voice(
                "zapal światła w kuchni", conversation_id=question["conversation_id"]
            )
            assert targets(result) == {"light.lab_kitchen_light"}, result
            assert key not in runtime.dialogue.questions
            calls.clear()
            before = states()
            await voice("nad stołem", conversation_id=question["conversation_id"])
            assert states() == before and not calls

        with case(report, "missing_reference_satellite_device_and_conversation_isolation"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            key, _ = turn_for(question)
            for changed in (
                {"satellite_id": None},
                {"device_id": fixture["devices"]["living_room"][1]},
                {"conversation_id": None},
            ):
                reply = await voice(
                    "nad stołem", **({"conversation_id": question["conversation_id"]} | changed)
                )
                assert not targets(reply), reply
                assert key in runtime.dialogue.questions
                assert states() == before and not calls
            result = await voice("nad stołem", conversation_id=question["conversation_id"])
            assert targets(result) == {TABLE}, result

        with case(report, "missing_reference_preview_cannot_consume_spoken_question"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            key, _ = turn_for(question)
            result = await preview("nad stołem", conversation_id=question["conversation_id"])
            assert result["status"] not in {"dry_run", "executed", "confirm"}, result
            assert key in runtime.dialogue.questions
            assert states() == before and not calls
            result = await voice("nad stołem", conversation_id=question["conversation_id"])
            assert targets(result) == {TABLE}, result

        with case(report, "missing_reference_followup_rechecks_assist_exposure"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            exposed_entities.async_expose_entity(hass, "conversation", TABLE, False)
            await hass.async_block_till_done()
            try:
                result = await voice("nad stołem", conversation_id=question["conversation_id"])
                assert not targets(result), result
                assert states() == before and not calls
            finally:
                exposed_entities.async_expose_entity(hass, "conversation", TABLE, True)
                await hass.async_block_till_done()

        with case(report, "missing_reference_group_membership_change_cannot_broaden_target"):
            await reset()
            group = registry.async_get_or_create(
                "light", DOMAIN, "d01_reference_group", suggested_object_id="lab_d01_group"
            )
            registry.async_update_entity(
                group.entity_id, area_id="living_room", aliases={"zestaw próbny"}
            )
            hass.states.async_set(group.entity_id, "on", {"entity_id": [MAIN, TABLE]})
            exposed_entities.async_expose_entity(hass, "conversation", group.entity_id, True)
            await hass.async_block_till_done()
            question = await voice("wyłącz je")
            assert_question(question)
            assert not calls
            hass.states.async_set(group.entity_id, "on", {"entity_id": [MAIN, AMBIENT]})
            await hass.async_block_till_done()
            before = states()
            result = await voice("zestaw próbny", conversation_id=question["conversation_id"])
            assert not targets(result), result
            assert states() == before and not calls
            registry.async_remove(group.entity_id)
            hass.states.async_remove(group.entity_id)
            group = None
            await hass.async_block_till_done()

        with case(report, "missing_reference_without_area_can_select_unique_global_alias"):
            await reset()
            before = states()
            question = await voice("wyłącz je", satellite_id=None)
            assert_question(question)
            assert states() == before and not calls
            result = await voice(
                "nad stołem", satellite_id=None, conversation_id=question["conversation_id"]
            )
            assert targets(result) == {TABLE}, result
            assert hass.states.is_state(TABLE, "off")
            assert hass.states.is_state(MAIN, "on")
            assert len(calls) == 1, calls

        with case(report, "missing_reference_authorized_other_user_cannot_consume_question"):
            await reset()
            before = states()
            question = await voice("wyłącz je")
            key, _ = turn_for(question)
            other_user = await hass.auth.async_create_user(
                "D01 synthetic admin", group_ids=[GROUP_ID_ADMIN]
            )
            refresh = await hass.auth.async_create_refresh_token(
                other_user, client_id="http://127.0.0.1"
            )
            token = hass.auth.async_create_access_token(refresh)
            async with session.ws_connect("http://127.0.0.1:18123/api/websocket") as socket:
                assert (await socket.receive_json())["type"] == "auth_required"
                await socket.send_json({"type": "auth", "access_token": token})
                assert (await socket.receive_json())["type"] == "auth_ok"
                other_client = type(client)(socket)
                result = await voice(
                    "nad stołem",
                    connection=other_client,
                    conversation_id=question["conversation_id"],
                )
                assert not targets(result), result
                assert key in runtime.dialogue.questions
                assert states() == before and not calls
            result = await voice("nad stołem", conversation_id=question["conversation_id"])
            assert targets(result) == {TABLE}, result

        report["missing_reference_dialogue"] = {
            "native_conversation_and_websocket": True,
            "synthetic_devices_only": True,
            "orphan_question_has_no_side_effects": True,
            "existing_memory_keeps_exact_targets": True,
            "brightness_value_and_off_action_preserved": True,
            "source_identity_preview_and_expiry_isolation": True,
            "fresh_exposure_and_group_membership_checked": True,
            "physical_devices_or_audio_tested": False,
        }
    finally:
        unsubscribe()
        registry.async_update_entity(TABLE, aliases=original_aliases)
        exposed_entities.async_expose_entity(hass, "conversation", TABLE, True)
        if group is not None:
            registry.async_remove(group.entity_id)
            hass.states.async_remove(group.entity_id)
        await dimmer.async_remove()
        if registry.async_get(dimmer.entity_id):
            registry.async_remove(dimmer.entity_id)
        if other_user is not None:
            await hass.auth.async_remove_user(other_user)
        runtime.dialogue.clear()
        await hass.async_block_till_done()
