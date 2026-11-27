from dataclasses import replace
from datetime import time

import pytest

from custom_components.polish_context_voice.core.capabilities import plan_capability
from custom_components.polish_context_voice.core.communication import response_speech
from custom_components.polish_context_voice.core.home_model import Area, Entity, Floor, HomeModel
from custom_components.polish_context_voice.core.maintenance import (
    maintenance_report,
    valid_threshold,
)
from custom_components.polish_context_voice.core.nlu.capabilities import parse_capability
from custom_components.polish_context_voice.core.types import Request, Result


def battery(state="10", **kwargs):
    return replace(
        Entity(
            "sensor.battery",
            "Bateria okna",
            "living_room",
            exposed=True,
            state=state,
            device_class="battery",
            attributes={"unit_of_measurement": "%"},
        ),
        **kwargs,
    )


def report(*entities, kind="health", threshold=20):
    return maintenance_report(kind, entities, threshold=threshold)


def test_report_identifies_room_from_registry_without_guessing_declension():
    speech, data = maintenance_report(
        "battery", [battery()], area_names={"living_room": "Pracownia Zosi"}
    )
    assert "pomieszczenie: Pracownia Zosi" in speech
    assert data["groups"][0]["area_ids"] == ["living_room"]
    assert "10 procent" in speech
    speech, _ = maintenance_report("battery", [battery(area_id=None)], area_names={})
    assert "bez przypisanego pomieszczenia" in speech


@pytest.mark.parametrize(
    "text,kind,location",
    [
        ("Czy wszystkie urządzenia są sprawne?", "health", None),
        ("Czy jakieś urządzenie ma problem?", "health", None),
        ("Czy trzeba gdzieś wymienić baterie?", "battery", None),
        ("Czy wszystkie urządzenia działają w salonie?", "health", "salonie"),
        ("Czy są jakieś problemy z urządzeniami w całym domu?", "health", "calym domu"),
        ("Które urządzenia mają słabą baterię w kuchni?", "battery", "kuchni"),
        ("Sprawdź baterie na parterze", "battery", "parterze"),
        ("Czy jakaś bateria jest słaba?", "battery", None),
        ("Czy urządzenia działają poprawnie?", "health", None),
        ("Gdzie trzeba wymienić baterie?", "battery", None),
    ],
)
def test_maintenance_queries(text, kind, location):
    cap = parse_capability(text)
    assert (cap.operation, cap.kind, cap.location) == ("maintenance", kind, location)


@pytest.mark.parametrize(
    "text",
    [
        "Czy wszystkie urządzenia są sprawne i włącz światła",
        "Nie sprawdzaj baterii",
        "Sprawdź baterie i wymień je",
        "Czy jakieś urządzenie ma problem czy nie wiem",
        "Wymień baterie",
    ],
)
def test_maintenance_never_discards_extra_commands(text):
    assert parse_capability(text) is None


def test_global_by_default_explicit_area_floor_and_unknown_location():
    living = battery()
    kitchen = battery(id="sensor.kitchen", area_id="kitchen")
    orphan = battery(id="sensor.orphan", area_id=None)
    model = HomeModel(
        [
            Area("living_room", "Salon", ("salon",), "ground"),
            Area("kitchen", "Kuchnia", ("kuchnia",), "upper"),
        ],
        [Floor("ground", "Parter", ("parter",))],
        [living, kitchen, orphan],
        satellites={"assist_satellite.test": "living_room"},
    )
    req = Request(
        "Sprawdź baterie", source_area="living_room", satellite_id="assist_satellite.test"
    )
    result = plan_capability(parse_capability(req.text), req, model)
    assert set(result.targets) == {living.id, kitchen.id, orphan.id} and not result.area_ids
    assert result.status == "ready" and not result.calls
    for text, targets in [
        ("Sprawdź baterie w kuchni", (kitchen.id,)),
        ("Sprawdź baterie na parterze", (living.id,)),
    ]:
        assert plan_capability(parse_capability(text), req, model).targets == targets
    for place in ("domu", "całym domu"):
        assert (
            plan_capability(parse_capability(f"Sprawdź baterie w {place}"), req, model).targets
            == result.targets
        )
    bad = plan_capability(parse_capability("Sprawdź baterie w garażu"), req, model)
    assert bad.reason == "unknown_location" and not bad.targets


def test_exposure_permissions_exclusion_and_no_implicit_device_siblings():
    low = battery(device_id="device")
    hidden = battery(id="sensor.hidden", exposed=False, device_id="device")
    blocked = battery(id="sensor.blocked", labels=frozenset({"voice_block"}))
    excluded = battery(id="sensor.excluded", labels=frozenset({"voice_exclude_all"}))
    denied = battery(id="sensor.denied")
    model = HomeModel(entities=[low, hidden, blocked, excluded, denied]).visible_to(
        lambda eid: eid != denied.id
    )
    req = Request("Sprawdź baterie")
    result = plan_capability(parse_capability(req.text), req, model)
    assert result.targets == (low.id,)
    empty = plan_capability(parse_capability(req.text), req, model.visible_to(lambda _: False))
    assert empty.status == "clarify" and empty.reason == "no_maintenance_sources"


@pytest.mark.parametrize(
    "state,code",
    [
        ("0", "low_battery"),
        ("19.9", "low_battery"),
        ("20", "low_battery"),
        ("20.01", None),
        ("100", None),
        ("-1", "invalid_battery_reading"),
        ("101", "invalid_battery_reading"),
        ("nan", "invalid_battery_reading"),
        ("inf", "invalid_battery_reading"),
        ("", "invalid_battery_reading"),
        ("unknown", "unknown"),
        ("unavailable", "unavailable"),
    ],
)
def test_numeric_battery_boundaries_and_missing_readings(state, code):
    speech, data = report(battery(state), kind="battery")
    if code:
        assert data["groups"][0]["findings"][0]["code"] == code
        assert "nie widzę słabych" not in speech
    else:
        assert not data["groups"] and "nie widzę słabych baterii" in speech


@pytest.mark.parametrize("unit", [None, "V", "mV", ""])
def test_battery_voltage_or_unknown_unit_is_not_treated_as_percent(unit):
    _, data = report(battery("3", attributes={"unit_of_measurement": unit}))
    assert data["groups"][0]["findings"][0]["code"] == "invalid_battery_reading"


@pytest.mark.parametrize(
    "kind,state,code",
    [
        ("battery", "on", "low_battery"),
        ("battery", "off", None),
        ("problem", "on", "problem"),
        ("problem", "off", None),
        ("connectivity", "off", "disconnected"),
        ("connectivity", "on", None),
        ("connectivity", "unknown", "unknown"),
        ("battery", "low", "unknown"),
        ("battery_charging", "off", None),
        ("window", "on", None),
    ],
)
def test_binary_classes_have_distinct_polarity(kind, state, code):
    e = battery(state, id="binary_sensor.test", device_class=kind)
    _, data = report(e)
    assert (
        ([f["code"] for g in data["groups"] for f in g["findings"]] == [code])
        if code
        else not data["groups"]
    )


def test_general_availability_does_not_mean_off_is_broken():
    lamp = Entity("light.test", "Lampka", None, exposed=True, state="off")
    assert not report(lamp)[1]["groups"]
    assert report(replace(lamp, state="unavailable", available=False))[1]["missing_readings"] == 1
    speech, _ = report(lamp)
    assert "wszystkie" not in speech and "sprawne" not in speech
    assert "Nie mam danych" in report()[0]
    _, data = report(battery("10", available=False))
    assert data["groups"][0]["findings"][0]["code"] == "unknown"


def test_device_grouping_and_separate_devices_with_identical_names():
    low = battery(device_id="a", device_name="Czujnik okna")
    binary = battery("on", id="binary_sensor.battery", device_id="a", device_class="battery")
    problem = battery("on", id="binary_sensor.problem", device_id="a", device_class="problem")
    second = battery(id="sensor.second", device_id="b", device_name="Czujnik okna")
    speech, data = report(low, binary, problem, second)
    assert data["groups_with_findings"] == 2
    assert len(data["groups"][0]["findings"]) == 3
    assert speech.count("słaba bateria") == 2 and "10 procent" in speech
    assert "wymień" not in speech  # Rechargeable vs replaceable is unknown.


def test_bounded_speech_preserves_full_report_and_mentions_omitted_unknowns():
    entities = tuple(battery(id=f"sensor.low_{i}", name=f"Bateria {i}") for i in range(6))
    speech, data = report(*entities, battery("unknown", id="sensor.last", name="Nieznany"))
    assert data["groups_with_findings"] == 7 and data["missing_readings"] == 1
    assert "jeszcze 2" in speech and "brakuje wiarygodnych odczytów: 1" in speech
    assert len(data["groups"]) == 7 and "Nieznany" not in speech


@pytest.mark.parametrize("value", [0, 51, -1, "20", True, None, float("nan"), float("inf"), 20.5])
def test_invalid_threshold_falls_back_to_20(value):
    assert not valid_threshold(value)
    _, data = report(battery("20"), threshold=value)
    assert data["battery_threshold_percent"] == 20 and data["groups"]


@pytest.mark.parametrize("value", [1, 20, 50, 30.0])
def test_threshold_is_configurable_and_inclusive(value):
    assert valid_threshold(value)
    _, data = report(battery(str(value)), threshold=value)
    assert data["groups"] and data["battery_threshold_percent"] == value
    assert not report(battery(str(value + 0.1)), threshold=value)[1]["groups"]


def test_maintenance_not_silenced_at_night():
    request = Request("Sprawdź baterie", satellite_id="assist_satellite.test")
    model = HomeModel(satellites={request.satellite_id: "living_room"})
    result = Result("answered", "Słaba bateria.", route="ha_native")
    assert (
        response_speech(result, request, model, {"quiet_ack_enabled": True}, time(22))
        == result.speech
    )
