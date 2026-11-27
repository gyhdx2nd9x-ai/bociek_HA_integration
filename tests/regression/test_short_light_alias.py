from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request


def light(identifier="light.table", area="living_room", alias="nad stołem", **kwargs):
    return Entity(
        identifier,
        identifier,
        area,
        aliases=(alias,),
        exposed=True,
        attributes={"supported_color_modes": ["brightness"]},
        **kwargs,
    )


async def run(executor, entities, text="Włącz światła nad stołem", **kwargs):
    model = HomeModel(
        [Area("living_room", "Salon"), Area("kitchen", "Kuchnia")],
        entities=entities,
        satellites={"assist_satellite.test": "living_room"},
    )
    request = Request(text, **({"satellite_id": "assist_satellite.test"} | kwargs))
    return await Engine(lambda: model, executor).process(request)


@pytest.mark.parametrize("noun", ["światła", "światło", "oświetlenie", "lampy", "lampki", "lampę"])
@pytest.mark.parametrize("verb,service", [("Włącz", "turn_on"), ("Wyłącz", "turn_off")])
async def test_type_plus_short_alias_controls_only_named_light(executor, noun, verb, service):
    result = await run(
        executor, [light(), light("light.other", alias="inne")], f"{verb} {noun} nad stołem"
    )
    assert result.status == "executed" and result.targets == ("light.table",)
    assert len(executor.calls) == 1 and executor.calls[0].service == service


async def test_full_name_has_priority_over_short_alias(executor):
    result = await run(executor, [light(), light("light.full", alias="światła nad stołem")])
    assert result.targets == ("light.full",)


@pytest.mark.parametrize(
    "change", [{"exposed": False}, {"available": False}, {"labels": frozenset({"voice_block"})}]
)
async def test_ineligible_full_name_never_falls_back_to_different_short_alias(executor, change):
    exact = replace(light("light.full", alias="światła nad stołem"), **change)
    result = await run(executor, [light(), exact])
    assert result.status != "executed" and not executor.calls


async def test_type_hint_prevents_short_alias_from_controlling_switch(executor):
    result = await run(executor, [light("switch.socket")])
    assert result.reason == "no_targets" and not executor.calls


async def test_duplicate_short_alias_is_not_an_implicit_group(executor):
    result = await run(
        executor, [light(), light("light.duplicate", labels=frozenset({"voice_primary"}))]
    )
    assert result.reason == "ambiguous_target" and not executor.calls


async def test_explicit_area_overrides_satellite_for_short_alias(executor):
    result = await run(
        executor, [light(), light("light.kitchen", "kitchen")], "włącz światła nad stołem w kuchni"
    )
    assert result.targets == ("light.kitchen",) and result.area_ids == ("kitchen",)


async def test_unknown_area_never_reverts_to_satellite(executor):
    result = await run(executor, [light()], "włącz światła nad stołem w piwnicy")
    assert result.reason == "unknown_location" and not executor.calls


async def test_unknown_words_are_not_discarded(executor):
    result = await run(executor, [light()], "włącz światła nad stołem obok krzesła")
    assert result.reason == "no_targets" and not executor.calls


async def test_short_alias_still_requires_exposure(executor):
    result = await run(executor, [replace(light(), exposed=False)])
    assert result.reason == "no_targets" and not executor.calls


async def test_global_unique_short_alias_without_satellite(executor):
    result = await run(executor, [light()], satellite_id=None)
    assert result.targets == ("light.table",)


async def test_short_alias_brightness_keeps_value(executor):
    result = await run(executor, [light()], "ustaw światła nad stołem na 30 procent")
    assert result.status == "executed" and result.calls[0].data == {"brightness_pct": 30}


async def test_short_group_alias_expands_to_exact_members(executor):
    group = replace(light(), attributes={"entity_id": ["light.one", "light.two"]})
    result = await run(
        executor, [group, light("light.one", alias="pierwsza"), light("light.two", alias="druga")]
    )
    assert result.targets == ("light.one", "light.two")
    assert {c.entity_ids[0] for c in executor.calls} == {"light.one", "light.two"}


@pytest.mark.parametrize(
    "member,reason",
    [
        (replace(light("light.one", alias="pierwsza"), exposed=False), "ineligible_target"),
        (light("light.one", "kitchen", alias="pierwsza"), "cross_area_group"),
    ],
)
async def test_short_group_alias_cannot_bypass_member_guards(executor, member, reason):
    group = replace(light(), attributes={"entity_id": ["light.one"]})
    result = await run(executor, [group, member])
    assert result.reason == reason and not executor.calls


async def test_direct_short_alias_still_works(executor):
    result = await run(executor, [light()], "włącz nad stołem")
    assert result.targets == ("light.table",)


@pytest.mark.parametrize(
    "labels,status", [(frozenset(), "clarify"), (frozenset({"voice_main"}), "executed")]
)
async def test_role_qualifier_is_not_lost_when_stripping_type_noun(executor, labels, status):
    result = await run(executor, [light(labels=labels)], "włącz główne światła nad stołem")
    assert result.status == status
    assert bool(executor.calls) == (status == "executed")
