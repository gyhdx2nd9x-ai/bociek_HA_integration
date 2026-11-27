"""Offline correctness and latency; does not include HA/STT/TTS/network latency."""

import argparse
import asyncio
import json
import math
import platform
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.types import Request


def request_for(case):
    return Request(
        case["text"],
        source_area=case.get("area"),
        satellite_id=case.get("satellite"),
        device_id=case.get("device"),
        dry_run=True,
    )


def correct(case, result):
    if result.status != case["status"] or set(result.targets) != set(case["targets"]):
        return False
    expected_service = case.get("service")
    if expected_service:
        if not result.calls or any(
            c.service != expected_service or c.data != case.get("data", {}) for c in result.calls
        ):
            return False
        if set(e for c in result.calls for e in c.entity_ids) != set(case["targets"]):
            return False
    elif result.calls:
        return False
    return case.get("speech_contains", "") in result.speech


def percentile(values, proportion):
    return sorted(values)[max(0, math.ceil(len(values) * proportion) - 1)]


async def benchmark(
    iterations=200,
    household=Path("examples/household.json"),
    cases_path=Path("benchmark/cases.json"),
    inventory=None,
):
    data = json.loads(household.read_text())
    if inventory:
        # Size test only: real IDs retain unknown metadata, disabled and unexposed.
        reference = json.loads(inventory.read_text())
        known = {e["id"] for e in data["entities"]}
        data["entities"].extend(e for e in reference["entities"] if e["id"] not in known)
    cases = json.loads(cases_path.read_text())
    home = HomeModel.from_dict(data)
    engine = Engine(lambda: home)
    results = [(case, await engine.process(request_for(case))) for case in cases]
    # Cache warm-up excluded from measured samples.
    for case in cases:
        await engine.process(request_for(case))
    elapsed = []
    for _ in range(iterations):
        for case in cases:
            started = perf_counter()
            await engine.process(request_for(case))
            elapsed.append((perf_counter() - started) * 1000)
    failures = [
        dict(id=c["id"], expected=c, actual=r.to_dict()) for c, r in results if not correct(c, r)
    ]
    actionable = [(c, r) for c, r in results if c.get("service")]
    negative = [(c, r) for c, r in results if not c.get("service")]
    wrong = sum(
        bool(set(e for call in r.calls for e in call.entity_ids) - set(c["targets"]))
        for c, r in results
    )
    rebuild = []
    for _ in range(20):
        started = perf_counter()
        HomeModel.from_dict(data)
        rebuild.append((perf_counter() - started) * 1000)
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "household": str(household),
        "inventory_for_size_only": str(inventory) if inventory else None,
        "entity_count": len(home.entities),
        "scope": (
            "Offline warm NLU + resolver + policy + dry-run orchestration. "
            "No HA, STT, TTS, LLM or network."
        ),
        "cases": len(cases),
        "samples": len(elapsed),
        "correct": len(cases) - len(failures),
        "correctness": (len(cases) - len(failures)) / len(cases),
        "wrong_target_plans": wrong,
        "false_positive_plans": sum(bool(r.calls) for _, r in negative),
        "false_negative_plans": sum(not r.calls for _, r in actionable),
        "unnecessary_clarifications": sum(r.status == "clarify" for _, r in actionable),
        "latency_ms": {
            "p50": statistics.median(elapsed),
            "p95": percentile(elapsed, 0.95),
            "p99": percentile(elapsed, 0.99),
        },
        "model_rebuild_ms_p95": percentile(rebuild, 0.95),
        "failures": failures,
        "caveat": (
            "Curated synthetic regression corpus; not evidence of 99% household accuracy "
            "or measured physical-device execution."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path("benchmark/latest.json"))
    parser.add_argument("--household", type=Path, default=Path("examples/household.json"))
    parser.add_argument("--cases", type=Path, default=Path("benchmark/cases.json"))
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    result = asyncio.run(benchmark(args.iterations, args.household, args.cases, args.inventory))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(0 if not result["failures"] else 1)


if __name__ == "__main__":
    main()
