from types import SimpleNamespace

from custom_components.polish_context_voice.core.vacuum import mapped_rooms, parse

ROOMS = {"1": "Kuchnia", "2": "Korytarz", "3": "Salon"}


def test_general_cleaning_uses_whole_map_default():
    assert parse("Alexa, posprzątaj!", ROOMS).room is None
    assert parse("odkurz", ROOMS).room is None
    assert parse("uruchom odkurzacz", ROOMS).room is None


def test_named_cleaning_resolves_only_mapped_room_and_keeps_default_mode():
    assert parse("odkurz kuchnię", ROOMS).room == "1"
    assert parse("posprzątaj w korytarzu", ROOMS).room == "2"
    assert parse("uruchom odkurzacz w salonie", ROOMS).room == "3"


def test_common_polish_room_inflections_use_shared_lightweight_morphology():
    rooms = {"7": "Sypialnia", "8": "Łazienka"}
    assert parse("odkurz sypialnię", rooms).room == "7"
    assert parse("posprzątaj łazienkę", rooms).room == "8"


def test_unknown_room_is_not_broadened_to_whole_map():
    assert parse("odkurz sypialnię", ROOMS).room == ""


def test_unrelated_commands_do_not_match():
    assert parse("włącz światła w kuchni", ROOMS) is None
    assert parse("czy odkurzacz pracuje", ROOMS) is None


def test_only_rooms_from_selected_map_are_exposed_to_parser():
    state = SimpleNamespace(
        attributes={
            "selected_map": "Mapa 1",
            "rooms": {
                "Mapa 1": [{"id": 1, "name": "Kuchnia"}, {"id": 3, "name": "Salon"}],
                "Mapa 2": [{"id": 9, "name": "Sypialnia"}],
            },
        }
    )
    assert mapped_rooms(state) == {"1": "Kuchnia", "3": "Salon"}
