"""Natural calendar speech retains local dates and every appointment."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.polish_context_voice.core.calendar import event_overview, event_when

ZONE = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 21, 10, tzinfo=ZONE)


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (15, 0, "jutro o piętnastej"),
        (15, 30, "jutro o piętnastej trzydzieści"),
        (14, 15, "jutro o czternastej piętnaście"),
        (12, 45, "jutro o dwunastej czterdzieści pięć"),
        (0, 0, "jutro o północy"),
        (12, 0, "jutro w południe"),
    ],
)
def test_confirmation_times_are_spoken_without_numeric_clock(hour, minute, expected):
    assert event_when(datetime(2026, 10, 22, hour, minute, tzinfo=ZONE), NOW) == expected


def test_overview_groups_days_uses_colloquial_minutes_and_keeps_repeated_titles():
    events = [
        ("Badanie", datetime(2026, 10, 22, 12, 45, tzinfo=ZONE)),
        ("Spacer", datetime(2026, 10, 22, 14, 15, tzinfo=ZONE)),
        ("Próba", datetime(2026, 10, 22, 15, 0, tzinfo=ZONE)),
        ("Próba", datetime(2026, 10, 22, 15, 30, tzinfo=ZONE)),
        ("Wyjazd", date(2026, 10, 23)),
    ]
    speech = event_overview(events, NOW)
    assert speech == (
        "Jutro za piętnaście pierwsza po południu — Badanie. "
        "Kwadrans po drugiej po południu — Spacer. O piętnastej — Próba. "
        "Wpół do czwartej po południu — Próba. Pojutrze przez cały dzień — Wyjazd."
    )
    assert speech.count("Jutro") == 1
    assert speech.count("Próba") == 2
    assert ":00" not in speech and "zero zero" not in speech


def test_all_day_confirmation_does_not_invent_a_midnight_appointment():
    assert event_when(date(2026, 10, 22), NOW) == "jutro, przez cały dzień"


def test_overview_midnight_changes_day_without_changing_the_event_date():
    events = [
        ("Koniec próby", datetime(2026, 10, 21, 23, 45, tzinfo=ZONE)),
        ("Nowa próba", datetime(2026, 10, 22, 0, 0, tzinfo=ZONE)),
    ]
    assert event_overview(events, NOW) == (
        "Dzisiaj za piętnaście północ — Koniec próby. Jutro o północy — Nowa próba."
    )


def test_future_dates_use_month_names_and_keep_a_different_year():
    assert event_when(datetime(2026, 10, 29, 15, tzinfo=ZONE), NOW) == (
        "29 października o piętnastej"
    )
    assert event_when(datetime(2027, 1, 1, 12, tzinfo=ZONE), NOW) == (
        "1 stycznia 2027 roku w południe"
    )


def test_empty_overview_has_no_spoken_punctuation():
    assert event_overview([], NOW) == ""


def test_early_morning_event_and_quoted_title_are_spoken_without_rewriting_the_title():
    title = "Nie odwołałem próby"
    at = datetime(2026, 10, 22, 5, 15, tzinfo=ZONE)
    assert event_overview([(title, at)], NOW) == (
        "Jutro kwadrans po piątej rano — Nie odwołałem próby."
    )
    assert at.hour == 5 and at.minute == 15


def test_all_day_and_timed_events_share_one_day_heading():
    events = [
        ("Urlop", date(2026, 10, 22)),
        ("Spacer", datetime(2026, 10, 22, 15, tzinfo=ZONE)),
        ("Wyjazd", date(2026, 10, 23)),
    ]
    assert event_overview(events, NOW) == (
        "Jutro przez cały dzień — Urlop. O piętnastej — Spacer. Pojutrze przez cały dzień — Wyjazd."
    )
