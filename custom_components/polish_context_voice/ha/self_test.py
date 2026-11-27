"""Manual, isolated self-test. No calls to the live conversation runtime or services."""

import asyncio
import json
import platform
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.exceptions import ServiceValidationError

from ..core.self_test import run_suite
from .registry_sync import snapshot

MAX_REPORT_BYTES = 256_000


def _failure(code):
    return {
        "schema_version": 1,
        "suite_version": 1,
        "suite_name": "bociek_safe_self_test",
        "summary": {"pass": 0, "fail": 1, "warn": 0, "skip": 0},
        "cases": [
            {
                "id": "RUNNER",
                "status": "fail",
                "expected_code": "suite_completed",
                "actual_code": code,
                "duration_ms": 0.0,
            }
        ],
    }


async def async_run_self_test(hass, runtime):
    """Called only by the HA admin options flow or the explicitly guarded service.

    Keep one report in this runtime's RAM. Do not save a household inventory or
    alter the live cache, dialogue, confirmation maps, metrics or fallback.
    """
    if getattr(runtime, "self_test_running", False):
        raise ServiceValidationError("Trwa już test Boćka. Poczekaj na jego zakończenie.")
    runtime.self_test_running = True
    started_at = datetime.now(UTC).isoformat()
    started = perf_counter()
    try:

        def version():
            return json.loads((Path(__file__).parents[1] / "manifest.json").read_text())["version"]

        integration_version = await hass.async_add_executor_job(version)
        # These methods expose counts and fixed-vocabulary metadata only.
        diagnostics = runtime.diagnostics.snapshot()
        cache_metrics = runtime.cache.metrics()
        snapshot_started = perf_counter()
        try:
            # A separate read: runtime.health/cache.get would alter live baselines.
            try:
                model = snapshot(hass)
            finally:
                snapshot_ms = round((perf_counter() - snapshot_started) * 1000, 3)
            result = await asyncio.wait_for(run_suite(model, runtime.music_settings), timeout=15)
        except TimeoutError:
            result = _failure("suite_timeout")
        except Exception:
            # Exception text can contain private entity names or provider URLs.
            result = _failure("suite_internal_error")
        result.update(
            {
                "started_utc": started_at,
                "finished_utc": datetime.now(UTC).isoformat(),
                "total_elapsed_ms": round((perf_counter() - started) * 1000, 3),
                "registry_snapshot_ms": snapshot_ms,
                "environment": {
                    "integration_version": integration_version,
                    "ha_core": HA_VERSION,
                    "python": platform.python_version(),
                    "architecture": platform.machine(),
                    "ha_language": hass.config.language,
                },
                "mode": "isolated_core_and_registry_read",
                "device_execution": "disabled",
                "provider_access": "disabled",
                "llm_access": "disabled",
                "runtime_diagnostics": diagnostics,
                "model_cache_metrics": cache_metrics,
                "retention": "last_report_in_memory_until_reload",
                "not_tested": [
                    "microphone",
                    "stt",
                    "tts",
                    "assist_pipeline",
                    "provider_api",
                    "physical_devices",
                    "audible_response_start",
                ],
            }
        )
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_REPORT_BYTES:
            # Bounded even if a future collector accidentally widens its window.
            result = {
                **_failure("report_size_limit"),
                "started_utc": started_at,
                "environment": result["environment"],
            }
        runtime.self_test_report = deepcopy(result)
        return deepcopy(result)
    finally:
        # Cancellation propagates and keeps the previous completed report.
        runtime.self_test_running = False


def report_state(runtime):
    return {
        "running": bool(runtime and getattr(runtime, "self_test_running", False)),
        "latest_report": deepcopy(getattr(runtime, "self_test_report", None)) if runtime else None,
    }
