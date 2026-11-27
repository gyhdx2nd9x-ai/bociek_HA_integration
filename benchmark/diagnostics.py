"""Isolated request metadata overhead; no Home Assistant, audio, network or devices."""

import argparse
import gc
import json
import platform
import statistics
import tracemalloc
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter_ns

from custom_components.polish_context_voice.core.diagnostics import (
    RequestDiagnostics,
    mark_milestone,
    measure_service_call,
    measure_stage,
)
from custom_components.polish_context_voice.core.types import Call, Result

from .run import percentile

STATUSES = ("executed", "dry_run", "clarify", "error")
REASONS = ("", "", "no_targets", "execution_failed")
EXPECTED_CATEGORIES = ("success", "success", "resolution", "provider_or_execution")


def _operation(index):
    """A synthetic Result allocation shared unchanged by both timed paths."""
    return Result(
        status=STATUSES[index % 4],
        speech="Synthetic private speech, never export this phrase",
        targets=("light.synthetic_private_target",),
        calls=(Call("light", "turn_on", ("light.synthetic_private_target",)),),
        completed=("light.synthetic_private_target",) if index % 4 == 0 else (),
        reason=REASONS[index % 4],
    )


def _baseline(index):
    # Same local decisions and Result construction as the recorded path; its only
    # difference is the absence of trace creation, stage contexts and collection.
    has_source_area = bool(index % 2)
    authorized = index >= 0
    route = "fast" if authorized and has_source_area else "ha_native"
    result = _operation(index)
    result.route = route
    return result


def _recorded(recorder, index):
    trace = recorder.begin(
        dry_run=index % 4 == 1,
        has_source_area=bool(index % 2),
        has_satellite=True,
        has_device=True,
    )
    with trace.stage("source_context"):
        has_source_area = bool(index % 2)
    with trace.stage("authorization"):
        authorized = index >= 0
    with trace.stage("routing"):
        route = "fast" if authorized and has_source_area else "ha_native"
    with trace.activate(), trace.stage("handler"):
        with measure_stage("interpretation"):
            result = _operation(index)
        with measure_stage("resolution_policy"):
            result.route = route
        if index % 4 in {0, 1}:
            mark_milestone("plan_ready")
        if index % 4 == 0:
            # Synthetic boundary overhead only; there is no real HA service.
            with measure_stage("execution"), measure_stage("ha_preflight"):
                with measure_service_call():
                    pass
    trace.finish(result)
    return result


def _batch(call, size):
    started = perf_counter_ns()
    for index in range(size):
        call(index)
    return (perf_counter_ns() - started) / size / 1_000_000


def _distribution(samples):
    return {"p50": statistics.median(samples), "p95": percentile(samples, 0.95)}


def correctness():
    """Check observable accounting, privacy and window eviction under mixed outcomes."""
    recorder = RequestDiagnostics()
    request_count = recorder.capacity * 4 + 1
    identifiers = []
    expected_categories = Counter()
    for index in range(request_count):
        _recorded(recorder, index)
        identifiers.append(recorder.snapshot()["recent_requests"][-1]["request_id"])
        expected_categories[EXPECTED_CATEGORIES[index % 4]] += 1
    snapshot = recorder.snapshot()
    rows = snapshot["recent_requests"]
    exported = json.dumps(snapshot)
    checks = {
        "all_requests_counted": snapshot["completed_since_reload"] == request_count,
        "category_totals_match_requests": sum(snapshot["categories_since_reload"].values())
        == request_count,
        "mixed_outcomes_counted_correctly": all(
            snapshot["categories_since_reload"][category] == expected
            for category, expected in expected_categories.items()
        ),
        "window_stays_bounded": snapshot["window_size"] == recorder.capacity == len(rows),
        "old_requests_evicted_in_order": [row["request_id"] for row in rows]
        == identifiers[-recorder.capacity :],
        "request_identifiers_unique": len(set(identifiers)) == request_count,
        "target_and_call_counts_preserved": all(
            row["target_count"] == row["call_count"] == 1 for row in rows
        ),
        "base_and_planning_stages_present": all(
            {
                "source_context",
                "authorization",
                "routing",
                "handler",
                "interpretation",
                "resolution_policy",
            }
            <= set(row["stages_ms"])
            for row in rows
        ),
        "only_ready_controls_have_milestone": all(
            bool(row["milestones_ms"]) == (row["status"] in {"executed", "dry_run"}) for row in rows
        ),
        "synthetic_service_boundary_counts_match": all(
            row["ha_service_calls"]["attempted"]
            == row["ha_service_calls"]["returned"]
            == (1 if row["status"] == "executed" else 0)
            for row in rows
        ),
        "private_result_details_absent": "synthetic_private_target" not in exported
        and "Synthetic private speech" not in exported,
    }
    return {
        "requests": request_count,
        "checks": len(checks),
        "passed": sum(checks.values()),
        "failures": [name for name, passed in checks.items() if not passed],
        "checks_by_name": checks,
    }


def memory_profile():
    """Separate allocation probe; traced timings are never used for speed claims."""
    gc.collect()
    tracemalloc.start()
    recorder = RequestDiagnostics()
    for index in range(recorder.capacity):
        _recorded(recorder, index)
    gc.collect()
    initial_retained, initial_peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    request_count = 4096
    for index in range(recorder.capacity, request_count):
        _recorded(recorder, index)
    gc.collect()
    final_retained, final_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    snapshot = recorder.snapshot()
    return {
        "requests": request_count,
        "window_size": snapshot["window_size"],
        "retained_after_64_requests_bytes": initial_retained,
        "retained_after_4096_requests_bytes": final_retained,
        "retained_delta_bytes": final_retained - initial_retained,
        "peak_first_64_requests_bytes": initial_peak,
        "peak_remaining_requests_bytes": final_peak,
        "scope": "Incremental Python allocations for this collector and synthetic operations. "
        "Imports and inputs predate tracing. No snapshot is retained during measurement. "
        "This is not total process or Home Assistant RAM usage.",
    }


def run(batches=25, batch_size=1000, warmup=500):
    recorder = RequestDiagnostics()

    def baseline(index):
        return _baseline(index)

    def recorded(index):
        return _recorded(recorder, index)

    _batch(baseline, warmup)
    _batch(recorded, warmup)
    baseline_samples, recorded_samples, paired_deltas = [], [], []
    for index in range(batches):
        # Alternating pair order reduces a persistent warm-up/clock-drift bias.
        if index % 2:
            with_metadata = _batch(recorded, batch_size)
            without_metadata = _batch(baseline, batch_size)
        else:
            without_metadata = _batch(baseline, batch_size)
            with_metadata = _batch(recorded, batch_size)
        baseline_samples.append(without_metadata)
        recorded_samples.append(with_metadata)
        paired_deltas.append(with_metadata - without_metadata)

    snapshot_samples, export_samples = [], []
    # Export is on demand, outside request handling, and timed independently.
    for _ in range(batches):
        snapshot_samples.append(_batch(lambda _: recorder.snapshot(), batch_size))
        export_samples.append(
            _batch(lambda _: json.dumps(recorder.snapshot(), ensure_ascii=False), batch_size)
        )
    manifest = (
        Path(__file__).resolve().parents[1] / "custom_components/polish_context_voice/manifest.json"
    )
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "component_version": json.loads(manifest.read_text())["version"],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "source_sha256": {
            "collector": sha256((manifest.parent / "core/diagnostics.py").read_bytes()).hexdigest(),
            "benchmark": sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "scope": "Isolated in-process metadata collection around identical synthetic Result "
        "operations. No HA runtime, registry sync, permissions, event loop contention, network, "
        "STT, TTS, LLM or physical devices. Not measured on N5105.",
        "method": {
            "warmup_requests_per_path": warmup,
            "paired_batches": batches,
            "requests_per_batch": batch_size,
            "timed_requests_per_path": batches * batch_size,
            "order": "baseline/recorded and recorded/baseline alternate by batch",
            "unit": "Milliseconds per operation, averaged within each batch; percentiles "
            "describe batch averages, not individual request tail latency.",
            "baseline": "Four local synthetic stages and Result construction, no collector",
            "recorded": "Same operation plus UUID, request-local activation, base and nested "
            "stage contexts, conditional milestone/service counters and bounded collection. "
            "Synthetic service span only; no HA API call.",
        },
        "request_ms": {
            "without_collector": _distribution(baseline_samples),
            "with_collector": _distribution(recorded_samples),
            "paired_added_cost": _distribution(paired_deltas),
            "paired_added_cost_batch_samples": paired_deltas,
        },
        "on_demand_export_ms": {
            "window_size": len(recorder.records),
            "samples_per_operation": batches * batch_size,
            "snapshot": _distribution(snapshot_samples),
            "snapshot_and_json_serialization": _distribution(export_samples),
        },
        "correctness": correctness(),
        "memory": memory_profile(),
        "caveats": [
            "A small synthetic operation exposes metadata overhead; it is not assistant latency.",
            "Actual HA host load and N5105 CPU/RAM need separate measurements on that machine.",
            "Paired differences retain measurement noise and are not clamped to zero.",
            "Memory tracing runs separately and does not contribute to timing samples.",
            "No performance or whole-home correctness acceptance threshold is inferred here.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.batches, args.batch_size, args.warmup) < 1:
        parser.error("batches, batch-size and warmup must be positive")
    report = run(args.batches, args.batch_size, args.warmup)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["correctness"]["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
