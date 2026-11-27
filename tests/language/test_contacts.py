from dataclasses import replace
from datetime import time

import pytest

from custom_components.polish_context_voice.core.capabilities import plan_capability
from custom_components.polish_context_voice.core.communication import response_speech
from custom_components.polish_context_voice.core.contacts import contact_speech
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.nlu.capabilities import parse_capability
from custom_components.polish_context_voice.core.types import Request, Result


def contact(state="off", **kwargs):
    return replace(
        Entity(
            "binary_sensor.window",
            "Okno przy stole",
            "living_room",
            device_class="window",
            exposed=True,
            state=state,
        ),
        **kwargs,
    )


@pytest.mark.parametrize("noun,kind", [("okna", "window"), ("drzwi", "door")])
@pytest.mark.parametrize(
    "words,query",
    [
        ("Czy {noun} są zamknięte?", "closed"),
        ("Czy wszystkie {noun} są zamknięte?", "closed"),
        ("Czy {noun} są otwarte?", "open"),
        ("Czy wszystkie {noun} są otwarte?", "all_open"),
        ("Które {noun} są zamknięte?", "which_closed"),
        ("Które {noun} są otwarte?", "which_open"),
    ],
)
def test_contact_questions_preserve_requested_state(noun, kind, words, query):
    cap = parse_capability(words.format(noun=noun))
    assert (cap.operation, cap.domain, cap.kind, cap.query) == (
        "state",
        "binary_sensor",
        kind,
        query,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Czy okna są zamknięte i wyłącz światła",
        "Nie pytam czy okna są zamknięte",
        "Czy drzwi są zamknięte na klucz",
        "Czy okna są zamknięte albo otwarte",
        "Zamknij okna",
    ],
)
def test_contact_query_never_discards_qualifiers_or_becomes_control(text):
    assert parse_capability(text) is None


@pytest.mark.parametrize(
    "query,states,expected",
    [
        ("closed", ["off"], "Tak, sprawdzone okna są zamknięte."),
        ("closed", ["on", "off"], "Nie, otwarte: Okno 0."),
        (
            "closed",
            ["off", "unknown"],
            "Okna z dostępnym odczytem są zamknięte. Nie mam aktualnego odczytu: Okno 1.",
        ),
        (
            "closed",
            ["on", "unavailable"],
            "Nie, otwarte: Okno 0. Nie mam aktualnego odczytu: Okno 1.",
        ),
        ("closed", ["unknown", "unavailable"], "Nie mam aktualnego odczytu: Okno 0 i Okno 1."),
        ("open", ["off"], "Sprawdzone okna są zamknięte."),
        ("open", ["on", "off"], "Otwarte: Okno 0."),
        ("which_closed", ["on", "off"], "Zamknięte: Okno 1."),
        (
            "which_closed",
            ["on", "unknown"],
            "Okna z dostępnym odczytem są otwarte. Nie mam aktualnego odczytu: Okno 1.",
        ),
        ("which_open", ["on", "unknown"], "Otwarte: Okno 0. Nie mam aktualnego odczytu: Okno 1."),
        ("all_open", ["on", "on"], "Tak, sprawdzone okna są otwarte."),
        ("all_open", ["on", "off"], "Nie, zamknięte: Okno 1."),
        (
            "all_open",
            ["on", "unknown"],
            "Okna z dostępnym odczytem są otwarte. Nie mam aktualnego odczytu: Okno 1.",
        ),
    ],
)
def test_truthful_contact_speech(query, states, expected):
    entities = tuple(contact(state, name=f"Okno {i}") for i, state in enumerate(states))
    assert contact_speech("window", query, entities) == expected


@pytest.mark.parametrize("state,available", [("off", False), ("closed", True), ("", True)])
def test_only_available_ha_off_means_closed(state, available):
    speech = contact_speech("window", "closed", (contact(state, available=available),))
    assert speech == "Nie mam aktualnego odczytu: Okno przy stole."


def test_door_state_does_not_claim_lock_status_and_empty_is_not_all_closed():
    assert contact_speech("door", "closed", (contact(),)) == ("Tak, sprawdzone drzwi są zamknięte.")
    assert contact_speech("window", "closed", ()) == (
        "Nie mam dostępnych czujników, żeby sprawdzić okna."
    )


def test_many_contacts_bound_speech_but_never_hide_unknowns():
    entities = tuple(contact("on", name=f"Okno {i}") for i in range(8)) + (
        contact("unknown", name="Okno bez odczytu"),
    )
    speech = contact_speech("window", "closed", entities)
    assert "oraz jeszcze 3" in speech and "Okno 5" not in speech
    assert "Nie mam aktualnego odczytu: Okno bez odczytu." in speech


def test_contact_scope_exposure_device_class_and_unavailable():
    entries = (
        contact(),
        contact("unknown", id="binary_sensor.unknown"),
        contact(id="binary_sensor.kitchen", area_id="kitchen"),
        contact(id="binary_sensor.hidden", exposed=False),
        contact(id="binary_sensor.blocked", labels=frozenset({"voice_block"})),
        contact(id="binary_sensor.excluded", labels=frozenset({"voice_exclude_all"})),
        contact(id="binary_sensor.lock", device_class="lock"),
        contact(id="binary_sensor.generic", device_class="opening"),
        contact(id="binary_sensor.door", device_class="door"),
    )
    model = HomeModel(
        [Area("living_room", "Salon", ("salon",)), Area("kitchen", "Kuchnia", ("kuchnia",))],
        entities=entries,
        satellites={"assist_satellite.test": "living_room"},
    )
    request = Request("Czy okna są zamknięte?", satellite_id="assist_satellite.test")
    result = plan_capability(parse_capability(request.text), request, model)
    assert set(result.targets) == {entries[0].id, entries[1].id}
    assert result.area_ids == ("living_room",) and not result.calls
    cap = parse_capability("Czy okna są zamknięte w kuchni?")
    result = plan_capability(cap, request, model)
    assert result.targets == (entries[2].id,) and result.area_ids == ("kitchen",)
    cap = parse_capability("Czy okna są zamknięte w garażu?")
    result = plan_capability(cap, request, model)
    assert result.reason == "unknown_location" and not result.targets
    cap = parse_capability(request.text)
    assert plan_capability(cap, replace(request, satellite_id=None), model).reason == "missing_area"
    visible = model.visible_to(lambda identifier: identifier != entries[0].id)
    assert plan_capability(cap, request, visible).targets == (entries[1].id,)
    hidden = model.visible_to(lambda _: False)
    result = plan_capability(cap, request, hidden)
    assert result.reason == "no_targets" and "czujników okien" in result.speech


def test_contact_answer_remains_audible_during_quiet_hours():
    request = Request("Czy okna są zamknięte?", satellite_id="assist_satellite.test")
    model = HomeModel(satellites={request.satellite_id: "living_room"})
    result = Result("answered", "Tak, sprawdzone okna są zamknięte.", route="ha_native")
    assert (
        response_speech(
            result,
            request,
            model,
            {"quiet_ack_enabled": True},
            time(22),
        )
        == result.speech
    )
