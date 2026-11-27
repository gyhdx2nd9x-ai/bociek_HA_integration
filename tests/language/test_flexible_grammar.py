"""Polish discovery cases from the audit and independent boundary variants."""

import pytest

from custom_components.polish_context_voice.core.nlu import parse
from custom_components.polish_context_voice.core.types import Action, Request


@pytest.mark.parametrize(
    "text",
    [
        "Światła w salonie włącz",
        "W salonie zapal światła",
        "Zapal w salonie światła",
        "Zapal proszę światła",
        "Światła proszę zapal",
        "W salonie proszę włącz światła",
        "Światła włącz w salonie",
        "Czy możesz proszę włączyć światła w salonie?",
    ],
)
async def test_word_order_retains_exact_main_targets(engine, text):
    result = await engine.process(Request(text, source_area="living_room", dry_run=True))
    assert result.status == "dry_run"
    assert result.targets == ("light.living_room_main",)
    assert all(c.service == "turn_on" for c in result.calls)


@pytest.mark.parametrize(
    "text,value",
    [
        ("Ustaw światła na czterdzieści procent", 40),
        ("W salonie ustaw światła na dwadzieścia pięć procent", 25),
        ("Ustaw proszę światła na dziewięćdziesiąt dziewięć procent w salonie", 99),
        ("Światła ustaw na sto procent", 100),
        ("Przyciemnij światła do piętnastu procent", None),
        ("Ustaw światła na dwadzieścia trzy cztery procent", None),
        ("Ustaw światła na 20 trzy procent", None),
    ],
)
def test_slot_numbers_are_consumed_in_full(text, value):
    result = parse(text)
    if value is None:
        assert result is None
    else:
        assert result.value == value
        assert result.action == Action.BRIGHTNESS


def test_temperature_words():
    result = parse("Ustaw temperaturę na dwadzieścia jeden stopni w salonie")
    assert result.action == Action.SET_TEMPERATURE
    assert result.value == 21
    assert result.location == "salonie"


@pytest.mark.parametrize(
    "text",
    [
        "W piwnicy zapal światła",
        "Zapal w piwnicy światła",
        "Światła w piwnicy zapal",
        "W salonie nie zapalaj światła",
        "Zapal proszę światła i wyłącz telewizor",
        "Światła jutro włącz",
        "W salonie zapal światła oprócz lampki",
        "Ustaw światła na czterdzieści procent i zgaś telewizor",
        "W kuchni albo w salonie włącz światła",
    ],
)
async def test_extra_structure_and_unknown_area_never_execute(engine, executor, text):
    result = await engine.process(Request(text, source_area="living_room"))
    assert result.status in {"unsupported", "clarify"}
    assert not result.calls
    assert not executor.calls


async def test_words_still_check_range(engine, executor):
    result = await engine.process(
        Request("ustaw światła na sto jeden procent", source_area="living_room")
    )
    assert result.reason == "range"
    assert not executor.calls
