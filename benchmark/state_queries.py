"""Offline F1 query cost on cached synthetic homes; no HA, provider or audio access."""

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from time import perf_counter_ns

from custom_components.polish_context_voice.core.capabilities import (
    plan_capability,
    selected_entities,
)
from custom_components.polish_context_voice.core.contacts import contact_speech
from custom_components.polish_context_voice.core.home_model import Area, Entity, Floor, HomeModel
from custom_components.polish_context_voice.core.nlu.capabilities import parse_capability
from custom_components.polish_context_voice.core.state_queries import light_state_speech
from custom_components.polish_context_voice.core.types import Request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "core/nlu/capabilities.py",
    "core/capabilities.py",
    "core/state_queries.py",
    "core/contacts.py",
    "core/home_model/__init__.py",
)


def distribution(samples):
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50_ms": round(ordered[ceil(len(ordered) * 0.5) - 1], 6),
        "p95_ms": round(ordered[ceil(len(ordered) * 0.95) - 1], 6),
        "max_ms": round(ordered[-1], 6),
    }


def home(size):
    return HomeModel(
        areas=(
            Area("living", "Salon", ("salonie",), "ground"),
            Area("kitchen", "Kuchnia", ("kuchni",), "ground"),
            Area("bedroom", "Sypialnia", ("sypialni",), "upper"),
        ),
        floors=(Floor("ground", "Parter", ("parterze",)), Floor("upper", "Piętro")),
        entities=(
            Entity("light.local", "Lokalna", "living", exposed=True, state="off"),
            Entity("light.remote", "Kuchenna", "kitchen", exposed=True, state="on"),
            Entity("light.upstairs", "Sypialnia", "bedroom", exposed=True, state="off"),
            Entity("light.unassigned", "Bez pokoju", None, exposed=True, state="off"),
            Entity("light.hidden", "Ukryta", "living", exposed=False, state="on"),
            Entity(
                "binary_sensor.window",
                "Okno",
                "kitchen",
                exposed=True,
                state="off",
                device_class="window",
            ),
            *(
                Entity(
                    f"sensor.synthetic_{i}",
                    "Pomiar",
                    "living",
                    exposed=True,
                    state="21",
                    device_class="temperature",
                )
                for i in range(size - 6)
            ),
        ),
    )


CASES = (
    ("room_all_off", "Czy wszystkie światła są wyłączone?", {"light.local"}),
    ("explicit_area", "Czy w kuchni świeci się światło?", {"light.remote"}),
    (
        "floor",
        "Czy wszystkie światła są wyłączone na parterze?",
        {"light.local", "light.remote"},
    ),
    (
        "house",
        "Czy gdzieś świeci się światło?",
        {"light.local", "light.remote", "light.upstairs", "light.unassigned"},
    ),
    (
        "house_which",
        "Które światła są włączone w całym domu?",
        {"light.local", "light.remote", "light.upstairs", "light.unassigned"},
    ),
    (
        "house_contacts",
        "Czy wszystkie okna są zamknięte w całym domu?",
        {"binary_sensor.window"},
    ),
)


def run(iterations):
    component = ROOT / "custom_components/polish_context_voice"
    report = {
        "schema_version": 1,
        "component_version": json.loads((component / "manifest.json").read_text())["version"],
        "created_utc": datetime.now(UTC).isoformat(),
        "host": {
            "system": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "iterations_per_case_per_inventory": iterations,
        "source_sha256": {
            name: hashlib.sha256((component / name).read_bytes()).hexdigest() for name in SOURCES
        },
        "scope": "NLU + plan selection on a prebuilt HomeModel + bounded speech only.",
        "cases": [],
        "limitations": [
            "Mac ARM measurements, not N5105 or comparative speedup claims.",
            "Synthetic mixed inventories: five lights and one contact; other entries are sensors.",
            "Cached model build, HA visibility views, actual permissions/preflight, routing, "
            "registry updates, event-loop load, providers, STT, TTS and network are not measured.",
            "No LLM, HA service or physical-device access.",
        ],
    }
    for size in (100, 500, 2000):
        model = home(size)
        for identifier, text, expected in CASES:
            request = Request(text, source_area="living")
            samples = {name: [] for name in ("nlu", "selection", "speech", "total")}
            for _ in range(10):
                parse_capability(text)
            for _ in range(iterations):
                start = perf_counter_ns()
                cap = parse_capability(text)
                parsed = perf_counter_ns()
                result = plan_capability(cap, request, model)
                entities = selected_entities(result, model)
                planned = perf_counter_ns()
                speech = (
                    light_state_speech(cap.query, entities)
                    if cap.domain == "light"
                    else contact_speech(cap.kind, cap.query, entities)
                )
                finished = perf_counter_ns()
                assert result.status == "ready" and set(result.targets) == expected
                assert not result.calls and not result.completed and speech
                if identifier == "room_all_off":
                    assert speech == "Tak, sprawdzone światła są wyłączone."
                for name, duration in (
                    ("nlu", parsed - start),
                    ("selection", planned - parsed),
                    ("speech", finished - planned),
                    ("total", finished - start),
                ):
                    samples[name].append(duration / 1_000_000)
            report["cases"].append(
                {
                    "id": identifier,
                    "inventory_entities": len(model.entities),
                    "correct": True,
                    "latency": {name: distribution(values) for name, values in samples.items()},
                }
            )
    report["correct"] = all(row["correct"] for row in report["cases"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 10_000:
        parser.error("iterations must be between 1 and 10000")
    report = run(args.iterations)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "correct": report["correct"],
                "cases": len(report["cases"]),
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
