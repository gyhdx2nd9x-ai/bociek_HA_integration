from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.types import Action, Proposal, Request


class Fallback:
    async def propose(self, *args):
        return Proposal(Action.ON, "light", location="salon", confidence=0.9, origin="llm")


async def test_explicit_area_survives_incorrect_llm_location(engine, executor):
    engine.fallback = Fallback()
    request = Request("jest za ciemno w kuchni", source_area="living_room", conversation_id="c")
    result = await engine.process(request)
    assert result.status == "confirm"
    assert result.targets == ("light.kitchen_main",)
    confirmed = await engine.process(replace(request, text="potwierdzam"))
    assert confirmed.status == "executed"
    assert executor.calls[0].entity_ids == ("light.kitchen_main",)


@pytest.mark.parametrize(
    "text",
    [
        "nie zapalaj światła",
        "zapal światła za 10 minut",
        "zapal światła i zgaś telewizor",
        "jeśli pada to zapal światła",
    ],
)
async def test_negation_and_compound_do_not_reach_fallback(engine, executor, text):
    engine.fallback = Fallback()
    result = await engine.process(Request(text, source_area="living_room"))
    assert result.status in {"unsupported", "clarify"}
    assert not result.calls
    assert not executor.calls
