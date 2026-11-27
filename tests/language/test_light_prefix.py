"""Candidate extraction is bounded and preserves meaningful trailing speech."""

import pytest

from custom_components.polish_context_voice.core.nlu.light_prefix import (
    MAX_BOUNDARIES,
    light_prefixes,
)


@pytest.mark.parametrize(
    "text",
    [
        "Włącz światła wow jaka figura",
        "Zgaś światła wow jaka figura",
        "Proszę włącz światła wow jaka figura",
        "Czy możesz mi włączyć światła wow jaka figura",
        "Włącz światła nad stołem wow jaka figura",
        "Włącz światła w kuchni wow jaka figura",
    ],
)
def test_candidates_keep_a_complete_command_and_trailing_words(text):
    candidates = light_prefixes(text)
    assert candidates
    assert len(candidates) <= MAX_BOUNDARIES
    assert all(
        candidate.prefix_words >= 2 and candidate.trailing_words >= 1 for candidate in candidates
    )
    assert any(not candidate.unsafe_tail for candidate in candidates)
    assert [candidate.prefix_words for candidate in candidates] == sorted(
        [candidate.prefix_words for candidate in candidates], reverse=True
    )


@pytest.mark.parametrize(
    "tail",
    [
        "nie",
        "ale nie w kuchni",
        "jednak zgaś",
        "zamiast tego otwórz roletę",
        "jeśli mama wróci",
        "gdy będzie ciemno",
        "oprócz lampy",
        "bez dekoracyjnych",
        "i zamknij drzwi",
        "oraz odlicz minutę",
        "na czerwono",
        "trochę ciemniej",
        "mocniej",
        "na pół",
        "za chwilę",
        "jutro",
        "wieczorem",
        "o zmroku",
        "po zachodzie",
        "przed snem",
        "główne wow jaka figura",
        "robocze wow jaka figura",
        "ozdobne wow jaka figura",
        "sufitowe wow jaka figura",
        "w nieznanym pokoju",
        "obok krzesła",
        "pod stołem",
        "drugą lampę",
        "kuchenne",
        "o 15:00",
        "na piętnaście procent",
        "uruchom odkurzacz",
        "dodaj zakupy",
        "powiedział tata",
        "mówiła mama",
    ],
)
def test_a_meaningful_tail_cannot_be_classified_as_background(tail):
    candidates = light_prefixes(f"Włącz światła {tail}")
    ordinary = [
        candidate
        for candidate in candidates
        if candidate.proposal.kind == "light" and candidate.prefix_words == 2
    ]
    assert ordinary
    assert all(candidate.unsafe_tail for candidate in ordinary)


@pytest.mark.parametrize(
    "text",
    [
        "Włącz światła",
        "Przyciemnij światła na 30 procent wow jaka figura",
        "Wyłącz wszystko wow jaka figura",
        "Włącz je wow jaka figura",
        "Otwórz rolety wow jaka figura",
        "Odlicz dziesięć sekund wow jaka figura",
        "wow jaka figura włącz światła",
        "Włącz światła " + "szum " * 33,
        "Włącz światła " + "s" * 260,
    ],
)
def test_non_light_or_unbounded_input_does_not_give_a_generic_light_prefix(text):
    assert not any(candidate.proposal.kind == "light" for candidate in light_prefixes(text))


@pytest.mark.parametrize("text", ['"Włącz światła", powiedział tata', "„Włącz światła” wow"])
def test_quoted_command_is_never_an_eligible_candidate(text):
    candidates = light_prefixes(text)
    assert candidates and all(candidate.unsafe_tail for candidate in candidates)
