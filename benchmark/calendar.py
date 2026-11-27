"""Offline Polish calendar parsing and dates; no HA, provider, Kiosk, STT or TTS."""

import argparse
import json
import platform
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from custom_components.polish_context_voice.core.calendar import moment, parse_calendar, period
from custom_components.polish_context_voice.core.context.dialogue import is_confirmation_reply
from custom_components.polish_context_voice.core.spoken_time import spoken_time

from .run import percentile

CASES = [
    ("co jest w moim kalendarzu", "find"),
    ("jakie mam dzisiaj plany", "find"),
    ("jaki jest plan na dziś", "find"),
    ("co jest w planie na dziś", "find"),
    ("jakie mam jutro plany", "find"),
    ("jaki jest plan na jutro", "find"),
    ("co jest w moim kalendarzu jutro", "find"),
    ("co mam zaplanowane na jutro", "find"),
    ("jakie mam jutro plany w kalendarzu Rodzina", "find"),
    ("co mamy jutro w kalendarzu", "find"),
    ("co mamy w przyszłym tygodniu", "find"),
    ("kiedy jest dentysta", "find"),
    ("dodaj Dentystę na jutro o piętnastej, na godzinę", "add"),
    ("dodaj Badanie 2026-11-12 o 15:30 na dwie godziny", "add"),
    ("dodaj Wyjazd jutro cały dzień", "add"),
    ("przełóż jutrzejszego dentystę na szesnastą", "change"),
    ("odwołaj trening w czwartek", "cancel"),
    ("odwołaj jutro badanie", "cancel"),
    ("dodaj jutro Badanie o piętnastej", "add"),
    ("przełóż jutro Badanie na piątek o 16", "change"),
    ("pokaż kalendarz na przyszły tydzień", "show"),
    ("włącz światła w kuchni", None),
    ("graj muzykę", None),
    ("dodaj mleko do zakupów", None),
]

SPEECH_CASES = [
    (5, 15, "conversational", "kwadrans po piątej rano"),
    (15, 0, "exact", "o piętnastej"),
    (15, 30, "exact", "o piętnastej trzydzieści"),
    (15, 30, "conversational", "wpół do czwartej po południu"),
    (12, 45, "conversational", "za piętnaście pierwsza po południu"),
    (14, 15, "conversational", "kwadrans po drugiej po południu"),
    (23, 45, "conversational", "za piętnaście północ"),
]
CONFIRMATION_CASES = [
    ("Tak. Tak", True),
    ("tak tak tak", True),
    ("Potwierdzam", True),
    ("Так", False),
    ("tak, nie", False),
    ("tak ale nie dodawaj", False),
]


def speech_benchmark(iterations, now):
    """Measure a bounded set of pure speech/consent operations independently."""
    cases = [
        (
            lambda h=h, m=m, style=style: spoken_time(now.replace(hour=h, minute=m), style=style),
            expected,
        )
        for h, m, style, expected in SPEECH_CASES
    ] + [
        (lambda text=text: is_confirmation_reply(text), expected)
        for text, expected in CONFIRMATION_CASES
    ]
    failures = [
        {"case": i, "expected": expected, "actual": call()}
        for i, (call, expected) in enumerate(cases)
        if call() != expected
    ]
    samples = []
    for _ in range(iterations):
        for call, _ in cases:
            started = perf_counter()
            call()
            samples.append((perf_counter() - started) * 1000)
    return {
        "cases": len(cases),
        "correct": len(cases) - len(failures),
        "failures": failures,
        "samples": len(samples),
        "latency_ms": {"p50": percentile(samples, 0.5), "p95": percentile(samples, 0.95)},
    }


def run(iterations=200):
    now = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo("Europe/London"))

    def parse(text):
        command = parse_calendar(text)
        if command and command.when:
            period(command.when, now)
            moment(command.when, now)
        return command.operation if command else None

    failures = [
        {"text": text, "expected": expected, "actual": parse(text)}
        for text, expected in CASES
        if parse(text) != expected
    ]
    samples = []
    for _ in range(iterations):
        for text, _ in CASES:
            started = perf_counter()
            parse(text)
            samples.append((perf_counter() - started) * 1000)
    return {
        "component_version": json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "custom_components/polish_context_voice/manifest.json"
            ).read_text()
        )["version"],
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cases": len(CASES),
        "correct": len(CASES) - len(failures),
        "failures": failures,
        "samples": len(samples),
        "latency_ms": {"p50": percentile(samples, 0.5), "p95": percentile(samples, 0.95)},
        "speech_and_confirmation": speech_benchmark(iterations, now),
        "scope": "Offline warm parser and local Polish dates only; no HA/provider/Kiosk, "
        "STT/TTS or physical N5105. Small curated regression set, not field accuracy.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.iterations)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failures"] or report["speech_and_confirmation"]["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
