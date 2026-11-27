"""Offline content-routing cost; no HA, catalogs, network, audio or N5105 claims."""

import argparse
import json
import platform
from math import ceil
from pathlib import Path
from time import perf_counter_ns

from custom_components.polish_context_voice.core.media_search import parse_media_search
from custom_components.polish_context_voice.core.music import parse_music
from custom_components.polish_context_voice.core.stoic import parse_stoic


def route(text, settings):
    stoic = parse_stoic(text)
    music = parse_music(text, settings)
    media = None if music and music.operation == "start" else parse_media_search(text, settings)
    return "stoic" if stoic else "media_search" if media else "existing"


def run(iterations):
    cases = (
        ("ordinary_light", "Włącz światła", "existing"),
        ("ordinary_weather", "Jaka jest pogoda?", "existing"),
        ("stoic", "Przeczytaj dzisiejszą medytację", "stoic"),
        ("track", "Puść utwór Gwiezdna droga", "media_search"),
        ("audiobook", "Puść audiobook Cicha wyspa", "media_search"),
    )
    rows = []
    for count in (0, 64):
        settings = {
            "music_shortcuts": {
                str(i): {"name": f"Ulubiona playlista numer {i}", "aliases": [f"Zestaw numer {i}"]}
                for i in range(count)
            }
        }
        for name, text, expected in cases:
            for _ in range(20):
                assert route(text, settings) == expected
            samples = []
            for _ in range(iterations):
                started = perf_counter_ns()
                assert route(text, settings) == expected
                samples.append((perf_counter_ns() - started) / 1_000_000)
            samples.sort()
            rows.append(
                {
                    "case": name,
                    "configured_shortcuts": count,
                    "samples": iterations,
                    "correct": iterations,
                    "p50_ms": samples[ceil(iterations * 0.5) - 1],
                    "p95_ms": samples[ceil(iterations * 0.95) - 1],
                    "max_ms": samples[-1],
                }
            )
    return {
        "component_version": json.loads(
            Path("custom_components/polish_context_voice/manifest.json").read_text()
        )["version"],
        "host": platform.platform(),
        "python": platform.python_version(),
        "scope": "Stoic/music/media routing parsers only; original titles; synthetic shortcuts",
        "not_measured": ["HA", "MA", "providers", "network", "audio", "N5105", "speedup"],
        "combinations": len(rows),
        "samples": sum(row["samples"] for row in rows),
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.iterations < 1:
        parser.error("iterations must be positive")
    report = run(arguments.iterations)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"combinations": report["combinations"], "samples": report["samples"]}))
