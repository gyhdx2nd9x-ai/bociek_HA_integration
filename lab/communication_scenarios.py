"""Native Conversation and automation blueprint; no microphone or speaker audio."""

import asyncio
from pathlib import Path
from types import SimpleNamespace as NS

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.conversation.chat_log import async_get_chat_log
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util

from .assist_pipeline_scenarios import open_chat_session
from .capability_scenarios import Player

ROOT = Path(__file__).resolve().parents[1]


async def eventually(predicate, seconds=4):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.02)


async def exercise_communication(hass, entry, fixture, client, agent, user, report, case):
    original_options = dict(entry.options)
    signal = "input_boolean.lab_quiet"
    hass.states.async_set(signal, "on")

    async def voice(text, **kwargs):
        return await client.call(
            "conversation/process",
            agent_id=agent,
            language="pl",
            text=text,
            **({"satellite_id": fixture["satellite"]} | kwargs),
        )

    def speech(result):
        return result["response"]["speech"]["plain"]["speech"]

    try:
        with case(report, "communication_options_flow_validation_and_reload"):
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], user_input={"next_step_id": "general"}
            )
            try:
                await hass.config_entries.options.async_configure(
                    flow["flow_id"],
                    user_input={**original_options, "maintenance_battery_threshold": 0},
                )
            except InvalidData as err:
                assert err.path == ["maintenance_battery_threshold"], err
            else:
                raise AssertionError("HA accepted a threshold outside the selector range")
            invalid_threshold = await hass.config_entries.options.async_configure(
                flow["flow_id"],
                user_input={**original_options, "maintenance_battery_threshold": 20.5},
            )
            assert invalid_threshold["errors"] == {"base": "invalid_battery_threshold"}, (
                invalid_threshold
            )
            invalid = await hass.config_entries.options.async_configure(
                flow["flow_id"], user_input={**original_options, "quiet_start": "25:00"}
            )
            assert invalid["errors"] == {"base": "invalid_quiet_time"}, invalid
            result = await hass.config_entries.options.async_configure(
                flow["flow_id"],
                user_input={
                    **original_options,
                    "quiet_ack_enabled": True,
                    "maintenance_battery_threshold": 30,
                    "quiet_start": "00:00",
                    "quiet_end": "00:00",
                    "quiet_signals": [signal],
                    "quiet_areas": ["living_room"],
                },
            )
            assert result["type"] == "create_entry", result
            await hass.async_block_till_done()
            assert entry.state.value == "loaded"
            runtime = hass.data["polish_context_voice"][entry.entry_id]
            assert runtime.maintenance_battery_threshold == 30

        with case(report, "communication_silent_local_light_complete_and_no_chat_text"):
            result = await voice("zapal światła")
            assert speech(result) == "", result
            assert result["response"]["response_type"] == "action_done"
            assert result["response"]["speech"]["plain"]["extra_data"]["silent_ack"] is True
            assert result["response"]["data"]["success"][0]["id"] == "light.lab_main_lights"
            assert hass.states.is_state("light.lab_main_lights", "on")
            assert not result["continue_conversation"]
            with (
                open_chat_session(hass, result["conversation_id"], user) as session,
                async_get_chat_log(hass, session) as log,
            ):
                assert log.content[-1].content == ""  # Nothing for streamed TTS either.

        with case(report, "communication_queries_errors_remote_rooms_and_text_remain_spoken"):
            for text, extras in (
                ("jaka jest temperatura", {}),
                ("Czy wszystkie urządzenia są sprawne?", {}),
                ("zapal światła w kuchni", {}),
                ("zapal światła w garażu", {}),
                ("wyłącz gniazdo testowe", {}),
                ("zapal światła w salonie", {"satellite_id": None}),
                ("odlicz dziesięć sekund", {}),
            ):
                assert speech(await voice(text, **extras)), (text, extras)
            preview = await client.call(
                "polish_context_voice/process",
                text="zapal światła",
                satellite_id=fixture["satellite"],
                dry_run=True,
            )
            assert preview["speech"] and preview["status"] == "dry_run"
            hass.states.async_set(signal, "unknown")
            assert speech(await voice("zapal światła"))
            hass.states.async_set(signal, "on")

        with case(report, "communication_varied_acknowledgements_preserve_quiet_and_results"):
            from custom_components.polish_context_voice.core.presentation import ACKNOWLEDGEMENTS

            hass.states.async_set(signal, "off")
            replies = []
            # This case checks varied acknowledgements for actual state changes.
            # Already-satisfied commands now receive separate state feedback.
            await hass.services.async_call(
                "light", "turn_off", {"entity_id": "light.lab_main_lights"}, blocking=True
            )
            await hass.async_block_till_done()
            for text in ("zapal światła", "wyłącz światła") * 3:
                result = await voice(text)
                replies.append(speech(result))
                assert result["response"]["response_type"] == "action_done"
                assert result["response"]["data"]["success"]
            assert set(replies) == set(ACKNOWLEDGEMENTS), replies
            assert all(a != b for a, b in zip(replies, replies[1:], strict=False)), replies
            hass.states.async_set(signal, "on")
            assert speech(await voice("zapal światła")) == ""

        with case(report, "communication_native_pipeline_skips_tts_for_empty_speech"):
            from homeassistant.components.assist_pipeline import PipelineStage
            from homeassistant.components.assist_pipeline.default_pipeline import (
                _DefaultPipelineProcessor,
            )

            tts_calls = []

            class Processor(_DefaultPipelineProcessor):
                async def recognize_intent(self, text, conversation_id, extra):
                    return speech(await voice(text)), False

                async def text_to_speech(self, text, override_media_path=None):
                    tts_calls.append(text)

            processor = Processor(
                NS(
                    start_stage=PipelineStage.INTENT,
                    end_stage=PipelineStage.TTS,
                    audio_settings=NS(needs_processor=False),
                )
            )
            request = NS(
                stt_stream=None,
                intent_input="zapal światła",
                tts_input=None,
                session=NS(conversation_id=None),
                conversation_extra_system_prompt=None,
            )
            await processor.async_execute(request)
            assert not tts_calls
            request.intent_input = "która godzina"
            await processor.async_execute(request)
            assert len(tts_calls) == 1 and tts_calls[0]

        # Load the shipped blueprint using native HA substitution, then its actual automation.
        player = Player("Lab Quiet Echo")
        player._attr_state = "idle"
        player._attr_is_volume_muted = False
        assert await async_setup_component(hass, "media_player", {})
        await hass.data["media_player"].async_add_entities([player])
        registry = er.async_get(hass)
        registry.async_update_entity(
            player.entity_id, device_id=fixture["devices"]["living_room"][1]
        )
        path = ROOT / "deployment/blueprints/automation/polish_context_voice/quiet_response.yaml"
        blueprint = Blueprint(yaml_util.load_yaml(str(path)), schema=AUTOMATION_BLUEPRINT_SCHEMA)
        inputs = BlueprintInputs(
            blueprint,
            {
                "id": "lab_quiet_response",
                "alias": "Lab quiet response",
                "use_blueprint": {
                    "path": "polish_context_voice/quiet_response.yaml",
                    "input": {
                        "satellite": fixture["satellite"],
                        "player": player.entity_id,
                        "quiet_start": "00:00:00",
                        "quiet_end": "00:00:00",
                        "quiet_signals": [signal],
                    },
                },
            },
        )
        inputs.validate()
        config = inputs.async_substitute()
        with case(report, "communication_native_blueprint_schema_and_setup"):
            assert await async_setup_component(hass, "automation", {"automation": [config]})
            await hass.async_block_till_done()
            assert hass.states.is_state("automation.lab_quiet_response", "on")

        async def reset(volume=0.5, state="idle"):
            hass.states.async_set(fixture["satellite"], "idle")
            await asyncio.sleep(1.1)
            player._attr_state = state
            player._attr_is_volume_muted = False
            player._attr_volume_level = volume
            player.async_write_ha_state()
            await hass.async_block_till_done()
            player.operations.clear()

        def satellite(state):
            hass.states.async_set(fixture["satellite"], state)

        with case(report, "communication_volume_cap_followup_then_restore_exact_value"):
            await reset()
            satellite("listening")
            await eventually(lambda: player.operations == [0.2])
            satellite("responding")
            satellite("listening")  # Follow-up cannot replace the original volume snapshot.
            await asyncio.sleep(0.05)
            assert player.operations == [0.2]
            satellite("idle")
            await eventually(lambda: player.operations == [0.2, 0.5])

        with case(report, "communication_manual_volume_change_wins_over_restore"):
            await reset()
            satellite("listening")
            await eventually(lambda: player.operations == [0.2])
            await player.async_set_volume_level(0.12)
            await asyncio.sleep(0.05)
            satellite("idle")
            await asyncio.sleep(1.1)
            assert player.operations == [0.2, 0.12] and player.volume_level == 0.12

        with case(report, "communication_does_not_raise_volume_or_override_music_or_unknown"):
            for volume, state in ((0.1, "idle"), (0.5, "playing"), (None, "idle")):
                await reset(volume, state)
                satellite("listening")
                await asyncio.sleep(0.1)
                assert player.operations == [], (volume, state, player.operations)
            await reset()
            hass.states.async_set(signal, "unknown")
            satellite("listening")
            await asyncio.sleep(0.1)
            assert not player.operations
            hass.states.async_set(signal, "on")

        with case(report, "communication_wrong_device_and_disconnect_do_not_restore"):
            await reset()
            registry.async_update_entity(
                player.entity_id, device_id=fixture["devices"]["kitchen"][0]
            )
            satellite("listening")
            await asyncio.sleep(0.1)
            assert not player.operations
            registry.async_update_entity(
                player.entity_id, device_id=fixture["devices"]["living_room"][1]
            )
            await reset()
            satellite("listening")
            await eventually(lambda: player.operations == [0.2])
            satellite("unavailable")
            await asyncio.sleep(0.1)
            satellite("idle")
            await asyncio.sleep(1.1)
            assert player.operations == [0.2]
        report["communication_scope"] = (
            "Native Conversation, pipeline TTS-skip branch and automation blueprint; "
            "virtual states/player, no EchoMuse audio"
        )
    finally:
        hass.config_entries.async_update_entry(entry, options=original_options)
        await hass.async_block_till_done()
