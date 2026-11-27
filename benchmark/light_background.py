"""Paired offline D02 timings; no HA, executor, audio, LLM or N5105 measurement."""

import argparse
import asyncio
import gc
import json
import platform
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter_ns

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request

from .reference_dialogue import timings

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/polish_context_voice"
PAIRS = (
    ("default_on", "Włącz światła", "Włącz światła wow jaka figura", ("light.synthetic_0",)),
    (
        "semantic_off",
        "Wyłącz światła",
        "Wyłącz światła wow jaka figura",
        tuple(f"light.synthetic_{index}" for index in range(8)),
    ),
    (
        "explicit_alias",
        "Włącz światła nad stołem",
        "Włącz światła nad stołem wow jaka figura",
        ("light.synthetic_1",),
    ),
)


def fictional_home(size):
    areas = [Area("salon", "Salon"), Area("kuchnia", "Kuchnia")]
    entities = [
        Entity(
            f"light.synthetic_{index}",
            f"Lampa syntetyczna {index}",
            "salon" if index < 8 else "kuchnia",
            aliases=("nad stołem",) if index == 1 else (),
            labels=frozenset({"voice_all_off", *(("voice_default_on",) if index == 0 else ())}),
            exposed=True,
            available=True,
            state="off",
            attributes={"supported_color_modes": ["onoff"]},
        )
        for index in range(size)
    ]
    return HomeModel(areas=areas, entities=entities)


async def measure(home, pair, iterations, warmup):
    name, clean, background, expected = pair
    engine = Engine(lambda: home)
    assert engine.executor is None and engine.fallback is None
    samples = {"clean": [], "background_question": []}
    expected_calls = None
    for index in range(warmup + iterations):
        # Alternate which phrase is timed first, while keeping setup untimed.
        order = (("clean", clean), ("background_question", background))
        if index % 2:
            order = tuple(reversed(order))
        for phase, text in order:
            engine.pending.clear()
            engine.dialogue.clear()
            request = Request(
                text,
                source_area="salon",
                conversation_id="d02-synthetic-benchmark",
                user_id="synthetic-user",
                dry_run=True,
            )
            started = perf_counter_ns()
            result = await engine.process(request)
            elapsed = (perf_counter_ns() - started) / 1_000_000
            assert result.targets == expected and result.area_ids == ("salon",), result.to_dict()
            assert result.risk == "low" and result.route == "fast" and not result.completed
            assert not engine.pending, "A preview must never arm background authorization"
            calls = [
                (call.domain, call.service, call.entity_ids, call.data, call.guard_entity_ids)
                for call in result.calls
            ]
            if expected_calls is None:
                expected_calls = calls
            assert calls == expected_calls
            if phase == "clean":
                assert result.status == "dry_run" and not result.continue_conversation
            else:
                assert result.status == "confirm" and result.reason == "background_confirmation"
                assert not result.continue_conversation, "Preview cannot open a spoken follow-up"
                assert result.response_data["background"]["kind"] == "light_prefix"
            if index >= warmup:
                samples[phase].append(elapsed)
    return {
        "case": name,
        "expected_target_count": len(expected),
        "phases": {
            phase: timings(values) | {"samples_ms": [round(value, 6) for value in values]}
            for phase, values in samples.items()
        },
    }


async def run(iterations, warmup):
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    rows = []
    for size in (100, 500, 2000):
        home = fictional_home(size)
        rows.append(
            {
                "inventory_entities": size,
                "source_room_lights": 8,
                "cases": [await measure(home, pair, iterations, warmup) for pair in PAIRS],
            }
        )
    sources = sorted((COMPONENT / "core").rglob("*.py"))
    sources.extend(
        [
            COMPONENT / "manifest.json",
            Path(__file__).resolve(),
            ROOT / "benchmark/reference_dialogue.py",
        ]
    )
    return {
        "benchmark": "d02_paired_warmed_offline_light_background",
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
            "iterations_per_phase_case_inventory": iterations,
            "excluded_warmup_pairs_per_case_inventory": warmup,
            "timed_boundary": "await Engine.process(request), perf_counter_ns wall clock",
            "pair_order": "Alternates clean-first and background-first",
            "excluded_setup": "Indexed HomeModel creation, request construction, dialogue reset",
            "all_requests_dry_run": True,
            "executor": None,
            "fallback": None,
            "assertions": (
                "Every sample: exact targets, area, calls, low risk, fast route, no pending"
            ),
        },
        "source_sha256": {
            str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "results": rows,
        "limits": [
            "Fictional indexed inventories; one source room has eight eligible lights.",
            "The background path prepares a question; this excludes spoken confirmation latency.",
            "No HA adapter, permissions, real devices, STT, TTS, network or audio separation.",
            "A deterministic microbenchmark does not measure household language accuracy.",
            "This machine is not the user's N5105; do not extrapolate these measurements.",
            "CPU isolation and other host workloads are not controlled; rerun to compare tails.",
            "Clean and background paths differ in work; this is not an old-version uplift claim.",
        ],
        "passed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100_000 or not 1 <= args.warmup <= 10_000:
        parser.error("Iterations and warmup must be positive and bounded")
    report = asyncio.run(run(args.iterations, args.warmup))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
