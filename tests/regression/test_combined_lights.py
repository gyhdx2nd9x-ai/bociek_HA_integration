from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request


@pytest.mark.parametrize(
    "text",
    [
        "włącz światła dekoracyjne",
        "włącz światła nastrojowe",
        "włącz dekoracyjne",
        "włącz nastrojowe",
        "wyłącz światła dekoracyjne",
        "wyłącz światła nastrojowe",
    ],
)
async def test_combined_category_accepts_legacy_labels_and_deduplicates(executor, text):
    def light(name, *labels, area="room"):
        return Entity("light." + name, name, area, labels=frozenset(labels), exposed=True)

    entities = [
        light("old", "voice_accent"),
        light("new", "voice_ambient"),
        light("both", "voice_ambient", "voice_accent"),
        light("other", "voice_ambient", area="other"),
        light("blocked", "voice_accent", "voice_block"),
        light("excluded", "voice_ambient", "voice_exclude_all"),
        replace(light("hidden", "voice_accent"), exposed=False),
        light("main", "voice_main", "voice_default_on"),
    ]
    model = HomeModel([Area("room", "Salon"), Area("other", "Kuchnia")], entities=entities)
    result = await Engine(lambda: model, executor).process(Request(text, source_area="room"))
    assert result.status == "executed"
    assert set(result.targets) == {"light.old", "light.new", "light.both"}
    assert len(executor.calls) == 3


async def test_combining_categories_does_not_grant_collective_on_off(executor):
    entity = Entity("light.old", "Stara", "room", labels=frozenset({"voice_accent"}), exposed=True)
    model = HomeModel([Area("room", "Salon")], entities=[entity])
    engine = Engine(lambda: model, executor)
    for text in ("włącz światła", "wyłącz światła"):
        result = await engine.process(Request(text, source_area="room"))
        assert not result.targets and not executor.calls
