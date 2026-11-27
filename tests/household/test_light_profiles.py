import pytest

from custom_components.polish_context_voice.core.home_model.light_profiles import (
    DEFAULT_PROFILES,
    PROFILE_ROLES,
    apply_profile,
    profiles_for,
    valid_profile,
)


def test_default_recipes_keep_main_and_default_on_explicit():
    assert DEFAULT_PROFILES["main"] == {"voice_main", "voice_default_on", "voice_all_off"}
    for key in ("task", "ambient"):
        assert "voice_all_off" in DEFAULT_PROFILES[key]
        assert "voice_default_on" not in DEFAULT_PROFILES[key]


def test_custom_recipe_is_room_specific_and_invalid_saved_data_falls_back():
    options = {
        "voice_room_presets": {"any_room_id": {"main": ["voice_main"], "accent": ["voice_block"]}}
    }
    assert profiles_for(options, "any_room_id")["main"] == {"voice_main"}
    assert profiles_for(options, "another_home_room") == DEFAULT_PROFILES
    assert profiles_for(options, "any_room_id")["ambient"] == DEFAULT_PROFILES["ambient"]


@pytest.mark.parametrize(
    "roles",
    [[], ["voice_block"], ["voice_confirm"], ["voice_voice_default_on"], "voice_main", None],
)
def test_recipes_cannot_grant_guard_roles_or_accept_malformed_values(roles):
    assert not valid_profile(roles)


def test_add_recipe_preserves_existing_roles_and_other_entities():
    current = {
        "light.a": frozenset({"voice_ambient", "voice_confirm"}),
        "light.b": frozenset({"voice_task"}),
    }
    desired = apply_profile(current, ["light.a"], DEFAULT_PROFILES["main"])
    assert desired == {"light.a": current["light.a"] | DEFAULT_PROFILES["main"]}
    assert current["light.b"] == {"voice_task"}


def test_replace_recipe_only_replaces_six_lighting_roles():
    current = {"light.a": PROFILE_ROLES | {"voice_confirm", "voice_safe_all"}}
    assert apply_profile(current, ["light.a"], DEFAULT_PROFILES["task"], replace=True) == {
        "light.a": DEFAULT_PROFILES["task"] | {"voice_confirm", "voice_safe_all"}
    }


@pytest.mark.parametrize("selection", [[], ["light.other_room"]])
def test_empty_or_out_of_scope_targets_cannot_apply_recipe(selection):
    with pytest.raises(ValueError):
        apply_profile({"light.here": frozenset()}, selection, DEFAULT_PROFILES["main"])


def test_legacy_recipe_is_read_without_rewriting_and_ambient_override_wins():
    options = {"voice_room_presets": {"room": {"accent": ["voice_accent"]}}}
    assert profiles_for(options, "room")["ambient"] == {"voice_ambient"}
    assert options["voice_room_presets"]["room"] == {"accent": ["voice_accent"]}
    options["voice_room_presets"]["room"]["ambient"] = ["voice_ambient", "voice_all_off"]
    assert profiles_for(options, "room")["ambient"] == {"voice_ambient", "voice_all_off"}
    assert set(profiles_for(options, "room")) == {"main", "task", "ambient"}
