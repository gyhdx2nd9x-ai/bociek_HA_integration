"""Assertions against actual HA registries, services, Conversation and MCP APIs."""

import json
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

import yaml
from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, SupportsResponse, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import _load_services_file
from homeassistant.loader import async_get_integration

from .assist_pipeline_scenarios import exercise_assist_pipeline
from .branding_scenarios import exercise_branding
from .calendar_regression_scenarios import exercise_calendar_regressions
from .calendar_scenarios import exercise_calendar
from .capability_scenarios import exercise_capabilities
from .communication_scenarios import exercise_communication
from .content_options_scenarios import exercise_content_options
from .diagnostics_scenarios import exercise_diagnostics
from .dialogue_scenarios import exercise_dialogue
from .e3_catalog_scenarios import exercise_e3_catalog
from .light_background_scenarios import exercise_light_background
from .light_category_scenarios import exercise_light_categories
from .media_search_scenarios import exercise_media_search
from .missing_reference_scenarios import exercise_missing_references
from .music_scenarios import exercise_music
from .profile_scenarios import exercise_profiles
from .registry_context_scenarios import exercise_registry_context
from .response_facts_scenarios import exercise_response_facts
from .role_scenarios import exercise_roles
from .self_test_scenarios import exercise_self_test
from .state_scope_scenarios import exercise_state_scope
from .stoic_scenarios import exercise_stoic
from .temperature_feedback_scenarios import exercise_temperature_feedback
from .timer_scenarios import exercise_timers
from .translation_scenarios import exercise_translations
from .vacuum_scenarios import exercise_vacuum

DOMAIN = "polish_context_voice"
MAIN = "light.lab_main_lights"
TABLE = "light.lab_table_light"
AMBIENT = "light.lab_ambient_light"
KITCHEN = "light.lab_kitchen_light"
HIDDEN = "light.lab_hidden_light"
SWITCH = "switch.lab_test_switch"
LIVING = {MAIN, TABLE, AMBIENT}
SYNTHETIC_STRUCTURE = json.loads(Path(__file__).with_name("household.json").read_text())


@contextmanager
def case(report, name):
    started = perf_counter()
    row = {"name": name, "passed": False}
    report["cases"].append(row)
    try:
        yield
        row["passed"] = True
    except Exception as exc:
        row["error"] = str(exc)
        raise
    finally:
        row["duration_ms"] = round((perf_counter() - started) * 1000, 3)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {name}", flush=True)


def assert_plan(result, expected, status="dry_run"):
    assert result["status"] == status, result
    assert set(result["targets"]) == set(expected), result
    assert result["route"] == "fast", result
    assert result["confidence_class"] == "high", result


async def exercise(hass, entry, fixture, client, session, token, user, report):
    registry = er.async_get(hass)
    services = []

    @callback
    def capture(event):
        if event.data["domain"] in {"light", "switch"}:
            services.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    async def command(text, **kwargs):
        result = await client.call(DOMAIN + "/process", text=text, **kwargs)
        await hass.async_block_till_done()
        return result

    def states():
        return {
            s.entity_id: s.state for s in hass.states.async_all() if s.domain in {"light", "switch"}
        }

    async def set_lights(value, identifiers=LIVING):
        await hass.services.async_call(
            "light", "turn_" + value, {"entity_id": sorted(identifiers)}, blocking=True
        )
        await hass.async_block_till_done()
        services.clear()

    async def action(spec):
        domain, service = spec["action"].split(".")
        result = await client.call(
            "call_service",
            domain=domain,
            service=service,
            service_data=spec["data"],
            return_response=True,
        )
        await hass.async_block_till_done()
        return result["response"]

    try:
        with case(report, "synthetic_household_8_areas_2_floors"):
            snapshot = await client.call(DOMAIN + "/snapshot")
            assert len(snapshot["areas"]) == 8, snapshot["areas"]
            assert len(snapshot["floors"]) == 2, snapshot["floors"]
            assert sum(map(len, fixture["devices"].values())) == sum(
                area["devices"] for area in SYNTHETIC_STRUCTURE["areas"]
            )
            assert ar.async_get(hass).async_get_area("hallway").floor_id is None
            assert registry.async_get(MAIN).area_id is None  # Area inherited from device.
            assert er.COMPUTED_NAME in registry.async_get(MAIN).aliases
            aliases = er.async_get_entity_aliases(hass, registry.async_get(MAIN))
            assert all(isinstance(alias, str) for alias in aliases), aliases
            assert len(aliases) >= 2, aliases
            assert hass.states.get("sensor.voice_health_check").state.isdigit()

        with case(report, "dry_run_asymmetric_on_off_no_service_calls"):
            before = states()
            assert_plan(await command("zapal światła", source_area="living_room"), {MAIN})
            assert_plan(await command("wyłącz światła", source_area="living_room"), LIVING)
            assert states() == before
            assert not services

        with case(report, "execute_on_main_only_preserves_user_context"):
            await set_lights("off")
            before = states()
            assert_plan(
                await command("zapal światła", source_area="living_room", dry_run=False),
                {MAIN},
                "executed",
            )
            assert states() == {**before, MAIN: "on"}
            assert len(services) == 1
            assert services[0].context.user_id == user.id
            assert set(services[0].data["service_data"]["entity_id"]) == {MAIN}

        with case(report, "explicit_all_lights_cross_area_native_execution_and_hidden_exclusion"):
            await set_lights("off")
            before = states()
            result = await command(
                "włącz wszystkie światła w salonie", source_area="kitchen", dry_run=False
            )
            assert_plan(result, LIVING, "executed")
            assert states() == {**before, **dict.fromkeys(LIVING, "on")}
            assert all(
                HIDDEN not in event.data["service_data"].get("entity_id", []) for event in services
            )

        with case(report, "execute_off_all_semantic_living_lights"):
            await set_lights("on")
            before = states()
            result = await command("wyłącz światła", source_area="living_room", dry_run=False)
            assert_plan(result, LIVING, "executed")
            assert set(result["completed"]) == LIVING
            assert states() == {**before, **dict.fromkeys(LIVING, "off")}

        with case(report, "explicit_kitchen_overrides_living_satellite"):
            before = states()
            result = await command(
                "włącz światła w kuchni",
                satellite_id=fixture["satellite"],
                source_area="living_room",
                dry_run=False,
            )
            assert_plan(result, {KITCHEN}, "executed")
            assert states() == {**before, KITCHEN: "on"}

        for field, value in (
            ("satellite_id", fixture["satellite"]),
            ("device_id", fixture["devices"]["living_room"][1]),
        ):
            with case(report, "context_from_" + field):
                assert_plan(await command("zapal światła", **{field: value}), {MAIN})

        for text, context in (
            ("zapal światła", {}),
            ("zapal światła w garażu", {"source_area": "living_room"}),
        ):
            with case(report, "unresolved_location_" + text):
                before = states()
                result = await command(text, dry_run=False, **context)
                assert result["status"] == "clarify", result
                assert not result["calls"]
                assert before == states()

        room_cases = [
            ("salonie", MAIN),
            ("kuchni", KITCHEN),
            ("wejściu", "light.lab_front_door_light"),
            ("sypialni", "light.lab_bedroom_light"),
            ("biurze", "light.lab_office_light"),
            ("pokoju gościnnego", "light.lab_guest_room_light"),
            ("pokoju gościnnym", "light.lab_guest_room_light"),
            ("aneksie", KITCHEN),
            ("łazience", "light.lab_bathroom_light"),
            ("korytarzu", "light.lab_hallway_light"),
        ]
        for phrase, entity in room_cases:
            with case(report, "polish_area_alias_" + phrase):
                assert_plan(
                    await command("zapal światła w " + phrase, source_area="living_room"), {entity}
                )

        with case(report, "floor_ground_excludes_unassigned_hallway"):
            assert_plan(
                await command("zapal światła na parterze", source_area="office"),
                {MAIN, KITCHEN, "light.lab_front_door_light"},
            )
        with case(report, "floor_first_excludes_unassigned_hallway"):
            assert_plan(
                await command("zapal światła na pierwszym piętrze"),
                {
                    "light.lab_" + room + "_light"
                    for room in ("bedroom", "office", "guest_room", "bathroom")
                },
            )

        with case(report, "assist_exposure_invalidates_plan"):
            exposed_entities.async_expose_entity(hass, "conversation", MAIN, False)
            await hass.async_block_till_done()
            try:
                result = await command("zapal światła", source_area="living_room")
                assert result["status"] != "dry_run" and not result["calls"], result
            finally:
                exposed_entities.async_expose_entity(hass, "conversation", MAIN, True)
                await hass.async_block_till_done()
            assert_plan(await command("zapal światła", source_area="living_room"), {MAIN})
            assert (
                HIDDEN
                not in (await command("wyłącz światła", source_area="living_room"))["targets"]
            )

        with case(report, "registry_label_change_refreshes_targets"):
            original = registry.async_get(TABLE).labels
            registry.async_update_entity(TABLE, labels=original | {"voice_default_on"})
            await hass.async_block_till_done()
            try:
                assert_plan(
                    await command("zapal światła", source_area="living_room"), {MAIN, TABLE}
                )
            finally:
                registry.async_update_entity(TABLE, labels=original)
                await hass.async_block_till_done()

        with case(report, "registry_entity_area_overrides_device_area"):
            registry.async_update_entity(TABLE, area_id="kitchen")
            await hass.async_block_till_done()
            try:
                assert_plan(
                    await command("wyłącz światła", source_area="living_room"), {MAIN, AMBIENT}
                )
                assert_plan(await command("wyłącz światła w kuchni"), {KITCHEN, TABLE})
            finally:
                registry.async_update_entity(TABLE, area_id=None)
                await hass.async_block_till_done()

        with case(report, "temperature_state_updates_without_metadata_rebuild"):
            runtime = hass.data[DOMAIN][entry.entry_id]
            await command("jaka jest temperatura", source_area="living_room")
            rebuilds = runtime.cache.metrics()["rebuilds"]
            sensor = hass.states.get("sensor.lab_temperature")
            hass.states.async_set(sensor.entity_id, "23.7", dict(sensor.attributes))
            await hass.async_block_till_done()
            result = await command("jaka jest temperatura", source_area="living_room")
            assert result["status"] == "answered" and "około 24 stopni" in result["speech"], result
            assert runtime.cache.metrics()["rebuilds"] == rebuilds
            assert runtime.cache.metrics()["state_updates"] > 0

        with case(report, "health_missing_labels_alias_conflict_and_satellite_area"):
            await client.call(DOMAIN + "/health")
            entity = registry.async_get_or_create(
                "light",
                DOMAIN,
                "unlabelled_lab",
                suggested_object_id="lab_unlabelled",
                device_id=fixture["devices"]["living_room"][0],
            )
            hass.states.async_set(entity.entity_id, "off", {"friendly_name": "Unlabelled light"})
            exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, True)
            old_aliases = registry.async_get(AMBIENT).aliases
            registry.async_update_entity(AMBIENT, aliases=["lampa przy stole"])
            device_id = fixture["devices"]["living_room"][1]
            dr.async_get(hass).async_update_device(device_id, area_id=None)
            await hass.async_block_till_done()
            try:
                findings = (await client.call(DOMAIN + "/health"))["findings"]
                assert {"missing_semantic_labels", "alias_conflict", "satellite_without_area"} <= {
                    f["code"] for f in findings
                }, findings
                assert any(
                    f["code"] == "missing_semantic_labels"
                    and f["new"]
                    and entity.entity_id in f["ids"]
                    for f in findings
                ), findings
            finally:
                dr.async_get(hass).async_update_device(device_id, area_id="living_room")
                registry.async_update_entity(AMBIENT, aliases=old_aliases)
                registry.async_remove(entity.entity_id)
                hass.states.async_remove(entity.entity_id)
                await hass.async_block_till_done()

        with case(report, "medium_risk_confirmation_single_use"):
            assert hass.states.get(SWITCH).state == "off"
            result = await command(
                "włącz gniazdo testowe", source_area="living_room", dry_run=False
            )
            assert result["status"] == "confirm" and result["risk"] == "medium", result
            assert hass.states.get(SWITCH).state == "off"
            context = {
                "source_area": "living_room",
                "dry_run": False,
                "conversation_id": result["conversation_id"],
            }
            assert_plan(await command("potwierdzam", **context), {SWITCH}, "executed")
            assert hass.states.get(SWITCH).state == "on"
            assert (await command("potwierdzam", **context))["reason"] == "no_pending"

        with case(report, "native_conversation_satellite_and_confirmation"):
            agent = next(
                s.entity_id
                for s in hass.states.async_all("conversation")
                if (registered := registry.async_get(s.entity_id))
                and registered.config_entry_id == entry.entry_id
            )
            await set_lights("off")
            conversation = {
                "agent_id": agent,
                "language": "pl",
                "satellite_id": fixture["satellite"],
            }
            result = await client.call("conversation/process", text="zapal światła", **conversation)
            await hass.async_block_till_done()
            assert result["response"]["response_type"] == "action_done", result
            assert hass.states.get(MAIN).state == "on"
            assert hass.states.get(TABLE).state == "off"
            result = await client.call(
                "conversation/process", text="wyłącz gniazdo testowe", **conversation
            )
            assert result["continue_conversation"], result
            assert hass.states.get(SWITCH).state == "on"
            result = await client.call(
                "conversation/process",
                text="potwierdzam",
                conversation_id=result["conversation_id"],
                **conversation,
            )
            await hass.async_block_till_done()
            assert not result["continue_conversation"], result
            assert hass.states.get(SWITCH).state == "off"

        await exercise_vacuum(hass, client, agent, user, report, case, fixture["satellite"])

        with case(report, "inflected_light_alias_native_conversation_and_preview"):
            await set_lights("on")
            before = states()
            text = "Zgaś taśmę LED w salonie"
            assert_plan(await command(text, source_area="kitchen"), {AMBIENT})
            assert not services and states() == before
            result = await client.call("conversation/process", text=text, **conversation)
            await hass.async_block_till_done()
            assert result["response"]["response_type"] == "action_done", result
            assert states() == {**before, AMBIENT: "off"}
            assert len(services) == 1 and services[0].context.user_id == user.id

        with case(report, "inflected_light_alias_native_collision_is_read_only"):
            aliases = registry.async_get(TABLE).aliases
            try:
                registry.async_update_entity(TABLE, aliases={"taśma LED"})
                await hass.async_block_till_done()
                services.clear()
                before = states()
                result = await command("Zgaś taśmę LED w salonie", dry_run=False)
                assert result["reason"] == "ambiguous_target" and not result["calls"], result
                assert not services and states() == before
            finally:
                registry.async_update_entity(TABLE, aliases=aliases)
                await hass.async_block_till_done()

        with case(report, "short_light_alias_user_transcript_preview_and_conversation"):
            aliases = registry.async_get(TABLE).aliases
            registry.async_update_entity(TABLE, aliases={"nad stołem"})
            await hass.async_block_till_done()
            try:
                await set_lights("off")
                before = states()
                assert_plan(
                    await command("Włącz światła nad stołem", satellite_id=fixture["satellite"]),
                    {TABLE},
                )
                assert not services and states() == before
                result = await client.call(
                    "conversation/process", text="Włącz światła nad stołem", **conversation
                )
                await hass.async_block_till_done()
                assert result["response"]["response_type"] == "action_done", result
                assert states() == {**before, TABLE: "on"}
                assert len(services) == 1 and services[0].context.user_id == user.id
                assert_plan(
                    await command(
                        "Wyłącz światła nad stołem w salonie",
                        satellite_id=fixture["satellite"],
                        dry_run=False,
                    ),
                    {TABLE},
                    "executed",
                )
                assert hass.states.get(TABLE).state == "off"
            finally:
                registry.async_update_entity(TABLE, aliases=aliases)
                await hass.async_block_till_done()

        with case(report, "short_light_alias_full_priority_exposure_and_ambiguity"):
            old_table, old_ambient = (
                registry.async_get(TABLE).aliases,
                registry.async_get(AMBIENT).aliases,
            )
            try:
                registry.async_update_entity(TABLE, aliases={"nad stołem"})
                registry.async_update_entity(AMBIENT, aliases={"światła nad stołem"})
                await hass.async_block_till_done()
                assert_plan(
                    await command("Włącz światła nad stołem", source_area="living_room"), {AMBIENT}
                )
                exposed_entities.async_expose_entity(hass, "conversation", AMBIENT, False)
                await hass.async_block_till_done()
                services.clear()
                result = await command(
                    "Włącz światła nad stołem", source_area="living_room", dry_run=False
                )
                assert result["status"] == "clarify" and not services, result
                exposed_entities.async_expose_entity(hass, "conversation", AMBIENT, True)
                registry.async_update_entity(AMBIENT, aliases={"nad stołem"})
                await hass.async_block_till_done()
                result = await command(
                    "Włącz światła nad stołem", source_area="living_room", dry_run=False
                )
                assert result["reason"] == "ambiguous_target" and not services, result
            finally:
                exposed_entities.async_expose_entity(hass, "conversation", AMBIENT, True)
                registry.async_update_entity(TABLE, aliases=old_table)
                registry.async_update_entity(AMBIENT, aliases=old_ambient)
                await hass.async_block_till_done()

        with case(report, "short_light_group_alias_expands_and_rechecks_members"):
            group = registry.async_get_or_create(
                "light", DOMAIN, "above_table_group", suggested_object_id="lab_above_table_group"
            )
            registry.async_update_entity(
                group.entity_id, area_id="living_room", aliases={"nad stołem"}
            )
            hass.states.async_set(group.entity_id, "off", {"entity_id": [MAIN, TABLE]})
            exposed_entities.async_expose_entity(hass, "conversation", group.entity_id, True)
            await hass.async_block_till_done()
            try:
                await set_lights("off")
                assert_plan(
                    await command(
                        "Włącz światła nad stołem", source_area="living_room", dry_run=False
                    ),
                    {MAIN, TABLE},
                    "executed",
                )
                assert {hass.states.get(i).state for i in (MAIN, TABLE)} == {"on"}
                assert hass.states.get(AMBIENT).state == "off"
                assert len(services) == 2
                exposed_entities.async_expose_entity(hass, "conversation", TABLE, False)
                await hass.async_block_till_done()
                services.clear()
                result = await command(
                    "Wyłącz światła nad stołem", source_area="living_room", dry_run=False
                )
                assert result["reason"] == "ineligible_target" and not services, result
            finally:
                exposed_entities.async_expose_entity(hass, "conversation", TABLE, True)
                registry.async_remove(group.entity_id)
                hass.states.async_remove(group.entity_id)
                await hass.async_block_till_done()

        await exercise_timers(hass, fixture, client, agent, report, case)
        await exercise_capabilities(hass, fixture, client, agent, user, report, case)

        with case(report, "native_mcp_initialize_list_preview_no_execution"):
            mcp_flow = await hass.config_entries.flow.async_init(
                "mcp_server", context={"source": "user"}, data={}
            )
            assert mcp_flow["type"] == "create_entry", mcp_flow
            await hass.async_block_till_done()
            assert mcp_flow["result"].state.value == "loaded"
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}

            async def rpc(method, params):
                async with session.post(
                    "http://127.0.0.1:18123/api/mcp/" + DOMAIN,
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                ) as response:
                    assert response.status == 200, (response.status, await response.text())
                    body = await response.json()
                    assert "error" not in body, body
                    return body["result"]

            initialized = await rpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pcv-lab", "version": "1"},
                },
            )
            assert initialized["protocolVersion"]
            listed = (await rpc("tools/list", {}))["tools"]
            assert len(listed) == 1 and listed[0]["annotations"]["readOnlyHint"], listed
            before, calls_before = states(), len(services)
            response = await rpc(
                "tools/call",
                {
                    "name": listed[0]["name"],
                    "arguments": {"text": "zapal światła"},
                    "_meta": {"io.home-assistant/device_id": fixture["devices"]["living_room"][1]},
                },
            )
            assert not response.get("isError"), response
            preview = json.loads(
                next(item["text"] for item in response["content"] if item["type"] == "text")
            )
            assert_plan(preview, {MAIN})
            assert states() == before and len(services) == calls_before

        with case(report, "integration_reload_rebinds_agent_api_and_health"):
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert_plan(await command("zapal światła", source_area="living_room"), {MAIN})
            assert hass.states.get("sensor.voice_health_check").state.isdigit()
            assert (
                await client.call(
                    "conversation/process",
                    text="jaka jest temperatura w salonie",
                    agent_id=agent,
                    language="pl",
                )
            )["response"]["response_type"] == "query_answer"

        with case(report, "preview_action_native_schema_after_reload"):
            # Validate our action using HA's actual services.yaml loader. Listing ALL
            # domains also imports camera/calendar extras absent from this minimal lab.
            integration = await async_get_integration(hass, DOMAIN)
            descriptions = await hass.async_add_executor_job(_load_services_file, integration)
            metadata = descriptions["preview"]
            assert "source_area" in metadata["fields"], metadata
            assert hass.services.supports_response(DOMAIN, "preview") == SupportsResponse.ONLY

        specifications = Path(__file__).resolve().parents[1] / "deployment/testy"
        preview_cases = [
            ("01-salon-wlacz-podglad.yaml", {MAIN}),
            ("02-salon-wylacz-podglad.yaml", LIVING),
            ("03-kuchnia-podglad.yaml", {KITCHEN}),
            ("04-brak-kontekstu-podglad.yaml", None),
            ("05-satelita-podglad.yaml", {MAIN}),
        ]
        for filename, expected in preview_cases:
            with case(report, "deployment_" + filename):
                spec = yaml.safe_load((specifications / filename).read_text())
                if "satellite_id" in spec["data"]:
                    spec["data"]["satellite_id"] = fixture["satellite"]
                before, calls_before = states(), len(services)
                result = await action(spec)
                if expected is None:
                    assert result["status"] == "clarify" and not result["calls"], result
                else:
                    assert_plan(result, expected)
                assert not result["completed"]
                assert states() == before and len(services) == calls_before

        with case(report, "preview_action_rejects_execution_and_identity_overrides"):
            before, calls_before = states(), len(services)
            for injected in ({"dry_run": False}, {"user_id": user.id}):
                await client.call(
                    "call_service",
                    domain=DOMAIN,
                    service="preview",
                    return_response=True,
                    service_data={
                        "text": "zapal światła",
                        "source_area": "living_room",
                        **injected,
                    },
                    expected_error="invalid_format",
                )
            assert states() == before and len(services) == calls_before

        for filename, expected in (
            ("10-salon-wlacz-WYKONANIE.yaml", {MAIN}),
            ("11-salon-wylacz-WYKONANIE.yaml", LIVING),
        ):
            with case(report, "deployment_" + filename):
                await set_lights("off" if filename.startswith("10") else "on")
                spec = yaml.safe_load((specifications / filename).read_text())
                spec["data"]["agent_id"] = agent
                before = states()
                result = await action(spec)
                assert result["response"]["response_type"] == "action_done", result
                changed = "on" if filename.startswith("10") else "off"
                assert states() == {**before, **dict.fromkeys(expected, changed)}
                assert all(event.context.user_id == user.id for event in services)

        reader = await hass.auth.async_create_user("Lab reader", group_ids=[GROUP_ID_READ_ONLY])
        reader_refresh = await hass.auth.async_create_refresh_token(
            reader, client_id="http://127.0.0.1"
        )
        reader_token = hass.auth.async_create_access_token(reader_refresh)
        async with session.ws_connect("http://127.0.0.1:18123/api/websocket") as ws:
            assert (await ws.receive_json())["type"] == "auth_required"
            await ws.send_json({"type": "auth", "access_token": reader_token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            restricted = type(client)(ws)
            with case(report, "health_snapshot_and_panel_trial_require_admin"):
                for endpoint in ("health", "snapshot", "trial"):
                    await restricted.call(DOMAIN + "/" + endpoint, expected_error="unauthorized")
                await restricted.call(
                    DOMAIN + "/lighting", area="living_room", expected_error="unauthorized",
                )
                await restricted.call(
                    DOMAIN + "/lighting", area="living_room", action="save_profile",
                    profile={"key": "main", "roles": ["voice_main"]}, expected_error="unauthorized",
                )
            with case(report, "read_only_user_cannot_execute"):
                before, calls_before = states(), len(services)
                result = await restricted.call(
                    DOMAIN + "/process",
                    text="wyłącz światła",
                    source_area="living_room",
                    dry_run=False,
                )
                assert result["status"] == "error" and not result["completed"], result
                assert states() == before and len(services) == calls_before
                result = await restricted.call(
                    DOMAIN + "/process", text="jaka jest temperatura", source_area="living_room"
                )
                assert result["status"] == "answered", result
            with case(report, "preview_action_rejects_inactive_context"):
                inactive = await hass.auth.async_create_user(
                    "Lab inactive", group_ids=[GROUP_ID_READ_ONLY]
                )
                await hass.auth.async_deactivate_user(inactive)
                before, calls_before = states(), len(services)
                # HA closes inactive users' sockets itself. Exercise a service Context
                # directly to verify this adapter retains identity inside the runtime.
                result = await hass.services.async_call(
                    DOMAIN,
                    "preview",
                    {"text": "zapal światła", "source_area": "living_room"},
                    blocking=True,
                    return_response=True,
                    context=Context(user_id=inactive.id),
                )
                assert result["status"] == "blocked" and result["reason"] == "user_unavailable"
                assert states() == before and len(services) == calls_before

        with case(report, "mcp_rejects_unauthenticated_and_non_admin"):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            for headers in ({}, {"Authorization": "Bearer " + reader_token}):
                async with session.post(
                    "http://127.0.0.1:18123/api/mcp/" + DOMAIN,
                    json=request,
                    headers={"Accept": "application/json", **headers},
                ) as response:
                    assert response.status in {401, 403}, response.status

        with case(report, "websocket_latency_100_correct_dry_runs"):
            samples = []
            for _ in range(100):
                start = perf_counter()
                result = await client.call(
                    DOMAIN + "/process",
                    text="zapal światła",
                    source_area="living_room",
                    dry_run=True,
                )
                samples.append((perf_counter() - start) * 1000)
                assert_plan(result, {MAIN})
            samples.sort()
            report["websocket_latency_ms"] = {
                "samples": len(samples),
                "correct": len(samples),
                "p50": round(samples[49], 3),
                "p95": round(samples[94], 3),
                "p99": round(samples[98], 3),
                "max": round(samples[-1], 3),
                "scope": "Authenticated local WebSocket + runtime; warm cache; no audio/LLM",
            }
        with case(report, "panel_preview_uses_real_rooms_and_never_executes_or_collects_history"):
            before, calls_before = states(), len(services)
            runtime = hass.data[DOMAIN][entry.entry_id]
            diagnostics = runtime.diagnostics.snapshot()
            rooms = await client.call(DOMAIN + "/trial")
            assert {"id": "kitchen", "name": "Kitchen"} in rooms["areas"]
            result = await client.call(
                DOMAIN + "/trial", text="Włącz światło w kuchni", area="living_room",
            )
            assert result["actions"] == ["light.turn_on"] and result["areas"] == ["Kitchen"]
            assert any("podanej w poleceniu" in reason for reason in result["explanation"])
            await client.call(
                DOMAIN + "/trial", text="Włącz światło", area="missing",
                expected_error="invalid_area",
            )
            await client.call(
                DOMAIN + "/trial", text="Włącz światło", dry_run=False,
                expected_error="invalid_format",
            )
            assert runtime.diagnostics.snapshot() == diagnostics
            assert states() == before and len(services) == calls_before
        await exercise_calendar(hass, entry, fixture, client, report, case)
        await exercise_calendar_regressions(hass, entry, fixture, client, report, case)
        await exercise_dialogue(hass, fixture, client, agent, report, case)
        await exercise_missing_references(
            hass, entry, fixture, client, session, agent, report, case
        )
        await exercise_light_categories(hass, entry, fixture, client, session, agent, report, case)
        await exercise_e3_catalog(hass, entry, client, user, report, case)
        await exercise_response_facts(hass, entry, fixture, client, user, agent, report, case)
        await exercise_temperature_feedback(hass, entry, client, agent, report, case)
        await exercise_state_scope(hass, entry, client, agent, report, case)
        await exercise_light_background(hass, entry, fixture, client, session, agent, report, case)
        await exercise_communication(hass, entry, fixture, client, agent, user, report, case)
        await exercise_music(hass, entry, client, agent, report, case)
        await exercise_media_search(hass, entry, client, agent, user, report, case)
        await exercise_stoic(hass, entry, client, agent, user, report, case)
        await exercise_content_options(hass, entry, client, report, case)
        await exercise_roles(hass, entry, client, report, case)
        await exercise_profiles(hass, entry, client, report, case)
        await exercise_diagnostics(hass, entry, user, report, case)
        await exercise_self_test(hass, entry, client, user, report, case)
        await exercise_branding(hass, client, session, token, report, case)
        await exercise_translations(hass, entry, report, case)
        await exercise_assist_pipeline(hass, fixture, client, agent, user, report, case)
        await exercise_registry_context(hass, entry, command, report, case)
    finally:
        unsubscribe()
