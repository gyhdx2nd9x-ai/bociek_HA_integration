"""Options flow and registry mutation tests against real HA, with synthetic devices."""

from copy import deepcopy
from time import perf_counter
from unittest.mock import patch

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr


def defaults(flow):
    return {
        marker.schema: marker.default()
        for marker in flow["data_schema"].schema
        if callable(marker.default)
    }


async def exercise_roles(hass, entry, client, report, case):
    from custom_components.polish_context_voice.ha.role_editor import (
        RolePlan,
        apply_roles,
        read_roles,
    )

    registry, labels = er.async_get(hass), lr.async_get(hass)
    areas, devices = ar.async_get(hass), dr.async_get(hass)
    area = areas.async_create("Oranżeria", aliases={"oranżerii"})
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("pcv_roles_lab", "synthetic")},
        name="Lampa testowa",
    )
    devices.async_update_device(device.id, area_id=area.id)
    ids = []
    for domain, name in (
        ("light", "a"),
        ("light", "b"),
        ("light", "hidden"),
        ("sensor", "temperature"),
        ("todo", "no_area"),
    ):
        entity = registry.async_get_or_create(
            domain, "pcv_roles_lab", name, suggested_object_id="role_" + name
        )
        registry.async_update_entity(
            entity.entity_id,
            device_id=device.id if name == "a" else None,
            area_id=area.id if name not in {"a", "no_area"} else None,
        )
        hass.states.async_set(entity.entity_id, "20" if domain == "sensor" else "off")
        exposed_entities.async_expose_entity(
            hass, "conversation", entity.entity_id, name != "hidden"
        )
        ids.append(entity.entity_id)
    a, b, hidden, temperature, todo = ids
    custom = labels.async_create("Własna etykieta — zachowaj")
    registry.async_update_entity(a, labels={custom.label_id})
    original_options = deepcopy(dict(entry.options))
    started = perf_counter()

    async def submit(flow, data):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=data)
        await hass.async_block_till_done()
        return result

    async def open_room(step, *, room=area.id, include_hidden=False):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await submit(flow, {"next_step_id": "voice_roles"})
        flow = await submit(flow, {"area": room, "include_hidden": include_hidden})
        if step == "voice_lights":
            flow = await submit(flow, {"next_step_id": "voice_lighting"})
        return await submit(flow, {"next_step_id": step})

    async def edit(key, roles, **kwargs):
        flow = await open_room("voice_entity", **kwargs)
        flow = await submit(flow, {"entity": key})
        return await submit(flow, {"roles": roles})

    def roles(key):
        return read_roles(hass)[0][key].direct

    def abort(flow):
        hass.config_entries.options.async_abort(flow["flow_id"])

    try:
        with case(report, "roles_wizard_suggestions_are_opt_in_and_do_not_write"):
            count = len(list(labels.async_list_labels()))
            flow = await open_room("voice_lights")
            assert defaults(flow)["voice_default_on"] == []
            flow = await submit(flow, {"action": "suggest"})
            assert defaults(flow)["voice_default_on"] == []  # No guessed main among two.
            assert set(defaults(flow)["voice_all_off"]) == {a, b}
            assert not roles(a) and not roles(b) and not roles(hidden)
            assert len(list(labels.async_list_labels())) == count
            abort(flow)

        with case(report, "roles_wizard_preview_edit_and_cancel_preserve_draft_and_registry"):
            flow = await open_room("voice_lights")
            selection = {"voice_default_on": [a], "voice_all_off": [a, b], "action": "review"}
            flow = await submit(flow, selection)
            assert (
                flow["step_id"] == "voice_review"
                and a in flow["description_placeholders"]["changes"]
            )
            assert not roles(a)
            flow = await submit(flow, {"action": "edit"})
            assert defaults(flow)["voice_default_on"] == [a]
            flow = await submit(flow, selection)
            flow = await submit(flow, {"action": "cancel"})
            assert not roles(a) and not roles(b)
            abort(flow)

        with case(report, "roles_wizard_apply_changes_only_native_labels_and_cache_targets"):
            flow = await open_room("voice_lights")
            flow = await submit(flow, selection)
            flow = await submit(flow, {"action": "apply"})
            assert flow["step_id"] == "voice_saved"
            assert roles(a) == {"voice_default_on", "voice_all_off"}
            assert roles(b) == {"voice_all_off"}
            assert custom.label_id in registry.async_get(a).labels
            assert registry.async_get(a).device_id == device.id
            assert entry.options == original_options
            assert not roles(hidden)
            for text, expected in (("zapal światła", [a]), ("wyłącz światła", [a, b])):
                result = await client.call(
                    "polish_context_voice/process", text=text, source_area=area.id
                )
                assert set(result["targets"]) == set(expected), result
            assert hass.states.get(a).state == "off"  # Wizard/preview never actuates.
            flow = await submit(flow, {"action": "done"})
            assert flow["type"] == "create_entry"
            assert entry.options == original_options

        with case(report, "roles_wizard_reopen_edit_remove_and_preserve_other_roles"):
            confirm = labels.async_get_label_by_name("voice_confirm") or labels.async_create(
                "voice_confirm"
            )
            registry.async_update_entity(
                a, labels=registry.async_get(a).labels | {confirm.label_id}
            )
            flow = await open_room("voice_lights")
            assert defaults(flow)["voice_default_on"] == [a]
            flow = await submit(
                flow, {"voice_default_on": [], "voice_all_off": [b], "action": "review"}
            )
            flow = await submit(flow, {"action": "apply"})
            assert roles(a) == {"voice_confirm"}
            assert custom.label_id in registry.async_get(a).labels
            abort(flow)
            flow = await edit(a, [])
            flow = await submit(flow, {"action": "apply"})
            assert registry.async_get(a).labels == {custom.label_id}
            abort(flow)

        with case(report, "roles_wizard_optional_hidden_entities_never_change_exposure"):
            flow = await open_room("voice_entity")
            options = next(iter(flow["data_schema"].schema.values())).config["options"]
            assert hidden not in {item["value"] for item in options}
            abort(flow)
            flow = await edit(hidden, ["voice_all_off"], include_hidden=True)
            flow = await submit(flow, {"action": "apply"})
            assert roles(hidden) == {"voice_all_off"}
            assert not exposed_entities.async_should_expose(hass, "conversation", hidden)
            abort(flow)

        with case(report, "roles_wizard_domain_roles_unassigned_area_and_draft_back"):
            flow = await open_room("voice_entity")
            flow = await submit(flow, {"entity": a})
            options = next(iter(flow["data_schema"].schema.values())).config["options"]
            assert "voice_primary" in {item["value"] for item in options}
            abort(flow)
            flow = await open_room("voice_entity")
            flow = await submit(flow, {"entity": temperature})
            options = next(iter(flow["data_schema"].schema.values())).config["options"]
            assert "voice_all_off" not in {item["value"] for item in options}
            assert "voice_primary" in {item["value"] for item in options}
            abort(flow)
            flow = await edit(todo, ["voice_shopping"], room="__unassigned__")
            flow = await submit(flow, {"action": "edit"})
            assert defaults(flow)["roles"] == ["voice_shopping"]
            flow = await submit(flow, {"roles": ["voice_shopping"]})
            flow = await submit(flow, {"action": "apply"})
            assert roles(todo) == {"voice_shopping"}
            abort(flow)

        with case(report, "roles_wizard_rejects_stale_entity_labels_and_preserves_manual_edit"):
            flow = await edit(a, ["voice_default_on"])
            registry.async_update_entity(
                a, labels=registry.async_get(a).labels | {confirm.label_id}
            )
            result = await submit(flow, {"action": "apply"})
            assert result["reason"] == "voice_stale", result
            assert roles(a) == {"voice_confirm"}

        with case(report, "roles_wizard_rejects_exposure_change_while_editing"):
            flow = await edit(b, ["voice_default_on"])
            exposed_entities.async_expose_entity(hass, "conversation", b, False)
            result = await submit(flow, {"action": "apply"})
            assert result["reason"] == "voice_stale", result
            assert roles(b) == {"voice_all_off"}
            exposed_entities.async_expose_entity(hass, "conversation", b, True)

        with case(report, "roles_wizard_inherited_area_and_device_guards_are_visible_and_enforced"):
            block = labels.async_get_label_by_name("voice_block") or labels.async_create(
                "voice_block"
            )
            for update, parent_id in (
                (devices.async_update_device, device.id),
                (areas.async_update, area.id),
            ):
                update(parent_id, labels={block.label_id})
                flow = await open_room("voice_entity")
                flow = await submit(flow, {"entity": a})
                assert "Wyklucz" in flow["description_placeholders"]["inherited"]
                flow = await submit(flow, {"roles": ["voice_default_on"]})
                assert flow["errors"]["base"] == "voice_conflict", flow
                abort(flow)
                update(parent_id, labels=set())

        with case(report, "roles_wizard_rejects_parent_guard_change_and_deleted_entity"):
            flow = await edit(a, ["voice_default_on"])
            devices.async_update_device(device.id, labels={block.label_id})
            result = await submit(flow, {"action": "apply"})
            assert result["reason"] == "voice_stale"
            devices.async_update_device(device.id, labels=set())
            flow = await edit(temperature, ["voice_primary"])
            registry.async_remove(temperature)
            result = await submit(flow, {"action": "apply"})
            assert result["reason"] == "voice_stale"

        with case(report, "roles_wizard_label_rename_detected_no_slug_guessing"):
            flow = await edit(b, ["voice_default_on"])
            labels.async_update(custom.label_id, name="Zmieniona ręcznie")
            result = await submit(flow, {"action": "apply"})
            assert result["reason"] == "voice_stale", result

        with case(report, "roles_wizard_case_conflict_fails_before_any_write"):
            existing = labels.async_get_label_by_name("voice_task")
            if existing:
                labels.async_update(existing.label_id, name="VOICE_TASK")
            else:
                existing = labels.async_create("VOICE_TASK")
            flow = await edit(b, ["voice_task"])
            result = await submit(flow, {"action": "apply"})
            assert result["reason"] == "voice_label_name", result
            assert roles(b) == {"voice_all_off"}
            labels.async_update(existing.label_id, name="voice_task")

        with case(report, "roles_wizard_partial_failure_reports_completed_entities"):
            records, stamp = read_roles(hass)
            plan = RolePlan(
                {key: records[key] for key in (a, b)},
                stamp,
                {a: frozenset({"voice_main"}), b: frozenset({"voice_main"})},
            )
            original_update = registry.async_update_entity

            def fail_second(key, **kwargs):
                if key == b:
                    raise RuntimeError("Simulated registry failure")
                return original_update(key, **kwargs)

            with patch.object(registry, "async_update_entity", side_effect=fail_second):
                completed, error = apply_roles(hass, plan)
            assert completed == [a] and error == "voice_write_failed"
            assert roles(a) == {"voice_main"} and roles(b) == {"voice_all_off"}
            assert custom.label_id in registry.async_get(a).labels

        with case(report, "roles_health_optional_roles_do_not_count_as_issues"):
            health_sensor = next(
                s for s in hass.states.async_all("sensor") if s.name == "Voice Health Check"
            )
            await hass.services.async_call(
                "homeassistant",
                "update_entity",
                {"entity_id": health_sensor.entity_id},
                blocking=True,
            )
            health_sensor = hass.states.get(health_sensor.entity_id)
            assert health_sensor.attributes["suggestions"] > 0
            assert int(health_sensor.state) == sum(
                row["severity"] in {"warning", "error"}
                for row in hass.data["polish_context_voice"][entry.entry_id].health()
            )

        async def lighting(**kwargs):
            return await client.call("polish_context_voice/lighting", area=area.id, **kwargs)

        with case(report, "lighting_panel_previews_exact_role_change_then_saves_labels_only"):
            room = await lighting()
            assert {row["id"] for row in room["lights"]} == {a, b, hidden}
            assert not next(row for row in room["lights"] if row["id"] == hidden)["exposed"]
            before_states = {key: hass.states.get(key).state for key in (a, b, hidden)}
            before_labels = {key: registry.async_get(key).labels for key in (a, b, hidden)}
            changes = [{"id": a, "on": True, "off": False}]
            draft = {"revision": room["revision"], "changes": changes}
            reviewed = await lighting(action="preview", **draft)
            assert reviewed["before"] == room["plans"]
            assert reviewed["after"]["on"]["actions"] == ["light.turn_on"]
            assert len(reviewed["after"]["on"]["targets"]) == 1
            assert reviewed["after"]["off"] == room["plans"]["off"]
            assert before_labels == {key: registry.async_get(key).labels for key in before_labels}
            await lighting(action="save", expected_error="voice_review_changed", **draft)
            invalid = [
                {"id": "light.lab_kitchen_light", "on": True, "off": False},
                {"id": a, "on": True, "off": False},
            ]
            await lighting(
                action="preview", revision=room["revision"], changes=invalid,
                expected_error="voice_invalid_selection",
            )
            await lighting(
                action="preview", revision=room["revision"], changes=changes * 2,
                expected_error="voice_invalid_selection",
            )
            saved = await lighting(action="save", review=reviewed["review"], **draft)
            assert saved == {"completed": [a], "error": None}, saved
            assert roles(a) == {"voice_main", "voice_default_on"}
            assert custom.label_id in registry.async_get(a).labels
            assert before_states == {key: hass.states.get(key).state for key in before_states}
            await hass.async_block_till_done()
            actual = await client.call(
                "polish_context_voice/process", text="Włącz światło", source_area=area.id,
            )
            assert actual["targets"] == [a]
            await lighting(
                action="save", review=reviewed["review"], expected_error="voice_stale", **draft,
            )

        with case(report, "lighting_panel_rejects_changed_draft_and_new_guard_before_save"):
            room = await lighting()
            draft = {"revision": room["revision"], "changes": [{"id": b, "on": True, "off": True}]}
            reviewed = await lighting(action="preview", **draft)
            assert len(reviewed["after"]["on"]["targets"]) == 2
            await lighting(
                action="save", revision=room["revision"],
                changes=[{"id": b, "on": False, "off": False}], review=reviewed["review"],
                expected_error="voice_review_changed",
            )
            areas.async_update(area.id, labels={block.label_id})
            try:
                await lighting(
                    action="save", review=reviewed["review"], expected_error="voice_stale", **draft,
                )
                blocked = await lighting()
                await lighting(
                    action="preview", revision=blocked["revision"], changes=draft["changes"],
                    expected_error="voice_conflict",
                )
                assert roles(b) == {"voice_all_off"}
            finally:
                areas.async_update(area.id, labels=set())
        with case(report, "lighting_panel_category_review_preserves_legacy_decorative_and_sets"):
            accent = labels.async_get_label_by_name("voice_accent") or labels.async_create(
                "voice_accent",
            )
            registry.async_update_entity(b, labels=registry.async_get(b).labels | {accent.label_id})
            room = await lighting()
            light = next(row for row in room["lights"] if row["id"] == b)
            assert light["categories"] == ["voice_ambient"] and light["off"] and not light["on"]
            assert room["plans"]["voice_ambient_on"]["targets"] == [light["name"]]
            assert room["plans"]["voice_main_on"]["targets"]
            assert not room["plans"]["voice_task_on"]["targets"]
            change = {
                "id": b, "on": False, "off": True, "categories": ["voice_ambient", "voice_task"],
            }
            draft = {"revision": room["revision"], "changes": [change]}
            checked = await lighting(action="preview", **draft)
            for key in ("on", "off", "voice_ambient_on", "voice_ambient_off"):
                assert checked["after"][key] == checked["before"][key]
            for key in ("voice_task_on", "voice_task_off"):
                assert checked["after"][key]["targets"] == [light["name"]]
            assert roles(b) == {"voice_accent", "voice_all_off"}
            await lighting(
                action="save", revision=room["revision"], review=checked["review"],
                changes=[{**change, "categories": ["voice_task"]}],
                expected_error="voice_review_changed",
            )
            assert await lighting(action="save", review=checked["review"], **draft) == {
                "completed": [b], "error": None,
            }
            assert roles(b) == {"voice_accent", "voice_all_off", "voice_task"}
            await hass.async_block_till_done()
            actual = await client.call(
                "polish_context_voice/process", text="Włącz światła robocze", source_area=area.id,
            )
            assert actual["targets"] == [b]
            assert hass.states.get(b).state == "off"

        with case(report, "lighting_panel_removes_legacy_category_and_rejects_stale_roles"):
            room = await lighting()
            change = {"id": b, "on": False, "off": True, "categories": ["voice_task"]}
            draft = {"revision": room["revision"], "changes": [change]}
            checked = await lighting(action="preview", **draft)
            assert not checked["after"]["voice_ambient_on"]["targets"]
            assert checked["after"]["voice_task_on"] == checked["before"]["voice_task_on"]
            manual = labels.async_get_label_by_name("voice_main").label_id
            registry.async_update_entity(b, labels=registry.async_get(b).labels | {manual})
            await lighting(
                action="save", review=checked["review"], expected_error="voice_stale", **draft,
            )
            assert "voice_accent" in roles(b) and "voice_main" in roles(b)
            room = await lighting()
            draft = {"revision": room["revision"], "changes": [change]}
            checked = await lighting(action="preview", **draft)
            assert await lighting(action="save", review=checked["review"], **draft) == {
                "completed": [b], "error": None,
            }
            assert roles(b) == {"voice_all_off", "voice_task"}

        with case(report, "lighting_panel_shows_generic_off_change_when_first_category_is_added"):
            original = {key: registry.async_get(key).labels for key in (a, b)}
            try:
                registry.async_update_entity(a, labels={custom.label_id})
                registry.async_update_entity(b, labels=set())
                room = await lighting()
                assert len(room["plans"]["off"]["targets"]) == 2
                checked = await lighting(action="preview", revision=room["revision"], changes=[{
                    "id": b, "on": False, "off": False, "categories": ["voice_task"],
                }])
                assert not checked["after"]["off"]["targets"]
                assert len(checked["after"]["voice_task_off"]["targets"]) == 1
                assert not roles(b)
            finally:
                for key, assigned_labels in original.items():
                    registry.async_update_entity(key, labels=assigned_labels)

        with case(report, "lighting_panel_uses_room_presets_and_invalidates_stale_drafts"):
            from custom_components.polish_context_voice.core.home_model.light_profiles import (
                DEFAULT_PROFILES,
            )

            options = dict(entry.options)
            room = await lighting()
            default_profiles = {key: sorted(roles) for key, roles in DEFAULT_PROFILES.items()}
            assert room["profiles"] == default_profiles
            changes = [
                {"id": key, "on": True, "off": False, "categories": ["voice_ambient"]}
                for key in (a, b)
            ]
            draft = {"revision": room["revision"], "changes": changes}
            checked = await lighting(action="preview", **draft)
            try:
                hass.config_entries.async_update_entry(entry, options={
                    **options,
                    "voice_room_presets": {
                        **options.get("voice_room_presets", {}),
                        area.id: {"accent": ["voice_accent", "voice_default_on"]},
                    },
                })
                await hass.async_block_till_done()
                current = await lighting()
                assert current["profiles"]["ambient"] == ["voice_ambient", "voice_default_on"]
                assert current["plans"] == room["plans"]
                assert current["revision"] != room["revision"]
                await lighting(
                    action="save", review=checked["review"], expected_error="voice_stale", **draft,
                )
                kitchen = areas.async_get_area_by_name("Kitchen")
                other = await client.call("polish_context_voice/lighting", area=kitchen.id)
                assert other["profiles"]["ambient"] == sorted(DEFAULT_PROFILES["ambient"])
            finally:
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()

        with case(report, "lighting_panel_edit_and_reset_room_recipe_keep_live_runtime"):
            options = dict(entry.options)
            runtime = hass.data["polish_context_voice"][entry.entry_id]
            kitchen = areas.async_get_area_by_name("Kitchen")
            before_labels = {key: registry.async_get(key).labels for key in (a, b, hidden)}
            before_states = {key: hass.states.get(key).state for key in before_labels}
            before_label_count = len(labels.async_list_labels())
            try:
                hass.config_entries.async_update_entry(entry, options={
                    **options,
                    "voice_room_presets": {
                        area.id: {"accent": ["voice_accent"]},
                        kitchen.id: {"main": ["voice_main"]},
                    },
                })
                await hass.async_block_till_done()
                old = await lighting()
                assert old["custom_profiles"] == ["ambient"]
                assert old["profiles"]["ambient"] == ["voice_ambient"]
                other = await client.call("polish_context_voice/lighting", area=kitchen.id)
                update = {"key": "ambient", "roles": ["voice_all_off", "voice_ambient"]}
                await lighting(
                    action="save_profile", profile=update, revision=other["profile_revision"],
                    expected_error="voice_profile_stale",
                )
                await lighting(
                    action="save_profile", profile={"key": "ambient", "roles": ["voice_block"]},
                    revision=old["profile_revision"], expected_error="invalid_format",
                )
                await lighting(
                    action="save_profile", profile={"key": "ambient", "roles": []},
                    revision=old["profile_revision"], expected_error="invalid_format",
                )
                await lighting(
                    action="save_profile", profile=update, revision=old["profile_revision"],
                )
                await hass.async_block_till_done()
                current = await lighting()
                assert current["profiles"]["ambient"] == update["roles"]
                assert current["custom_profiles"] == ["ambient"]
                assert current["plans"] == old["plans"]
                assert "accent" in entry.options["voice_room_presets"][area.id]
                reset = {"key": "ambient", "roles": None}
                await lighting(
                    action="save_profile", profile=reset, revision=old["profile_revision"],
                    expected_error="voice_profile_stale",
                )
                await lighting(
                    action="save_profile", profile=reset, revision=current["profile_revision"],
                )
                await hass.async_block_till_done()
                restored = await lighting()
                assert not restored["custom_profiles"]
                assert restored["profiles"] == restored["profile_defaults"]
                assert entry.options["voice_room_presets"] == {kitchen.id: {"main": ["voice_main"]}}
                assert runtime is hass.data["polish_context_voice"][entry.entry_id]
                assert before_labels == {
                    key: registry.async_get(key).labels for key in before_labels
                }
                assert before_states == {key: hass.states.get(key).state for key in before_states}
                assert len(labels.async_list_labels()) == before_label_count
            finally:
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()

        with case(report, "lighting_panel_previews_and_saves_choice_against_actual_aliases"):
            original_aliases = {key: registry.async_get(key).aliases for key in (a, b)}
            original_labels = {key: registry.async_get(key).labels for key in (a, b, hidden)}
            states = {key: hass.states.get(key).state for key in original_labels}
            try:
                for identifier, aliases in original_aliases.items():
                    registry.async_update_entity(
                        identifier, aliases=set(aliases) | {"panel próbny"},
                    )
                await hass.async_block_till_done()
                room = await lighting(choice_command="Włącz panel próbny")
                assert room["plans"]["preference"]["status"] == "clarify"
                assert not any(row["preferred"] for row in room["lights"])
                rows = {row["id"]: row for row in room["lights"]}
                change = {key: rows[b][key] for key in ("id", "on", "off")}
                change["preferred"] = True
                draft = {
                    "revision": room["revision"], "changes": [change],
                    "choice_command": room["commands"]["preference"],
                }
                reviewed = await lighting(action="preview", **draft)
                assert reviewed["before"] == room["plans"]
                assert reviewed["commands"] == room["commands"]
                wanted = reviewed["after"]["preference"]
                assert wanted["status"] == "clarify" and not wanted["actions"]
                assert "(opcja domyślna)" in wanted["speech"]
                for kind in ("on", "off", "voice_task_on", "voice_ambient_off"):
                    assert reviewed["after"][kind] == reviewed["before"][kind]
                assert registry.async_get(b).labels == original_labels[b]
                await lighting(
                    action="save", review=reviewed["review"], expected_error="voice_review_changed",
                    **{**draft, "choice_command": "Włącz inny panel"},
                )
                assert await lighting(action="save", review=reviewed["review"], **draft) == {
                    "completed": [b], "error": None,
                }
                await hass.async_block_till_done()
                persisted = await lighting(choice_command=draft["choice_command"])
                assert persisted["plans"]["preference"] == wanted
                assert {row["id"] for row in persisted["lights"] if row["preferred"]} == {b}
                assert states == {key: hass.states.get(key).state for key in states}

                question = await client.call(
                    "polish_context_voice/process", text=draft["choice_command"],
                    source_area=area.id,
                )
                answered = await client.call(
                    "polish_context_voice/process", text="Tę domyślną",
                    source_area=area.id, conversation_id=question["conversation_id"],
                )
                assert answered["status"] == "dry_run" and answered["targets"] == [b]

                other = {key: rows[a][key] for key in ("id", "on", "off")}
                other["preferred"] = True
                conflicting = await lighting(
                    action="preview", **{
                        **draft, "revision": persisted["revision"], "changes": [other],
                    },
                )
                assert conflicting["after"]["preference"]["preference_conflict"]
                assert "(opcja domyślna)" not in conflicting["after"]["preference"]["speech"]
            finally:
                for identifier, aliases in original_aliases.items():
                    registry.async_update_entity(
                        identifier, aliases=aliases, labels=original_labels[identifier],
                    )
                await hass.async_block_till_done()

        report["role_wizard_scenarios_ms"] = round((perf_counter() - started) * 1000, 3)
        samples = []
        for _ in range(20):
            tick = perf_counter()
            records, _ = read_roles(hass)
            samples.append((perf_counter() - tick) * 1000)
        report["role_wizard_registry_read_ms"] = {
            "samples": len(samples),
            "registered_entities": len(records),
            "p50": round(sorted(samples)[9], 3),
            "max": round(max(samples), 3),
            "scope": "Local development host, metadata read; not N5105/UI latency",
        }
    finally:
        for key in ids:
            if registry.async_get(key):
                registry.async_remove(key)
            hass.states.async_remove(key)
        devices.async_remove_device(device.id)
        areas.async_delete(area.id)
        await hass.async_block_till_done()
