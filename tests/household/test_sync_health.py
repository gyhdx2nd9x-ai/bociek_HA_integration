from dataclasses import replace

from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.home_model.cache import ModelCache
from custom_components.polish_context_voice.core.home_model.health import check
from custom_components.polish_context_voice.core.types import Request


def test_health_new_entity_alias_collision_and_satellite(home):
    baseline = frozenset(home.entities)
    entities = list(home.entities.values()) + [
        Entity("light.new", "Nowe", "living_room", aliases=("lampa przy stole",), exposed=True)
    ]
    model = HomeModel(
        home.areas.values(), home.floors.values(), entities, {"assist_satellite.orphan": None}
    )
    findings = check(model, baseline)
    assert any(f["code"] == "missing_semantic_labels" and f["new"] for f in findings)
    assert any(f["code"] == "alias_conflict" for f in findings)
    assert any(f["code"] == "satellite_without_area" for f in findings)


async def test_entity_move_and_label_change_take_effect(home, engine):
    main = home.entities["light.living_room_main"]
    home.entities[main.id] = replace(main, area_id="office")
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.reason == "no_targets"
    home.entities[main.id] = replace(main, labels=frozenset({"voice_all_off"}))
    assert (
        await engine.process(Request("zapal światła", source_area="living_room"))
    ).reason == "no_targets"


def test_cache_atomic_failure(home):
    calls = []

    def load():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("sync failed")
        return home

    cache = ModelCache(load)
    assert cache.get() is home
    assert cache.get() is home
    cache.invalidate()
    import pytest

    with pytest.raises(RuntimeError):
        cache.get()
    assert cache.dirty
    assert len(calls) == 2
