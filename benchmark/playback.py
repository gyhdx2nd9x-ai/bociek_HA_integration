"""Live music context benchmark on a synthetic 950-entity home, without HA/audio/network."""

import argparse
import json
import platform
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter

from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.music import parse_music, plan_music
from custom_components.polish_context_voice.core.types import Request

from .music import percentile


def run(iterations):
    cases, samples = [], []
    for name, text, state, grouped, ma, source, expected in (
        ("next", "następny", "playing", False, False, "living", "media_next_track"),
        ("next_feminine", "następna", "playing", False, False, "living", "media_next_track"),
        ("continue_playing", "dalej", "playing", False, False, "living", "media_next_track"),
        ("skip", "pomiń", "playing", False, False, "living", "media_next_track"),
        ("continue_paused", "dalej", "paused", False, False, "living", "media_play"),
        ("ha_group", "następna", "playing", True, False, "kitchen", "media_next_track"),
        ("ma_queue", "następny", "playing", True, True, "kitchen", "media_next_track"),
        ("independent_rooms", "następny", "playing", False, False, "kitchen", "media_next_track"),
        ("idle", "dalej", "idle", False, False, "living", None),
    ):
        identifiers = ("media_player.living", "media_player.kitchen")
        players = [
            Entity(
                identifier,
                area,
                area,
                exposed=True,
                state=state,
                device_class="speaker",
                platform="music_assistant" if ma else "test",
                config_entry_id="instance",
                unique_id="queue" if area == "living" else "child",
                attributes={
                    "supported_features": 65535,
                    "media_content_type": "music",
                    "media_content_id": "library://same-track",
                },
            )
            for identifier, area in zip(identifiers, ("living", "kitchen"), strict=True)
        ]
        if grouped:
            players = [
                replace(
                    p,
                    attributes={
                        **p.attributes,
                        "group_members": identifiers,
                        **({"active_queue": "queue"} if ma else {}),
                    },
                )
                for p in players
            ]
        model = HomeModel(
            areas=[Area("living", "Salon"), Area("kitchen", "Kuchnia")],
            entities=players
            + [Entity(f"sensor.synthetic_{i}", f"Sensor {i}", "living") for i in range(948)],
        )
        settings = {
            "music_rooms": {
                area: {"player": p.id}
                for area, p in zip(("living", "kitchen"), players, strict=True)
            }
        }
        request = Request(text, source_area=source, dry_run=True)
        times = []
        for iteration in range(iterations + 1):
            started = perf_counter()
            result = plan_music(parse_music(text, settings), request, model, settings)
            if iteration:
                times.append((perf_counter() - started) * 1000)
        target = identifiers[0] if grouped else f"media_player.{source}"
        correct = (
            (
                len(result.calls) == 1
                and result.calls[0].service == expected
                and result.calls[0].entity_ids == (target,)
                and len(result.targets) == (2 if grouped else 1)
            )
            if expected
            else result.reason == "music_not_playing" and not result.calls
        )
        cases.append({"case": name, "passed": bool(correct), "p95_ms": percentile(times, 0.95)})
        samples.extend(times)
    return {
        "component_version": json.loads(
            Path("custom_components/polish_context_voice/manifest.json").read_text()
        )["version"],
        "created_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "scope": (
            "Warm parser and live-context planner; synthetic 950 entities; no HA/MA/audio/network"
        ),
        "passed": all(c["passed"] for c in cases),
        "cases": cases,
        "samples": len(samples),
        "latency_ms": {"p50": median(samples), "p95": percentile(samples, 0.95)},
        "limits": "Not a measurement of N5105 or end-to-end speech response latency",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    result = run(args.iterations)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
