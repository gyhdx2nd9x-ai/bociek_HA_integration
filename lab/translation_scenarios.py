"""Verify the resources the HA frontend actually receives, not only strings.json."""

import json
from string import Formatter

from homeassistant.helpers.translation import async_get_translations


async def exercise_translations(hass, entry, report, case):
    domain = "polish_context_voice"
    catalogs = {}
    for language in ("pl", "en", "en-GB", "de"):
        with case(report, "ui_translations_available_" + language):
            resources = await async_get_translations(hass, language, "options", {domain})
            selectors = await async_get_translations(hass, language, "selector", {domain})
            catalogs[language] = resources | selectors
            prefix = f"component.{domain}.options.step.init."
            assert resources[prefix + "title"]
            assert resources[prefix + "description"]
            for key in ("voice_roles", "general", "music_rooms", "music_shortcuts", "voice_trial"):
                assert resources[prefix + "menu_options." + key]
                assert resources[prefix + "menu_option_descriptions." + key]

    def verify(flow):
        step = flow["step_id"]
        prefix = f"component.{domain}.options.step.{step}."
        for language, resources in catalogs.items():
            for name in ("title", "description"):
                text = resources[prefix + name]
                assert text.strip(), (language, step, name)
                placeholders = {f for _, f, _, _ in Formatter().parse(text) if f}
                if name == "title":
                    assert not placeholders, (language, step, "titles cannot use placeholders")
                assert placeholders <= (flow.get("description_placeholders") or {}).keys(), (
                    language,
                    step,
                    placeholders,
                )
            if flow["type"] == "menu":
                for key in flow["menu_options"]:
                    assert resources[prefix + "menu_options." + key]
                    assert resources[prefix + "menu_option_descriptions." + key]
            else:
                for marker, field in flow["data_schema"].schema.items():
                    assert resources[prefix + "data." + marker.schema]
                    if step.startswith("voice_"):
                        assert resources[prefix + "data_description." + marker.schema]
                    config = getattr(field, "config", {})
                    if translation := config.get("translation_key"):
                        for key in config["options"]:
                            assert resources[
                                f"component.{domain}.selector.{translation}.options.{key}"
                            ]

    async def submit(flow, data):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=data)
        if result["type"] in {"form", "menu"}:
            verify(result)
        return result

    with case(report, "ui_wizard_all_steps_have_labels_help_and_complete_placeholders"):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        verify(flow)
        flow = await submit(flow, {"next_step_id": "voice_roles"})
        flow = await submit(flow, {"area": "living_room"})
        flow = await submit(flow, {"next_step_id": "voice_lighting"})
        flow = await submit(flow, {"next_step_id": "voice_lights"})
        assert flow["last_step"] is False
        flow = await submit(
            flow,
            {
                "voice_default_on": ["light.lab_main_lights"],
                "voice_all_off": [
                    "light.lab_main_lights",
                    "light.lab_table_light",
                    "light.lab_ambient_light",
                ],
                "action": "review",
            },
        )
        flow = await submit(flow, {"action": "apply"})  # Existing sets: no label changes.
        flow = await submit(flow, {"action": "room"})
        flow = await submit(flow, {"next_step_id": "voice_entity"})
        flow = await submit(flow, {"entity": "light.lab_main_lights"})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"next_step_id": "voice_lighting"})
        flow = await submit(flow, {"next_step_id": "voice_light_types"})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"next_step_id": "voice_preset"})
        flow = await submit(flow, {"profile": "main"})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"next_step_id": "voice_profiles"})
        flow = await submit(flow, {"profile": "main"})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"next_step_id": "voice_room"})
        result = await submit(flow, {"next_step_id": "voice_done"})
        assert result["type"] == "create_entry"

    with case(report, "ui_new_calendar_forms_have_labels_help_and_back"):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        verify(flow)
        flow = await submit(flow, {"next_step_id": "calendar"})
        flow = await submit(flow, {"back": True})
        flow = await submit(flow, {"next_step_id": "calendar_kiosk"})
        flow = await submit(flow, {"back": True})
        assert flow["type"] == "menu"

    with case(report, "ui_command_trial_uses_room_and_does_not_write_devices_or_settings"):
        entity_id = "light.lab_kitchen_light"
        before = hass.states.get(entity_id)
        options = entry.options.copy()
        runtime = hass.data[domain][entry.entry_id]
        diagnostics = runtime.diagnostics.snapshot()
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await submit(flow, {"next_step_id": "voice_trial"})
        flow = await submit(flow, {"text": "   "})
        assert flow["errors"] == {"text": "voice_trial_text"}, flow
        flow = await submit(flow, {"text": "Zapal światło", "area": "kitchen"})
        assert flow["step_id"] == "voice_trial_result", flow
        rendered = flow["description_placeholders"]
        assert rendered["targets"] == before.name
        assert rendered["areas"] == "Kitchen"
        assert rendered["actions"] == r"light\.turn\_on"
        assert "pokoju wybranego w formularzu" in rendered["explanation"]
        assert "Włączane domyślnie" in rendered["explanation"]
        flow = await submit(flow, {"back": True})
        assert flow["step_id"] == "voice_trial"
        suggested = {field.schema: field.description for field in flow["data_schema"].schema}
        assert suggested["text"]["suggested_value"] == "Zapal światło"
        assert suggested["area"]["suggested_value"] == "kitchen"
        await submit(flow, {"back": True})
        assert hass.states.get(entity_id) is before and entry.options == options
        assert runtime.diagnostics.snapshot() == diagnostics

    with case(report, "ui_command_trial_explains_alias_scope_and_hidden_targets"):
        table = hass.states.get("light.lab_table_light").name
        hidden = hass.states.get("light.lab_hidden_light").name
        for command, area, expected, devices in (
            ("Zapal lampę przy stole w salonie", "kitchen", "Dopasowany alias", table),
            ("Zapal lampę przy stole", "kitchen", "poza wybranym pomieszczeniem", "—"),
            (f"Zapal {hidden}", "living_room", "encje nieudostępnione w Assist", "—"),
            ("Zapal światło w kosmosie", "kitchen", "Sprawdź nazwę lub alias pomieszczenia", "—"),
        ):
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            flow = await submit(flow, {"next_step_id": "voice_trial"})
            flow = await submit(flow, {"text": command, "area": area})
            rendered = flow["description_placeholders"]
            assert expected in rendered["explanation"], (command, rendered)
            assert rendered["targets"] == devices, (command, rendered)
            assert hidden not in rendered["explanation"]
            if devices != "—":
                assert rendered["areas"] == "Living Room", rendered
                assert "podanej w poleceniu" in rendered["explanation"], rendered
            else:
                assert rendered["actions"] == "—", rendered
            assert (await submit(flow, {}))["type"] == "menu"

    with case(report, "ui_language_switch_pl_en_gb_en_keeps_complete_menu_resources"):
        for language in ("pl", "en", "en-GB", "en", "pl", "en"):
            resources = await async_get_translations(hass, language, "options", {domain})
            assert resources[f"component.{domain}.options.step.init.title"]
            assert resources[
                f"component.{domain}.options.step.voice_lighting.menu_options.voice_preset"
            ]

    with case(report, "ui_feedback_example_is_manually_entered_reviewed_and_replayable"):
        # The lab must load the installed copy of the component before the replay imports it.
        from benchmark.feedback import replay

        options = entry.options.copy()
        runtime = hass.data[domain][entry.entry_id]
        diagnostics = runtime.diagnostics.snapshot()
        source = "Fikcyjna próba z prywatnym tekstem kontrolnym"
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await submit(flow, {"next_step_id": "voice_trial"})
        flow = await submit(flow, {"text": source, "area": "kitchen"})
        flow = await submit(flow, {"next": "example"})
        assert flow["step_id"] == "voice_feedback_example"
        suggested = {field.schema: field.description for field in flow["data_schema"].schema}
        assert not suggested["text"] and not suggested["source_area"]
        flow = await submit(flow, {"text": "  "})
        assert flow["errors"] == {"text": "voice_feedback_text"}
        flow = await submit(flow, {
            "text": "Zapal światła", "source_area": "kitchen", "expected_status": "dry_run",
            "operation": "turn_on", "targets": ["light.kitchen_main"],
        })
        assert flow["step_id"] == "voice_feedback_review"
        exported = flow["description_placeholders"]["report"]
        assert source not in exported and "lab_" not in exported
        played = await replay(json.loads(exported))
        assert played["result"]["passed"], played
        assert played["result"]["turns"][0]["actual"]["executor_calls"] == []
        flow = await submit(flow, {"back": True})
        suggested = {field.schema: field.description for field in flow["data_schema"].schema}
        assert suggested["text"]["suggested_value"] == "Zapal światła"
        flow = await submit(flow, {"back": True})
        assert flow["step_id"] == "voice_trial_result"
        flow = await submit(flow, {"next": "idea"})
        assert flow["step_id"] == "voice_feedback_idea"
        idea = "Opis przykładu ``` <tag> z fikcyjnym urządzeniem."
        flow = await submit(flow, {"idea": idea})
        rendered = flow["description_placeholders"]["report"]
        assert "```" not in rendered and "<tag>" not in rendered
        assert json.loads(rendered)["idea"] == idea
        flow = await submit(flow, {})
        assert flow["step_id"] == "voice_trial_result"
        assert (await submit(flow, {}))["type"] == "menu"
        assert runtime.diagnostics.snapshot() == diagnostics and entry.options == options

    with case(report, "ui_feedback_dialogue_replays_room_and_state_without_private_trial"):
        from benchmark.feedback import replay

        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await submit(flow, {"next_step_id": "voice_trial"})
        flow = await submit(flow, {"text": "Zapal światło w prywatnym przykładzie"})
        flow = await submit(flow, {"next": "dialogue"})
        assert flow["step_id"] == "voice_feedback_dialogue"
        assert flow["description_placeholders"]["turn"] == "1"
        first = {
            "text": "Zgaś zapalone światła", "source_area": "kitchen",
            "expected_status": "dry_run", "operation": "turn_off",
            "targets": ["light.kitchen_main"],
            "lights_off": ["light.kitchen_task"],
        }
        flow = await submit(flow, first)
        assert flow["description_placeholders"]["turn"] == "2"
        assert "source_area" not in {field.schema for field in flow["data_schema"].schema}
        flow = await submit(flow, {"back": True})
        draft = {field.schema: field.description for field in flow["data_schema"].schema}
        assert draft["source_area"]["suggested_value"] == "kitchen"
        assert draft["lights_off"]["suggested_value"] == ["light.kitchen_task"]
        flow = await submit(flow, first)
        second = {
            "text": "A w salonie?", "expected_status": "dry_run", "operation": "turn_off",
            "targets": ["light.living_main"], "lights_off": ["light.living_table"],
            "advance_seconds": 2, "turn_action": "review",
        }
        flow = await submit(flow, {**second, "lights_on": ["light.living_table"]})
        assert flow["errors"] == {"lights_off": "voice_feedback_lights_off"}, flow
        flow = await submit(flow, second)
        assert flow["step_id"] == "voice_feedback_review"
        raw = flow["description_placeholders"]["report"]
        assert "prywatnym" not in raw
        replayed = await replay(json.loads(raw))
        assert replayed["result"]["passed"], replayed
        assert [t["actual"]["area_ids"] for t in replayed["result"]["turns"]] == [
            ("kitchen",), ("living_room",),
        ]
        flow = await submit(flow, {"back": True})
        draft = {field.schema: field.description for field in flow["data_schema"].schema}
        assert draft["text"]["suggested_value"] == "A w salonie?"
        flow = await submit(flow, second)
        assert (await submit(flow, {}))["step_id"] == "voice_trial_result"

    report["frontend_translation_scope"] = (
        "Native HA translation loader: PL, EN, en-GB and DE fallback; options flow labels, "
        "descriptions, field help, selectors, interpolation and successful completion. "
        "Separate from browser rendering."
    )
