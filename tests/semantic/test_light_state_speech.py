"""State answers may describe readable HA entities, never infer missing readings."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.home_model import Entity
from custom_components.polish_context_voice.core.state_queries import light_state_speech


def light(state="off", **kwargs):
    return replace(Entity("light.table", "Nad stołem", "living_room", state=state), **kwargs)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("all_off", "Tak, sprawdzone światła są wyłączone."),
        ("any_on", "Nie, sprawdzone światła są wyłączone."),
    ],
)
def test_all_known_off_answers_only_the_selected_readable_set(query, expected):
    assert (
        light_state_speech(query, (light(), light(id="light.other", name="Przy sofie"))) == expected
    )


@pytest.mark.parametrize("query", ["all_off", "any_on", "which_on"])
def test_on_light_is_listed_without_claiming_all_other_lights_off(query):
    entities = (light("on"), light(id="light.sofa", name="Przy sofie"))
    speech = light_state_speech(query, entities)
    assert "Nad stołem" in speech and "Przy sofie" not in speech
    assert "włączone" in speech.lower()
    if query == "all_off":
        assert speech.startswith("Nie,") and "Tak," not in speech


@pytest.mark.parametrize("query", ["all_off", "any_on", "which_on"])
@pytest.mark.parametrize(
    "state,available",
    [("unknown", True), ("unavailable", True), ("off", False), ("", True), ("0", True)],
)
def test_missing_or_invalid_reading_never_becomes_off(query, state, available):
    speech = light_state_speech(query, (light(state, available=available),))
    assert "Nie mam aktualnego odczytu" in speech and "Nad stołem" in speech
    assert "wyłączone" not in speech and not speech.startswith("Tak,")


@pytest.mark.parametrize("query", ["all_off", "any_on", "which_on"])
def test_known_off_and_unknown_have_qualified_reading_not_universal_assertion(query):
    speech = light_state_speech(
        query,
        (light(), light("unknown", id="light.other", name="Przy sofie")),
    )
    assert "z dostępnym odczytem" in speech
    assert "Nie mam aktualnego odczytu: Przy sofie." in speech
    assert not speech.startswith(("Tak,", "Nie, sprawdzone"))


@pytest.mark.parametrize("query", ["all_off", "any_on", "which_on"])
def test_on_and_unknown_report_both_without_guessing_unknown(query):
    speech = light_state_speech(
        query,
        (light("on"), light("unknown", id="light.other", name="Przy sofie")),
    )
    assert "Nad stołem" in speech and "Nie mam aktualnego odczytu: Przy sofie." in speech
    assert "wyłączone" not in speech


@pytest.mark.parametrize("query", ["all_off", "any_on", "which_on"])
def test_empty_selection_never_means_all_off(query):
    speech = light_state_speech(query, ())
    assert speech and "sprawdzić" in speech
    assert "wyłączone" not in speech and "Tak," not in speech


def test_large_list_is_bounded_and_unknowns_remain_visible():
    entities = tuple(light("on", id=f"light.item_{i}", name=f"Lampa {i}") for i in range(12)) + (
        light("unknown", id="light.no_reading", name="Bez odczytu"),
    )
    speech = light_state_speech("which_on", entities)
    assert "Lampa 7" in speech and "Lampa 8" not in speech
    assert "oraz jeszcze 4" in speech
    assert "Nie mam aktualnego odczytu: Bez odczytu." in speech


def test_group_uses_its_own_ha_state_without_resolving_or_disclosing_members():
    group = light("off", attributes={"entity_id": ["light.hidden_member", "light.missing_member"]})
    assert light_state_speech("all_off", (group,)) == "Tak, sprawdzone światła są wyłączone."
    unknown_group = replace(group, state="unknown")
    speech = light_state_speech("all_off", (unknown_group,))
    assert "Nie mam aktualnego odczytu: Nad stołem." in speech
    assert "hidden_member" not in speech and "missing_member" not in speech
    assert "wyłączone" not in speech


def test_entity_names_are_bounded_without_mutating_cached_entities():
    entity = light("on", name="X" * 500)
    speech = light_state_speech("which_on", (entity,))
    assert "X" * 100 in speech and "X" * 101 not in speech
    assert entity.name == "X" * 500
