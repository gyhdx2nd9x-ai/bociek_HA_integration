"""Source identity, room precedence and shared music/audiobook target planning."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.music import MusicCommand, music_health, plan_music
from custom_components.polish_context_voice.core.types import Request


@pytest.fixture
def setup():
    home = HomeModel(
        areas=[Area("a", "Salon"), Area("b", "Kuchnia", ("kuchni",))],
        entities=[
            Entity("assist_satellite.a", "Satelita", "a", device_id="device-a", state="idle"),
            Entity(
                "media_player.raw",
                "Głośnik",
                "a",
                device_id="device-a",
                exposed=True,
                state="idle",
                attributes={"supported_features": 65535},
            ),
            Entity(
                "media_player.ma",
                "Dowolna nazwa MA",
                None,
                device_id="ma-device",
                exposed=True,
                state="idle",
                platform="music_assistant",
                config_entry_id="ma",
                unique_id="mass_media_player.raw",
                attributes={"supported_features": 65535},
            ),
            Entity(
                "media_player.other",
                "Inny",
                "a",
                device_id="other",
                exposed=True,
                state="idle",
                attributes={"supported_features": 65535},
            ),
        ],
        satellites={"assist_satellite.a": "a"},
        devices={"device-a": "a"},
    )
    settings = {
        "music_shortcuts": {
            "s": {
                "name": "Skrót",
                "backend": "home_assistant",
                "media": {
                    "media_content_id": "media-source://test/audio",
                    "media_content_type": "music",
                },
            }
        }
    }
    request = Request(
        "", satellite_id="assist_satellite.a", device_id="device-a", conversation_id="c"
    )
    return home, settings, request


def plan(setup, **changes):
    home, settings, request = setup
    return plan_music(MusicCommand("start", "s", **changes), request, home, settings)


def test_room_assigned_speaker_beats_satellite_player(setup):
    result = plan(setup)
    assert result.status == "ready" and result.targets == ("media_player.other",)
    assert result.area_ids == ("a",)


@pytest.mark.parametrize("kind", ["track", "audiobook"])
def test_ma_shortcut_finds_identity_link_without_duplicate_room_assignment(setup, kind):
    home, settings, _ = setup
    shortcut = settings["music_shortcuts"]["s"]
    shortcut.update(backend="music_assistant", config_entry_id="ma")
    shortcut["media"] = {"media_content_id": f"library://{kind}/1", "media_content_type": kind}
    result = plan(setup)
    assert result.status == "ready" and result.targets == ("media_player.ma",)
    assert result.area_ids == ("a",)
    assert home.entities["media_player.ma"].area_id is None


def test_default_shortcut_with_empty_player_and_health(setup):
    home, settings, _ = setup
    settings["music_rooms"] = {"a": {"player": "", "default_shortcut": "s"}}
    result = plan_music(MusicCommand("start"), setup[2], home, settings)
    assert result.targets == ("media_player.other",)
    assert not music_health(home, settings)


def test_room_default_wins_over_satellite(setup):
    setup[1]["music_rooms"] = {"a": {"player": "media_player.other"}}
    assert plan(setup).targets == ("media_player.other",)


def test_invalid_configured_default_does_not_fall_back(setup):
    setup[1]["music_rooms"] = {"a": {"player": "media_player.deleted"}}
    result = plan(setup)
    assert result.reason == "missing_music_player" and not result.calls


def test_explicit_other_room_never_plays_here(setup):
    result = plan(setup, location="kuchni")
    assert result.reason == "music_satellite_wrong_area" and not result.calls


def test_explicit_named_player_wins(setup):
    assert plan(setup, player_name="Inny").targets == ("media_player.other",)


def test_area_assigned_ha_player_is_found_without_room_configuration(setup):
    home, settings, request = setup
    home.entities["media_player.other"] = replace(
        home.entities["media_player.other"], area_id="b"
    )
    result = plan_music(
        MusicCommand("start", "s", location="kuchni"), request, home, settings
    )
    assert result.status == "ready"
    assert result.targets == ("media_player.other",)
    assert result.area_ids == ("b",)


def test_area_assigned_music_assistant_player_is_found_without_satellite_link(setup):
    home, settings, request = setup
    # Music Assistant's proxy has no Area, while its linked HA player does.
    home.entities["media_player.raw"] = replace(home.entities["media_player.raw"], area_id="b")
    settings["music_shortcuts"]["s"].update(
        backend="music_assistant",
        config_entry_id="ma",
        media={"media_content_id": "library://track/42", "media_content_type": "track"},
    )
    result = plan_music(
        MusicCommand(
            "start", "s", location="kuchni", backend="music_assistant", config_entry_id="ma"
        ),
        request,
        home,
        settings,
    )
    assert result.status == "ready"
    assert result.targets == ("media_player.ma",)
    assert result.area_ids == ("b",)


def test_ma_home_assistant_provider_id_inherits_area_from_source_entity(setup):
    home, settings, request = setup
    home.entities["media_player.raw"] = replace(home.entities["media_player.raw"], area_id="b")
    settings["music_shortcuts"]["s"].update(
        backend="music_assistant",
        config_entry_id="ma",
        media={"media_content_id": "library://track/43", "media_content_type": "track"},
    )

    result = plan_music(
        MusicCommand(
            "start", "s", location="kuchni", backend="music_assistant", config_entry_id="ma"
        ),
        request,
        home,
        settings,
    )

    assert result.status == "ready"
    assert result.targets == ("media_player.ma",)
    assert result.area_ids == ("b",)


def test_ma_entity_inherits_effective_device_area_when_device_is_assigned(setup):
    home, settings, request = setup
    home.devices["ma-device"] = "b"
    home.entities["media_player.ma"] = replace(
        home.entities["media_player.ma"], unique_id="native-ma-player"
    )
    settings["music_shortcuts"]["s"].update(
        backend="music_assistant",
        config_entry_id="ma",
        media={"media_content_id": "library://track/44", "media_content_type": "track"},
    )

    result = plan_music(
        MusicCommand(
            "start", "s", location="kuchni", backend="music_assistant", config_entry_id="ma"
        ),
        request,
        home,
        settings,
    )

    assert result.status == "ready" and result.targets == ("media_player.ma",)
    assert result.area_ids == ("b",)


def test_provider_identity_area_overrides_music_assistant_device_area(setup):
    home, settings, request = setup
    home.devices["ma-device"] = "a"
    home.entities["media_player.raw"] = replace(
        home.entities["media_player.raw"], area_id="b"
    )
    settings["music_shortcuts"]["s"].update(
        backend="music_assistant",
        config_entry_id="ma",
        media={"media_content_id": "library://track/45", "media_content_type": "track"},
    )

    result = plan_music(
        MusicCommand(
            "start", "s", location="kuchni", backend="music_assistant", config_entry_id="ma"
        ),
        request,
        home,
        settings,
    )

    assert result.status == "ready" and result.targets == ("media_player.ma",)
    assert result.area_ids == ("b",)


def test_ambiguous_area_players_require_a_room_default(setup):
    home, settings, request = setup
    home.entities["media_player.other"] = replace(
        home.entities["media_player.other"], area_id="b"
    )
    home.entities["media_player.second"] = replace(
        home.entities["media_player.other"], id="media_player.second", name="Drugi głośnik"
    )
    result = plan_music(
        MusicCommand("start", "s", location="kuchni"), request, home, settings
    )
    assert result.reason == "ambiguous_room_music_player"
    assert not result.calls


def test_area_player_is_used_if_satellites_linked_player_is_assigned_elsewhere(setup):
    home = setup[0]
    home.entities["media_player.raw"] = replace(
        home.entities["media_player.raw"], area_id="b"
    )
    result = plan(setup)
    assert result.status == "ready" and result.targets == ("media_player.other",)
    assert result.area_ids == ("a",)


@pytest.mark.parametrize("identity", ["mismatch", "missing", "device_only", "text_only"])
def test_source_identity(setup, identity):
    home, settings, request = setup
    changes = {
        "mismatch": {"device_id": "other"},
        "missing": {"satellite_id": "assist_satellite.nonexistent"},
        "device_only": {"satellite_id": None},
        "text_only": {"satellite_id": None, "device_id": None, "source_area": "a"},
    }[identity]
    result = plan_music(MusicCommand("start", "s"), replace(request, **changes), home, settings)
    if identity == "device_only":
        assert result.targets == ("media_player.other",)
    else:
        assert not result.calls


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"exposed": False}, "music_player_hidden"),
        ({"available": False}, "music_player_unavailable"),
        ({"labels": frozenset({"voice_block"})}, "music_player_hidden"),
        ({"labels": frozenset({"voice_high_risk"})}, "high_risk_disabled"),
        ({"attributes": {"supported_features": 0}}, "music_capability"),
    ],
)
def test_auto_player_keeps_guards(setup, change, reason):
    home = setup[0]
    home.entities["media_player.other"] = replace(home.entities["media_player.other"], area_id="b")
    home.entities["media_player.raw"] = replace(home.entities["media_player.raw"], **change)
    result = plan(setup)
    assert result.reason == reason and not result.calls


def test_multiple_same_device_players_require_choice_of_default(setup):
    home = setup[0]
    home.entities["media_player.extra"] = replace(
        home.entities["media_player.raw"], id="media_player.extra"
    )
    result = plan(setup)
    assert result.reason == "ambiguous_room_music_player" and not result.calls


def test_ma_without_identity_link_never_matches_by_name_or_room(setup):
    home = setup[0]
    home.entities["media_player.ma"] = replace(
        home.entities["media_player.ma"], unique_id="unrelated"
    )
    result = plan(setup, backend="music_assistant")
    assert result.reason == "music_satellite_ma_missing" and not result.calls


def test_ma_on_same_device_supported(setup):
    home = setup[0]
    home.entities["media_player.ma"] = replace(
        home.entities["media_player.ma"], unique_id="native-id", device_id="device-a"
    )
    assert plan(setup, backend="music_assistant").targets == ("media_player.ma",)


@pytest.mark.parametrize("operation", ["media_next_track", "media_pause", "media_stop"])
def test_controls_follow_active_satellite_ma_even_without_area_on_proxy(setup, operation):
    home, settings, request = setup
    home.entities["media_player.ma"] = replace(home.entities["media_player.ma"], state="playing")
    result = plan_music(MusicCommand(operation, contextual=True), request, home, settings)
    assert result.status == "ready" and result.targets == ("media_player.ma",)


def test_already_playing_still_requires_confirmation(setup):
    home = setup[0]
    home.entities["media_player.other"] = replace(home.entities["media_player.other"], area_id="b")
    home.entities["media_player.raw"] = replace(home.entities["media_player.raw"], state="playing")
    assert plan(setup).status == "confirm"
