"""Room recipes, categories and back navigation against the native HA flow manager."""

import json
from copy import deepcopy
from uuid import UUID

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr


async def exercise_profiles(hass, entry, client, report, case):
    from custom_components.polish_context_voice.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.polish_context_voice.ha.role_editor import (
        RolePlan,
        apply_roles,
        read_roles,
    )

    original = deepcopy(dict(entry.options))
    registry, labels = er.async_get(hass), lr.async_get(hass)
    areas = ar.async_get(hass)
    room = areas.async_create("Biblioteka profili", aliases={"bibliotece profili"})
    other = areas.async_create("Inne pomieszczenie profili")
    ids = []
    for name, area in (("one", room.id), ("two", room.id), ("remote", other.id)):
        entity = registry.async_get_or_create("light", "pcv_profile_lab", name)
        registry.async_update_entity(entity.entity_id, area_id=area)
        hass.states.async_set(entity.entity_id, "off")
        exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, True)
        ids.append(entity.entity_id)
    a, b, remote = ids
    own = labels.async_create("Etykieta prywatna profili")
    confirm = labels.async_get_label_by_name("voice_confirm")
    registry.async_update_entity(a, labels={own.label_id, confirm.label_id})

    async def submit(flow, data):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=data)
        await hass.async_block_till_done()
        return result

    def abort(flow):
        hass.config_entries.options.async_abort(flow["flow_id"])

    async def form(step):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await submit(flow, {"next_step_id": "voice_roles"})
        flow = await submit(flow, {"area": room.id})
        flow = await submit(flow, {"next_step_id": "voice_lighting"})
        return await submit(flow, {"next_step_id": step})

    def roles(key):
        return read_roles(hass)[0][key].direct

    def field_default(flow, name):
        return next(m.default() for m in flow["data_schema"].schema if m.schema == name)

    try:
        with case(report, "ui_back_to_root_general_music_room_and_empty_media_without_writes"):
            for step in ("voice_roles", "general", "music_shortcuts", "music_rooms"):
                flow = await hass.config_entries.options.async_init(entry.entry_id)
                flow = await submit(flow, {"next_step_id": step})
                flow = await submit(flow, {"back": True})
                assert flow["step_id"] == "init"
                abort(flow)
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            flow = await submit(flow, {"next_step_id": "music_shortcuts"})
            flow = await submit(flow, {"shortcut": "__new__"})
            flow = await submit(flow, {"back": True})
            assert flow["step_id"] == "music_shortcuts"
            abort(flow)
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            flow = await submit(flow, {"next_step_id": "music_rooms"})
            flow = await submit(flow, {"area": room.id})
            flow = await submit(flow, {"back": True})
            assert flow["step_id"] == "music_rooms"
            abort(flow)
            assert dict(entry.options) == original

        with case(report, "profiles_preview_back_and_cancel_do_not_assign_labels"):
            before = len(list(labels.async_list_labels()))
            flow = await form("voice_preset")
            flow = await submit(flow, {"profile": "main"})
            flow = await submit(flow, {"entities": [a]})
            assert flow["step_id"] == "voice_review"
            assert "Światło główne" in flow["description_placeholders"]["changes"]
            assert roles(a) == {"voice_confirm"}
            flow = await submit(flow, {"action": "edit"})
            assert field_default(flow, "entities") == [a]
            flow = await submit(flow, {"entities": [a]})
            flow = await submit(flow, {"action": "cancel"})
            assert flow["step_id"] == "voice_lighting"
            assert len(list(labels.async_list_labels())) == before
            abort(flow)

        with case(
            report, "profiles_main_assigns_three_roles_only_to_selected_light_and_preserves_guards"
        ):
            flow = await form("voice_preset")
            flow = await submit(flow, {"profile": "main"})
            flow = await submit(flow, {"entities": [a]})
            flow = await submit(flow, {"action": "apply"})
            assert roles(a) == {"voice_main", "voice_default_on", "voice_all_off", "voice_confirm"}
            assert own.label_id in registry.async_get(a).labels
            assert not roles(b) and not roles(remote)
            assert hass.states.get(a).state == "off"
            abort(flow)

        with case(
            report, "profiles_custom_recipe_is_room_specific_and_does_not_relabel_existing_lights"
        ):
            before = {key: roles(key) for key in ids}
            flow = await form("voice_profiles")
            flow = await submit(flow, {"profile": "ambient"})
            flow = await submit(flow, {"roles": ["voice_ambient"]})
            assert flow["step_id"] == "voice_profile_saved"
            assert entry.options["voice_room_presets"][room.id]["ambient"] == ["voice_ambient"]
            assert {key: roles(key) for key in ids} == before
            assert other.id not in entry.options["voice_room_presets"]
            assert all(entry.options.get(k) == v for k, v in original.items())
            abort(flow)

        with case(report, "profiles_missing_label_created_on_apply_not_on_preview"):
            accent = labels.async_get_label_by_name("voice_accent")
            if accent:
                assert not any(accent.label_id in e.labels for e in registry.entities.values())
                labels.async_delete(accent.label_id)
            records, catalog = read_roles(hass)
            plan = RolePlan({b: records[b]}, catalog, {b: frozenset({"voice_accent"})})
            assert plan.changes and labels.async_get_label_by_name("voice_accent") is None
            apply_roles(hass, plan)
            assert roles(b) == {"voice_accent"}
            assert labels.async_get_label_by_name("voice_accent") is not None

        with case(
            report,
            "profiles_replace_changes_lighting_roles_only_and_preview_reaches_semantic_target",
        ):
            flow = await form("voice_preset")
            flow = await submit(flow, {"profile": "task"})
            flow = await submit(flow, {"entities": [a], "replace": True})
            flow = await submit(flow, {"action": "apply"})
            assert roles(a) == {"voice_task", "voice_all_off", "voice_confirm"}
            assert own.label_id in registry.async_get(a).labels
            result = await client.call(
                "polish_context_voice/process", text="włącz światła robocze", source_area=room.id
            )
            assert result["targets"] == [a]
            abort(flow)

        with case(report, "profiles_reset_restores_recipe_without_changing_labels"):
            before = {key: roles(key) for key in ids}
            flow = await form("voice_profiles")
            flow = await submit(flow, {"profile": "ambient"})
            flow = await submit(flow, {"reset": True})
            assert flow["step_id"] == "voice_profile_saved"
            assert room.id not in entry.options["voice_room_presets"]
            assert {key: roles(key) for key in ids} == before
            abort(flow)

        with case(report, "profiles_empty_foreign_room_and_changed_recipe_rejected"):
            flow = await form("voice_preset")
            flow = await submit(flow, {"profile": "main"})
            flow = await submit(flow, {"entities": []})
            assert flow["errors"]["base"] == "voice_empty_selection"
            try:
                await submit(flow, {"entities": [remote]})
            except InvalidData:
                pass  # Native selector validation rejects a foreign room before our handler.
            else:
                raise AssertionError("Foreign-room light accepted")
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    "voice_room_presets": {room.id: {"main": ["voice_main"]}},
                },
            )
            flow = await submit(flow, {"entities": [a]})
            assert flow["reason"] == "voice_stale"

        with case(report, "profiles_classifications_preserve_default_sets_and_are_reversible"):
            before_a = roles(a)
            flow = await form("voice_light_types")
            assert field_default(flow, "voice_ambient") == [b]  # Legacy accent read as combined.
            assert "voice_accent" not in {marker.schema for marker in flow["data_schema"].schema}
            flow = await submit(
                flow,
                {"voice_main": [a], "voice_task": [], "voice_ambient": [b]},
            )
            flow = await submit(flow, {"action": "apply"})
            assert roles(a) == (before_a - {"voice_task"}) | {"voice_main"}
            assert roles(b) == {"voice_ambient"}
            abort(flow)
            flow = await form("voice_light_types")
            assert field_default(flow, "voice_main") == [a]
            assert field_default(flow, "voice_ambient") == [b]
            flow = await submit(flow, {"back": True})
            assert flow["step_id"] == "voice_lighting"
            abort(flow)

        with case(
            report, "ui_footer_navigation_metadata_drives_native_back_without_draft_validation"
        ):
            before = dict(entry.options)
            flow = await form("voice_profiles")
            nav = json.loads(flow["description_placeholders"]["pcv_navigation"])
            assert nav["domain"] == "polish_context_voice"
            flow = await submit(flow, nav["data"])
            assert flow["step_id"] == "voice_lighting"
            flow = await submit(
                flow, json.loads(flow["description_placeholders"]["pcv_navigation"])["data"]
            )
            assert flow["step_id"] == "voice_room"
            flow = await submit(
                flow, json.loads(flow["description_placeholders"]["pcv_navigation"])["data"]
            )
            assert flow["step_id"] == "voice_roles"
            flow = await submit(
                flow, json.loads(flow["description_placeholders"]["pcv_navigation"])["data"]
            )
            assert flow["step_id"] == "init"
            assert dict(entry.options) == before
            abort(flow)

        with case(report, "ui_diagnostics_checks_all_catalogs_without_credentials_or_inventory"):
            result = await async_get_config_entry_diagnostics(hass, entry)
            assert all(result["translation_files_present"].values())
            assert all(row["menu_title"] for row in result["loaded_options"].values())
            assert "token" not in str(result).lower() and not any(key in str(result) for key in ids)

        private = {
            "text": "włącz światła nastrojowe",
            "source_area": room.id,
            "satellite_id": "assist_satellite.pcv_private_diagnostics",
            "device_id": "pcv-private-diagnostics-device",
            "conversation_id": "calendar.pcv_private_diagnostics_conversation",
        }
        with case(report, "request_diagnostics_correlates_native_preview_without_private_data"):
            before = {key: hass.states.get(key).state for key in ids}
            preview = await hass.services.async_call(
                "polish_context_voice", "preview", private, blocking=True, return_response=True
            )
            assert preview["status"] == "dry_run" and tuple(preview["targets"]) == (b,), preview
            assert UUID(preview["request_id"]).version == 4
            result = await async_get_config_entry_diagnostics(hass, entry)
            diagnostics = result["request_diagnostics"]
            row = diagnostics["recent_requests"][-1]
            assert row["request_id"] == preview["request_id"]
            assert row["dry_run"] and row["has_source_area"] and row["has_satellite"]
            assert row["has_device"] and row["category"] == "success"
            assert row["target_count"] == row["call_count"] == 1
            assert row["completed_target_count"] == 0
            assert set(row["stages_ms"]) == {
                "source_context",
                "authorization",
                "routing",
                "handler",
                "interpretation",
                "resolution_policy",
            }
            assert set(row["milestones_ms"]) == {"plan_ready"}
            assert row["ha_service_calls"] == {
                "attempted": 0, "returned": 0, "failed": 0, "interrupted": 0,
            }

            private_user = "pcv-private-diagnostics-user"
            denied = await hass.services.async_call(
                "polish_context_voice",
                "preview",
                private,
                blocking=True,
                return_response=True,
                context=Context(user_id=private_user),
            )
            assert denied["reason"] == "user_unavailable", denied
            result = await async_get_config_entry_diagnostics(hass, entry)
            diagnostics = result["request_diagnostics"]
            assert diagnostics["recent_requests"][-1]["request_id"] == denied["request_id"]
            assert diagnostics["recent_requests"][-1]["category"] == "authorization"
            exported = json.dumps(diagnostics, ensure_ascii=False)
            assert not any(value in exported for value in (*private.values(), private_user, *ids))
            assert not any(identifier in exported for identifier in registry.entities)
            assert all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
                for value in result["model_cache_metrics"].values()
            )
            assert {key: hass.states.get(key).state for key in ids} == before

        with case(report, "request_diagnostics_native_preview_window_is_bounded_and_numeric"):
            baseline = diagnostics["completed_since_reload"]
            latest = None
            # Repeated dry-runs exercise retention, not new language examples or device writes.
            for _ in range(66):
                latest = await hass.services.async_call(
                    "polish_context_voice",
                    "preview",
                    private,
                    blocking=True,
                    return_response=True,
                )
                assert latest["status"] == "dry_run", latest
            result = await async_get_config_entry_diagnostics(hass, entry)
            diagnostics = result["request_diagnostics"]
            assert diagnostics["capacity"] == diagnostics["window_size"] == 64
            assert len(diagnostics["recent_requests"]) == 64
            assert diagnostics["completed_since_reload"] == baseline + 66
            assert diagnostics["recent_requests"][-1]["request_id"] == latest["request_id"]
            assert len({row["request_id"] for row in diagnostics["recent_requests"]}) == 64
            assert diagnostics["runtime_elapsed"]["count"] == 64
            assert diagnostics["schema_version"] == 2
            for metric in (
                diagnostics["runtime_elapsed"], *diagnostics["stages"].values(),
                *diagnostics["milestones"].values(),
            ):
                if metric["count"]:
                    assert isinstance(metric["p50_ms"], (int, float))
                    assert isinstance(metric["p95_ms"], (int, float))
                    assert 0 <= metric["p50_ms"] <= metric["p95_ms"]
                else:
                    assert metric["p50_ms"] is None and metric["p95_ms"] is None
            for stage in ("ha_preflight", "ha_service", "execution"):
                assert diagnostics["stages"][stage]["count"] == 0
            assert diagnostics["milestones"]["plan_ready"]["count"] == 64
            assert set(diagnostics["not_measured"]) == {
                "stt",
                "ha_provider_acknowledgement",
                "physical_state",
                "tts",
                "audible_response_start",
            }
            assert {key: hass.states.get(key).state for key in ids} == before
    finally:
        hass.config_entries.async_update_entry(entry, options=original)
        for key in ids:
            registry.async_remove(key)
            hass.states.async_remove(key)
        areas.async_delete(room.id)
        areas.async_delete(other.id)
        await hass.async_block_till_done()
