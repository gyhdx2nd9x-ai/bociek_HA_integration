"""Compare the first E3 catalog slice against trusted, local b5 Git source.

Run ``python -m benchmark.e3_comparison --output <report.json>`` from the repo.
This reads and executes our versioned policy and engine modules from the pinned
local commit; it does not checkout files, install dependencies, execute HA calls,
or read household data. Only the warmed policy and Engine.plan calls are timed.
It is not a microphone-to-response benchmark or a measurement on an N5105.
"""

import argparse
import gc
import json
import math
import platform
import statistics
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from pathlib import Path
from time import perf_counter_ns
from types import ModuleType

from custom_components.polish_context_voice.core import engine as current_engine
from custom_components.polish_context_voice.core import policy as current_policy
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.resolver import Resolution
from custom_components.polish_context_voice.core.types import Action, Proposal, Request

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/polish_context_voice"
BASELINE_COMMIT = "48dc759"
POLICY_PATH = "custom_components/polish_context_voice/core/policy/__init__.py"
ENGINE_PATH = "custom_components/polish_context_voice/core/engine.py"


@dataclass(frozen=True)
class Case:
    id: str
    proposal: Proposal
    entity: Entity


def cases():
    lamp = Entity(
        "light.synthetic_table",
        "Światło testowe",
        "test_room",
        labels=frozenset({"voice_default_on", "voice_all_off"}),
        exposed=True,
        state="off",
        attributes={"supported_color_modes": ["brightness"]},
    )
    thermometer = Entity(
        "sensor.synthetic_temperature",
        "Temperatura testowa",
        "test_room",
        exposed=True,
        device_class="temperature",
        state="21.5",
        attributes={"unit_of_measurement": "°C"},
    )
    return (
        *(
            Case(f"brightness_{value}", Proposal(Action.BRIGHTNESS, "light", value=value), lamp)
            for value in (25, 0, 100)
        ),
        Case("temperature_21_5_c", Proposal(Action.TEMPERATURE, "sensor"), thermometer),
        Case(
            "temperature_invalid_state",
            Proposal(Action.TEMPERATURE, "sensor"),
            replace(thermometer, state="unavailable"),
        ),
        Case("unmigrated_on_control", Proposal(Action.ON, "light"), lamp),
    )


def git_output(*arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def baseline_module(path, name, package):
    """Execute only the two explicitly pinned modules from this local repository."""
    source = git_output("show", f"{BASELINE_COMMIT}:{path}")
    module = ModuleType(name)
    module.__package__ = package
    module.__file__ = f"{BASELINE_COMMIT}:{path}"
    # Dataclass processing needs the module registered while its class is built.
    # These private names do not replace the normal imported modules.
    sys.modules[name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module, sha256(source.encode()).hexdigest()


def legacy_fields(result):
    """Ignore exactly the additive catalog report; compare every legacy field."""
    data = result.to_dict()
    if data["response_data"] is not None:
        response = dict(data["response_data"])
        response.pop("capability", None)
        data["response_data"] = response or None
    return data


def percentile(samples, percent):
    return sorted(samples)[math.ceil(len(samples) * percent / 100) - 1]


def distribution(samples):
    return {
        "samples": len(samples),
        "p50_ms": round(statistics.median(samples), 6),
        "p95_ms": round(percentile(samples, 95), 6),
        "p99_ms": round(percentile(samples, 99), 6),
        "max_ms": round(max(samples), 6),
    }


def current_source_hashes():
    sources = sorted((COMPONENT / "core").rglob("*.py"))
    sources.extend([COMPONENT / "manifest.json", Path(__file__).resolve()])
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in sources}


def compare(before, after, iterations, warmup):
    samples = {"baseline": [], "catalog": []}
    functions = {"baseline": before, "catalog": after}
    reference = legacy_fields(before())
    for index in range(warmup + iterations):
        order = ("baseline", "catalog") if index % 2 == 0 else ("catalog", "baseline")
        results = {}
        for name in order:
            started = perf_counter_ns()
            results[name] = functions[name]()
            elapsed = (perf_counter_ns() - started) / 1_000_000
            # Assertions and serialization are deliberately outside the timer.
            assert legacy_fields(results[name]) == reference, name
            assert results[name].completed == ()
            if index >= warmup:
                samples[name].append(elapsed)
    paired_delta = [
        after_ms - before_ms
        for before_ms, after_ms in zip(samples["baseline"], samples["catalog"], strict=True)
    ]
    before_stats, after_stats = distribution(samples["baseline"]), distribution(samples["catalog"])
    return {
        "legacy_fields_identical": True,
        "baseline": before_stats,
        "catalog": after_stats,
        "paired_delta": distribution(paired_delta),
        "p50_ratio_catalog_over_baseline": round(
            statistics.median(samples["catalog"]) / statistics.median(samples["baseline"]), 4
        ),
    }


def run(iterations, warmup):
    source_hashes = current_source_hashes()
    legacy_policy, policy_hash = baseline_module(
        POLICY_PATH,
        "custom_components.polish_context_voice.core.policy._e3_baseline",
        "custom_components.polish_context_voice.core.policy",
    )
    try:
        legacy_engine, engine_hash = baseline_module(
            ENGINE_PATH,
            "custom_components.polish_context_voice.core._e3_baseline_engine",
            "custom_components.polish_context_voice.core",
        )
    except BaseException:
        sys.modules.pop(legacy_policy.__name__, None)
        raise
    try:
        # The legacy engine's relative policy import normally resolves to current
        # policy. Replace only this private module's reference with the b5 policy.
        legacy_engine.evaluate = legacy_policy.evaluate
        rows = []
        request = Request("Próba syntetyczna", source_area="test_room", dry_run=True)
        for case in cases():
            resolution = Resolution((case.entity,), ("test_room",))
            home = HomeModel(areas=[Area("test_room", "Pokój testowy")], entities=[case.entity])
            before = legacy_engine.Engine(lambda home=home: home)
            after = current_engine.Engine(lambda home=home: home)
            assert before.executor is after.executor is before.fallback is after.fallback is None
            rows.append(
                {
                    "case": case.id,
                    "policy": compare(
                        partial(legacy_policy.evaluate, case.proposal, resolution),
                        partial(current_policy.evaluate, case.proposal, resolution),
                        iterations,
                        warmup,
                    ),
                    "engine_plan": compare(
                        partial(before.plan, case.proposal, request),
                        partial(after.plan, case.proposal, request),
                        iterations,
                        warmup,
                    ),
                }
            )
        assert source_hashes == current_source_hashes(), "Core changed during the benchmark"
        return {
            "benchmark": "e3_b5_vs_catalog_warmed_offline",
            "schema_version": 1,
            "component_version": json.loads((COMPONENT / "manifest.json").read_text())["version"],
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "baseline": {
                "commit": git_output("rev-parse", BASELINE_COMMIT).strip(),
                "source_sha256": {POLICY_PATH: policy_hash, ENGINE_PATH: engine_hash},
                "loading": "Pinned local Git policy/engine modules, private names; no checkout",
                "shared_dependencies": "Both import the same current core dependency modules",
            },
            "environment": {
                "python": sys.version,
                "implementation": platform.python_implementation(),
                "os": platform.system(),
                "os_release": platform.release(),
                "machine": platform.machine(),
                "gc_enabled": gc.isenabled(),
            },
            "method": {
                "iterations_per_case_and_boundary": iterations,
                "excluded_warmup_iterations_per_case_and_boundary": warmup,
                "boundaries": ["policy.evaluate", "Engine.plan"],
                "timing": "perf_counter_ns wall clock, milliseconds",
                "pair_order": "Alternates baseline/catalog then catalog/baseline each iteration",
                "median": "statistics.median",
                "tail_percentiles": "nearest rank",
                "delta": "Catalog minus baseline in each interleaved pair",
                "excluded": "Imports, Git reads, home/index construction, assertions, to_dict",
                "correctness": "Every legacy field equal, ignoring only response_data.capability",
                "requests_dry_run": True,
                "executor": None,
                "fallback": None,
            },
            "source_sha256": source_hashes,
            "results": rows,
            "limits": [
                "Microbenchmark on this host, not an N5105 measurement.",
                "One prebuilt synthetic entity per case and a reused indexed home.",
                "No HA, network, permissions, fresh HA preflight, STT, TTS or real devices.",
                "Engine.plan does not execute, even when it returns a ready call plan.",
                "No cold-start, concurrency, registry rebuild or end-to-end voice measurement.",
                "Ratios on microsecond baselines can look large despite a small absolute delta.",
                "Two hundred samples provide a local comparison, not a latency guarantee.",
            ],
            "passed": True,
        }
    finally:
        sys.modules.pop(legacy_policy.__name__, None)
        sys.modules.pop(legacy_engine.__name__, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10_000 or not 1 <= args.warmup <= 1_000:
        parser.error("Iterations and warmup must be positive and bounded")
    report = run(args.iterations, args.warmup)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"passed": report["passed"], "results": report["results"]}, indent=2))


if __name__ == "__main__":
    main()
