"""Small whole-question state grammar: quantifiers never silently choose an Area."""

import pytest

from custom_components.polish_context_voice.core.nlu.capabilities import (
    Capability,
    parse_capability,
)


@pytest.mark.parametrize(
    "text,domain,kind,query,location,scope",
    [
        ("Czy gdzieś świeci się światło?", "light", "on", "any_on", None, "house"),
        ("Czy gdzieś świecą się światła?", "light", "on", "any_on", None, "house"),
        ("Czy jakieś światła są włączone?", "light", "on", "any_on", None, ""),
        ("Czy świeci się światło?", "light", "on", "any_on", None, ""),
        ("Czy świecą się światła?", "light", "on", "any_on", None, ""),
        ("Czy wszystkie światła są wyłączone?", "light", "on", "all_off", None, ""),
        (
            "Czy wszystkie światła są wyłączone w całym domu?",
            "light",
            "on",
            "all_off",
            None,
            "house",
        ),
        ("Czy światła są włączone w całym domu?", "light", "on", "any_on", None, "house"),
        ("Które światła są włączone w całym domu?", "light", "on", "which_on", None, "house"),
        ("Czy w kuchni świeci się światło?", "light", "on", "any_on", "kuchni", ""),
        ("Czy na piętrze świeci się światło?", "light", "on", "any_on", "pietrze", ""),
        ("Czy we wnęce świecą się światła?", "light", "on", "any_on", "wnece", ""),
        ("Czy w całym domu świeci się światło?", "light", "on", "any_on", None, "house"),
        ("Czy świeci się światło w salonie?", "light", "on", "any_on", "salonie", ""),
        (
            "Czy wszystkie światła są wyłączone na pierwszym piętrze?",
            "light",
            "on",
            "all_off",
            "pierwszym pietrze",
            "",
        ),
        (
            "Czy wszystkie okna są zamknięte w całym domu?",
            "binary_sensor",
            "window",
            "closed",
            None,
            "house",
        ),
        (
            "Czy wszystkie okna są otwarte w całym domu?",
            "binary_sensor",
            "window",
            "all_open",
            None,
            "house",
        ),
        (
            "Które okna są otwarte w całym domu?",
            "binary_sensor",
            "window",
            "which_open",
            None,
            "house",
        ),
        (
            "Które okna są zamknięte w całym domu?",
            "binary_sensor",
            "window",
            "which_closed",
            None,
            "house",
        ),
        (
            "Czy drzwi są zamknięte w całym domu?",
            "binary_sensor",
            "door",
            "closed",
            None,
            "house",
        ),
        (
            "Które drzwi są otwarte w całym domu?",
            "binary_sensor",
            "door",
            "which_open",
            None,
            "house",
        ),
    ],
)
def test_one_state_question_preserves_kind_quantifier_and_scope(
    text, domain, kind, query, location, scope
):
    cap = parse_capability(text)
    assert cap == Capability(
        "state", domain, kind=kind, query=query, location=location, scope=scope
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Czy światła są włączone?", Capability("state", "light", kind="on")),
        ("Które światła są włączone?", Capability("state", "light", kind="on")),
        (
            "Które światła są włączone w kuchni?",
            Capability("state", "light", kind="on", location="kuchni"),
        ),
        (
            "Czy wszystkie okna są zamknięte?",
            Capability("state", "binary_sensor", kind="window", query="closed"),
        ),
        (
            "Które drzwi są otwarte na piętrze?",
            Capability(
                "state", "binary_sensor", kind="door", query="which_open", location="pietrze"
            ),
        ),
        ("Czy trzeba gdzieś wymienić baterie?", Capability("maintenance", kind="battery")),
    ],
)
def test_legacy_question_slots_and_maintenance_definition_stay_unchanged(text, expected):
    assert parse_capability(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Nie pytam czy wszystkie światła są wyłączone",
        "Czy wszystkie światła nie są wyłączone?",
        "Czy gdzieś nie świeci się światło?",
        "Czy wszystkie światła są wyłączone i włącz światła",
        "Czy wszystkie światła są wyłączone ale zamknij drzwi",
        "Czy wszystkie światła są wyłączone oprócz kuchni",
        "Czy wszystkie światła są wyłączone poza kuchnią",
        "Czy wszystkie światła są wyłączone jutro",
        "Jeśli wszystkie światła są wyłączone zamknij drzwi",
        "Czy w kuchni świeci się światło i wyłącz je",
        "Czy gdzieś świeci się światło w kuchni?",
        "Czy gdzieś świeci się światło na piętrze?",
        "Czy gdzieś świeci się światło w całym domu?",
        "Czy w kuchni świeci się światło w całym domu?",
        "Czy w całym domu świeci się światło w całym domu?",
        "Czy wszystkie światła są wyłączone w kuchni w całym domu?",
        "Czy wszystkie światła są wyłączone w całym domu w kuchni?",
        "Czy wszystkie okna są zamknięte w kuchni w całym domu?",
        "Czy wszystkie okna są zamknięte w całym domu w kuchni?",
        "Czy wszystkie okna są zamknięte w całym domu i kuchni?",
        "Czy wszystkie okna są zamknięte w całym domu oprócz kuchni?",
        "Czy drzwi są zamknięte na klucz w całym domu?",
        "Czy wszystkie które drzwi są otwarte w całym domu?",
        "Które wszystkie okna są zamknięte w całym domu?",
        "Czy wszystkie światła są włączone w całym domu?",
        "Zgaś wszystkie światła w całym domu",
        "Uruchom scenę Kino w całym domu",
        "Czy wszystkie światła są wyłączone w " + "bardzo " * 50 + "długim pokoju",
    ],
)
def test_scope_question_does_not_discard_negation_exceptions_or_another_request(text):
    # Generic routines retain their legacy literal-name grammar; no house scope is inferred.
    cap = parse_capability(text)
    if text.startswith("Uruchom scenę"):
        assert cap.scope == "" and cap.operation == "routine"
    else:
        assert cap is None
