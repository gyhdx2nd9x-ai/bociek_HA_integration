"""Reproducible warmed D01 timings on fictional inventories, with no HA or I/O.

Run from the repository root with ``python -m benchmark.reference_dialogue``.
Only Engine.process is timed; constructing the home, resetting dialogue and
preparing a preceding turn are outside the samples. This is not a voice-pipeline
benchmark or an N5105 measurement.
"""

import argparse
import asyncio
import gc
import json
import math
import platform
import statistics
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter_ns

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/polish_context_voice"
SIZES = (100, 500, 2000)
ROOM_LIGHTS = 8
TARGET = "light.table"
PHASES = ("initial_orphan_question", "alias_continuation", "valid_memory_reference")


def fictional_home(size):
    """Exactly eight eligible room lights; all remaining lights are elsewhere."""
    areas = [
        Area("salon", "Salon"),
        Area("kuchnia", "Kuchnia"),
        Area("gabinet", "Gabinet"),
        Area("sypialnia", "Sypialnia"),
        Area("taras", "Taras"),
    ]
    entities = []
    for index in range(size):
        local = index < ROOM_LIGHTS
        entities.append(
            Entity(
                TARGET if index == 0 else f"light.synthetic_{index}",
                "Lampka stołowa" if index == 0 else f"Lampa syntetyczna {index}",
                "salon" if local else areas[1 + (index - ROOM_LIGHTS) % 4].id,
                aliases=("nad stołem", "lampka stołowa") if index == 0 else (),
                labels=frozenset({"voice_all_off"}),
                exposed=True,
                available=True,
                state="off",
                attributes={"supported_color_modes": ["brightness"]},
            )
        )
    home = HomeModel(areas=areas, entities=entities)
    assert len(home.entities) == size
    assert sum(entity.area_id == "salon" for entity in entities) == ROOM_LIGHTS
    return home


def request(text):
    return Request(
        text,
        source_area="salon",
        conversation_id="synthetic-benchmark",
        user_id="synthetic-user",
        dry_run=True,
    )


def check_plan(result, service):
    assert result.status == "dry_run", result.to_dict()
    assert result.targets == (TARGET,) and result.area_ids == ("salon",)
    assert not result.continue_conversation and not result.completed
    assert len(result.calls) == 1
    call = result.calls[0]
    assert (call.domain, call.service, call.entity_ids, call.data, call.guard_entity_ids) == (
        "light",
        service,
        (TARGET,),
        {},
        (),
    )


def check_question(result):
    assert result.status == "clarify" and result.reason == "no_reference"
    assert result.continue_conversation
    assert result.targets == result.calls == result.completed == ()


def percentile(samples, percent):
    return sorted(samples)[math.ceil(len(samples) * percent / 100) - 1]


def timings(samples):
    assert samples and all(math.isfinite(value) and value >= 0 for value in samples)
    return {
        "count": len(samples),
        "p50_ms": round(statistics.median(samples), 6),
        "p95_ms": round(percentile(samples, 95), 6),
        "p99_ms": round(percentile(samples, 99), 6),
        "max_ms": round(max(samples), 6),
    }


async def measure(home, phase, iterations, warmup):
    engine = Engine(lambda: home)
    assert engine.executor is None and engine.fallback is None
    samples = []
    for index in range(warmup + iterations):
        # Preserve one warmed Engine and indexed model, but start each sample
        # from the same bounded dialogue state. Preparation is never timed.
        engine.dialogue.clear()
        engine.pending.clear()
        if phase == "alias_continuation":
            check_question(await engine.process(request("Wyłącz je")))
            assert len(next(iter(engine.dialogue.questions.values())).candidates) == ROOM_LIGHTS
            command = request("Nad stołem")
        elif phase == "valid_memory_reference":
            check_plan(await engine.process(request("Włącz lampkę stołową")), "turn_on")
            command = request("Wyłącz je")
        else:
            command = request("Wyłącz je")
        started = perf_counter_ns()
        result = await engine.process(command)
        elapsed_ms = (perf_counter_ns() - started) / 1_000_000
        if phase == "initial_orphan_question":
            check_question(result)
            assert len(next(iter(engine.dialogue.questions.values())).candidates) == ROOM_LIGHTS
        else:
            check_plan(result, "turn_off")
        if index >= warmup:
            samples.append(elapsed_ms)
    assert len(samples) == iterations
    return timings(samples) | {"samples_ms": [round(value, 6) for value in samples]}


async def run(iterations, warmup):
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    rows = []
    for size in SIZES:
        home = fictional_home(size)
        rows.append(
            {
                "inventory_entities": size,
                "current_room_eligible_lights": ROOM_LIGHTS,
                "other_area_entities": size - ROOM_LIGHTS,
                "phases": {
                    phase: await measure(home, phase, iterations, warmup) for phase in PHASES
                },
            }
        )
    sources = sorted((COMPONENT / "core").rglob("*.py"))
    sources.extend([COMPONENT / "manifest.json", Path(__file__).resolve()])
    return {
        "benchmark": "d01_warmed_offline_dialogue",
        "component_version": manifest["version"],
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "gc_enabled": gc.isenabled(),
        },
        "method": {
            "iterations_per_phase_and_inventory": iterations,
            "excluded_warmup_iterations_per_phase_and_inventory": warmup,
            "timed_boundary": "await Engine.process(request), outer perf_counter_ns wall clock",
            "excluded_setup": "HomeModel indexes, dialogue reset and preceding setup turns",
            "median": "statistics.median",
            "tail_percentiles": "nearest rank, including p99",
            "executor": None,
            "fallback": None,
            "all_requests_dry_run": True,
            "assertions": "Every warmup/sample must match exact status, targets and call semantics",
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "results": rows,
        "limits": [
            "Fictional offline inventories; no Home Assistant, network or real devices.",
            "No STT, TTS, microphone, audible response, HA authorization or provider latency.",
            "Warm sequential calls with a reused indexed model; no cold start or registry rebuild.",
            "Eight eligible lights in the source room; other distributions can cost more.",
            "This host is not the user's N5105; do not extrapolate these times to that CPU.",
            "A microbenchmark validates exact plans here, not household language accuracy.",
            "One hundred samples make p99 sensitive to a few observations; rerun for comparison.",
        ],
        "passed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100_000 or not 1 <= args.warmup <= 10_000:
        parser.error("Iterations and warmup must be positive and bounded")
    report = asyncio.run(run(args.iterations, args.warmup))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "component_version": report["component_version"],
                "environment": report["environment"],
                "results": [
                    row
                    | {
                        "phases": {
                            phase: {
                                key: value for key, value in stats.items() if key != "samples_ms"
                            }
                            for phase, stats in row["phases"].items()
                        }
                    }
                    for row in report["results"]
                ],
                "passed": report["passed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
