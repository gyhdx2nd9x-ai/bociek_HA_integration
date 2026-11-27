"""Request timing against native HA service dispatch and synthetic light providers."""

import asyncio
from copy import deepcopy
from time import perf_counter
from uuid import uuid4

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr


class DiagnosticsLight(LightEntity):
    """Actual HA entity service target with controllable, synthetic provider work."""

    _attr_should_poll = False
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_is_on = False

    def __init__(self, suffix):
        self._attr_name = "PCV Diagnostics " + suffix
        self._attr_unique_id = "pcv_diagnostics_" + suffix
        self.failure = False
        self.delay = 0
        self.entered = asyncio.Event()
        self.release = None
        self.observations = []
        self.background_factory = None
        self.background_tasks = []

    async def async_turn_on(self, **kwargs):
        from custom_components.polish_context_voice.core.diagnostics import _active_trace

        trace = _active_trace()
        self.observations.append(
            {
                "trace_id": trace.identifier if trace else None,
                "plan_ready": trace.milestones.get("plan_ready") if trace else None,
                "service_entered": perf_counter() - trace.started if trace else None,
                "user_id": self._context.user_id,
            }
        )
        self.entered.set()
        if self.background_factory:
            self.background_tasks.append(asyncio.create_task(self.background_factory()))
        if self.release is not None:
            await self.release.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise HomeAssistantError("Synthetic diagnostics provider failure")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


async def exercise_diagnostics(hass, entry, user, report, case):
    from custom_components.polish_context_voice.core.types import Call, Request
    from custom_components.polish_context_voice.ha.intents import HAExecutor

    domain = "polish_context_voice"
    runtime = hass.data[domain][entry.entry_id]
    registry = er.async_get(hass)
    areas = ar.async_get(hass)
    room = areas.async_create("Laboratorium pomiarów", aliases={"laboratorium pomiarów"})
    lights = [DiagnosticsLight("A"), DiagnosticsLight("B")]
    await hass.data["light"].async_add_entities(lights)
    label = lr.async_get(hass).async_get_label_by_name("voice_default_on")
    assert label is not None
    for light in lights:
        registry.async_update_entity(
            light.entity_id, area_id=room.id, labels={label.label_id},
            aliases={"pomiar pierwszy" if light is lights[0] else "pomiar drugi"},
        )
        exposed_entities.async_expose_entity(hass, "conversation", light.entity_id, True)
    await hass.async_block_till_done()
    context = Context(user_id=user.id)
    tasks = []

    async def command(text="włącz światła", *, dry_run=False, identity=user.id):
        return await runtime.process(
            Request(text, source_area=room.id, conversation_id=uuid4().hex,
                    user_id=identity, dry_run=dry_run),
            context if identity == user.id else Context(user_id=identity),
        )

    def row_for(result):
        return next(
            row for row in runtime.diagnostics.snapshot()["recent_requests"]
            if row["request_id"] == result.request_id
        )

    def service_counts(row, *, attempted=0, returned=0, failed=0, interrupted=0):
        assert row["ha_service_calls"] == {
            "attempted": attempted, "returned": returned,
            "failed": failed, "interrupted": interrupted,
        }, row

    def measured(row, *stages):
        assert all(name in row["stages_ms"] for name in stages), row
        assert all(0 <= value <= row["runtime_elapsed_ms"] + 0.005
                   for value in row["stages_ms"].values()), row

    try:
        with case(report, "diagnostics_v2_native_preview_plan_without_service_samples"):
            before = [hass.states.get(light.entity_id).state for light in lights]
            result = await command(dry_run=True)
            assert result.status == "dry_run" and len(result.calls) == 2, result
            row = row_for(result)
            assert set(row["milestones_ms"]) == {"plan_ready"}, row
            assert 0 <= row["milestones_ms"]["plan_ready"] <= row["runtime_elapsed_ms"], row
            measured(row, "interpretation", "resolution_policy")
            assert "ha_service" not in row["stages_ms"]
            assert "ha_preflight" not in row["stages_ms"]
            service_counts(row)
            assert not any(light.observations for light in lights)
            assert before == [hass.states.get(light.entity_id).state for light in lights]

        with case(report, "diagnostics_native_service_plan_boundary_counts_and_context"):
            for light in lights:
                light.delay = 0.02
            result = await command()
            assert result.status == "executed" and len(result.completed) == 2, result
            row = row_for(result)
            service_counts(row, attempted=2, returned=2)
            measured(row, "interpretation", "resolution_policy", "execution",
                     "ha_preflight", "ha_service")
            assert row["stages_ms"]["ha_service"] >= 30, row
            for light in lights:
                observed = light.observations[-1]
                assert observed["trace_id"] == result.request_id, observed
                assert observed["user_id"] == user.id, observed
                assert observed["plan_ready"] <= observed["service_entered"], observed
            assert row["stages_ms"]["execution"] >= row["stages_ms"]["ha_service"], row
            for light in lights:
                light.delay = 0

        with case(report, "diagnostics_native_preflight_denial_has_no_service_attempt"):
            reader = await hass.auth.async_create_user(
                "Diagnostics read only", group_ids=[GROUP_ID_READ_ONLY]
            )
            before = sum(len(light.observations) for light in lights)
            result = await command(identity=reader.id)
            assert result.status == "error" and not result.completed, result
            row = row_for(result)
            service_counts(row)
            assert "ha_service" not in row["stages_ms"]
            measured(row, "ha_preflight")
            assert before == sum(len(light.observations) for light in lights)

        with case(report, "diagnostics_native_provider_failure_is_not_service_return"):
            lights[0].failure = True
            result = await command("włącz światło pomiar pierwszy")
            assert result.status == "error" and not result.completed, result
            row = row_for(result)
            assert row["call_count"] == 1
            service_counts(row, attempted=1, failed=1)
            measured(row, "ha_service")
            lights[0].failure = False

        with case(report, "diagnostics_native_partial_execution_keeps_planned_and_actual_counts"):
            lights[1].failure = True
            result = await command()
            assert result.status == "partial" and result.completed == (lights[0].entity_id,), result
            row = row_for(result)
            assert row["call_count"] == 2 and row["completed_target_count"] == 1, row
            service_counts(row, attempted=2, returned=1, failed=1)
            lights[1].failure = False

        with case(report, "diagnostics_native_other_adapter_has_no_invented_plan_sample"):
            result = await command("która godzina")
            assert result.status == "answered" and result.route == "ha_native", result
            row = row_for(result)
            assert not row["milestones_ms"], row
            assert "interpretation" not in row["stages_ms"]
            assert "ha_service" not in row["stages_ms"]
            service_counts(row)

        with case(report, "diagnostics_native_concurrent_requests_keep_separate_services"):
            lights[0].entered.clear()
            lights[0].release = asyncio.Event()
            slow = asyncio.create_task(command("włącz światło pomiar pierwszy"))
            tasks.append(slow)
            await asyncio.wait_for(lights[0].entered.wait(), 5)
            fast = await command("włącz światło pomiar drugi")
            fast_row = row_for(fast)
            service_counts(fast_row, attempted=1, returned=1)
            assert not slow.done()
            lights[0].release.set()
            result = await asyncio.wait_for(slow, 5)
            slow_row = row_for(result)
            assert result.request_id != fast.request_id
            service_counts(slow_row, attempted=1, returned=1)
            assert lights[0].observations[-1]["trace_id"] == result.request_id
            assert lights[1].observations[-1]["trace_id"] == fast.request_id
            lights[0].release = None

        with case(report, "diagnostics_native_cancelled_wait_is_recorded_as_interrupted"):
            lights[0].entered.clear()
            lights[0].release = asyncio.Event()
            prior = runtime.diagnostics.snapshot()["completed_since_reload"]
            task = asyncio.create_task(command("włącz światło pomiar pierwszy"))
            tasks.append(task)
            await asyncio.wait_for(lights[0].entered.wait(), 5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Cancellation must propagate from runtime")
            snapshot = runtime.diagnostics.snapshot()
            assert snapshot["completed_since_reload"] == prior + 1
            row = snapshot["recent_requests"][-1]
            assert row["category"] == "interrupted", row
            service_counts(row, attempted=1, interrupted=1)
            measured(row, "execution", "ha_service")
            lights[0].release.set()
            lights[0].release = None

        with case(report, "diagnostics_native_late_inherited_background_cannot_mutate_traces"):
            release_background = asyncio.Event()
            background_done = asyncio.Event()

            async def background():
                await release_background.wait()
                await HAExecutor(hass, context).execute(
                    Call("light", "turn_off", (lights[0].entity_id,)),
                    Request("synthetic background", user_id=user.id),
                )
                background_done.set()

            lights[0].background_factory = background
            first = await command("włącz światło pomiar pierwszy")
            lights[0].background_factory = None
            sealed = deepcopy(row_for(first))
            lights[1].entered.clear()
            lights[1].release = asyncio.Event()
            second = asyncio.create_task(command("włącz światło pomiar drugi"))
            tasks.append(second)
            await asyncio.wait_for(lights[1].entered.wait(), 5)
            release_background.set()
            await asyncio.wait_for(background_done.wait(), 5)
            assert row_for(first) == sealed
            assert not second.done()
            lights[1].release.set()
            result = await asyncio.wait_for(second, 5)
            service_counts(row_for(result), attempted=1, returned=1)
            service_counts(row_for(first), attempted=1, returned=1)
            assert hass.states.get(lights[0].entity_id).state == "off"
            lights[1].release = None

        with case(report, "diagnostics_native_v2_aggregate_matches_actual_recent_rows"):
            snapshot = runtime.diagnostics.snapshot()
            rows = snapshot["recent_requests"]
            assert snapshot["schema_version"] == 2
            for outcome, total in snapshot["ha_service_calls"].items():
                assert total == sum(row["ha_service_calls"][outcome] for row in rows)
            assert snapshot["milestones"]["plan_ready"]["count"] == sum(
                "plan_ready" in row["milestones_ms"] for row in rows
            )
            for name, metric in snapshot["stages"].items():
                assert metric["count"] == sum(name in row["stages_ms"] for row in rows)
            assert snapshot["measurement_scope"]["timings_are_nested"]
            report["request_diagnostics_v2"] = {
                "native_provider": "Synthetic LightEntity through actual HA service dispatch",
                "schema_version": snapshot["schema_version"],
                "service_counts_checked": True,
                "plan_before_service_checked": True,
                "parallel_and_late_task_isolation_checked": True,
                "audio_or_physical_device_latency_measured": False,
            }
    finally:
        for light in lights:
            if light.release is not None:
                light.release.set()
            for task in light.background_tasks:
                if not task.done():
                    task.cancel()
            tasks.extend(light.background_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for light in lights:
            await light.async_remove()
            if registry.async_get(light.entity_id):
                registry.async_remove(light.entity_id)
        areas.async_delete(room.id)
        await hass.async_block_till_done()
