import pytest

from custom_components.polish_context_voice.core.nlu import parse
from custom_components.polish_context_voice.core.nlu.morphology import (
    LightMorphology,
    MorfeuszMorphology,
)
from custom_components.polish_context_voice.core.nlu.normalize import normalize
from custom_components.polish_context_voice.core.types import Action


@pytest.mark.parametrize(
    "text",
    [
        "zapal światła",
        "WŁĄCZ ŚWIATŁA!",
        "zapal swiatla",
        "włącz mi światło",
        "Możesz mi włączyć światła?",
        "Czy mógłbyś zapalić światło?",
        "proszę zapal światła",
        "włącz tutaj światła",
        "włącz światła proszę",
        "zapalisz światło?",
        "włączcie światła",
    ],
)
def test_on_variants(text):
    result = parse(text)
    assert result.action == Action.ON
    assert result.kind == "light"


@pytest.mark.parametrize(
    "text",
    ["zgaś światła", "wyłącz lampki", "powygaszaj lampy", "Mógłbyś mi zgasić lampki w salonie?"],
)
def test_off_variants(text):
    assert parse(text).action == Action.OFF


@pytest.mark.parametrize(
    "text",
    [
        "nie włącz światła",
        "zapal światła za 10 minut",
        "jeśli pada włącz światła",
        "włącz światła i wyłącz telewizor",
        "wyłącz wszystko oprócz lodówki",
        "chyba zgaś światła",
        "zgaś światła jutro",
        "włącz światła potem telewizor",
        "",
        "przygotuj salon do filmu",
    ],
)
def test_no_partial_match(text):
    assert parse(text) is None


def test_preserves_explicit_unknown_location():
    assert parse("włącz światła w piwnicy").location == "piwnicy"


def test_numeric_slots():
    assert parse("ustaw światła na 30%").value == 30
    assert parse("ustaw temperaturę na 21,5 stopni w salonie").value == 21.5
    assert parse("ustaw temperaturę na 21,5 stopni w salonie").location == "salonie"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("salon", "salonie"),
        ("biuro", "biurze"),
        ("kuchnia", "kuchni"),
        ("lampa przy stole", "lampę przy stole"),
        ("parter", "parterze"),
    ],
)
def test_morphology(a, b):
    assert LightMorphology().key(a) == LightMorphology().key(b)


def test_morfeusz_ambiguity_falls_back():
    class Analyzer:
        def analyse(self, token):
            return [(0, 1, (token, "kot", "subst")), (0, 1, (token, "kota", "subst"))]

    assert MorfeuszMorphology(Analyzer()).key("koty") == "koty"


def test_normalization():
    assert normalize("  ŻÓŁĆ! 21,5  ") == "zolc 21.5"
