"""Shared Polish presentation helpers; never change source values or action plans."""

import math


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rounded(value):
    """Nearest integer, halves away from zero."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def temperature_speech(value, unit):
    if not number(value):
        return ""
    whole = rounded(value)
    if unit in {"°C", "°F"}:
        degrees = "stopnia" if abs(whole) == 1 else "stopni"
        scale = " Fahrenheita" if unit == "°F" else ""
        return f"około {whole} {degrees}{scale}"
    return f"około {whole} {unit}".strip()


def around_time(at, now):
    """No redundant :00; retain minutes and distinguish tomorrow in HA local time."""
    clock = str(at.hour) if at.minute == 0 else f"{at.hour}:{at.minute:02d}"
    if at.hour == 0 and at.minute == 0:
        clock = "północy"
    day = (at.date() - now.date()).days
    prefix = "jutro " if day == 1 else f"{at:%d.%m} " if day != 0 else ""
    return f"{prefix}około {clock}"


_ONES = ("zero", "jeden", "dwie", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć")
_TEENS = (
    "dziesięć",
    "jedenaście",
    "dwanaście",
    "trzynaście",
    "czternaście",
    "piętnaście",
    "szesnaście",
    "siedemnaście",
    "osiemnaście",
    "dziewiętnaście",
)
_TENS = (
    "",
    "",
    "dwadzieścia",
    "trzydzieści",
    "czterdzieści",
    "pięćdziesiąt",
    "sześćdziesiąt",
    "siedemdziesiąt",
    "osiemdziesiąt",
    "dziewięćdziesiąt",
)
_HUNDREDS = (
    "",
    "sto",
    "dwieście",
    "trzysta",
    "czterysta",
    "pięćset",
    "sześćset",
    "siedemset",
    "osiemset",
    "dziewięćset",
)


def duration_number(value):
    """Feminine cardinal for timer units, bounded without language dependencies."""
    if not 0 <= value < 1000:
        return str(value)
    if value == 1:
        return "jedną"
    if value < 10:
        return _ONES[value]
    parts = []
    hundreds, tail = divmod(value, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if 10 <= tail < 20:
        parts.append(_TEENS[tail - 10])
    else:
        tens, ones = divmod(tail, 10)
        if tens:
            parts.append(_TENS[tens])
        if ones:
            parts.append(_ONES[ones])
    return " ".join(parts)
