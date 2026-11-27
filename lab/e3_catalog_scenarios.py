"""First capability-catalog slice against HA, with virtual providers only."""

import json

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import Context, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

DOMAIN = "polish_context_voice"
TEMPERATURE = "sensor.lab_temperature"
MAIN = "light.lab_main_lights"


class CatalogDimmer(LightEntity):
    """Actual HA brightness service provider without external I/O."""

    _attr_name = "Lab E3 Catalog Dimmer"
    _attr_unique_id = "pcv_lab_e3_catalog_dimmer"
    _attr_should_poll = False
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_is_on = False
    _attr_brightness = 255

    def __init__(self):
        self.operations = []

    async def async_turn_on(self, **kwargs):
        self.operations.append(("turn_on", dict(kwargs)))
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.operations.append(("turn_off", dict(kwargs)))
        self._attr_is_on = False
        self.async_write_ha_state()


async def exercise_e3_catalog(hass, entry, client, user, report, case):
    from custom_components.polish_context_voice.core.capability_catalog import catalog_snapshot
    from custom_components.polish_context_voice.core.types import Request
    from custom_components.polish_context_voice.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    runtime = hass.data[DOMAIN][entry.entry_id]
    registry, labels = er.async_get(hass), lr.async_get(hass)
    original_temperature = hass.states.get(TEMPERATURE)
    assert original_temperature is not None
    dimmer = CatalogDimmer()
    await hass.data["light"].async_add_entities([dimmer])
    registry.async_update_entity(
        dimmer.entity_id, area_id="living_room", aliases={"ściemniacz katalogowy"}
    )
    exposed_entities.async_expose_entity(hass, "conversation", dimmer.entity_id, True)
    await hass.async_block_till_done()
    confirm_label = labels.async_get_label_by_name("voice_confirm")
    created_label = confirm_label is None
    if confirm_label is None:
        confirm_label = labels.async_create("voice_confirm")
    reader = None
    calls = []

    @callback
    def capture(event):
        calls.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    async def command(text, *, dry_run=True, **kwargs):
        result = await client.call(
            DOMAIN + "/process",
            text=text,
            dry_run=dry_run,
            **({"source_area": "living_room"} | kwargs),
        )
        await hass.async_block_till_done()
        return result

    def capability(result, stage, evidence, *, value=None, value_role="requested"):
        metadata = result["response_data"]["capability"]
        assert metadata["schema_version"] == 1, metadata
        assert metadata["stage"] == stage and metadata["evidence"] == evidence, metadata
        assert metadata["completed_target_count"] == len(result["completed"]), metadata
        if value is not None:
            assert metadata["facts"]["value"] == value, metadata
            assert metadata["facts"]["value_role"] == value_role, metadata
        return metadata

    def assert_no_executor_calls(result):
        row = next(
            item
            for item in runtime.diagnostics.snapshot()["recent_requests"]
            if item["request_id"] == result["request_id"]
        )
        assert row["ha_service_calls"] == {
            "attempted": 0,
            "returned": 0,
            "failed": 0,
            "interrupted": 0,
        }, row
        assert not result["completed"] and not calls, result

    async def reset():
        runtime.dialogue.clear()
        runtime.pending.clear()
        registry.async_update_entity(dimmer.entity_id, labels=set())
        exposed_entities.async_expose_entity(hass, "conversation", dimmer.entity_id, True)
        await hass.async_block_till_done()
        dimmer.operations.clear()
        calls.clear()

    try:
        with case(report, "e3_catalog_diagnostics_partial_static_two_operations_no_home_inventory"):
            exported = (await async_get_config_entry_diagnostics(hass, entry))["capability_catalog"]
            assert exported == catalog_snapshot(), exported
            assert exported["partial"] and exported["schema_version"] == 1, exported
            assert {item["id"] for item in exported["capabilities"]} == {
                "light.set_brightness",
                "sensor.read_temperature",
            }, exported
            serialized = json.dumps(exported)
            assert not any(state.entity_id in serialized for state in hass.states.async_all())
            assert "entity_ids" not in serialized and "targets" not in serialized
            assert not dimmer.operations and not calls

        with case(report, "e3_catalog_brightness_preview_keeps_call_and_requested_facts"):
            await reset()
            before = hass.states.get(dimmer.entity_id)
            result = await command("ustaw ściemniacz katalogowy na 35%")
            assert result["status"] == "dry_run", result
            assert result["targets"] == [dimmer.entity_id], result
            assert result["calls"] == [
                {
                    "domain": "light",
                    "service": "turn_on",
                    "entity_ids": [dimmer.entity_id],
                    "data": {"brightness_pct": 35.0},
                }
            ], result
            metadata = capability(result, "preview", "validated_plan", value=35.0)
            assert metadata["id"] == "light.set_brightness", metadata
            assert metadata["facts"]["unit"] == "%", metadata
            assert metadata["facts"]["source"] == "validated_parameter", metadata
            assert hass.states.get(dimmer.entity_id) == before
            assert not dimmer.operations
            assert_no_executor_calls(result)

        with case(
            report, "e3_catalog_brightness_execute_reports_executor_return_without_observed_value"
        ):
            await reset()
            result = await command("ustaw ściemniacz katalogowy na 35%", dry_run=False)
            assert result["status"] == "executed" and result["completed"] == [dimmer.entity_id], (
                result
            )
            metadata = capability(result, "executed", "executor_returned", value=35.0)
            assert metadata["facts"]["source"] == "validated_parameter", metadata
            assert metadata["completed_target_count"] == 1
            assert len(dimmer.operations) == 1 and len(calls) == 1
            assert calls[0].data["domain"] == "light"
            assert calls[0].data["service_data"]["brightness_pct"] == 35
            assert calls[0].context.user_id == user.id
            assert abs(hass.states.get(dimmer.entity_id).attributes["brightness"] - 255 * 0.35) <= 1

        with case(report, "e3_catalog_temperature_fresh_cached_observation_has_no_executor_calls"):
            await reset()
            # Warm the metadata cache before changing only the HA state.
            await command("jaka jest temperatura")
            rebuilds = runtime.cache.metrics()["rebuilds"]
            for value, rounded in (("19.6", "20"), ("18.4", "18")):
                hass.states.async_set(TEMPERATURE, value, dict(original_temperature.attributes))
                await hass.async_block_till_done()
                calls.clear()
                result = await command("jaka jest temperatura", dry_run=False)
                assert result["status"] == "answered" and result["targets"] == [TEMPERATURE], result
                assert result["speech"] == f"Jest około {rounded} stopni.", result
                assert result["calls"] == []
                metadata = capability(
                    result, "answered", "ha_state_cache", value=float(value), value_role="observed"
                )
                assert metadata["id"] == "sensor.read_temperature", metadata
                assert metadata["facts"]["source"] == "ha_state_cache", metadata
                assert metadata["facts"]["unit"] == "°C", metadata
                assert runtime.cache.metrics()["rebuilds"] == rebuilds
                assert_no_executor_calls(result)

        with case(report, "e3_catalog_brightness_voice_confirm_stays_pending_until_yes"):
            await reset()
            registry.async_update_entity(dimmer.entity_id, labels={confirm_label.label_id})
            await hass.async_block_till_done()
            question = await command("ustaw ściemniacz katalogowy na 40%", dry_run=False)
            assert question["status"] == "confirm", question
            capability(question, "confirmation_required", "validated_plan", value=40.0)
            assert not dimmer.operations
            assert_no_executor_calls(question)
            malformed = await command(
                "tak да", dry_run=False, conversation_id=question["conversation_id"]
            )
            assert malformed["status"] != "executed" and not dimmer.operations
            assert_no_executor_calls(malformed)
            question = await command("ustaw ściemniacz katalogowy na 40%", dry_run=False)
            assert question["status"] == "confirm", question
            result = await command(
                "Tak. Tak", dry_run=False, conversation_id=question["conversation_id"]
            )
            assert result["status"] == "executed" and result["completed"] == [dimmer.entity_id], (
                result
            )
            capability(result, "executed", "executor_returned", value=40.0)
            assert len(dimmer.operations) == 1 and len(calls) == 1

        with case(
            report, "e3_catalog_brightness_onoff_and_range_rejections_have_no_service_attempt"
        ):
            await reset()
            for text, reason in (
                ("ustaw główne światła na 35%", "capability"),
                ("ustaw ściemniacz katalogowy na 101%", "range"),
            ):
                result = await command(text, dry_run=False)
                assert result["status"] == "blocked" and result["reason"] == reason, result
                metadata = capability(result, "rejected", "none")
                assert metadata["facts"]["value"] is None, metadata
                assert metadata["facts"]["unit"] is None, metadata
                assert not dimmer.operations
                assert_no_executor_calls(result)

        with case(report, "e3_catalog_fresh_exposure_and_ha_permissions_cannot_claim_completion"):
            await reset()
            registry.async_update_entity(dimmer.entity_id, labels={confirm_label.label_id})
            await hass.async_block_till_done()
            question = await command("ustaw ściemniacz katalogowy na 45%", dry_run=False)
            assert question["status"] == "confirm", question
            exposed_entities.async_expose_entity(hass, "conversation", dimmer.entity_id, False)
            await hass.async_block_till_done()
            result = await command(
                "tak", dry_run=False, conversation_id=question["conversation_id"]
            )
            assert result["status"] == "clarify" and result["reason"] == "plan_changed", result
            metadata = (result.get("response_data") or {}).get("capability")
            if metadata:
                assert metadata["evidence"] == "none" and not metadata["completed_target_count"]
            assert not dimmer.operations
            assert_no_executor_calls(result)
            await reset()
            reader = await hass.auth.async_create_user(
                "E3 catalog reader", group_ids=[GROUP_ID_READ_ONLY]
            )
            result = (
                await runtime.process(
                    Request(
                        "ustaw ściemniacz katalogowy na 45%",
                        source_area="living_room",
                        user_id=reader.id,
                        conversation_id="e3_reader",
                    ),
                    Context(user_id=reader.id),
                )
            ).to_dict()
            assert result["status"] == "error" and result["reason"] == "execution_failed", result
            capability(result, "failed", "none", value=45.0)
            assert not dimmer.operations
            assert_no_executor_calls(result)

        with case(report, "e3_catalog_unmigrated_light_on_off_has_no_catalog_metadata"):
            await reset()
            for text in ("włącz ściemniacz katalogowy", "wyłącz ściemniacz katalogowy"):
                result = await command(text, dry_run=False)
                assert result["status"] == "executed" and result["completed"] == [
                    dimmer.entity_id
                ], result
                assert "capability" not in result["response_data"], result
            assert len(dimmer.operations) == 2 and len(calls) == 2

        report["e3_capability_catalog"] = {
            "native_registry_websocket_diagnostics_and_entity_services": True,
            "synthetic_provider_only": True,
            "partial_catalog_operations": ["light.set_brightness", "sensor.read_temperature"],
            "golden_legacy_behavior_verified_separately": True,
            "requested_and_observed_facts_distinct": True,
            "preview_confirmation_execution_and_failure_evidence_checked": True,
            "cached_temperature_values_remain_live_without_metadata_rebuild": True,
            "fresh_exposure_and_ha_permissions_checked": True,
            "physical_devices_audio_and_n5105_not_tested": True,
        }
    finally:
        unsubscribe()
        hass.states.async_set(
            TEMPERATURE, original_temperature.state, dict(original_temperature.attributes)
        )
        runtime.dialogue.clear()
        runtime.pending.clear()
        await dimmer.async_remove()
        if registry.async_get(dimmer.entity_id):
            registry.async_remove(dimmer.entity_id)
        if created_label:
            labels.async_delete(confirm_label.label_id)
        if reader is not None:
            await hass.auth.async_remove_user(reader)
        await hass.async_block_till_done()
