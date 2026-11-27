"""Polish clock phrases for speech, without changing dates or action parameters."""

from datetime import datetime, time
from typing import Literal

_HOURS_AFTER_O = (
    "północy",
    "pierwszej",
    "drugiej",
    "trzeciej",
    "czwartej",
    "piątej",
    "szóstej",
    "siódmej",
    "ósmej",
    "dziewiątej",
    "dziesiątej",
    "jedenastej",
    "dwunastej",
    "trzynastej",
    "czternastej",
    "piętnastej",
    "szesnastej",
    "siedemnastej",
    "osiemnastej",
    "dziewiętnastej",
    "dwudziestej",
    "dwudziestej pierwszej",
    "dwudziestej drugiej",
    "dwudziestej trzeciej",
)
_HOURS_NOMINATIVE = (
    "dwunasta",
    "pierwsza",
    "druga",
    "trzecia",
    "czwarta",
    "piąta",
    "szósta",
    "siódma",
    "ósma",
    "dziewiąta",
    "dziesiąta",
    "jedenasta",
)
_ONES = ("", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć")
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
_TENS = ("", "", "dwadzieścia", "trzydzieści", "czterdzieści", "pięćdziesiąt")


def _minute_number(value: int, *, feminine: bool = False) -> str:
    if 10 <= value <= 19:
        return _TEENS[value - 10]
    tens, ones = divmod(value, 10)
    tail = "dwie" if feminine and ones == 2 else _ONES[ones]
    return " ".join(part for part in (_TENS[tens], tail) if part)


def _daypart(hour: int) -> str:
    """Use local clock conventions, independent of seasonal sunrise times."""
    if hour < 5 or hour >= 22:
        return "w nocy"
    if hour < 10:
        return "rano"
    if hour < 12:
        return "przed południem"
    if hour < 18:
        return "po południu"
    return "wieczorem"


def _after_midnight(minute: int) -> str:
    if minute == 1:
        return "minutę po północy"
    unit = "minuty" if minute % 10 in (2, 3, 4) and minute not in (12, 13, 14) else "minut"
    return f"{_minute_number(minute, feminine=True)} {unit} po północy"


def spoken_time(
    value: datetime | time, *, style: Literal["exact", "conversational"] = "exact"
) -> str:
    """Return a complete Polish time phrase, including its preposition.

    ``exact`` retains the 24-hour clock and is suitable for confirmation prompts.
    ``conversational`` uses quarter/half-hour phrases with a daypart so that, for
    example, 03:30 and 15:30 remain distinguishable. Both styles are deterministic.
    The caller supplies HA local time; this helper neither converts its timezone
    nor changes seconds, dates or calendar values. Speech has minute precision.
    """
    if not isinstance(value, (datetime, time)):
        raise TypeError("value must be a datetime or time")
    if style not in {"exact", "conversational"}:
        raise ValueError("style must be exact or conversational")
    hour, minute = value.hour, value.minute
    if minute == 0:
        if hour == 0:
            return "o północy"
        if hour == 12:
            return "w południe"
        return f"o {_HOURS_AFTER_O[hour]}"
    if style == "conversational":
        if minute == 15:
            if hour == 0:
                return "kwadrans po północy"
            clock_hour = hour % 12 or 12
            return f"kwadrans po {_HOURS_AFTER_O[clock_hour]} {_daypart(hour)}"
        if minute == 30:
            next_hour = (hour + 1) % 12 or 12
            return f"wpół do {_HOURS_AFTER_O[next_hour]} {_daypart(hour)}"
        if minute == 45:
            if hour == 23:
                return "za piętnaście północ"
            next_hour = _HOURS_NOMINATIVE[(hour + 1) % 12]
            return f"za piętnaście {next_hour} {_daypart(hour)}"
    if hour == 0:
        return _after_midnight(minute)
    return f"o {_HOURS_AFTER_O[hour]} {_minute_number(minute)}"
