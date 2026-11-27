"""Polish category synonyms select an exact semantic set, never a named guess."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.nlu import parse
from custom_components.polish_context_voice.core.types import Action, Request


@pytest.mark.parametrize("verb,action", [("włącz", Action.ON), ("wyłącz", Action.OFF)])
@pytest.mark.parametrize("category", ["ozdobne", "dekoracyjne", "nastrojowe"])
@pytest.mark.parametrize(
    "target", ["światła {category}", "{category} światła", "oświetlenie {category}"]
)
def test_decorative_synonyms_preserve_action_and_explicit_room(verb, action, category, target):
    proposal = parse(f"{verb} {target.format(category=category)} w kuchni")
    assert proposal.action == action
    assert proposal.kind == "light" and proposal.qualifier == "voice_ambient"
    assert proposal.location == "kuchni"


@pytest.mark.parametrize("role", ["voice_ambient", "voice_accent"])
@pytest.mark.parametrize("category", ["ozdobne", "dekoracyjne", "nastrojowe"])
async def test_synonyms_honor_canonical_and_legacy_roles_without_other_lights(
    home, executor, role, category
):
    target = "light.living_room_ambient"
    model = HomeModel(
        areas=home.areas.values(),
        entities=[
            replace(e, labels=frozenset({role})) if e.id == target else e
            for e in home.entities.values()
        ],
    )
    engine = Engine(lambda: model, executor)
    result = await engine.process(Request(f"wyłącz światła {category}", source_area="living_room"))
    assert result.status == "executed" and result.targets == (target,)
    assert len(executor.calls) == 1 and executor.calls[0].service == "turn_off"


@pytest.mark.parametrize(
    "text",
    [
        "nie wyłączaj świateł ozdobnych",
        "wyłącz światła ozdobne i robocze",
        "wyłącz światła ozdobne ale nie lampę",
    ],
)
def test_synonym_does_not_truncate_other_instructions(text):
    assert parse(text) is None


@pytest.mark.parametrize(
    "text, action, role",
    [
        ("W pokoju dziennym zapal tylko nastrojowe oświetlenie", Action.ON, "voice_ambient"),
        ("Wyłącz jedynie światła robocze w pokoju dziennym", Action.OFF, "voice_task"),
        ("Włącz wyłącznie główne światła w pokoju dziennym", Action.ON, "voice_main"),
        ("Zgaś tylko ozdobne w pokoju dziennym", Action.OFF, "voice_ambient"),
        ("Zapal tylko wszystkie główne światła w pokoju dziennym", Action.ON, "voice_main"),
    ],
)
def test_exclusive_modifier_preserves_recognized_light_category(text, action, role):
    proposal = parse(text)
    assert proposal.action == action
    assert proposal.kind in {"light", "all_lights"}
    assert proposal.qualifier == role
    assert proposal.location == "pokoju dziennym"


@pytest.mark.parametrize(
    "text",
    [
        "Zapal światła tylko na pięć minut",
        "Zapal tylko nastrojowe oświetlenie i wyłącz główne",
        "Zapal światła wyłącznie kiedy wrócę",
        "Tyko źle odczytane jedynie",
        "Wyłącz tylko wszystko",
        "Zapal tylko światła",
        "Zapal tylko robocze ozdobne światła",
    ],
)
async def test_unsupported_restrictions_are_not_dropped_for_background_or_fallback(
    engine, executor, text
):
    class Fallback:
        async def propose(self, *args):
            pytest.fail("A request with an unhandled restriction reached fallback")

    engine.fallback = Fallback()
    result = await engine.process(Request(text, source_area="living_room"))
    assert result.status in {"clarify", "unsupported"}
    assert not result.calls and not executor.calls


async def test_exclusive_category_without_targets_does_not_offer_other_roles(engine, executor):
    result = await engine.process(
        Request("Włącz tylko nastrojowe światła", source_area="kitchen")
    )
    assert result.status == "clarify"
    assert not result.continue_conversation
    assert not result.calls and not executor.calls


async def test_exclusive_category_preserves_literal_alias_outside_reserved_phrases(
    home, executor
):
    target = "light.living_room_table"
    model = HomeModel(
        areas=home.areas.values(),
        entities=[
            replace(entity, aliases=("Tylko do czytania",)) if entity.id == target else entity
            for entity in home.entities.values()
        ],
    )
    engine = Engine(lambda: model, executor)
    result = await engine.process(Request("Zapal tylko do czytania", source_area="living_room"))
    assert result.status == "executed"
    assert result.targets == (target,)
    assert [call.entity_ids for call in executor.calls] == [(target,)]
