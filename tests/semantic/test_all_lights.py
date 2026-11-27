from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Entity
from custom_components.polish_context_voice.core.types import Request


@pytest.mark.parametrize("verb", ["włącz", "zapal", "wyłącz", "zgaś"])
async def test_explicit_all_is_not_default_on_or_limited_off(home, executor, verb):
    extra = Entity("light.no_roles", "Dodatkowa", "living_room", exposed=True, state="off")
    model = home.with_entities([*home.entities.values(), extra])
    result = await Engine(lambda: model, executor).process(
        Request(f"{verb} wszystkie światła w salonie", source_area="kitchen", dry_run=True)
    )
    assert result.status == "dry_run"
    assert "light.no_roles" in result.targets
    assert all(model.entities[e].area_id == "living_room" for e in result.targets)
    assert not executor.calls


@pytest.mark.parametrize("label", ["voice_block", "voice_exclude_all"])
async def test_collective_exclusions_remain_enforced(home, executor, label):
    extra = Entity(
        "light.excluded",
        "Wykluczona",
        "living_room",
        exposed=True,
        state="off",
        labels=frozenset({label}),
    )
    model = home.with_entities([*home.entities.values(), extra])
    result = await Engine(lambda: model, executor).process(
        Request("włącz wszystkie światła", source_area="living_room", dry_run=True)
    )
    assert result.status == "dry_run" and extra.id not in result.targets


async def test_all_without_source_never_becomes_whole_house(home, executor):
    result = await Engine(lambda: home, executor).process(Request("włącz wszystkie światła"))
    assert result.reason == "missing_area" and not executor.calls


async def test_default_on_stays_asymmetric(home, executor):
    engine = Engine(lambda: home, executor)
    ordinary = await engine.process(
        Request("włącz światła", source_area="living_room", dry_run=True)
    )
    all_lights = await engine.process(
        Request("włącz wszystkie światła", source_area="living_room", dry_run=True)
    )
    assert set(ordinary.targets) < set(all_lights.targets)
    assert all("voice_default_on" in home.entities[e].labels for e in ordinary.targets)


async def test_hidden_group_member_does_not_get_access_via_all(home, executor):
    leaf = next(
        e for e in home.entities.values() if e.domain == "light" and e.area_id == "living_room"
    )
    group = Entity(
        "light.group",
        "Grupa",
        "living_room",
        exposed=True,
        state="off",
        attributes={"entity_id": [leaf.id]},
    )
    model = home.with_entities(
        [
            *(replace(e, exposed=False) if e.id == leaf.id else e for e in home.entities.values()),
            group,
        ]
    )
    result = await Engine(lambda: model, executor).process(
        Request("włącz wszystkie światła", source_area="living_room")
    )
    assert result.status != "executed" and not executor.calls
