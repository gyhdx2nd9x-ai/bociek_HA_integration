from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.types import Request


async def test_living_room_on_main_only(engine, executor):
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.status == "executed"
    assert result.targets == ("light.living_room_main",)
    assert executor.calls[0].service == "turn_on"


async def test_living_room_off_all_labelled(engine, executor):
    result = await engine.process(Request("wyłącz światła", source_area="living_room"))
    assert result.status == "executed"
    assert set(result.targets) == {
        "light.living_room_main",
        "light.living_room_table",
        "light.living_room_ambient",
    }
    assert {c.service for c in executor.calls} == {"turn_off"}


async def test_explicit_kitchen_overrides_living_room(engine):
    result = await engine.process(Request("włącz światła w kuchni", source_area="living_room"))
    assert result.targets == ("light.kitchen_main",)


@pytest.mark.parametrize(
    "context",
    [
        {"satellite_id": "assist_satellite.living_room"},
        {"device_id": "satellite_device_living_room"},
    ],
)
async def test_satellite_context(engine, context):
    assert (await engine.process(Request("zapal światła", **context))).targets == (
        "light.living_room_main",
    )


async def test_unknown_explicit_area_never_falls_back(engine, executor):
    result = await engine.process(Request("zapal światła w piwnicy", source_area="living_room"))
    assert result.reason == "unknown_location"
    assert not executor.calls


async def test_no_context_never_expands_house(engine, executor):
    assert (await engine.process(Request("zapal światła"))).reason == "missing_area"
    assert not executor.calls


async def test_floor_is_only_explicit(engine):
    result = await engine.process(Request("zapal światła na parterze", source_area="office"))
    assert set(result.targets) == {"light.living_room_main", "light.kitchen_main"}


async def test_single_lamp_asks(engine, executor):
    result = await engine.process(Request("włącz lampę", source_area="living_room"))
    assert result.reason == "ambiguous_target"
    assert not executor.calls


async def test_named_lamp_uses_alias_and_morphology(engine):
    result = await engine.process(Request("zapal lampę przy stole", source_area="living_room"))
    assert result.targets == ("light.living_room_table",)


async def test_safe_all_excludes_fridge(engine):
    result = await engine.process(Request("wyłącz wszystko", source_area="living_room"))
    assert result.status == "executed"
    assert set(result.targets) == {
        "light.living_room_main",
        "light.living_room_table",
        "light.living_room_ambient",
    }


async def test_named_block_cannot_be_bypassed(engine, executor):
    assert (
        await engine.process(Request("wyłącz router", source_area="living_room"))
    ).status == "clarify"
    assert not executor.calls


async def test_brightness(engine, executor):
    result = await engine.process(Request("ustaw światła na 30%", source_area="living_room"))
    assert result.status == "executed"
    assert executor.calls[0].data == {"brightness_pct": 30.0}


async def test_temperature_uses_device_class(engine):
    result = await engine.process(Request("jaka jest temperatura", source_area="living_room"))
    assert result.status == "answered"
    assert "około 22 stopni" in result.speech


async def test_named_alias_conflict_not_hidden_by_primary(home, engine, executor):
    first = home.entities["light.living_room_table"]
    home.entities["light.living_room_ambient"] = replace(
        home.entities["light.living_room_ambient"], aliases=first.aliases
    )
    # Rebuild indexes after changing the HA snapshot.
    from custom_components.polish_context_voice.core.home_model import HomeModel

    newer = HomeModel(home.areas.values(), home.floors.values(), home.entities.values())
    engine.model_provider = lambda: newer
    result = await engine.process(Request("zapal lampę przy stole", source_area="living_room"))
    assert result.reason == "ambiguous_target"
    assert not executor.calls
