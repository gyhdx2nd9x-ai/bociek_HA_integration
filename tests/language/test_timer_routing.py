import pytest

from custom_components.polish_context_voice.core.nlu.timers import (
    canonical_timer_text,
    is_timer_request,
    spoken_duration,
    timer_prefix_candidates,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Odlicz dziesięć sekund", "ustaw minutnik na dziesięć sekund"),
        ("Odlicz minutę i sekundę", "ustaw minutnik na 1 minutę i 1 sekundę"),
        (
            "ustaw minutnik na minutę o nazwie i sekundę",
            "ustaw minutnik na 1 minutę o nazwie i sekundę",
        ),
        ("  Proszę odlicz mi 5 minut.  ", "ustaw minutnik na 5 minut."),
        ("Nie odlicz dziesięć sekund", "Nie odlicz dziesięć sekund"),
        ("Czy możesz odlicz 10 sekund", "Czy możesz odlicz 10 sekund"),
        ("Odlicz 10 sekund i włącz światła", "ustaw minutnik na 10 sekund i włącz światła"),
    ],
)
def test_alias_replaces_only_opening_imperative(text, expected):
    assert canonical_timer_text(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("odlicz dziesięć sekund", True),
        ("wstrzymaj minutnik", True),
        ("stan minutników", True),
        ("wyłącz światła", False),
        ("zapal światła w salonie", False),
        ("wyłącz światła za 10 sekund", False),
        ("Wąć się od warstwowania.", False),
    ],
)
def test_timer_gate_keeps_home_commands_on_existing_path(text, expected):
    assert is_timer_request(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Odlicz dziesięć sekund Usiądź normalnie, żebyś się nie ochlapał. Okej, masz więcej.",
            ["Odlicz dziesięć sekund"],
        ),
        (
            "Odlicz minutę i trzydzieści sekund Usiądź normalnie.",
            ["Odlicz minutę i trzydzieści sekund"],
        ),
        ("Ustaw minutnik na 10 sekund. Herbata jest gotowa.", ["Ustaw minutnik na 10 sekund"]),
        ("Odlicz dziesięć sekund", []),
        ("Nie odlicz dziesięć sekund Usiądź normalnie.", []),
        ("Powiedziałem odlicz dziesięć sekund Usiądź normalnie.", []),
        ("odlicz 10 sekund nie jednak 20 sekund", []),
        ("odlicz 10 sekund albo 20", []),
        ("odlicz 10 sekund, jednak nie", []),
        ("odlicz 10 sekund i włącz światła", []),
        ("odlicz 10 sekund. Anuluj minutnik", []),
        ("odlicz 10 sekund. Ustaw temperaturę", []),
        ("odlicz 10 sekund o nazwie herbata", []),
        ("wyłącz światła Usiądź normalnie", []),
        ("włącz światła w salonie tylko nie w kuchni", []),
        ("odlicz 10 sekund " + "tekst " * 400, []),
    ],
)
def test_background_speech_candidates_are_opening_timers_only(text, expected):
    assert timer_prefix_candidates(text) == expected


@pytest.mark.parametrize(
    "slots,expected",
    [
        ({"seconds": 10}, "10 sekund"),
        ({"minutes": 1, "seconds": 30}, "1 minutę i 30 sekund"),
        ({"minutes": 2}, "2 minuty"),
        ({"minutes": 12}, "12 minut"),
        ({"hours": 22, "minutes": 1}, "22 godziny i 1 minutę"),
    ],
)
def test_confirmation_says_complete_duration(slots, expected):
    assert spoken_duration(slots) == expected


@pytest.mark.parametrize(
    "slots,expected",
    [
        ({"seconds": 10}, "dziesięć sekund"),
        ({"minutes": 2}, "dwie minuty"),
        ({"minutes": 1}, "minutę"),
        ({"hours": 1, "minutes": 1, "seconds": 1}, "godzinę, minutę i sekundę"),
        ({"minutes": 1, "seconds": 30}, "minutę i trzydzieści sekund"),
        ({"minutes": 12}, "dwanaście minut"),
        ({"minutes": 21}, "dwadzieścia jeden minut"),
        ({"minutes": 22}, "dwadzieścia dwie minuty"),
        ({"minutes": 24}, "dwadzieścia cztery minuty"),
        ({"minutes": 25}, "dwadzieścia pięć minut"),
        ({"seconds": 101}, "sto jeden sekund"),
        ({"seconds": 112}, "sto dwanaście sekund"),
        ({"seconds": 122}, "sto dwadzieścia dwie sekundy"),
        ({"seconds": 1000}, "1000 sekund"),
        ({"hours": None, "minutes": 0, "seconds": 5}, "pięć sekund"),
    ],
)
def test_natural_timer_duration_uses_accepted_values_and_polish_forms(slots, expected):
    assert spoken_duration(slots, words=True) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Przerwij odliczanie", "anuluj minutnik"),
        ("Anuluj odliczanie", "anuluj minutnik"),
        ("Proszę zakończ odliczanie", "anuluj minutnik"),
        ("przerwij minutnik herbata", "anuluj minutnik herbata"),
        ("przerwij odliczanie na 10 minut", "anuluj minutnik na 10 minut"),
        ("wstrzymaj odliczanie", "wstrzymaj minutnik"),
        ("wznów odliczanie", "wznów minutnik"),
        ("Nie przerwij odliczanie", "Nie przerwij odliczanie"),
        ("Przerwij odliczanie i włącz światła", "anuluj minutnik i włącz światła"),
    ],
)
def test_countdown_lifecycle_alias_preserves_selectors_and_negation(text, expected):
    assert is_timer_request(text)
    assert canonical_timer_text(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Tak!", True),
        ("Potwierdzam.", True),
        ("Dobrze", True),
        ("OK", True),
        ("Okej", True),
        ("Tak, proszę", True),
        ("Zgoda", True),
        ("Tak. Tak", True),
        ("Potwierdzam minutnik", True),
        ("Nie, dziękuję", True),
        ("Anuluj", True),
        ("Tak, ale za dwie minuty", False),
        ("Tak nie", False),
        ("tak да", False),
        ("potwierdzam minutnik Так", False),
        ("Tak ♥", False),
        ("Dobrze, włącz światła", False),
        ("Nie anuluj", False),
        ("Czy potwierdzam?", False),
    ],
)
def test_timer_followup_is_a_whole_reply(text, expected):
    from custom_components.polish_context_voice.core.nlu.timers import is_timer_followup

    assert is_timer_followup(text) == expected


@pytest.mark.parametrize("verb", ["Przerwij", "Zakończ", "Zatrzymaj"])
def test_background_prefix_does_not_drop_cancellation(verb):
    assert not timer_prefix_candidates(f"Odlicz minutę. {verb} odliczanie")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Nie rozumiem polecenia minutnika. Powiedz na przykład: odlicz dziesięć sekund", True),
        ("Nie rozumiem polecenia minutnika Powtórz je proszę", True),
        ("Nie rozumiem polecenia minutnika. Powtórz je proszę.", True),
        ("Odlicz dziesięć sekund", False),
        ("Nie rozumiem polecenia minutnika. Odlicz dwie minuty", False),
    ],
)
def test_timer_echo_filter_matches_only_known_response_sentences(text, expected):
    from custom_components.polish_context_voice.core.nlu.timers import is_timer_response_echo

    assert is_timer_response_echo(text) == expected
