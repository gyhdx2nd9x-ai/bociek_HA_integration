"""Dialogue through real HA Conversation/WebSocket with synthetic lights only."""

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

DOMAIN = "polish_context_voice"
MAIN = "light.lab_main_lights"
TABLE = "light.lab_table_light"
KITCHEN = "light.lab_kitchen_light"


async def exercise_dialogue(hass, fixture, client, agent, report, case):
    async def voice(text, **kwargs):
        result = await client.call(
            "conversation/process",
            text=text,
            language="pl",
            agent_id=agent,
            **({"satellite_id": fixture["satellite"]} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    def targets(result):
        return {item["id"] for item in result["response"]["data"].get("success", [])}

    with case(report, "dialogue_flexible_polish_exact_target"):
        result = await voice("W salonie proszę zapal światła")
        assert targets(result) == {MAIN}, result
        assert hass.states.is_state(MAIN, "on")

    with case(report, "dialogue_clarification_is_question_and_continues"):
        question = await voice("włącz lampę")
        assert question["continue_conversation"], question
        assert question["response"]["response_type"] == "query_answer", question
        assert "lampa przy stole" in question["response"]["speech"]["plain"]["speech"]

    with case(report, "dialogue_short_reply_selects_only_table"):
        result = await voice("lampa przy stole", conversation_id=question["conversation_id"])
        assert targets(result) == {TABLE}, result
        assert not result["continue_conversation"]
        assert hass.states.is_state(TABLE, "on")

    with case(report, "dialogue_pronoun_uses_same_target_after_selection"):
        result = await voice("zgaś ją", conversation_id=result["conversation_id"])
        assert targets(result) == {TABLE}, result
        assert hass.states.is_state(TABLE, "off")
        assert hass.states.is_state(MAIN, "on")

    with case(report, "dialogue_numbered_choice_uses_announced_order_and_source"):
        question = await client.call(
            DOMAIN + "/process", text="włącz lampę", satellite_id=fixture["satellite"],
            dry_run=False,
        )
        options = question["response_data"]["clarification_options"]
        cid = question["conversation_id"]
        other = await voice("ta druga", conversation_id=cid, satellite_id=None)
        assert not targets(other), other
        rejected = await voice("druga да", conversation_id=cid)
        assert rejected["continue_conversation"] and not targets(rejected), rejected
        selected = await voice("Nie, ta druga", conversation_id=cid)
        assert targets(selected) == {options[1]["entity_id"]}, selected
        duplicate = await voice("ta druga", conversation_id=cid)
        assert not targets(duplicate), duplicate

    with case(report, "dialogue_native_implicit_room_question"):
        question = await voice("zapal światła", satellite_id=None)
        assert question["continue_conversation"], question
        result = await voice(
            "w kuchni", satellite_id=None, conversation_id=question["conversation_id"]
        )
        assert targets(result) == {KITCHEN}, result

    with case(report, "dialogue_area_ellipsis_is_explicit_scope"):
        first = await voice("zapal światła")
        result = await voice("a w kuchni?", conversation_id=first["conversation_id"])
        assert targets(result) == {KITCHEN}, result

    with case(report, "dialogue_room_correction_and_live_lights_have_exact_scope"):
        question = await voice("Włącz światło", satellite_id=None)
        result = await voice(
            "Nie, w kuchni", conversation_id=question["conversation_id"], satellite_id=None,
        )
        assert targets(result) == {KITCHEN}
        await hass.services.async_call("light", "turn_on", {"entity_id": TABLE}, blocking=True)
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": [MAIN, "light.lab_ambient_light"]}, blocking=True,
        )
        await hass.async_block_till_done()
        result = await voice("Zgaś zapalone w tym pokoju")
        assert targets(result) == {TABLE} and hass.states.is_state(TABLE, "off"), result
        assert hass.states.is_state(KITCHEN, "on")
        next_result = await voice("A w kuchni?", conversation_id=result["conversation_id"])
        assert targets(next_result) == {KITCHEN} and hass.states.is_state(KITCHEN, "off")
        await hass.services.async_call("light", "turn_on", {"entity_id": MAIN}, blocking=True)
        await hass.async_block_till_done()

    with case(report, "dialogue_same_conversation_other_source_cannot_consume"):
        first = await voice("zapal światła")
        before = {
            identifier: hass.states.get(identifier).state for identifier in (MAIN, TABLE, KITCHEN)
        }
        calls = []

        @callback
        def capture(event):
            if event.data["domain"] == "light":
                calls.append(event)

        unsubscribe = hass.bus.async_listen("call_service", capture)
        try:
            result = await voice(
                "zgaś je", satellite_id=None, conversation_id=first["conversation_id"]
            )
        finally:
            unsubscribe()
        assert result["response"]["response_type"] == "query_answer", result
        assert result["continue_conversation"], result
        assert not targets(result) and not calls, result
        assert before == {
            identifier: hass.states.get(identifier).state for identifier in (MAIN, TABLE, KITCHEN)
        }
        own = await voice("zgaś je", conversation_id=first["conversation_id"])
        assert targets(own) == {MAIN}, own

    with case(report, "dialogue_fresh_exposure_before_pronoun"):
        first = await voice("włącz lampę przy stole")
        exposed_entities.async_expose_entity(hass, "conversation", TABLE, False)
        await hass.async_block_till_done()
        try:
            result = await voice("zgaś ją", conversation_id=first["conversation_id"])
            assert result["response"]["response_type"] == "error", result
            assert hass.states.is_state(TABLE, "on")
        finally:
            exposed_entities.async_expose_entity(hass, "conversation", TABLE, True)
            await hass.async_block_till_done()

    with case(report, "dialogue_room_repeat_respects_remote_light_capabilities"):
        first = await voice("Zapal tutejsze światło")
        assert targets(first) == {MAIN}, first
        cid = first["conversation_id"]
        second = await voice("W kuchni też", conversation_id=cid)
        assert targets(second) == {KITCHEN} and not second["continue_conversation"], second
        result = await voice("I jeszcze na 55 procent", conversation_id=cid)
        assert not targets(result) and result["response"]["response_type"] == "error", result
        assert (
            result["response"]["speech"]["plain"]["speech"] == "To światło nie obsługuje jasności."
        )
        assert hass.states.is_state(KITCHEN, "on")

    with case(report, "dialogue_natural_pronoun_follows_light_after_explicit_room"):
        first = await voice("Włącz światło w kuchni")
        result = await voice("A teraz je zgaś", conversation_id=first["conversation_id"])
        assert targets(result) == {KITCHEN}, result
        assert hass.states.is_state(KITCHEN, "off")
        assert hass.states.is_state(MAIN, "on")

    with case(report, "dialogue_light_outcome_uses_requested_room_and_rejects_condition"):
        before_main = hass.states.get(MAIN).state
        before_kitchen = hass.states.get(KITCHEN).state
        rejected = await voice("Zrób jasno w kuchni, kiedy wrócę")
        assert not targets(rejected), rejected
        assert hass.states.is_state(MAIN, before_main)
        assert hass.states.is_state(KITCHEN, before_kitchen)

        result = await voice("Ale ciemno w kuchni")
        assert targets(result) == {KITCHEN} and not result["continue_conversation"], result
        assert hass.states.is_state(KITCHEN, "on")
        assert hass.states.is_state(MAIN, before_main)

        result = await voice("Niech zgaśnie światło w kuchni")
        assert targets(result) == {KITCHEN}, result
        assert hass.states.is_state(KITCHEN, "off")
        assert hass.states.is_state(MAIN, before_main)

        result = await voice("Ale tu ciemno")
        assert targets(result) == {MAIN}, result
        assert hass.states.is_state(MAIN, "on")
        assert hass.states.is_state(KITCHEN, "off")

    with case(report, "dialogue_side_reply_matches_room_and_keeps_short_pronoun_scope"):
        registry = er.async_get(hass)
        aliases = {identifier: registry.async_get(identifier).aliases for identifier in (
            MAIN, TABLE, KITCHEN,
        )}
        original = {
            identifier: hass.states.get(identifier).state for identifier in aliases
        }
        try:
            for identifier, side in ((MAIN, "Lewy"), (TABLE, "Prawy"), (KITCHEN, "Prawy")):
                registry.async_update_entity(
                    identifier, aliases=[*aliases[identifier], side + " kinkiet testowy"],
                )
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": list(aliases)}, blocking=True,
            )
            await hass.async_block_till_done()
            first = await voice("Zapal lewy kinkiet testowy")
            assert targets(first) == {MAIN}
            cid = first["conversation_id"]
            other = await voice("Prawy też", satellite_id=None, conversation_id=cid)
            assert not targets(other)
            assert hass.states.is_state(TABLE, "off")

            second = await voice("Prawy też, poproszę", conversation_id=cid)
            assert targets(second) == {TABLE} and not second["continue_conversation"], second
            assert hass.states.is_state(MAIN, "on")
            assert hass.states.is_state(TABLE, "on")
            assert hass.states.is_state(KITCHEN, "off")
            third = await voice("Zgaś go", conversation_id=cid)
            assert targets(third) == {TABLE}
            assert hass.states.is_state(TABLE, "off")
            assert hass.states.is_state(MAIN, "on")

            result = await voice("Zapal lewy kinkiet testowy")
            assert result["response"]["speech"]["plain"]["speech"] == "Kinkiet był już włączony."

            registry.async_update_entity(MAIN, aliases=[*aliases[MAIN], "Lewa lampka"])
            registry.async_update_entity(TABLE, aliases=[*aliases[TABLE], "Prawa lampka"])
            await hass.async_block_till_done()
            first = await voice("Zapal lewą lampkę")
            assert targets(first) == {MAIN}, first
            assert first["response"]["speech"]["plain"]["speech"] == "Lampka była już włączona."
            wrong = await voice("Prawy też", conversation_id=first["conversation_id"])
            assert not targets(wrong) and hass.states.is_state(TABLE, "off"), wrong

            first = await voice("Zapal lewą lampkę")
            assert targets(first) == {MAIN}, first
            second = await voice("Prawa też", conversation_id=first["conversation_id"])
            assert targets(second) == {TABLE}, second
            assert hass.states.is_state(KITCHEN, "off")
        finally:
            for identifier in aliases:
                registry.async_update_entity(identifier, aliases=aliases[identifier])
                await hass.services.async_call(
                    "light", "turn_on" if original[identifier] == "on" else "turn_off",
                    {"entity_id": identifier}, blocking=True,
                )
            await hass.async_block_till_done()
