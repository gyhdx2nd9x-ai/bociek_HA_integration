from datetime import date

import pytest

from custom_components.polish_context_voice.core.stoic import (
    MAX_SPEECH_CHARS,
    StoicEntry,
    date_status,
    entry_from_attributes,
    parse_stoic,
    reading,
)


@pytest.mark.parametrize(
    ("text", "section", "today"),
    [
        ("Przeczytaj dzisiejszą medytację", "full", True),
        ("Przeczytaj mi Stoica", "full", False),
        ("Odczytaj tekst ze stoica", "full", False),
        ("Proszę przeczytaj cytat stoicki", "quote", False),
        ("Jaki jest dzisiejszy cytat?", "quote", True),
        ("Jaki jest cytat na dziś?", "quote", True),
        ("medytacja na dziś", "full", True),
        ("dzisiejszy cytat", "quote", True),
        ("cytat na dziś", "quote", True),
        ("dzisiejsza medytacja", "full", True),
        ("Jaka jest dzisiejsza medytacja?", "full", True),
        ("Jaka jest medytacja na dziś?", "full", True),
        ("Co zawiera dzisiejsza medytacja?", "full", True),
        ("Jak brzmi dzisiejszy cytat?", "quote", True),
        ("Czytaj cytat dnia", "quote", False),
        ("Przeczytaj cytat na dziś", "quote", True),
        ("Przeczytaj medytację na dzisiaj", "full", True),
        ("Przeczytaj omówienie", "commentary", False),
        ("Przeczytaj komentarz do dzisiejszego cytatu", "commentary", True),
        ("Podaj tytuł medytacji", "title", False),
        ("Przeczytaj cytat i omówienie", "full", False),
    ],
)
def test_reading_grammar(text, section, today):
    command = parse_stoic(text)
    assert (command.section, command.today) == (section, today)


@pytest.mark.parametrize(
    "text",
    [
        "włącz światła",
        "przeczytaj cytat i włącz światło",
        "nie przeczytaj cytatu",
        "odlicz minutę",
        "puść Stoic w salonie",
        "jaka jest pogoda",
        "przeczytaj moją pocztę",
    ],
)
def test_unrelated_or_compound_requests_do_not_match(text):
    assert parse_stoic(text) is None


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"date": "2026-10-29"}, "current"),
        ({"date": "2026-10-28"}, "stale"),
        ({"dateKey": "10-29"}, "current"),
        ({"dateKey": "10-28"}, "stale"),
        ({"dateKey": "10-29", "date": "2025-10-29"}, "stale"),
        ({"date": "29 października"}, "current"),
        ({"dateKey": "10-99"}, "invalid"),
        ({"dateKey": ["10-29"]}, "invalid"),
        ({"date": "2026-02-99"}, "invalid"),
        ({}, "unknown"),
    ],
)
def test_explicit_dates_only(attributes, expected):
    assert date_status(attributes, date(2026, 10, 29)) == expected


def test_leap_day_and_isotimestamp():
    assert date_status({"dateKey": "02-29"}, date(2028, 2, 29)) == "current"
    assert date_status({"date": "2026-10-29T22:00:00+00:00"}, date(2026, 10, 29)) == "current"


def test_fields_are_existing_texts_with_whitespace_normalized():
    entry = entry_from_attributes(
        {
            "quote": "  Cytat\nbez zmian. ",
            "authorAndSource": "Autor · źródło",
            "title": "  Tytuł  ",
            "commentary": "Cały\nkomentarz z polskimi znakami.",
            "dateKey": "10-29",
        },
        date(2026, 10, 29),
    )
    speech, reason, missing = reading(entry, "full")
    assert speech == ("Cytat bez zmian. Autor · źródło. Tytuł. Cały komentarz z polskimi znakami.")
    assert reason == "" and missing == ()


def test_fallback_author_and_missing_sections_are_explicit():
    entry = entry_from_attributes(
        {"quote": "Cytat", "author": "Autor", "source": "Źródło"}, date(2026, 10, 29)
    )
    assert entry.author == "Autor · Źródło"
    speech, reason, missing = reading(entry, "full")
    assert speech.startswith("Cytat. Autor · Źródło.")
    assert "Omówienie nie jest dostępne" in speech
    assert reason == "stoic_partial" and missing == ("title", "commentary")
    assert reading(entry, "commentary")[1] == "stoic_missing_commentary"


def test_long_text_is_not_silently_shortened_or_cached_as_a_full_reading():
    entry = StoicEntry("cytat", "autor", "tytuł", "x" * MAX_SPEECH_CHARS, "unknown")
    speech, reason, _ = reading(entry, "full")
    assert reason == "stoic_text_too_long" and "zbyt długa" in speech
    assert reading(entry, "quote")[0] == "cytat. autor."


def test_nontext_fields_do_not_get_stringified_into_spoken_data():
    entry = entry_from_attributes({"quote": {"private": "data"}}, date(2026, 10, 29))
    assert reading(entry, "quote")[1] == "stoic_missing_quote"


@pytest.mark.parametrize(
    "today,ordinal,leap,expected",
    [
        (date(2026, 10, 29), 302, False, "current"),
        (date(2026, 10, 29), 301, False, "stale"),
        (date(2028, 2, 29), 60, True, "current"),
        (date(2028, 3, 1), 61, True, "current"),
        (date(2027, 3, 1), 60, False, "current"),
        (date(2027, 1, 1), 365, False, "stale"),
        (date(2026, 10, 29), 302, True, "stale"),
        (date(2026, 10, 29), 366, False, "invalid"),
        (date(2026, 10, 29), True, False, "invalid"),
        (date(2026, 10, 29), "302", False, "invalid"),
        (date(2026, 10, 29), 302, "false", "invalid"),
    ],
)
def test_daily_stoic_ordinal_date_contract(today, ordinal, leap, expected):
    assert (
            date_status({"day_of_year": ordinal, "is_leap_year": leap}, today)
        == expected
    )


def test_stoic_explicit_conflicting_date_is_not_hidden_by_ordinal():
    assert (
        date_status(
            {"date": "2025-10-29", "day_of_year": 302, "is_leap_year": False}, date(2026, 10, 29)
        )
        == "stale"
    )


def test_localized_calendar_date_takes_precedence_over_book_day_index():
    assert (
        date_status(
            {
                "date": "4 listopada",
                "day_of_year": 309,
                "is_leap_year": False,
            },
            date(2026, 11, 4),
        )
        == "current"
    )


@pytest.mark.parametrize("text", ["Przeczytaj omówienie stoica", "Przeczytaj komentarz ze stoica"])
def test_explicit_stoic_commentary(text):
    assert parse_stoic(text).section == "commentary"
