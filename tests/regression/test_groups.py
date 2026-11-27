from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.types import Request


def group(home, member):
    item = home.entities["light.living_room_main"]
    home.entities[item.id] = replace(item, attributes={"entity_id": [member]})


async def test_safe_group_expands_to_exact_leaves(home, engine, executor):
    group(home, "light.living_room_table")
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.targets == ("light.living_room_table",)
    assert executor.calls[0].entity_ids == ("light.living_room_table",)


@pytest.mark.parametrize(
    ("member", "reason"),
    [
        ("light.kitchen_main", "cross_area_group"),
        ("light.living_room_hidden", "ineligible_target"),
        ("light.living_room_main", "group_cycle"),
        ("light.missing", "unknown_group_member"),
        ("switch.fridge", "unknown_group_member"),
    ],
)
async def test_unsafe_groups(home, engine, executor, member, reason):
    group(home, member)
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.reason == reason
    assert not executor.calls


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"exposed": False}, "not_exposed"),
        ({"available": False}, "unavailable"),
        ({"labels": frozenset({"voice_block"})}, "blocked"),
    ],
)
async def test_preview_explains_ineligible_group_member(home, changes, problem):
    member = home.entities["light.living_room_table"]
    home.entities[member.id] = replace(member, **changes)
    group(home, member.id)
    result = await Engine(lambda: home, explain_resolution=True).process(
        Request("zapal światła", source_area="living_room", dry_run=True)
    )
    assert result.reason == "ineligible_target" and not result.calls and not result.targets
    assert result._resolution_trace.roots == ("light.living_room_main",)
    assert result._resolution_trace.member_problem == problem
    assert member.id not in str(result.to_dict())
