"""Paired P6 response timings against pinned b7 engine source, offline only.

Run ``python -m benchmark.response_facts --output <report.json>``. The old
engine is loaded under a private module name from this repository's Git object;
both engines use the same current dependency modules. A synthetic executor
returns immediately or raises deliberately. This measures presentation overhead,
not HA services, audio, household correctness or N5105 performance.
"""

import argparse
import ast
import asyncio
import gc
import json
import math
import platform
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter_ns
from types import ModuleType

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.response_facts import capability_speech
from custom_components.polish_context_voice.core.types import Request

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/polish_context_voice"
BASELINE_COMMIT = "51bff025ba4e7d43e65a3f2f425cdd6b77a29a70"
ENGINE_PATH = "custom_components/polish_context_voice/core/engine.py"


@dataclass(frozen=True)
class Case:
    id: str
    text: str
    status: str
    speech: str
    state: str = "21.5"
    unit: str = "°C"
    temperature_present: bool = True
    fail: bool = False
    dry_run: bool = False
    reads: int = 2
    preflights: int = 1
    calls: int = 1


CASES = (
    *(
        Case(
            f"brightness_{value}",
            f"ustaw światła na {value}%",
            "executed",
            f"OK, jasność na {value} procent.",
        )
        for value in (0, 35, 100)
    ),
    Case(
        "temperature_celsius",
        "jaka jest temperatura",
        "answered",
        "Jest około 22 stopni.",
        reads=1,
        preflights=0,
        calls=0,
    ),
    Case(
        "temperature_fahrenheit",
        "jaka jest temperatura",
        "answered",
        "Jest około 77 stopni Fahrenheita.",
        state="77.2",
        unit="°F",
        reads=1,
        preflights=0,
        calls=0,
    ),
    Case(
        "temperature_kelvin",
        "jaka jest temperatura",
        "answered",
        "Jest około 294 kelwinów.",
        state="294.4",
        unit="K",
        reads=1,
        preflights=0,
        calls=0,
    ),
    Case("unmigrated_on_control", "włącz światła", "executed", "Gotowe."),
    Case(
        "brightness_failed_executor",
        "ustaw światła na 35%",
        "error",
        "Nie udało się wykonać polecenia.",
        fail=True,
    ),
    Case(
        "brightness_preview",
        "ustaw światła na 35%",
        "dry_run",
        "Plan gotowy; urządzenia nie zostały zmienione.",
        dry_run=True,
        preflights=0,
        calls=0,
    ),
    Case(
        "temperature_missing",
        "jaka jest temperatura",
        "clarify",
        "Nie mogę teraz sprawdzić temperatury.",
        temperature_present=False,
        reads=1,
        preflights=0,
        calls=0,
    ),
    Case(
        "temperature_invalid_reading",
        "jaka jest temperatura",
        "clarify",
        "Nie mam teraz prawidłowego odczytu temperatury.",
        state="nan",
        reads=1,
        preflights=0,
        calls=0,
    ),
)


class Provider:
    def __init__(self, home):
        self.home = home
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.home


class Executor:
    """No HA or I/O: preserve the engine's normal awaiting and failure boundary."""

    def __init__(self, fail=False):
        self.fail = fail
        self.preflights = self.calls = 0

    async def preflight(self, calls, request):
        self.preflights += 1

    async def execute(self, call, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Synthetic benchmark provider failure")


def home_for(case):
    return HomeModel(
        areas=[Area("test_room", "Pokój testowy")],
        entities=[
            entity
            for entity in (
                Entity(
                    "light.synthetic",
                    "Światło testowe",
                    "test_room",
                    exposed=True,
                    labels=frozenset({"voice_default_on", "voice_all_off"}),
                    state="off",
                    attributes={"supported_color_modes": ["brightness"]},
                ),
                Entity(
                    "sensor.synthetic",
                    "Temperatura testowa",
                    "test_room",
                    exposed=True,
                    device_class="temperature",
                    state=case.state,
                    attributes={"unit_of_measurement": case.unit},
                ),
            )
            if case.temperature_present or entity.domain != "sensor"
        ],
    )


def git_output(*arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def load_baseline():
    source = git_output("show", f"{BASELINE_COMMIT}:{ENGINE_PATH}")
    name = "custom_components.polish_context_voice.core._p6_b7_benchmark_engine"
    module = ModuleType(name)
    module.__package__ = "custom_components.polish_context_voice.core"
    module.__file__ = f"{BASELINE_COMMIT}:{ENGINE_PATH}"
    sys.modules[name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module, source


def engine_method_hashes(source):
    engine = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name == "Engine"
    )
    return {
        node.name: sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        for node in engine.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "process"
    }


def distribution(samples):
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "p50_ms": round(statistics.median(samples), 6),
        "p95_ms": round(ordered[math.ceil(len(samples) * 0.95) - 1], 6),
        "min_ms": round(ordered[0], 6),
        "max_ms": round(ordered[-1], 6),
    }


def paired_statistics(samples):
    return {name: distribution(values) for name, values in samples.items()} | {
        "paired_extra_current_minus_b7": distribution(
            [
                current - baseline
                for baseline, current in zip(samples["b7"], samples["current"], strict=True)
            ]
        )
    }


def action_view(result):
    fields = result.to_dict()
    # These are the only fields intentionally changed or non-deterministic.
    fields.pop("speech")
    fields.pop("latency_ms")
    return fields


def source_hashes():
    paths = [
        *sorted((COMPONENT / "core").rglob("*.py")),
        COMPONENT / "manifest.json",
        Path(__file__).resolve(),
    ]
    return {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in paths}


async def compare_engine(case, old_engine, iterations, warmup):
    home = home_for(case)
    providers = {name: Provider(home) for name in ("b7", "current")}
    executors = {name: Executor(case.fail) for name in providers}
    engines = {
        "b7": old_engine(providers["b7"], executors["b7"]),
        "current": Engine(providers["current"], executors["current"]),
    }
    request = Request(
        case.text,
        source_area="test_room",
        conversation_id="p6-synthetic-benchmark",
        user_id="synthetic-user",
        dry_run=case.dry_run,
    )
    samples = {name: [] for name in providers}
    reference = example = None
    for index in range(warmup + iterations):
        order = ("b7", "current") if index % 2 == 0 else ("current", "b7")
        pair = {}
        for name in order:
            engine, provider, executor = engines[name], providers[name], executors[name]
            engine.pending.clear()
            engine.dialogue.clear()
            provider.reads = executor.preflights = executor.calls = 0
            started = perf_counter_ns()
            result = await engine.process(request)
            elapsed = (perf_counter_ns() - started) / 1_000_000
            assert result.status == case.status, result.to_dict()
            assert (provider.reads, executor.preflights, executor.calls) == (
                case.reads,
                case.preflights,
                case.calls,
            ), (case.id, name, provider.reads, executor.preflights, executor.calls)
            assert not engine.pending
            if reference is None:
                reference = action_view(result)
            assert action_view(result) == reference, (case.id, name)
            if name == "current":
                assert result.speech == case.speech, (case.id, result.speech)
            pair[name] = result
            if index >= warmup:
                samples[name].append(elapsed)
        assert action_view(pair["b7"]) == action_view(pair["current"])
        example = pair["b7"]
    return {
        "all_non_speech_result_fields_identical": True,
        "model_reads_per_request": case.reads,
        "synthetic_preflights_per_request": case.preflights,
        "synthetic_service_attempts_per_request": case.calls,
        **paired_statistics(samples),
    }, example


def preserve_speech(result):
    return result.speech


def render_speech(result):
    return capability_speech(result) or result.speech


def compare_renderer(case, result, iterations, warmup):
    samples = {"b7": [], "current": []}
    functions = {"b7": preserve_speech, "current": render_speech}
    original = result.to_dict()
    for index in range(warmup + iterations):
        order = ("b7", "current") if index % 2 == 0 else ("current", "b7")
        for name in order:
            started = perf_counter_ns()
            text = functions[name](result)
            elapsed = (perf_counter_ns() - started) / 1_000_000
            assert text == (result.speech if name == "b7" else case.speech)
            assert result.to_dict() == original, "Renderer must not mutate the input result"
            if index >= warmup:
                samples[name].append(elapsed)
    return {"input_result_unchanged": True, **paired_statistics(samples)}


async def run(iterations, warmup):
    hashes = source_hashes()
    baseline, old_source = load_baseline()
    try:
        unchanged = engine_method_hashes(old_source)
        assert unchanged == engine_method_hashes((ROOT / ENGINE_PATH).read_text()), (
            "This benchmark isolates Engine.process presentation changes; "
            "other Engine methods changed"
        )
        rows = []
        # Deliberate failing executor cases otherwise log thousands of stack traces.
        # Suppress this module's warnings only while measuring; status/reason,
        # completions and attempted-call counts are asserted for every request.
        loggers = (baseline.LOGGER, __import__(Engine.__module__, fromlist=["LOGGER"]).LOGGER)
        previous_disabled = [logger.disabled for logger in loggers]
        try:
            for logger in loggers:
                logger.disabled = True
            for case in CASES:
                measured, example = await compare_engine(case, baseline.Engine, iterations, warmup)
                rows.append(
                    {
                        "case": case.id,
                        "engine_process": measured,
                        "renderer_only": compare_renderer(case, example, iterations, warmup),
                    }
                )
        finally:
            for logger, disabled in zip(loggers, previous_disabled, strict=True):
                logger.disabled = disabled
        assert hashes == source_hashes(), "Sources changed during measurement; rerun"
        return {
            "benchmark": "p6_b7_vs_response_facts_paired_warmed_offline",
            "schema_version": 1,
            "component_version": json.loads((COMPONENT / "manifest.json").read_text())["version"],
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "baseline": {
                "commit": BASELINE_COMMIT,
                "source_sha256": {ENGINE_PATH: sha256(old_source.encode()).hexdigest()},
                "loading": "Pinned local Git engine module under private name, no checkout",
                "dependencies": "Both engines import identical current core dependencies",
                "unchanged_engine_methods_except_process": unchanged,
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
                "case_count": len(CASES),
                "iterations_per_case_and_boundary": iterations,
                "excluded_warmup_pairs_per_case_and_boundary": warmup,
                "boundaries": ["await Engine.process(request)", "speech selection only"],
                "renderer_baseline": "Return existing result.speech",
                "renderer_current": "capability_speech(result) or existing result.speech",
                "timing": "perf_counter_ns wall clock, milliseconds",
                "pair_order": "Alternates b7/current and current/b7 in each pair",
                "median": "statistics.median",
                "tail_percentiles": "nearest rank",
                "paired_extra": (
                    "Current minus b7 for each interleaved pair; may be negative from noise"
                ),
                "excluded": (
                    "Imports, Git reads, home construction, request/reset setup, "
                    "assertions and serialization"
                ),
                "executor": (
                    "Synchronous-returning async synthetic executor; one deliberate failure case"
                ),
                "correctness": (
                    "Every non-speech result field, plan, model lookup and executor count "
                    "checked per sample"
                ),
                "logging": (
                    "Only engine module warnings disabled during timed runs of deliberate failures"
                ),
            },
            "source_sha256": hashes,
            "results": rows,
            "limits": [
                "Local warmed microbenchmark; this host is not the user's N5105.",
                "Two prebuilt fictional entities in one indexed room, "
                "no registry rebuild or cold start.",
                "Both engines share current dependencies; this isolates the Engine.process "
                "response step, not a full-version deployment comparison.",
                "No HA adapter, permissions, network, physical devices, STT, TTS "
                "or end-to-end voice timing.",
                "The synthetic executor has no provider delay and does not prove physical effects.",
                "No concurrent users or controlled host CPU isolation; tail and paired deltas "
                "include scheduler noise.",
                "Renderer-only timing excludes Presentation variation, Assist quiet-hours policy "
                "and transport serialization.",
                "Passing these cases establishes bounded implementation invariants, "
                "not household language accuracy or a latency guarantee.",
            ],
            "passed": True,
        }
    finally:
        sys.modules.pop(baseline.__name__, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10_000 or not 1 <= args.warmup <= 1_000:
        parser.error("Iterations and warmup must be positive and bounded")
    report = asyncio.run(run(args.iterations, args.warmup))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "cases": len(report["results"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
