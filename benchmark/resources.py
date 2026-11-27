"""Standalone resource profile; never installs, starts or connects to Home Assistant."""

import argparse
import asyncio
import gc
import json
import platform
import resource
import statistics
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, process_time

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.home_model.cache import ModelCache

from .run import correct, percentile, request_for


def distribution(samples):
    return {
        "p50": statistics.median(samples),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
    }


async def profile(iterations, household, inventory, cases_path):
    data = json.loads(household.read_text())
    reference = json.loads(inventory.read_text())
    known = {entity["id"] for entity in data["entities"]}
    data["entities"].extend(entity for entity in reference["entities"] if entity["id"] not in known)
    cases = json.loads(cases_path.read_text())
    cache = ModelCache(lambda: HomeModel.from_dict(data), max_age=float("inf"))
    # Same view implementation as Runtime; HA's actual permission checker is not measured.
    engine = Engine(lambda: cache.get().visible_to(lambda entity_id: True))
    results = [(case, await engine.process(request_for(case))) for case in cases]
    failures = [case["id"] for case, result in results if not correct(case, result)]
    wall, cpu = [], []
    for _ in range(iterations):
        for case in cases:
            request = request_for(case)
            wall_start, cpu_start = perf_counter(), process_time()
            await engine.process(request)
            cpu.append((process_time() - cpu_start) * 1000)
            wall.append((perf_counter() - wall_start) * 1000)
    cold_wall, cold_cpu = [], []
    for case in cases:
        request = request_for(case)
        cache.invalidate()
        wall_start, cpu_start = perf_counter(), process_time()
        result = await engine.process(request)
        cold_cpu.append((process_time() - cpu_start) * 1000)
        cold_wall.append((perf_counter() - wall_start) * 1000)
        if not correct(case, result) and case["id"] not in failures:
            failures.append(case["id"])

    rebuild = []
    for _ in range(20):
        started = perf_counter()
        cache.get(force=True)
        rebuild.append((perf_counter() - started) * 1000)

    # Memory measured separately: tracing substantially changes timing. JSON input and
    # Python/module imports already exist, so report incremental allocations explicitly.
    gc.collect()
    tracemalloc.start()
    memory_model = HomeModel.from_dict(data)
    gc.collect()
    model_retained, model_peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    views = [memory_model.visible_to(lambda entity_id: False) for _ in range(8)]
    assert len(views) == 8
    gc.collect()
    with_views, with_views_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = int(max_rss if platform.system() == "Darwin" else max_rss * 1024)
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "component_version": json.loads(
            Path("custom_components/polish_context_voice/manifest.json").read_text()
        )["version"],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "entity_count": len(cache.model.entities),
        "household": str(household),
        "inventory_for_size_only": str(inventory),
        "scope": "Offline core plus per-plan visibility views. Stub permission predicate. No HA.",
        "cases": len(cases),
        "correct": len(cases) - len(failures),
        "failures": failures,
        "samples": len(wall),
        "wall_ms": distribution(wall),
        "cpu_ms": distribution(cpu),
        "cold_cache": {
            "samples": len(cold_wall),
            "wall_ms": distribution(cold_wall),
            "cpu_ms": distribution(cold_cpu),
        },
        "rebuild_ms_p95": percentile(rebuild, 0.95),
        "memory_bytes": {
            "model_incremental_retained": model_retained,
            "model_incremental_peak": model_peak,
            "eight_denied_views_incremental_retained": with_views - model_retained,
            "model_plus_views_peak": with_views_peak,
            "standalone_process_max_rss": rss_bytes,
        },
        "caveats": [
            "Not measured on N5105 unless platform identifies the target host.",
            "Reference inventory is unexposed with unknown metadata; only synthetic cases scored.",
            "Heap excludes input JSON and imports; RSS covers the entire standalone process.",
            "No HA registry, real permissions, HA load, network, STT, TTS, LLM or devices.",
            "Cold cache measures model invalidation after imports, not process startup.",
            "Eight views is an allocation stress probe, not eight simultaneous HA requests.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--household", type=Path, default=Path("examples/household.json"))
    parser.add_argument("--inventory", type=Path, default=Path("examples/inventory_950.json"))
    parser.add_argument("--cases", type=Path, default=Path("benchmark/cases.json"))
    parser.add_argument("--output", type=Path, default=Path("benchmark/resources.json"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    report = asyncio.run(profile(args.iterations, args.household, args.inventory, args.cases))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report["failures"] else 0)


if __name__ == "__main__":
    main()
