import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request


@pytest.mark.parametrize(
    ("alias", "spoken"),
    [
        ("pokój gościnny", "pokoju gościnnym"),
        ("pokój dzienny", "pokoju dziennym"),
        ("aneks", "aneksie"),
        ("aneks kuchenny", "aneksie kuchennym"),
    ],
)
async def test_explicit_room_inflection_uses_ha_alias_instead_of_satellite_room(alias, spoken):
    home = HomeModel(
        areas=[Area("explicit", "Guest Area", (alias,)), Area("satellite", "Other Room")],
        entities=[
            Entity(
                f"light.{room}", "Lampa", room, labels=frozenset({"voice_default_on"}),
                exposed=True, state="off",
            )
            for room in ("explicit", "satellite")
        ],
        satellites={"assist_satellite.lab": "satellite"},
    )
    result = await Engine(lambda: home).process(
        Request(f"zapal światło w {spoken}", satellite_id="assist_satellite.lab", dry_run=True)
    )
    assert result.status == "dry_run"
    assert result.targets == ("light.explicit",)


async def test_inflected_alias_collision_refuses_to_guess_a_room():
    home = HomeModel(
        areas=[Area("a", "Annex", ("aneks",)), Area("b", "Other Room", ("aneksie",))],
        entities=[Entity("light.other", "Lampa", "b", exposed=True, state="off")],
    )
    result = await Engine(lambda: home).process(
        Request("wyłącz światło w aneksie", source_area="b", dry_run=True)
    )
    assert result.status == "clarify"
    assert result.reason == "ambiguous_location"
    assert not result.calls


def test_room_forms_do_not_rewrite_device_aliases():
    home = HomeModel(entities=[Entity("light.demo", "Pokój gościnny", None)])
    assert home.location_key("pokoju gościnnym") == home.location_key("pokój gościnny")
    assert home.key("pokoju gościnnym") not in home.entity_index
    assert home.key("pokój gościnny") in home.entity_index
