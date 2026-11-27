from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.polish_context_voice.core.spoken_time import spoken_time


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        (time(0), "o północy"),
        (time(1), "o pierwszej"),
        (time(12), "w południe"),
        (time(15), "o piętnastej"),
        (time(21), "o dwudziestej pierwszej"),
        (time(22), "o dwudziestej drugiej"),
        (time(23), "o dwudziestej trzeciej"),
        (time(15, 5), "o piętnastej pięć"),
        (time(15, 11), "o piętnastej jedenaście"),
        (time(15, 15), "o piętnastej piętnaście"),
        (time(15, 30), "o piętnastej trzydzieści"),
        (time(15, 31), "o piętnastej trzydzieści jeden"),
        (time(15, 45), "o piętnastej czterdzieści pięć"),
        (time(23, 59), "o dwudziestej trzeciej pięćdziesiąt dziewięć"),
    ],
)
def test_exact_clock_without_digits_or_redundant_zero_minutes(clock, expected):
    assert spoken_time(clock) == expected


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        (time(0, 1), "minutę po północy"),
        (time(0, 2), "dwie minuty po północy"),
        (time(0, 5), "pięć minut po północy"),
        (time(0, 12), "dwanaście minut po północy"),
        (time(0, 14), "czternaście minut po północy"),
        (time(0, 21), "dwadzieścia jeden minut po północy"),
        (time(0, 22), "dwadzieścia dwie minuty po północy"),
        (time(0, 30), "trzydzieści minut po północy"),
    ],
)
def test_exact_midnight_minutes_use_natural_minute_units(clock, expected):
    assert spoken_time(clock) == expected


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        (time(0, 15), "kwadrans po północy"),
        (time(0, 30), "wpół do pierwszej w nocy"),
        (time(3, 30), "wpół do czwartej w nocy"),
        (time(4, 45), "za piętnaście piąta w nocy"),
        (time(5, 15), "kwadrans po piątej rano"),
        (time(5, 30), "wpół do szóstej rano"),
        (time(5, 45), "za piętnaście szósta rano"),
        (time(7, 45), "za piętnaście ósma rano"),
        (time(11, 30), "wpół do dwunastej przed południem"),
        (time(11, 45), "za piętnaście dwunasta przed południem"),
        (time(12, 15), "kwadrans po dwunastej po południu"),
        (time(12, 45), "za piętnaście pierwsza po południu"),
        (time(14, 15), "kwadrans po drugiej po południu"),
        (time(15, 30), "wpół do czwartej po południu"),
        (time(19, 30), "wpół do ósmej wieczorem"),
        (time(21, 45), "za piętnaście dziesiąta wieczorem"),
        (time(22, 15), "kwadrans po dziesiątej w nocy"),
        (time(23, 15), "kwadrans po jedenastej w nocy"),
        (time(23, 30), "wpół do dwunastej w nocy"),
        (time(23, 45), "za piętnaście północ"),
        (time(15, 17), "o piętnastej siedemnaście"),
        (time(15), "o piętnastej"),
    ],
)
def test_conversational_clock_preserves_time_of_day(clock, expected):
    assert spoken_time(clock, style="conversational") == expected


def test_local_datetime_and_seconds_are_not_rounded_or_converted():
    local = datetime(2026, 10, 21, 15, 0, 59, tzinfo=ZoneInfo("Europe/London"))
    assert spoken_time(local) == "o piętnastej"
    assert local.hour == 15 and local.second == 59


def test_formatter_has_no_random_or_history_dependent_variation():
    assert [spoken_time(time(15, 30), style="conversational") for _ in range(3)] == [
        "wpół do czwartej po południu"
    ] * 3


def test_invalid_inputs_fail_explicitly():
    with pytest.raises(TypeError, match="datetime or time"):
        spoken_time("15:00")
    with pytest.raises(ValueError, match="style"):
        spoken_time(time(15), style="random")
