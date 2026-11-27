"""P6 factual replies through HA APIs; synthetic providers, no audio or devices."""

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from .e3_catalog_scenarios import CatalogDimmer

DOMAIN = "polish_context_voice"
TEMPERATURE = "sensor.lab_temperature"


class FactualDimmer(CatalogDimmer):
    """Native brightness dispatch with a controlled local provider failure."""

    def __init__(self, suffix):
        super().__init__()
        self._attr_name = "Lab P6 Facts " + suffix
        self._attr_unique_id = "pcv_lab_p6_facts_" + suffix
        self.failure = False

    async def async_turn_on(self, **kwargs):
        if self.failure:
            raise HomeAssistantError("Synthetic P6 provider failure")
        await super().async_turn_on(**kwargs)


async def exercise_response_facts(hass, entry, fixture, client, user, agent, report, case):
    # Load from HA's copied component, rather than importing the repository copy
    # before the native lab installs its component under test.
    from custom_components.polish_context_voice.core.types import Request

    registry, labels, areas = er.async_get(hass), lr.async_get(hass), ar.async_get(hass)
    original_options = dict(entry.options)
    original_temperature = hass.states.get(TEMPERATURE)
    assert original_temperature is not None
    dimmers = [FactualDimmer("A"), FactualDimmer("B")]
    await hass.data["light"].async_add_entities(dimmers)
    aliases = ("ściemniacz faktów", "ściemniacz drugi")
    for dimmer, room, alias in zip(dimmers, ("living_room", "kitchen"), aliases, strict=True):
        registry.async_update_entity(dimmer.entity_id, area_id=room, aliases={alias})
        exposed_entities.async_expose_entity(hass, "conversation", dimmer.entity_id, True)
    satellites = []
    for room in ("living_room", "kitchen"):
        satellite = registry.async_get_or_create(
            "assist_satellite",
            DOMAIN,
            "lab_p6_facts_" + room,
            suggested_object_id="lab_p6_facts_" + room,
            device_id=fixture["devices"][room][1],
        )
        hass.states.async_set(satellite.entity_id, "idle")
        satellites.append(satellite.entity_id)
    room = areas.async_create("Laboratorium odpowiedzi", aliases={"laboratorium odpowiedzi"})
    confirm_label = labels.async_get_label_by_name("voice_confirm")
    created_label = confirm_label is None
    if confirm_label is None:
        confirm_label = labels.async_create("voice_confirm")
    default_label = labels.async_get_label_by_name("voice_default_on")
    assert default_label is not None
    calls, readers = [], []
    signal = "input_boolean.lab_p6_quiet"
    hass.states.async_set(signal, "off")
    await hass.async_block_till_done()

    @callback
    def capture(event):
        if event.data["domain"] == "light":
            calls.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    def runtime():
        return hass.data[DOMAIN][entry.entry_id]

    async def command(text, *, dry_run=False, **kwargs):
        result = await client.call(
            DOMAIN + "/process",
            text=text,
            dry_run=dry_run,
            **({"source_area": "living_room"} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    async def voice(text, *, satellite=satellites[0]):
        result = await client.call(
            "conversation/process",
            agent_id=agent,
            language="pl",
            text=text,
            satellite_id=satellite,
        )
        await hass.async_block_till_done()
        return result

    def speech(result):
        return result["response"]["speech"]["plain"]["speech"]

    def metadata(result):
        return result["response_data"]["capability"]

    async def reset():
        runtime().dialogue.clear()
        runtime().pending.clear()
        for dimmer, original_room in zip(dimmers, ("living_room", "kitchen"), strict=True):
            dimmer.failure = False
            dimmer._attr_available = True
            dimmer.async_write_ha_state()
            dimmer.operations.clear()
            registry.async_update_entity(dimmer.entity_id, area_id=original_room, labels=set())
            exposed_entities.async_expose_entity(hass, "conversation", dimmer.entity_id, True)
        calls.clear()
        await hass.async_block_till_done()

    try:
        with case(report, "p6_native_preview_keeps_plan_execute_speaks_requested_brightness"):
            await reset()
            preview = await command("ustaw ściemniacz faktów na 35%", dry_run=True)
            assert preview["status"] == "dry_run", preview
            assert preview["speech"] == "Plan gotowy; urządzenia nie zostały zmienione.", preview
            assert metadata(preview)["stage"] == "preview", preview
            assert not preview["completed"] and not dimmers[0].operations and not calls
            result = await command("ustaw ściemniacz faktów na 35%")
            assert result["status"] == "executed", result
            assert result["speech"] == "OK, jasność na 35 procent.", result
            assert result["targets"] == result["completed"] == [dimmers[0].entity_id], result
            assert result["calls"] == preview["calls"], result
            capability = metadata(result)
            assert capability["stage"] == "executed", capability
            assert capability["evidence"] == "executor_returned", capability
            assert capability["facts"]["value"] == 35.0, capability
            assert capability["facts"]["value_role"] == "requested", capability
            assert capability["facts"]["source"] == "validated_parameter", capability
            assert len(calls) == len(dimmers[0].operations) == 1
            assert calls[0].context.user_id == user.id

        with case(report, "p6_native_temperature_units_keep_scale_and_live_cached_reading"):
            await reset()
            for value, unit, normalized, expected in (
                ("21.5", "celsius", "°C", "Jest około 22 stopni."),
                ("71.6", "fahrenheit", "°F", "Jest około 72 stopni Fahrenheita."),
                ("294.5", "kelvin", "K", "Jest około 295 kelwinów."),
                ("-1.2", "C", "°C", "Jest około minus 1 stopnia."),
                ("7", "custom", "custom", "Jest około 7 custom."),
            ):
                attributes = {**original_temperature.attributes, "unit_of_measurement": unit}
                hass.states.async_set(TEMPERATURE, value, attributes)
                await hass.async_block_till_done()
                result = await command("jaka jest temperatura")
                assert result["status"] == "answered" and result["speech"] == expected, result
                facts = metadata(result)["facts"]
                assert facts["value"] == float(value) and facts["unit"] == normalized, facts
                assert facts["value_role"] == "observed" and facts["source"] == "ha_state_cache"
                assert result["calls"] == result["completed"] == []
                assert not calls
            hass.states.async_set(
                TEMPERATURE,
                "21.5",
                {
                    **original_temperature.attributes,
                    "unit_of_measurement": "°C",
                },
            )
            await hass.async_block_till_done()

        with case(report, "p6_native_conversation_brightness_varies_without_cross_source_drift"):
            await reset()
            replies = []
            for _ in range(3):
                result = await voice("ustaw ściemniacz faktów na 35%")
                assert result["response"]["response_type"] == "action_done", result
                assert result["response"]["data"]["success"][0]["id"] == dimmers[0].entity_id
                replies.append(speech(result))
            assert replies == [
                "OK, jasność na 35 procent.",
                "Jasność na 35 procent.",
                "Dobrze, jasność na 35 procent.",
            ], replies
            # Explicit location wins over the second satellite's kitchen room.
            other = await voice("ustaw ściemniacz faktów na 35% w salonie", satellite=satellites[1])
            assert speech(other) == replies[0], other
            assert speech(await voice("ustaw ściemniacz faktów na 35%")) == replies[0]
            assert len(dimmers[0].operations) == 5 and len(calls) == 5

        with case(report, "p6_native_conversation_temperature_varies_and_retains_query_semantics"):
            await reset()
            replies = [speech(await voice("jaka jest temperatura")) for _ in range(3)]
            assert replies == [
                "Jest około 22 stopni.",
                "Temperatura to około 22 stopni.",
                "Około 22 stopni.",
            ], replies
            result = await voice("jaka jest temperatura w salonie", satellite=satellites[1])
            assert speech(result) == replies[0], result
            assert result["response"]["response_type"] == "query_answer", result
            assert speech(await voice("jaka jest temperatura")) == replies[0]
            assert not calls and not dimmers[0].operations

        with case(report, "p6_native_quiet_local_brightness_stays_silent_remote_and_query_spoken"):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **original_options,
                    "quiet_ack_enabled": True,
                    "quiet_start": "00:00",
                    "quiet_end": "00:00",
                    "quiet_signals": [signal],
                    "quiet_areas": ["living_room", "kitchen"],
                },
            )
            await hass.async_block_till_done()
            await reset()
            hass.states.async_set(signal, "on")
            local = await voice("ustaw ściemniacz faktów na 35%")
            assert speech(local) == "" and local["response"]["response_type"] == "action_done"
            assert local["response"]["speech"]["plain"]["extra_data"]["silent_ack"] is True
            assert len(dimmers[0].operations) == 1
            remote = await voice("ustaw ściemniacz drugi na 35% w kuchni")
            assert speech(remote) == "OK, jasność na 35 procent.", remote
            query = await voice("jaka jest temperatura")
            assert speech(query) == "Jest około 22 stopni.", query
            assert query["response"]["response_type"] == "query_answer", query
            hass.states.async_set(signal, "off")
            # The silent completion did not consume a factual response variant.
            audible = await voice("ustaw ściemniacz faktów na 35%")
            assert speech(audible) == "Jasność na 35 procent.", audible

        with case(report, "p6_native_confirmation_and_fresh_exposure_never_render_success_early"):
            await reset()
            registry.async_update_entity(dimmers[0].entity_id, labels={confirm_label.label_id})
            await hass.async_block_till_done()
            question = await command("ustaw ściemniacz faktów na 35%")
            assert question["status"] == "confirm", question
            assert "jasność na 35 procent" not in question["speech"], question
            assert metadata(question)["evidence"] == "validated_plan", question
            assert not question["completed"] and not calls and not dimmers[0].operations
            exposed_entities.async_expose_entity(hass, "conversation", dimmers[0].entity_id, False)
            await hass.async_block_till_done()
            rejected = await command("tak", conversation_id=question["conversation_id"])
            assert rejected["status"] == "clarify" and rejected["reason"] == "plan_changed"
            assert "jasność na 35 procent" not in rejected["speech"], rejected
            assert not rejected["completed"] and not calls and not dimmers[0].operations

        with case(report, "p6_native_permission_denial_preserves_failure_without_positive_ack"):
            await reset()
            reader = await hass.auth.async_create_user(
                "P6 facts read only", group_ids=[GROUP_ID_READ_ONLY]
            )
            readers.append(reader)
            result = await runtime().process(
                Request(
                    "ustaw ściemniacz faktów na 35%",
                    source_area="living_room",
                    user_id=reader.id,
                    conversation_id="p6_reader",
                ),
                Context(user_id=reader.id),
            )
            assert result.status == "error" and result.reason == "execution_failed", result
            assert result.speech == "Nie mogę ci z tym pomóc.", result
            assert result.response_data["capability"]["evidence"] == "none", result
            assert not result.completed and not calls and not dimmers[0].operations

        with case(report, "p6_native_partial_and_provider_failure_keep_existing_error_speech"):
            await reset()
            for dimmer in dimmers:
                registry.async_update_entity(
                    dimmer.entity_id, area_id=room.id, labels={default_label.label_id}
                )
            dimmers[1].failure = True
            await hass.async_block_till_done()
            partial = await command("ustaw światła na 35%", source_area=room.id)
            assert partial["status"] == "partial", partial
            assert partial["completed"] == [dimmers[0].entity_id], partial
            assert partial["speech"] == "Wykonano tylko część polecenia.", partial
            assert metadata(partial)["evidence"] == "executor_partial_return", partial
            assert metadata(partial)["facts"]["value_role"] == "requested", partial
            failed = await command("ustaw ściemniacz drugi na 35%", source_area=room.id)
            assert failed["status"] == "error" and not failed["completed"], failed
            assert failed["speech"] == "Nie mogę ci z tym pomóc.", failed
            assert metadata(failed)["evidence"] == "none", failed

        with case(report, "short_spoken_responses_distinguish_unknown_text_and_unavailable_device"):
            await reset()
            unknown = await voice("Płyną po niebie kolorowe obłoki")
            assert speech(unknown) == "Nie rozumiem.", unknown
            dimmers[0]._attr_available = False
            dimmers[0].async_write_ha_state()
            await hass.async_block_till_done()
            unavailable = await voice("Ustaw ściemniacz faktów na 35 procent")
            assert speech(unavailable) == "Nie mogę ci z tym pomóc.", unavailable
            assert not dimmers[0].operations and not calls
            await reset()

        report["p6_response_facts"] = {
            "native_websocket_conversation_entity_services": True,
            "synthetic_providers_only": True,
            "brightness_requested_executor_returned_not_observed": True,
            "temperature_known_units_preserve_original_scale": True,
            "source_local_three_variants": True,
            "quiet_policy_preview_confirmation_partial_and_failures_preserved": True,
            "fresh_exposure_and_ha_permissions_checked": True,
            "physical_devices_audio_and_n5105_not_tested": True,
        }
    finally:
        unsubscribe()
        hass.config_entries.async_update_entry(entry, options=original_options)
        await hass.async_block_till_done()
        hass.states.async_set(
            TEMPERATURE, original_temperature.state, dict(original_temperature.attributes)
        )
        hass.states.async_remove(signal)
        runtime().dialogue.clear()
        runtime().pending.clear()
        for dimmer in dimmers:
            await dimmer.async_remove()
            if registry.async_get(dimmer.entity_id):
                registry.async_remove(dimmer.entity_id)
        for satellite in satellites:
            hass.states.async_remove(satellite)
            registry.async_remove(satellite)
        areas.async_delete(room.id)
        if created_label:
            labels.async_delete(confirm_label.label_id)
        for reader in readers:
            await hass.auth.async_remove_user(reader)
        await hass.async_block_till_done()
