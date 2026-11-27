"""Small exact Polish cardinal grammar for numeric slots, never entity names."""

import re

UNITS = dict(
    zip(
        "zero jeden dwa trzy cztery piec szesc siedem osiem dziewiec".split(),
        range(10),
        strict=True,
    )
)
TEENS = dict(
    zip(
        "dziesiec jedenascie dwanascie trzynascie czternascie pietnascie szesnascie "
        "siedemnascie osiemnascie dziewietnascie".split(),
        range(10, 20),
        strict=True,
    )
)
TENS = dict(
    zip(
        "dwadziescia trzydziesci czterdziesci piecdziesiat szescdziesiat siedemdziesiat "
        "osiemdziesiat dziewiecdziesiat".split(),
        range(20, 100, 10),
        strict=True,
    )
)
HUNDREDS = dict(
    zip(
        "sto dwiescie trzysta czterysta piecset szescset siedemset osiemset dziewiecset".split(),
        range(100, 1000, 100),
        strict=True,
    )
)


def number(value: str) -> float | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    words = value.split()
    total = HUNDREDS[words.pop(0)] if words and words[0] in HUNDREDS else 0
    if not words:
        return float(total) if total else None
    if len(words) == 1 and words[0] in UNITS | TEENS | TENS:
        return float(total + (UNITS | TEENS | TENS)[words[0]])
    if len(words) == 2 and words[0] in TENS and words[1] in UNITS and UNITS[words[1]]:
        return float(total + TENS[words[0]] + UNITS[words[1]])
    return None
