from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.home_model.health import ROLES, check
from custom_components.polish_context_voice.core.home_model.role_catalog import (
    CATALOG,
    GUARD_ROLES,
    allowed_roles,
    suggest_lights,
)


def light(name, **kwargs):
    return Entity("light." + name, name, "dowolny_pokoj", exposed=True, **kwargs)


def test_catalog_covers_runtime_roles():
    assert set(CATALOG) == ROLES
    assert allowed_roles("switch") == GUARD_ROLES | {"voice_safe_all"}
    assert "voice_routine" in allowed_roles("scene")
    assert "voice_all_off" not in allowed_roles("sensor")
    assert "voice_primary" in allowed_roles("weather")
    assert "voice_all_off" in allowed_roles("sensor", {"voice_all_off"})


def test_single_light_suggested_but_multiple_on_targets_never_guessed():
    assert suggest_lights([light("a")]) == {
        "voice_default_on": ["light.a"],
        "voice_all_off": ["light.a"],
    }
    assert suggest_lights([light("a"), light("b")]) == {
        "voice_default_on": [],
        "voice_all_off": ["light.a", "light.b"],
    }


@pytest.mark.parametrize("guard", ["voice_block", "voice_exclude_all", "voice_high_risk"])
def test_suggestions_do_not_add_guarded_lights(guard):
    suggested = suggest_lights([light("a"), light("b", labels=frozenset({guard}))])
    assert suggested["voice_all_off"] == ["light.a"]


def test_suggestions_keep_configured_on_set_and_skip_hidden_unavailable():
    a = light("a", labels=frozenset({"voice_default_on"}))
    b = light("b", labels=frozenset({"voice_main"}))
    hidden = replace(light("hidden"), exposed=False)
    offline = light("offline", available=False)
    result = suggest_lights([a, b, hidden, offline])
    assert result == {"voice_default_on": ["light.a"], "voice_all_off": ["light.a", "light.b"]}
    assert a.labels == {"voice_default_on"}


def test_explicit_main_role_is_the_basis_of_suggestion():
    assert suggest_lights(
        [
            light("a", labels=frozenset({"voice_main"})),
            light("b"),
        ]
    )["voice_default_on"] == ["light.a"]


def test_optional_roles_are_information_not_faults(home):
    entity = light("new")
    model = HomeModel(home.areas.values(), entities=[entity])
    findings = check(model)
    missing = [f for f in findings if f["code"] == "missing_semantic_labels"]
    assert len(missing) == 1 and missing[0]["severity"] == "info" and missing[0]["new"]
    hidden = HomeModel(entities=[replace(entity, exposed=False)])
    assert check(hidden) == []
