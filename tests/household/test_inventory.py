import json
from pathlib import Path

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.types import Request
from scripts.import_inventory import import_ids


async def test_import_does_not_invent_metadata_or_authorize_controls():
    home, report = import_ids(["light.main_lights", "assist_satellite.echomuse", "switch.fridge"])
    assert report["entity_count"] == 3
    assert all(e.area_id is None and not e.exposed for e in home.entities.values())
    assert home.satellites == {"assist_satellite.echomuse": None}
    result = await Engine(lambda: home).process(
        Request("zapal światła", source_area="living_room", dry_run=True)
    )
    assert not result.calls


def test_public_inventory_is_synthetic_and_does_not_authorize_control():
    home = HomeModel.from_dict(json.loads(Path("examples/inventory_950.json").read_text()))
    assert len(home.entities) == 950
    assert "light.main_lights" in home.entities
    assert "conversation.home_assistant" in home.entities
    assert all(not e.exposed and not e.available for e in home.entities.values())
    assert not home.areas


@pytest.mark.parametrize("data", [["x; rm file"], {"entity_id": "light.x"}, [1]])
def test_bad_inventory_rejected(data):
    with pytest.raises(ValueError):
        import_ids(data)
