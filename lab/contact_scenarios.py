"""Contact queries on native HA states, registry exposure and Conversation transport."""

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import entity_registry as er


async def exercise_contacts(hass, fixture, client, agent, report, case):
    domain = "polish_context_voice"
    registry = er.async_get(hass)
    living = ("binary_sensor.lab_window", "binary_sensor.lab_window_unknown")
    originals = [hass.states.get(identifier) for identifier in living]
    extra = []
    events = []
    unsub = hass.bus.async_listen("call_service", lambda event: events.append(event))

    async def readings(*states):
        for original, state in zip(originals, states, strict=True):
            hass.states.async_set(original.entity_id, state, original.attributes)
        await hass.async_block_till_done()

    async def command(text, **kwargs):
        return await client.call(
            domain + "/process",
            text=text,
            dry_run=False,
            satellite_id=fixture["satellite"],
            **kwargs,
        )

    try:
        with case(report, "contacts_user_closed_question_via_conversation"):
            answer = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="Czy okna są zamknięte?",
                satellite_id=fixture["satellite"],
            )
            assert answer["response"]["response_type"] == "query_answer", answer
            speech = answer["response"]["speech"]["plain"]["speech"]
            assert speech.startswith("Nie, otwarte: lab_window."), answer
            assert "Nie mam aktualnego odczytu: lab_window_unknown." in speech
            assert "secret" not in str(answer)

        with case(report, "contacts_all_closed_despite_hidden_open_contact"):
            await readings("off", "off")
            result = await command("Czy wszystkie okna są zamknięte?")
            assert result["status"] == "answered", result
            assert result["speech"] == "Tak, sprawdzone okna są zamknięte.", result
            assert set(result["targets"]) == set(living) and not result["calls"]
            assert hass.states.is_state("binary_sensor.lab_secret_window", "on")

        with case(report, "contacts_unknown_never_becomes_closed"):
            await readings("off", "unknown")
            result = await command("Czy okna są zamknięte?")
            assert (
                "Tak" not in result["speech"] and "Nie mam aktualnego odczytu" in result["speech"]
            )
            await readings("unknown", "unavailable")
            result = await command("Czy okna są zamknięte?")
            assert result["speech"] == (
                "Nie mam aktualnego odczytu: lab_window i lab_window_unknown."
            ), result

        with case(report, "contacts_explicit_area_wins_and_doors_use_own_class"):
            for name, kind, area in (
                ("lab_kitchen_window", "window", "kitchen"),
                ("lab_living_door", "door", "living_room"),
            ):
                entity = registry.async_get_or_create(
                    "binary_sensor", domain, name, suggested_object_id=name
                )
                extra.append(entity.entity_id)
                registry.async_update_entity(entity.entity_id, area_id=area)
                hass.states.async_set(
                    entity.entity_id, "off", {"device_class": kind, "friendly_name": name}
                )
                exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, True)
            await hass.async_block_till_done()
            await readings("on", "on")
            kitchen = await command("Czy okna są zamknięte w kuchni?")
            assert kitchen["targets"] == [extra[0]] and kitchen["area_ids"] == ["kitchen"], kitchen
            assert kitchen["speech"] == "Tak, sprawdzone okna są zamknięte."
            door = await command("Czy drzwi są zamknięte?")
            assert door["targets"] == [extra[1]], door
            assert door["speech"] == "Tak, sprawdzone drzwi są zamknięte."

        with case(report, "contacts_missing_exposure_does_not_answer_closed"):
            for identifier in living:
                exposed_entities.async_expose_entity(hass, "conversation", identifier, False)
            await hass.async_block_till_done()
            try:
                result = await command("Czy okna są zamknięte?")
                assert result["status"] == "clarify" and result["reason"] == "no_targets", result
                assert not result["targets"] and "czujników okien" in result["speech"]
            finally:
                for identifier in living:
                    exposed_entities.async_expose_entity(hass, "conversation", identifier, True)
                await hass.async_block_till_done()

        with case(report, "contacts_list_closed_and_preview_have_no_side_effects"):
            await readings("off", "on")
            result = await command("Które okna są zamknięte?")
            assert result["speech"] == "Zamknięte: lab_window.", result
            result = await command("Czy wszystkie okna są otwarte?")
            assert result["speech"] == "Nie, zamknięte: lab_window.", result
            preview = await client.call(
                domain + "/process",
                text="Czy okna są zamknięte?",
                satellite_id=fixture["satellite"],
                dry_run=True,
            )
            assert preview["status"] == "dry_run" and not preview["calls"], preview
            assert not events, events
            assert hass.states.is_state(living[0], "off") and hass.states.is_state(living[1], "on")
    finally:
        unsub()
        for original in originals:
            hass.states.async_set(original.entity_id, original.state, original.attributes)
        for identifier in extra:
            hass.states.async_remove(identifier)
            registry.async_remove(identifier)
        await hass.async_block_till_done()
