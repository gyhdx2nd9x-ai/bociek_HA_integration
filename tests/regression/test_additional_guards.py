from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.policy import evaluate
from custom_components.polish_context_voice.core.resolver import Resolution
from custom_components.polish_context_voice.core.types import Action, Proposal, Request


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 0.84])
def test_invalid_confidence_is_not_authorization(home, confidence):
    result = evaluate(
        Proposal(Action.ON, "light", confidence=confidence, origin="llm"),
        Resolution((home.entities["light.living_room_main"],)),
    )
    assert result.status == "clarify"
    assert not result.calls


@pytest.mark.parametrize(
    ("label", "status"), [("voice_confirm", "confirm"), ("voice_high_risk", "blocked")]
)
async def test_group_restrictions_survive_expansion(home, engine, executor, label, status):
    main = home.entities["light.living_room_main"]
    home.entities[main.id] = replace(
        main, labels=main.labels | {label}, attributes={"entity_id": ["light.living_room_table"]}
    )
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.status == status
    assert not executor.calls


async def test_empty_role_does_not_fall_back_to_all_lights(home, engine):
    main = home.entities["light.living_room_main"]
    home.entities[main.id] = replace(main, labels=frozenset())
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.reason == "no_targets"
    assert not result.calls
