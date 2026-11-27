"""HA adapter contract doubles: no device writes, bounded report and cancellation."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from test_native_boundary import load

from custom_components.polish_context_voice.core.diagnostics import RequestDiagnostics


@pytest.fixture
def boundary(monkeypatch, home):
    module = load(
        monkeypatch,
        "self_test",
        {
            "homeassistant.const": {"__version__": "2026.10.0"},
            "homeassistant.exceptions": {"ServiceValidationError": ValueError},
            "custom_components.polish_context_voice.ha.registry_sync": {
                "snapshot": lambda hass: home,
            },
        },
    )

    async def job(func):
        return func()

    runtime = NS(
        diagnostics=RequestDiagnostics(),
        cache=NS(metrics=lambda: {"rebuilds": 7, "state_updates": 8, "last_rebuild_ms": 0.5}),
        music_settings={},
        pending={"private": object()},
        process=AsyncMock(side_effect=AssertionError("Do not call the live runtime")),
        health=Mock(side_effect=AssertionError("Do not change health baseline")),
    )
    hass = NS(config=NS(language="pl"), async_add_executor_job=job)
    return module, hass, runtime


async def test_report_isolated_and_diagnostics_export_is_a_copy(boundary):
    module, hass, runtime = boundary
    pending = dict(runtime.pending)
    before = runtime.diagnostics.snapshot()
    report = await module.async_run_self_test(hass, runtime)
    assert report["summary"]["fail"] == 0
    assert report["environment"]["ha_core"] == "2026.10.0"
    assert report["device_execution"] == report["llm_access"] == "disabled"
    assert report["runtime_diagnostics"] == before
    assert runtime.diagnostics.snapshot() == before and runtime.pending == pending
    runtime.process.assert_not_called()
    runtime.health.assert_not_called()
    report["summary"]["pass"] = -99
    saved = module.report_state(runtime)
    assert saved["latest_report"]["summary"]["pass"] > 0
    saved["latest_report"]["cases"].clear()
    assert module.report_state(runtime)["latest_report"]["cases"]
    assert not saved["running"] and not runtime.self_test_running


async def test_second_run_replaces_one_report_not_a_history(boundary):
    module, hass, runtime = boundary
    first = await module.async_run_self_test(hass, runtime)
    second = await module.async_run_self_test(hass, runtime)
    assert first["run_id"] != second["run_id"]
    assert module.report_state(runtime)["latest_report"]["run_id"] == second["run_id"]
    assert "history" not in module.report_state(runtime)


async def test_busy_and_cancel_leave_previous_report_and_release_slot(boundary, monkeypatch):
    module, hass, runtime = boundary
    previous = await module.async_run_self_test(hass, runtime)
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "run_suite", wait)
    task = asyncio.create_task(module.async_run_self_test(hass, runtime))
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(ValueError, match="Trwa już test"):
        await module.async_run_self_test(hass, runtime)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runtime.self_test_running
    assert module.report_state(runtime)["latest_report"] == previous


@pytest.mark.parametrize("exception", [RuntimeError, TimeoutError])
async def test_suite_errors_become_fixed_codes_without_private_text(
    boundary, monkeypatch, exception
):
    module, hass, runtime = boundary

    async def broken(*args):
        raise exception("PRIVATE-alias-secret")

    monkeypatch.setattr(module, "run_suite", broken)
    result = await module.async_run_self_test(hass, runtime)
    assert result["summary"]["fail"] == 1
    assert result["cases"][0]["actual_code"] in {"suite_timeout", "suite_internal_error"}
    assert "PRIVATE" not in json.dumps(result)
    assert not runtime.self_test_running


async def test_oversized_report_is_bounded_without_retaining_payload(boundary, monkeypatch):
    module, hass, runtime = boundary

    async def huge(*args):
        return {"private_payload": "X" * 300_000}

    monkeypatch.setattr(module, "run_suite", huge)
    result = await module.async_run_self_test(hass, runtime)
    assert len(json.dumps(result)) < module.MAX_REPORT_BYTES
    assert result["cases"][0]["actual_code"] == "report_size_limit"
    assert "private_payload" not in json.dumps(module.report_state(runtime))


def test_no_report_before_run_or_after_new_runtime(boundary):
    module, _, _ = boundary
    assert module.report_state(NS()) == {"running": False, "latest_report": None}
    assert module.report_state(None) == {"running": False, "latest_report": None}
