"""Native options-flow checks for existing Stoic and Music Assistant content."""

import json
from copy import deepcopy

import probatio as vol
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations


async def exercise_content_options(hass, entry, client, report, case):
    original = deepcopy(dict(entry.options))
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create("sensor", "pcv_content_lab", "quote")
    entity_id = sensor.entity_id
    hass.states.async_set(entity_id, "2026-10-29", {"quote": "Syntetyczny cytat."})
    exposed = exposed_entities.async_should_expose(hass, "conversation", entity_id)

    async def submit(flow, data):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=data)
        await hass.async_block_till_done()
        return result

    async def form():
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert "content" in flow["menu_options"]
        return await submit(flow, {"next_step_id": "content"})

    def marker(flow, name):
        return next(key for key in flow["data_schema"].schema if key.schema == name)

    def abort(flow):
        hass.config_entries.options.async_abort(flow["flow_id"])

    try:
        with case(report, "content_options_optional_sources_and_native_back_discard_draft"):
            flow = await form()
            assert flow["type"] == "form" and flow["last_step"] is False
            assert {key.schema for key in flow["data_schema"].schema} == {
                "back",
                "stoic_entity_id",
                "media_search_enabled",
                "media_search_library_first",
                "media_search_username",
            }
            assert marker(flow, "stoic_entity_id").default is vol.UNDEFINED
            assert marker(flow, "media_search_enabled").default() is True
            assert marker(flow, "media_search_library_first").default() is False
            navigation = json.loads(flow["description_placeholders"]["pcv_navigation"])
            assert navigation == {"domain": "polish_context_voice", "data": {"back": True}}
            flow = await submit(
                flow,
                {
                    **navigation["data"],
                    "stoic_entity_id": "sensor.content_missing",
                    "media_search_username": "invalid\nusername",
                },
            )
            assert flow["step_id"] == "init"
            assert dict(entry.options) == original
            abort(flow)

        with case(report, "content_options_validate_sensor_and_ma_username_without_saving"):
            flow = await form()
            flow = await submit(flow, {"stoic_entity_id": "sensor.content_missing"})
            assert flow["errors"] == {"stoic_entity_id": "content_sensor_missing"}
            assert marker(flow, "stoic_entity_id").description == {
                "suggested_value": "sensor.content_missing"
            }
            flow = await submit(
                flow, {"stoic_entity_id": entity_id, "media_search_username": "x" * 129}
            )
            assert flow["errors"] == {"media_search_username": "content_username_invalid"}
            flow = await submit(
                flow,
                {"stoic_entity_id": entity_id, "media_search_username": "invalid\nusername"},
            )
            assert flow["errors"] == {"media_search_username": "content_username_invalid"}
            flow = await submit(
                flow, {"stoic_entity_id": entity_id, "media_search_username": "\nReader\n"}
            )
            assert flow["errors"] == {"media_search_username": "content_username_invalid"}
            assert dict(entry.options) == original
            abort(flow)

        with case(report, "content_options_save_clear_preserve_preferences_and_assist_exposure"):
            sentinel = {"nested": ["existing-value"]}
            hass.config_entries.async_update_entry(
                entry, options={**original, "content_lab_existing_preference": sentinel}
            )
            await hass.async_block_till_done()
            flow = await form()
            result = await submit(
                flow,
                {
                    "stoic_entity_id": entity_id,
                    "media_search_enabled": False,
                    "media_search_library_first": True,
                    "media_search_username": "  Reader  ",
                },
            )
            assert result["type"] == "create_entry"
            assert entry.options["stoic_entity_id"] == entity_id
            assert entry.options["media_search_enabled"] is False
            assert entry.options["media_search_library_first"] is True
            assert entry.options["media_search_username"] == "Reader"
            assert entry.options["content_lab_existing_preference"] == sentinel
            changed = {
                "stoic_entity_id",
                "media_search_enabled",
                "media_search_library_first",
                "media_search_username",
            }
            assert all(
                entry.options.get(key) == value
                for key, value in original.items()
                if key not in changed
            )
            assert exposed_entities.async_should_expose(hass, "conversation", entity_id) == exposed
            assert hass.states.get(entity_id).attributes["quote"] == "Syntetyczny cytat."
            flow = await form()
            assert marker(flow, "stoic_entity_id").default is vol.UNDEFINED
            assert marker(flow, "stoic_entity_id").description == {"suggested_value": entity_id}
            assert marker(flow, "media_search_username").default is vol.UNDEFINED
            assert marker(flow, "media_search_username").description == {
                "suggested_value": "Reader"
            }
            result = await submit(
                flow,
                {
                    "media_search_enabled": True,
                    "media_search_library_first": False,
                },
            )
            assert result["type"] == "create_entry"
            assert entry.options["stoic_entity_id"] is None
            assert entry.options["media_search_username"] is None
            assert entry.options["content_lab_existing_preference"] == sentinel

        with case(
            report, "content_options_loaded_translations_describe_fields_sources_and_navigation"
        ):
            flow = await form()
            for language in ("pl", "en", "en-GB", "de"):
                resources = await async_get_translations(
                    hass, language, "options", {"polish_context_voice"}
                )
                prefix = "component.polish_context_voice.options.step."
                for key in ("menu_options", "menu_option_descriptions"):
                    assert resources[prefix + "init." + key + ".content"].strip()
                for key in ("title", "description"):
                    assert resources[prefix + "content." + key].strip()
                for key in flow["data_schema"].schema:
                    assert resources[prefix + "content.data." + key.schema].strip()
                    assert resources[prefix + "content.data_description." + key.schema].strip()
                for error in ("content_sensor_missing", "content_username_invalid"):
                    key = f"component.polish_context_voice.options.error.{error}"
                    assert resources[key].strip()
            abort(flow)
    finally:
        hass.config_entries.async_update_entry(entry, options=original)
        await hass.async_block_till_done()
        registry.async_remove(entity_id)
        hass.states.async_remove(entity_id)
