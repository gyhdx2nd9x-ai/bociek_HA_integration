from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.polish_context_voice.core.calendar import (
    answer_slot,
    clock_time,
    kiosk_conflict,
    moment,
    parse_calendar,
    period,
)

NOW = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo("Europe/London"))


@pytest.mark.parametrize(
    "name,others,conflict",
    [
        ("Kuchnia", ["Kuchnia 2"], True),
        ("Kuchnia 2", ["kuchnia"], True),
        ("Salon", ["Kuchnia"], False),
        ("!!!", [], True),
    ],
)
def test_kiosk_names_do_not_address_multiple_displays(name, others, conflict):
    assert kiosk_conflict(name, others) is conflict


@pytest.mark.parametrize(
    "text,operation",
    [
        ("Co mamy jutro w kalendarzu?", "find"),
        ("Co mam dzisiaj w kalendarzu Rodzina?", "find"),
        ("Co mamy w przyszłym tygodniu?", "find"),
        ("Kiedy jest dentysta?", "find"),
        ("Dodaj dentystę na jutro o piętnastej, na godzinę", "add"),
        ("Dodaj Wizyta u lekarza w piątek o 15:30 na dwie godziny", "add"),
        ("Zapisz wydarzenie Wyjazd jutro cały dzień", "add"),
        ("Przełóż jutrzejszego dentystę na szesnastą", "change"),
        ("Przełóż dentystę na jutro o 16", "change"),
        ("Odwołaj trening domownika w czwartek", "cancel"),
        ("Pokaż kalendarz na przyszły tydzień", "show"),
    ],
)
def test_calendar_operations(text, operation):
    assert parse_calendar(text).operation == operation


@pytest.mark.parametrize(
    "text,when,calendar",
    [
        ("Co jest w moim kalendarzu?", "dzis", ""),
        ("Co jest w naszym kalendarzu?", "dzis", ""),
        ("Co mamy w naszych kalendarzach?", "dzis", ""),
        ("Co jest w moich kalendarzach?", "dzis", ""),
        ("Jakie mam plany?", "dzis", ""),
        ("Jakie mam dzisiaj plany?", "dzisiaj", ""),
        ("Jakie plany mam na jutro?", "na jutro", ""),
        ("Jakie są plany na jutro?", "na jutro", ""),
        ("Jakie mamy jutro plany?", "jutro", ""),
        ("Jakie mam plany pojutrze?", "pojutrze", ""),
        ("Jaki jest plan na dziś?", "na dziś", ""),
        ("Jaki jest plan na jutro?", "na jutro", ""),
        ("Jaki mamy jutro plan?", "jutro", ""),
        ("Co jest w planie na dziś?", "na dziś", ""),
        ("Co mamy jutro w planie?", "jutro", ""),
        ("Co jutro mam w planie?", "jutro", ""),
        ("Co mam zaplanowane na jutro?", "na jutro", ""),
        ("Co mamy jutro zaplanowane?", "jutro", ""),
        ("Jakie mam plany w środę?", "w środę", ""),
        ("Jakie mamy plany w przyszłym tygodniu?", "w przyszłym tygodniu", ""),
        ("Jaki jest plan na przyszły tydzień?", "na przyszły tydzień", ""),
        ("Co jest w planie w tym tygodniu?", "w tym tygodniu", ""),
        ("Co jest w kalendarzu jutro?", "jutro", ""),
        ("Co jest w moim kalendarzu pojutrze?", "pojutrze", ""),
        ("Co jest jutro w kalendarzu Rodzina?", "jutro", "Rodzina"),
        ("Co jest w kalendarzu Rodzina jutro?", "jutro", "Rodzina"),
        ("Co jest w kalendarzu jutro Rodzina?", "jutro", "Rodzina"),
        ("Jakie mam jutro plany w kalendarzu Próba Żółwia?", "jutro", "Próba Żółwia"),
        ("Co mam zaplanowane w kalendarzu Próba Żółwia?", "dzis", "Próba Żółwia"),
        ("Co jest jutro w kalendarzu Jutro?", "jutro", "Jutro"),
        ("Co mamy dzisiaj w kalendarzu Nasze Plany?", "dzisiaj", "Nasze Plany"),
    ],
)
def test_natural_overviews_preserve_one_date_and_explicit_calendar(text, when, calendar):
    command = parse_calendar(text)
    assert (command.operation, command.title, command.when, command.calendar) == (
        "find",
        "",
        when,
        calendar,
    )
    assert period(command.when, NOW) is not None


def test_overview_without_date_uses_the_callers_home_assistant_local_today():
    command = parse_calendar("Co jest w moim kalendarzu?")
    local_now = datetime(2026, 10, 21, 0, 15, tzinfo=ZoneInfo("Pacific/Auckland"))
    start, end = period(command.when, local_now)
    assert start.isoformat() == "2026-10-21T00:00:00+13:00"
    assert end.isoformat() == "2026-10-22T00:00:00+13:00"


@pytest.mark.parametrize(
    "text",
    [
        "Co jest w moim kalendarzu jutro o 15?",
        "Co jest w kalendarzu Rodzina jutro o piętnastej?",
        "Co jest w kalendarzu jutro i wyłącz światła?",
        "Co jest w kalendarzu za trzy dni?",
        "Co jest w kalendarzu o piętnastej?",
        "Co jest w kalendarzu o 15:30?",
        "Co jest w kalendarzu w przyszłym miesiącu?",
        "Co jest w kalendarzu Rodzina i odlicz minutę?",
        "Jakie mam dzisiaj plany jutro?",
        "Jaki jest plan na dziś i jutro?",
        "Co mam zaplanowane jutro o 15?",
        "Co jest w planie na dziś i odwołaj dentystę?",
        "Jakie mam jutro plany? Wyłącz światła",
        "Jaki jest plan ogrzewania na dziś?",
        "Jakie mam plany taryfowe?",
    ],
)
def test_unsupported_overview_tails_never_become_today_or_a_calendar_write(text):
    command = parse_calendar(text)
    assert command.operation == "help"
    assert command.error == "overview_syntax"
    assert command.when == command.calendar == ""


def test_explicit_unknown_calendar_is_not_removed_from_overview():
    command = parse_calendar("Jakie mam jutro plany w kalendarzu Nieistniejący?")
    assert command.operation == "find"
    assert command.calendar == "Nieistniejący"
    assert command.when == "jutro"


@pytest.mark.parametrize("calendar", ["W domu", "O nas", "Na wakacje", "Po pracy"])
def test_preposition_in_explicit_calendar_alias_is_not_mistaken_for_a_date(calendar):
    command = parse_calendar(f"Co mam w kalendarzu {calendar}?")
    assert (command.operation, command.calendar, command.when) == ("find", calendar, "dzis")


def test_invalid_calendar_overview_date_reaches_existing_date_validation():
    command = parse_calendar("Jaki jest plan na 31.02.2026?")
    assert command.operation == "find"
    assert command.when == "na 31.02.2026"
    assert period(command.when, NOW) is None


def test_title_and_calendar_spelling_and_time_are_preserved():
    command = parse_calendar(
        "Dodaj Dentystę na jutro o 15:30 na dwie godziny do kalendarza Rodzina"
    )
    assert command.title == "Dentystę"
    assert command.when == "jutro o 15:30"
    assert command.duration == 120
    assert command.calendar == "Rodzina"
    assert parse_calendar("Dodaj dentystę na jutro o piętnastej, na godzinę").duration == 60


@pytest.mark.parametrize(
    "text,operation,title,when",
    [
        ("Odwołaj jutro badanie", "cancel", "badanie", "jutro"),
        ("Odwołaj wydarzenie jutro Badanie", "cancel", "Badanie", "jutro"),
        ("Usuń w piątek Wizytę u lekarza", "cancel", "Wizytę u lekarza", "w piątek"),
        ("Anuluj 21.10.2026 Próbę Boćka", "cancel", "Próbę Boćka", "21.10.2026"),
        (
            "Odwołaj jutro Badanie o piętnastej",
            "cancel",
            "Badanie",
            "jutro o piętnastej",
        ),
        (
            "Dodaj jutro Badanie o piętnastej",
            "add",
            "Badanie",
            "jutro o piętnastej",
        ),
        (
            "Zapisz w środę o dwudziestej drugiej Próba Boćka",
            "add",
            "Próba Boćka",
            "w środę o dwudziestej drugiej",
        ),
        ("Przełóż jutro Badanie na piątek o 16", "change", "Badanie", "jutro"),
        ("Kiedy jest jutro Badanie?", "find", "Badanie", "jutro"),
        ("Kiedy jest Badanie jutro?", "find", "Badanie", "jutro"),
    ],
)
def test_day_before_event_title_is_a_date_filter(text, operation, title, when):
    command = parse_calendar(text)
    assert (command.operation, command.title, command.when) == (operation, title, when)
    assert moment(command.when, NOW) is not None


def test_day_before_title_preserves_calendar_duration_and_new_date():
    command = parse_calendar(
        "Zapisz jutro o piętnastej Próba Boćka na godzinę do kalendarza Rodzina"
    )
    assert (command.title, command.when, command.duration, command.calendar) == (
        "Próba Boćka",
        "jutro o piętnastej",
        60,
        "Rodzina",
    )
    command = parse_calendar("Przełóż jutro Badanie na piątek o 16")
    assert command.new_when == "piątek o 16"
    command = parse_calendar("Kiedy mamy jutro Badanie w kalendarzu Rodzina?")
    assert (command.operation, command.title, command.when, command.calendar) == (
        "find",
        "Badanie",
        "jutro",
        "Rodzina",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Dodaj jutro Badanie o piętnastej i wyłącz światła",
        "Dodaj jutro o około piętnastej Badanie",
        "Dodaj jutro o 25:00 Badanie",
        "Dodaj jutro Badanie o 15:99",
        "Odwołaj jutro Badanie o piętnastej i o szesnastej",
    ],
)
def test_day_before_title_does_not_hide_invalid_or_extra_time_words(text):
    command = parse_calendar(text)
    assert command is not None
    assert moment(command.when, NOW) is None


def test_date_words_inside_titles_are_not_extracted_without_word_boundaries():
    assert parse_calendar("Odwołaj jutrofon") is None
    command = parse_calendar("Odwołaj Jutrofon w kalendarzu Rodzina")
    assert command.title == "Jutrofon"
    assert command.when == ""


@pytest.mark.parametrize(
    "text",
    [
        "dodaj mleko do listy zakupów",
        "wyłącz światła",
        "odlicz dwie minuty",
        "kiedy przestanie padać",
        "anuluj minutnik",
        "graj muzykę",
    ],
)
def test_unrelated_commands_do_not_become_calendar_writes(text):
    assert parse_calendar(text) is None


@pytest.mark.parametrize(
    "text,hour,minute",
    [
        ("15:30", 15, 30),
        ("o piętnastej", 15, 0),
        ("dwudziestej drugiej", 22, 0),
    ],
)
def test_polish_clock(text, hour, minute):
    result = clock_time(text)
    assert (result.hour, result.minute) == (hour, minute)


@pytest.mark.parametrize("text", ["25:00", "15:90", "15 i wyłącz światła", "około piętnastej"])
def test_invalid_and_unconsumed_times_are_rejected(text):
    assert clock_time(text) is None


def test_relative_week_and_dates_use_local_calendar():
    assert moment("jutro o 15:30", NOW)[0].isoformat() == "2026-10-21T15:30:00+01:00"
    assert moment("w piątek o piętnastej", NOW)[0].day == 23
    assert moment("w przyszły wtorek", NOW)[0].day == 27
    start, end = period("na przyszły tydzień", NOW)
    assert start.day == 26 and end.day == 2 and end.month == 11
    assert moment("31.02.2026", NOW) is None


def test_dst_ambiguity_and_missing_hour_are_never_guessed():
    assert moment("2026-10-25 o 1:30", NOW) is None
    assert moment("2027-03-28 o 1:30", NOW) is None
    assert moment("jutro", NOW)[1] is False
    command = parse_calendar("dodaj dentystę jutro")
    answered = answer_slot(command, "time", "o piętnastej")
    assert moment(answered.when, NOW)[0].hour == 15
