"""Light category recovery and state feedback through real HA, synthetic devices only."""

from dataclasses import replace

from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

DOMAIN = "polish_context_voice"
MAIN = "light.lab_main_lights"
TABLE = "light.lab_table_light"
AMBIENT = "light.lab_ambient_light"
KITCHEN = "light.lab_kitchen_light"
HIDDEN = "light.lab_hidden_light"


class CategoryLamp(LightEntity):
    """In-process HA provider with no hardware or external network connection."""

    _attr_should_poll = False
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_is_on = False

    def __init__(self, name):
        self._attr_name = "Lab category " + name
        self._attr_unique_id = "pcv_lab_category_" + name

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


async def exercise_light_categories(hass, entry, fixture, client, session, agent, report, case):
    runtime = hass.data[DOMAIN][entry.entry_id]
    registry, labels = er.async_get(hass), lr.async_get(hass)
    saved = {identifier: registry.async_get(identifier).labels for identifier in (KITCHEN,)}
    original_states = {
        identifier: hass.states.get(identifier).state
        for identifier in (MAIN, TABLE, AMBIENT, KITCHEN)
    }
    created_labels = []

    def role(name):
        label = labels.async_get_label_by_name(name)
        if label is None:
            label = labels.async_create(name)
            created_labels.append(label.label_id)
        return label.label_id

    roles = {
        name: role(name)
        for name in ("voice_task", "voice_ambient", "voice_accent", "voice_all_off")
    }
    legacy, kitchen_ambient = CategoryLamp("legacy decor"), CategoryLamp("kitchen decor")
    await hass.data["light"].async_add_entities([legacy, kitchen_ambient])
    registry.async_update_entity(
        legacy.entity_id,
        area_id="living_room",
        labels={roles["voice_accent"], roles["voice_all_off"]},
    )
    registry.async_update_entity(
        kitchen_ambient.entity_id,
        area_id="kitchen",
        labels={roles["voice_ambient"], roles["voice_all_off"]},
    )
    for lamp in (legacy, kitchen_ambient):
        exposed_entities.async_expose_entity(hass, "conversation", lamp.entity_id, True)
    await hass.async_block_till_done()
    calls, group, other_user = [], None, None

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
            **({"satellite_id": fixture["satellite"]} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    def targets(result):
        return {item["id"] for item in result["response"]["data"].get("success", [])}

    def states():
        return {
            state.entity_id: state.state
            for state in hass.states.async_all()
            if state.domain == "light"
        }

    async def reset(*, both=False):
        runtime.dialogue.clear()
        runtime.pending.clear()
        registry.async_update_entity(KITCHEN, labels={roles["voice_task"], roles["voice_all_off"]})
        exposed_entities.async_expose_entity(hass, "conversation", KITCHEN, True)
        exposed_entities.async_expose_entity(hass, "conversation", kitchen_ambient.entity_id, both)
        await hass.services.async_call(
            "light",
            "turn_on",
            {
                "entity_id": [
                    MAIN,
                    TABLE,
                    AMBIENT,
                    KITCHEN,
                    legacy.entity_id,
                    kitchen_ambient.entity_id,
                ]
            },
            blocking=True,
        )
        await hass.async_block_till_done()
        calls.clear()

    def assert_question(result):
        assert result["continue_conversation"], result
        assert result["response"]["response_type"] == "query_answer", result
        assert not targets(result), result

    def speech(result):
        return result["response"]["speech"]["plain"]["speech"]

    def turn_for(result):
        matches = [
            (key, turn)
            for key, turn in runtime.dialogue.questions.items()
            if key[1] == result["conversation_id"]
        ]
        assert len(matches) == 1, matches
        return matches[0]

    try:
        with case(report, "categories_ozdobne_matches_ambient_and_legacy_decor_only"):
            await reset()
            result = await voice("Wyłącz światła ozdobne")
            assert targets(result) == {AMBIENT, legacy.entity_id}, result
            assert hass.states.is_state(MAIN, "on") and hass.states.is_state(TABLE, "on")
            assert hass.states.is_state(AMBIENT, "off") and hass.states.is_state(
                legacy.entity_id, "off"
            )
            assert len(calls) == 2, calls

        with case(report, "exclusive_category_preview_and_native_only_change_requested_role"):
            await reset(both=True)
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": kitchen_ambient.entity_id}, blocking=True
            )
            await hass.async_block_till_done()
            calls.clear()
            before = states()
            text = "W kuchni zapal tylko nastrojowe oświetlenie"
            plan = await command(text, dry_run=True)
            assert plan["status"] == "dry_run", plan
            assert plan["targets"] == [kitchen_ambient.entity_id], plan
            assert not calls and states() == before
            result = await voice(text)
            assert targets(result) == {kitchen_ambient.entity_id}, result
            assert states() == {**before, kitchen_ambient.entity_id: "on"}
            assert len(calls) == 1 and calls[0].data["service"] == "turn_on", calls

        with case(report, "exclusive_category_unavailable_role_never_uses_task_lights"):
            await reset()
            before = states()
            result = await voice("Wyłącz jedynie oświetlenie dekoracyjne w kuchni")
            assert not targets(result), result
            assert states() == before and not calls

        with case(report, "lighting_purpose_preview_and_assist_use_role_in_requested_room"):
            await reset(both=True)
            before = states()
            text = "Zapal w kuchni coś do czytania"
            preview = await client.call(DOMAIN + "/trial", text=text, area="living_room")
            assert preview["status"] == "dry_run" and len(preview["targets"]) == 1, preview
            assert any("Światło robocze" in line for line in preview["explanation"]), preview
            assert states() == before and not calls
            first = await voice(text)
            assert targets(first) == {KITCHEN} and not first["continue_conversation"], first
            assert len(calls) == 1 and calls[0].data["service"] == "turn_on"
            result = await voice("A teraz je zgaś", conversation_id=first["conversation_id"])
            assert targets(result) == {KITCHEN}, result
            assert states() == {**before, KITCHEN: "off"}

        with case(report, "lighting_purpose_missing_role_waits_for_explicit_light_name"):
            await reset(both=True)
            registry.async_update_entity(KITCHEN, labels={roles["voice_all_off"]})
            await hass.async_block_till_done()
            before = states()
            question = await voice("Potrzebuję światła do czytania w kuchni")
            assert_question(question)
            assert speech(question) == "Którą lampę włączyć? Podaj jej nazwę."
            assert states() == before and not calls

            identifier = kitchen_ambient.entity_id
            name = hass.states.get(identifier).name
            result = await voice(name, conversation_id=question["conversation_id"])
            assert targets(result) == {identifier}, result
            assert len(calls) == 1 and calls[0].data["service"] == "turn_on"

        with case(report, "categories_existing_default_has_priority_without_question"):
            await reset()
            result = await voice("Włącz światła")
            assert targets(result) == {MAIN}, result
            assert not result["continue_conversation"], result
            assert len(calls) == 1, calls

        with case(report, "singular_light_uses_only_kitchen_light_without_default_on"):
            await reset()
            before = states()
            result = await voice("Włącz światło w kuchni")
            assert targets(result) == {KITCHEN}, result
            assert not result["continue_conversation"], result
            assert states() == {**before, KITCHEN: "on"}
            assert len(calls) == 1, calls

        with case(report, "categories_missing_default_task_confirm_keeps_explicit_kitchen"):
            await reset()
            before = states()
            question = await voice("Włącz światła w kuchni")
            assert_question(question)
            assert "robocze" in speech(question).casefold(), question
            assert states() == before and not calls
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert targets(result) == {KITCHEN}, result
            assert not result["continue_conversation"], result
            assert len(calls) == 1, calls

        with case(report, "categories_two_choices_need_name_and_ozdobne_selects_only_decor"):
            await reset(both=True)
            before = states()
            question = await voice("Włącz światła w kuchni")
            assert_question(question)
            assert "robocze" in speech(question).casefold(), question
            assert states() == before and not calls
            repeated = await voice("Tak", conversation_id=question["conversation_id"])
            assert_question(repeated)
            assert states() == before and not calls
            result = await voice("ozdobne", conversation_id=question["conversation_id"])
            assert targets(result) == {kitchen_ambient.entity_id}, result
            assert len(calls) == 1, calls

        with case(report, "categories_explicit_kitchen_off_uses_all_off_without_default_on"):
            await reset()
            result = await voice("Wyłącz światło w kuchni")
            assert targets(result) == {KITCHEN}, result
            assert hass.states.is_state(KITCHEN, "off")
            assert hass.states.is_state(MAIN, "on") and hass.states.is_state(TABLE, "on")
            assert len(calls) == 1, calls

        with case(report, "unlabelled_room_off_falls_back_to_all_exposed_lights"):
            await reset()
            identifiers = (MAIN, TABLE, AMBIENT, legacy.entity_id)
            original_labels = {
                identifier: registry.async_get(identifier).labels for identifier in identifiers
            }
            for identifier in identifiers:
                registry.async_update_entity(identifier, labels=set())
            await hass.async_block_till_done()
            try:
                result = await voice("Wyłącz światła")
                expected = set(identifiers)
                assert targets(result) == expected, result
                assert all(hass.states.is_state(identifier, "off") for identifier in identifiers)
                assert HIDDEN not in targets(result)
                assert len(calls) == len(expected), calls
            finally:
                for identifier, labels_before in original_labels.items():
                    registry.async_update_entity(identifier, labels=labels_before)
                await hass.async_block_till_done()

        with case(report, "explicit_unlabelled_room_off_overrides_satellite_area"):
            await reset()
            original_labels = registry.async_get(KITCHEN).labels
            registry.async_update_entity(KITCHEN, labels=set())
            await hass.async_block_till_done()
            try:
                result = await voice("Wyłącz światło w kuchni")
                assert targets(result) == {KITCHEN}, result
                assert hass.states.is_state(KITCHEN, "off")
                assert all(
                    MAIN not in event.data["service_data"].get("entity_id", []) for event in calls
                )
            finally:
                registry.async_update_entity(KITCHEN, labels=original_labels)
                await hass.async_block_till_done()

        with case(report, "categories_rejection_and_expiry_do_not_execute"):
            await reset()
            before = states()
            question = await voice("Włącz światła w kuchni")
            assert_question(question)
            result = await voice("Nie", conversation_id=question["conversation_id"])
            assert not result["continue_conversation"], result
            await voice("Tak", conversation_id=question["conversation_id"])
            assert states() == before and not calls
            question = await voice("Włącz światła w kuchni")
            key, turn = turn_for(question)
            runtime.dialogue.questions[key] = replace(turn, expires_at=0)
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert not targets(result), result
            assert states() == before and not calls

        with case(report, "categories_identity_and_preview_cannot_consume_spoken_choice"):
            await reset()
            before = states()
            question = await voice("Włącz światła w kuchni")
            key, _ = turn_for(question)
            preview = await command(
                "Tak", dry_run=True, conversation_id=question["conversation_id"]
            )
            assert preview["status"] not in {"executed", "dry_run", "confirm"}, preview
            assert key in runtime.dialogue.questions
            reply = await voice(
                "Tak", conversation_id=question["conversation_id"], satellite_id=None
            )
            assert not targets(reply), reply
            assert key in runtime.dialogue.questions
            other_user = await hass.auth.async_create_user(
                "Category synthetic admin", group_ids=[GROUP_ID_ADMIN]
            )
            refresh = await hass.auth.async_create_refresh_token(
                other_user, client_id="http://127.0.0.1"
            )
            token = hass.auth.async_create_access_token(refresh)
            async with session.ws_connect("http://127.0.0.1:18123/api/websocket") as socket:
                assert (await socket.receive_json())["type"] == "auth_required"
                await socket.send_json({"type": "auth", "access_token": token})
                assert (await socket.receive_json())["type"] == "auth_ok"
                reply = await voice(
                    "Tak",
                    connection=type(client)(socket),
                    conversation_id=question["conversation_id"],
                )
                assert not targets(reply), reply
            assert key in runtime.dialogue.questions and states() == before and not calls
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert targets(result) == {KITCHEN}, result

        with case(report, "categories_changed_labels_do_not_authorize_stale_category"):
            await reset()
            question = await voice("Włącz światła w kuchni")
            assert_question(question)
            registry.async_update_entity(KITCHEN, labels={roles["voice_all_off"]})
            await hass.async_block_till_done()
            before = states()
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert not targets(result), result
            assert states() == before and not calls

        with case(report, "categories_changed_exposure_and_group_membership_are_rejected"):
            await reset()
            question = await voice("Włącz światła w kuchni")
            assert_question(question)
            exposed_entities.async_expose_entity(hass, "conversation", KITCHEN, False)
            await hass.async_block_till_done()
            before = states()
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert not targets(result), result
            assert states() == before and not calls
            await reset()
            registry.async_update_entity(KITCHEN, labels={roles["voice_all_off"]})
            group = registry.async_get_or_create(
                "light", DOMAIN, "category_group", suggested_object_id="lab_category_group"
            )
            registry.async_update_entity(
                group.entity_id, area_id="kitchen", labels={roles["voice_task"]}
            )
            hass.states.async_set(group.entity_id, "on", {"entity_id": [KITCHEN]})
            exposed_entities.async_expose_entity(hass, "conversation", group.entity_id, True)
            await hass.async_block_till_done()
            question = await voice("Włącz światła w kuchni")
            assert_question(question)
            hass.states.async_set(
                group.entity_id, "on", {"entity_id": [KITCHEN, kitchen_ambient.entity_id]}
            )
            await hass.async_block_till_done()
            before = states()
            result = await voice("Tak", conversation_id=question["conversation_id"])
            assert not targets(result), result
            assert states() == before and not calls
            registry.async_remove(group.entity_id)
            hass.states.async_remove(group.entity_id)
            group = None
            await hass.async_block_till_done()

        with case(report, "light_feedback_already_off_retains_real_service_and_completed_target"):
            await reset()
            await hass.services.async_call("light", "turn_off", {"entity_id": MAIN}, blocking=True)
            await hass.async_block_till_done()
            calls.clear()
            result = await command("Wyłącz główne światła", dry_run=False)
            assert result["status"] == "executed" and result["completed"] == [MAIN], result
            assert "już" in result["speech"].casefold(), result
            assert len(calls) == 1 and calls[0].data["service"] == "turn_off", calls

        with case(report, "light_feedback_mixed_states_use_normal_acknowledgement"):
            await reset()
            await hass.services.async_call("light", "turn_off", {"entity_id": TABLE}, blocking=True)
            await hass.async_block_till_done()
            calls.clear()
            result = await command("Wyłącz światła", dry_run=False)
            expected = {MAIN, TABLE, AMBIENT, legacy.entity_id}
            assert result["status"] == "executed" and set(result["completed"]) == expected, result
            assert "już" not in result["speech"].casefold(), result
            assert len(calls) == len(expected), calls

        with case(report, "light_feedback_unknown_state_never_claims_success_or_already_off"):
            await reset()
            original = hass.states.get(MAIN)
            try:
                hass.states.async_set(MAIN, "unknown", original.attributes)
                await hass.async_block_till_done()
                result = await command("Wyłącz główne światła", dry_run=False)
                assert result["status"] == "clarify" and not result["completed"], result
                assert result["reason"] == "ineligible_target", result
                assert "już" not in result["speech"].casefold(), result
                assert not calls, calls
            finally:
                # Undo the artificial state override even when the backing helper
                # did not change and therefore would not trigger a template update.
                hass.states.async_set(MAIN, original.state, original.attributes)
                await hass.async_block_till_done()

        report["light_categories"] = {
            "native_conversation_and_services": True,
            "synthetic_devices_only": True,
            "ozdobne_matches_ambient_and_legacy_accent": True,
            "missing_default_requires_choice_or_confirmation": True,
            "explicit_area_keeps_priority": True,
            "generic_off_falls_back_only_when_room_has_no_light_roles": True,
            "fallback_respects_exposure_and_explicit_area": True,
            "fresh_roles_exposure_and_membership_checked": True,
            "source_user_expiry_preview_isolation": True,
            "already_satisfied_feedback_preserves_service_execution": True,
            "physical_devices_or_audio_tested": False,
        }
    finally:
        unsubscribe()
        for identifier, original in original_states.items():
            if original in {"on", "off"}:
                await hass.services.async_call(
                    "light", "turn_" + original, {"entity_id": identifier}, blocking=True
                )
        for identifier, original in saved.items():
            registry.async_update_entity(identifier, labels=original)
        exposed_entities.async_expose_entity(hass, "conversation", KITCHEN, True)
        if group is not None:
            registry.async_remove(group.entity_id)
            hass.states.async_remove(group.entity_id)
        for lamp in (legacy, kitchen_ambient):
            await lamp.async_remove()
            if registry.async_get(lamp.entity_id):
                registry.async_remove(lamp.entity_id)
        for identifier in created_labels:
            labels.async_delete(identifier)
        if other_user is not None:
            await hass.auth.async_remove_user(other_user)
        runtime.dialogue.clear()
        runtime.pending.clear()
        await hass.async_block_till_done()
