"""Offline music grammar/target correctness; no HA services, MA, audio or network."""

import argparse
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter

from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.music import parse_music, plan_music
from custom_components.polish_context_voice.core.types import Request

from .run import percentile

URI = "library://playlist/42"
SETTINGS = {
    "music_shortcuts": {
        "favorite": {
            "name": "Moja playlista YouTube",
            "aliases": ["moja muzyka YouTube", "ulubione nagrania", "YouTube na wieczór"],
            "backend": "music_assistant",
            "config_entry_id": "synthetic_ma",
            "media": {"media_content_id": URI, "media_content_type": "playlist"},
        }
    },
    "music_rooms": {
        "a17": {"player": "media_player.columns", "default_shortcut": "favorite"},
        "b93": {"player": "media_player.garden", "default_shortcut": "favorite"},
    },
}
MEDIA = {"media_content_id": URI, "media_content_type": "playlist"}
# Independent expected targets and exact calls. A synthetic title does not validate YouTube.
CASES = [
    ("Odpal moją playlistę YouTube", "a17", "ready", "media_player.columns", "play_media", MEDIA),
    ("Puść moją muzykę YouTube", "a17", "ready", "media_player.columns", "play_media", MEDIA),
    ("Włącz playlistę YouTube", "a17", "ready", "media_player.columns", "play_media", MEDIA),
    ("Odtwórz YouTube na wieczór", "a17", "ready", "media_player.columns", "play_media", MEDIA),
    ("Puść ulubione nagrania", "a17", "ready", "media_player.columns", "play_media", MEDIA),
    ("Puść muzykę", "b93", "ready", "media_player.garden", "play_media", MEDIA),
    ("Puść muzykę w ogrodzie", "a17", "ready", "media_player.garden", "play_media", MEDIA),
    ("Puść muzykę na poddaszu", "b93", "ready", "media_player.columns", "play_media", MEDIA),
    ("Zatrzymaj muzykę", "a17", "ready", "media_player.columns", "media_pause", {}),
    ("Wyłącz muzykę w ogrodzie", "a17", "ready", "media_player.garden", "media_stop", {}),
    ("Puść muzykę", None, "clarify", None, None, {}),
    ("Puść muzykę w piwnicy", "a17", "clarify", None, None, {}),
    ("Odpal moją playlistę YouTube i wyłącz światła", "a17", "clarify", None, None, {}),
    ("Puść muzykę wszędzie", "a17", "clarify", None, None, {}),
    ("Ustaw głośność na 110 procent", "a17", "blocked", None, None, {}),
    ("Nie odpal mojej playlisty YouTube", "a17", None, None, None, {}),
    ("Jaka będzie jutro pogoda", "a17", None, None, None, {}),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path("benchmark/music-latest.json"))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    model = HomeModel(
        areas=[Area("a17", "Poddasze"), Area("b93", "Ogród")],
        entities=[
            Entity(
                identifier,
                name,
                area,
                exposed=True,
                state="idle",
                attributes={"supported_features": 65535},
            )
            for identifier, name, area in (
                ("media_player.columns", "Kolumny", "a17"),
                ("media_player.garden", "Głośnik ogrodowy", "b93"),
            )
        ],
    )
    failures, times = [], []
    for iteration in range(args.iterations + 1):
        for text, area, status, target, service, data in CASES:
            started = perf_counter()
            command = parse_music(text, SETTINGS)
            result = (
                plan_music(command, Request(text, source_area=area), model, SETTINGS)
                if command
                else None
            )
            if iteration:
                times.append((perf_counter() - started) * 1000)
                continue
            if result is None:
                correct = status is None
            else:
                calls = [(c.domain, c.service, c.entity_ids, c.data) for c in result.calls]
                expected = [("media_player", service, (target,), data)] if service else []
                correct = (
                    result.status == status
                    and calls == expected
                    and result.targets == ((target,) if target else ())
                )
            if not correct:
                failures.append({"text": text, "actual": result.to_dict() if result else None})
    result = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "component_version": json.loads(
            Path("custom_components/polish_context_voice/manifest.json").read_text()
        )["version"],
        "scope": (
            "Warm music parser and exact plan; synthetic unrelated house, no HA/MA/audio/network"
        ),
        "cases": len(CASES),
        "correct": len(CASES) - len(failures),
        "samples": len(times),
        "latency_ms": {
            "p50": median(times),
            "p95": percentile(times, 0.95),
            "p99": percentile(times, 0.99),
        },
        "failures": failures,
        "caveat": "Curated regression cases, not real-house accuracy or N5105 performance.",
    }
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
