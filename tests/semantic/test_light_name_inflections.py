from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request


def lamp(identifier, alias, area="lounge", **changes):
    return replace(
        Entity(identifier, "Oświetlenie testowe", area, aliases=(alias,), exposed=True, state="on"),
        **changes,
    )


def model(entities):
    return HomeModel(
        areas=[Area("lounge", "Living Area", ("pokój dzienny",)), Area("kitchen", "Kitchen")],
        entities=entities,
        satellites={"assist_satellite.test": "kitchen"},
    )


async def preview(home, text):
    return await Engine(lambda: home).process(
        Request(text, satellite_id="assist_satellite.test", dry_run=True)
    )


@pytest.mark.parametrize(
    ("alias", "spoken"),
    [
        ("taśma LED", "taśmę LED"),
        ("taśmę LED", "taśma LED"),
        ("listwa RGB przy biurku", "listwę RGB przy biurku"),
        ("żarówka RGB", "żarówkę RGB"),
        ("girlanda", "girlandę"),
    ],
)
async def test_inflected_light_alias_selects_only_target_in_explicit_room(alias, spoken):
    home = model([lamp("light.local", alias, "kitchen"), lamp("light.remote", alias)])
    result = await preview(home, f"Zgaś {spoken} w pokoju dziennym")
    assert result.status == "dry_run"
    assert result.targets == ("light.remote",)
    assert result.calls[0].service == "turn_off"


async def test_inflected_light_alias_collision_is_ambiguous():
    home = model([lamp("light.a", "taśma LED"), lamp("light.b", "taśma LED")])
    result = await preview(home, "Zgaś taśmę LED w pokoju dziennym")
    assert result.reason == "ambiguous_target"
    assert not result.calls


@pytest.mark.parametrize(
    "changes",
    [{"exposed": False}, {"available": False}, {"labels": frozenset({"voice_block"})}],
)
async def test_ineligible_exact_alias_does_not_fall_back_to_different_light(changes):
    home = model([lamp("light.exact", "taśmę LED", **changes), lamp("light.other", "taśma LED")])
    result = await preview(home, "Zgaś taśmę LED w pokoju dziennym")
    assert result.status == "clarify"
    assert not result.calls


@pytest.mark.parametrize(
    ("identifier", "spoken"),
    [
        ("switch.test", "taśmę LED"),
        ("light.test", "taśmę LED wczoraj"),
        ("light.test", "taśmy LED"),
    ],
)
def test_inflected_alias_preserves_full_name_and_light_domain(identifier, spoken):
    home = model([lamp(identifier, "taśma LED")])
    assert not home.match_entity_name(spoken)
