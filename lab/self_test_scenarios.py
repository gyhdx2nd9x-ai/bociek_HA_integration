"""Exercise the safe self-test through actual HA services and options flows.

The lab contains virtual devices only. Guards below make an attempted runtime,
device, provider or LLM call fail the test even if the runner catches its error.
"""

import asyncio
import json
from contextlib import ExitStack, suppress
from copy import deepcopy
from string import Formatter
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import _load_services_file
from homeassistant.helpers.translation import async_get_translations
from homeassistant.loader import async_get_integration

DOMAIN = "polish_context_voice"
COUNTS = {"pass", "fail", "warn", "skip"}


async def exercise_self_test(hass, entry, client, user, report, case):
    from custom_components.polish_context_voice import self_test_options
    from custom_components.polish_context_voice.core.llm.limits import LimitedFallback
    from custom_components.polish_context_voice.core.llm.router import OpenAICompatibleFallback
    from custom_components.polish_context_voice.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.polish_context_voice.ha import self_test
    from custom_components.polish_context_voice.ha.intents import HAExecutor

    runtime = hass.data[DOMAIN][entry.entry_id]
    assert user.is_active and user.is_admin
    original_options = deepcopy(dict(entry.options))
    registry, areas = er.async_get(hass), ar.async_get(hass)
    room = areas.async_create("Prywatny pokój selftest 7f62", aliases={"prywatny alias 7f62"})
    entity = registry.async_get_or_create(
        "light", DOMAIN, "selftest-private-7f62", suggested_object_id="pcv_private_selftest_7f62"
    )
    registry.async_update_entity(
        entity.entity_id, area_id=room.id, aliases={"prywatna lampa selftest 7f62"}
    )
    private = (
        entity.entity_id,
        room.id,
        room.name,
        "prywatny alias 7f62",
        "prywatna lampa selftest 7f62",
        "Prywatna nazwa urządzenia 7f62",
        "https://private-selftest.invalid/playlist?token=private-token-7f62",
        "Prywatny tytuł kalendarza 7f62",
        "Prywatna wypowiedź mieszkańca 7f62",
    )
    hass.states.async_set(
        entity.entity_id,
        "off",
        {"friendly_name": private[5], "media_content_id": private[6], "description": private[7]},
    )
    exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, True)
    await hass.async_block_till_done()

    # Use nonempty live maps so accidentally clearing a session is observable.
    maps = [
        runtime.pending,
        runtime.pending_timers,
        runtime.pending_capabilities,
        runtime.pending_calendars,
        runtime.dialogue.questions,
        runtime.dialogue.memory,
        runtime.music.pending,
        runtime.music.questions,
        runtime.music.memory,
    ]
    originals = [dict(mapping) for mapping in maps]
    key = ("pcv-private-selftest-user-7f62", "pcv-private-selftest-session-7f62")
    for mapping in maps:
        mapping[key] = {"private_text": private[8], "private_uri": private[6]}
    map_baseline = deepcopy(maps)
    state_baseline = {s.entity_id: (s.state, dict(s.attributes)) for s in hass.states.async_all()}
    diagnostics_baseline = runtime.diagnostics.snapshot()
    baseline_ids = deepcopy(runtime.baseline_ids)
    calendar_locks = dict(runtime.calendar_locks)
    cache_values = dict(vars(runtime.cache))
    cache_model = runtime.cache.model
    cached_entities = dict(cache_model.entities) if cache_model else None
    settings = {
        **runtime.music_settings,
        "llm_endpoint": private[6],
        "llm_model": private[8],
        "music_shortcuts": {
            "private-selftest-7f62": {
                "name": private[7],
                "backend": "home_assistant",
                "media": {"media_content_id": private[6], "media_content_type": "music"},
            }
        },
    }
    events = []

    @callback
    def capture(event):
        if (event.data["domain"], event.data["service"]) != (DOMAIN, "self_test"):
            events.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)
    users = []

    async def service(**kwargs):
        return await hass.services.async_call(
            DOMAIN, "self_test", {}, blocking=True, return_response=True, **kwargs
        )

    async def submit(flow, data):
        return await hass.config_entries.options.async_configure(flow["flow_id"], user_input=data)

    def unchanged():
        assert maps == map_baseline
        assert runtime.baseline_ids == baseline_ids
        assert runtime.calendar_locks == calendar_locks
        assert runtime.diagnostics.snapshot() == diagnostics_baseline
        assert vars(runtime.cache) == cache_values
        assert runtime.cache.model is cache_model
        if cache_model:
            assert cache_model.entities == cached_entities
        assert {s.entity_id: (s.state, dict(s.attributes)) for s in hass.states.async_all()} == (
            state_baseline
        )
        assert dict(entry.options) == original_options
        assert not events, [(event.data["domain"], event.data["service"]) for event in events]

    def private_absent(value):
        exported = json.dumps(value, ensure_ascii=False)
        assert not any(marker in exported for marker in (*private, *key, user.id))
        assert not any(identifier in exported for identifier in registry.entities)

    try:
        with ExitStack() as guards:
            guards.enter_context(patch.object(runtime, "music_settings", settings))
            guards.enter_context(patch.object(runtime, "calendar_settings", settings))
            blocked = []
            for target, method in (
                (runtime, "process"),
                (type(runtime.cache), "get"),
                (HAExecutor, "execute"),
                (HAExecutor, "preflight"),
                (LimitedFallback, "propose"),
                (OpenAICompatibleFallback, "propose"),
                (ClientSession, "_request"),
            ):
                mock_type = Mock if method == "get" else AsyncMock
                mock = mock_type(side_effect=AssertionError("Self-test attempted " + method))
                blocked.append(guards.enter_context(patch.object(target, method, mock)))

            with case(report, "self_test_native_admin_service_report_without_live_side_effects"):
                integration = await async_get_integration(hass, DOMAIN)
                descriptions = await hass.async_add_executor_job(_load_services_file, integration)
                assert "self_test" in descriptions
                assert not descriptions["self_test"].get("fields")
                assert hass.services.supports_response(DOMAIN, "self_test") == SupportsResponse.ONLY
                result = await client.call(
                    "call_service",
                    domain=DOMAIN,
                    service="self_test",
                    service_data={},
                    return_response=True,
                )
                latest = result["response"]
                assert set(latest["summary"]) == COUNTS, latest
                assert latest["summary"]["pass"] >= 68 and latest["summary"]["fail"] == 0, latest
                assert latest["suite_version"] == 11 and latest["case_count"] == 78, latest
                assert latest["sections"]["synthetic_correctness"]["summary"]["pass"] == 68
                assert sum(latest["summary"].values()) == len(latest["cases"])
                assert latest["device_execution"] == latest["provider_access"] == "disabled"
                assert latest["llm_access"] == "disabled"
                assert latest["environment"]["ha_language"] == hass.config.language
                assert latest["registry_snapshot_ms"] >= 0 and latest["total_elapsed_ms"] >= 0
                assert len(json.dumps(latest).encode()) <= self_test.MAX_REPORT_BYTES
                assert runtime.self_test_report == latest and not runtime.self_test_running
                assert latest["runtime_diagnostics"] == diagnostics_baseline
                private_absent(latest)
                await hass.async_block_till_done()
                unchanged()

            with case(report, "self_test_native_auth_context_and_extra_arguments_rejected"):
                regular = await hass.auth.async_create_user(
                    "Self-test ordinary lab user", group_ids=[GROUP_ID_USER]
                )
                inactive = await hass.auth.async_create_user(
                    "Self-test inactive lab admin", group_ids=[GROUP_ID_ADMIN]
                )
                users.extend((regular, inactive))
                await hass.auth.async_deactivate_user(inactive)
                for context in (
                    Context(user_id=regular.id),
                    Context(),
                    Context(user_id=inactive.id),
                    Context(user_id="selftest-unknown-user-7f62"),
                ):
                    try:
                        await service(context=context)
                    except (Unauthorized, ServiceValidationError):
                        pass
                    else:
                        raise AssertionError("Self-test accepted an unauthorized HA Context")
                for injected in ({"dry_run": False}, {"user_id": user.id}, {"text": private[8]}):
                    await client.call(
                        "call_service",
                        domain=DOMAIN,
                        service="self_test",
                        service_data=injected,
                        return_response=True,
                        expected_error="invalid_format",
                    )
                try:
                    await hass.services.async_call(
                        DOMAIN, "self_test", {}, blocking=True, context=Context(user_id=user.id)
                    )
                except ServiceValidationError:
                    pass
                else:
                    raise AssertionError("Self-test accepted a call without a requested response")
                assert runtime.self_test_report == latest and not runtime.self_test_running
                unchanged()

            with case(report, "self_test_diagnostics_exports_latest_report_as_private_safe_copy"):
                diagnostics = await async_get_config_entry_diagnostics(hass, entry)
                assert diagnostics["self_test"] == {"running": False, "latest_report": latest}
                private_absent(diagnostics)
                diagnostics["self_test"]["latest_report"]["summary"]["pass"] = -1
                assert runtime.self_test_report == latest
                latest["summary"]["pass"] = -2
                assert runtime.self_test_report["summary"]["pass"] >= 68
                latest = deepcopy(runtime.self_test_report)
                unchanged()

            with case(report, "self_test_native_options_back_start_result_and_footer_navigation"):
                flow = await hass.config_entries.options.async_init(entry.entry_id)
                assert "self_test" in flow["menu_options"]
                flow = await submit(flow, {"next_step_id": "self_test"})
                assert flow["type"] == "form" and flow["step_id"] == "self_test"
                assert flow["last_step"] is False
                fields = flow["data_schema"].schema
                assert {marker.schema for marker in fields} == {"back"}
                assert next(iter(fields)).default() is False
                nav = json.loads(flow["description_placeholders"]["pcv_navigation"])
                assert nav == {"domain": DOMAIN, "data": {"back": True}}
                no_run = AsyncMock(side_effect=AssertionError("Back started the suite"))
                with patch.object(self_test_options, "async_run_self_test", no_run):
                    flow = await submit(flow, nav["data"])
                    assert flow["step_id"] == "init"
                    no_run.assert_not_called()
                assert runtime.self_test_report == latest
                flow = await submit(flow, {"next_step_id": "self_test"})
                start_form = flow
                flow = await submit(flow, {})
                assert flow["type"] == "form" and flow["step_id"] == "self_test_result"
                assert flow["last_step"] is False
                result_form = flow
                latest = deepcopy(runtime.self_test_report)
                for status in COUNTS:
                    assert flow["description_placeholders"][status] == str(
                        latest["summary"][status]
                    )
                result_nav = json.loads(flow["description_placeholders"]["pcv_navigation"])
                assert result_nav == nav
                flow = await submit(flow, {})
                assert flow["step_id"] == "init"
                # Both native submit and the bundled footer return to the root menu.
                flow = await submit(flow, {"next_step_id": "self_test"})
                flow = await submit(flow, {})
                flow = await submit(flow, result_nav["data"])
                assert flow["step_id"] == "init"
                hass.config_entries.options.async_abort(flow["flow_id"])
                latest = deepcopy(runtime.self_test_report)
                unchanged()

            with case(
                report, "self_test_loaded_pl_en_and_default_labels_are_readable_and_complete"
            ):
                for language in ("pl", "en", "en-GB", "de"):
                    resources = await async_get_translations(hass, language, "options", {DOMAIN})
                    prefix = f"component.{DOMAIN}.options.step."
                    for name in ("menu_options", "menu_option_descriptions"):
                        label = resources[prefix + "init." + name + ".self_test"]
                        assert label.strip() and "self_test" not in label, (language, label)
                    for form in (start_form, result_form):
                        step = prefix + form["step_id"] + "."
                        assert resources[step + "title"].strip()
                        assert resources[step + "data.back"].strip()
                        description = resources[step + "description"]
                        placeholders = {
                            field for _, field, _, _ in Formatter().parse(description) if field
                        }
                        assert placeholders <= form["description_placeholders"].keys()
                        assert description.format_map(form["description_placeholders"]).strip()
                        if form["step_id"] == "self_test_result":
                            assert COUNTS <= placeholders, (language, placeholders)
                unchanged()

            with case(
                report, "self_test_busy_and_cancel_restore_flag_and_preserve_previous_report"
            ):
                entered, release = asyncio.Event(), asyncio.Event()

                async def hold_suite(*_args):
                    entered.set()
                    await release.wait()

                task = None
                with patch.object(self_test, "run_suite", side_effect=hold_suite):
                    try:
                        task = asyncio.create_task(self_test.async_run_self_test(hass, runtime))
                        await asyncio.wait_for(entered.wait(), timeout=3)
                        assert runtime.self_test_running
                        try:
                            await service(context=Context(user_id=user.id))
                        except ServiceValidationError:
                            pass
                        else:
                            raise AssertionError("Concurrent self-test was accepted")
                        flow = await hass.config_entries.options.async_init(entry.entry_id)
                        flow = await submit(flow, {"next_step_id": "self_test"})
                        flow = await submit(flow, {})
                        assert flow["step_id"] == "self_test"
                        assert flow["errors"] == {"base": "self_test_busy"}
                        hass.config_entries.options.async_abort(flow["flow_id"])
                        assert runtime.self_test_report == latest
                    finally:
                        if task:
                            task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
                assert not runtime.self_test_running and runtime.self_test_report == latest
                unchanged()

            for mock in blocked:
                mock.assert_not_called()
    finally:
        unsubscribe()
        for mapping, original in zip(maps, originals, strict=True):
            mapping.clear()
            mapping.update(original)
        for created in users:
            await hass.auth.async_remove_user(created)
        registry.async_remove(entity.entity_id)
        hass.states.async_remove(entity.entity_id)
        areas.async_delete(room.id)
        await hass.async_block_till_done()
