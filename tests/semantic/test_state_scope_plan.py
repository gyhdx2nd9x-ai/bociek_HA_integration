"""Read-only house/floor/room selection keeps HA exposure and permissions authoritative."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.capabilities import plan_capability
from custom_components.polish_context_voice.core.home_model import Area, Entity, Floor, HomeModel
from custom_components.polish_context_voice.core.nlu.capabilities import (
    Capability,
    parse_capability,
)
from custom_components.polish_context_voice.core.types import Request


def light(identifier, area, **changes):
    return replace(
        Entity("light." + identifier, identifier, area, exposed=True, state="off"), **changes
    )


def home(entities):
    return HomeModel(
        areas=(
            Area("living", "Living Room", ("salon",), "ground"),
            Area("kitchen", "Kitchen", ("kuchnia",), "ground"),
            Area("bedroom", "Bedroom", ("sypialnia",), "upper"),
        ),
        floors=(
            Floor("ground", "Ground Floor", ("parter",)),
            Floor("upper", "First Floor", ("piętro", "pierwsze piętro")),
            Floor("empty", "Empty Floor", ("puste piętro", "pustym piętrze")),
        ),
        entities=entities,
        satellites={"assist_satellite.living": "living", "assist_satellite.kitchen": "kitchen"},
        devices={"bedroom_device": "bedroom"},
    )


def plan(text, model, **request_args):
    return plan_capability(parse_capability(text), Request(text, **request_args), model)


@pytest.mark.parametrize(
    "text,areas,targets",
    [
        ("Czy wszystkie światła są wyłączone?", ("living",), ("light.living",)),
        ("Czy jakieś światła są włączone?", ("living",), ("light.living",)),
        ("Czy w kuchni świeci się światło?", ("kitchen",), ("light.kitchen",)),
        ("Czy wszystkie światła są wyłączone w kuchni?", ("kitchen",), ("light.kitchen",)),
        (
            "Czy wszystkie światła są wyłączone na parterze?",
            ("kitchen", "living"),
            ("light.kitchen", "light.living"),
        ),
        ("Czy na piętrze świeci się światło?", ("bedroom",), ("light.bedroom",)),
        (
            "Czy wszystkie światła są wyłączone w całym domu?",
            (),
            ("light.bedroom", "light.kitchen", "light.living", "light.unassigned"),
        ),
        (
            "Czy gdzieś świeci się światło?",
            (),
            ("light.bedroom", "light.kitchen", "light.living", "light.unassigned"),
        ),
    ],
)
def test_room_floor_and_explicit_house_scope(text, areas, targets):
    model = home(
        (
            light("living", "living"),
            light("kitchen", "kitchen"),
            light("bedroom", "bedroom"),
            light("unassigned", None),
        )
    )
    result = plan(text, model, source_area="living", satellite_id="assist_satellite.kitchen")
    assert result.status == "ready" and result.targets == targets
    assert result.area_ids == areas and not result.calls


@pytest.mark.parametrize(
    "request_args,target",
    [
        ({"satellite_id": "assist_satellite.kitchen"}, "light.kitchen"),
        ({"device_id": "bedroom_device"}, "light.bedroom"),
        ({"source_area": "living", "satellite_id": "assist_satellite.kitchen"}, "light.living"),
    ],
)
def test_unscoped_quantifier_uses_existing_context_precedence(request_args, target):
    model = home(
        (light("living", "living"), light("kitchen", "kitchen"), light("bedroom", "bedroom"))
    )
    result = plan("Czy wszystkie światła są wyłączone?", model, **request_args)
    assert result.targets == (target,) and not result.calls


@pytest.mark.parametrize("kind", ["light", "window", "door"])
def test_house_reads_include_unknown_but_not_denied_hidden_blocked_or_excluded(kind):
    entries = (
        light("observed", "living", state="on"),
        light("unknown", "bedroom", state="unknown"),
        light("unavailable", None, available=False, state="unavailable"),
        light("hidden", "kitchen", exposed=False),
        light("blocked", "kitchen", labels=frozenset({"voice_block"})),
        light("excluded", "bedroom", labels=frozenset({"voice_exclude_all"})),
        light("denied", "living"),
    )
    if kind != "light":
        entries = tuple(
            replace(e, id=e.id.replace("light.", "binary_sensor."), device_class=kind)
            for e in entries
        )
    model = home(entries).visible_to(lambda identifier: not identifier.endswith(".denied"))
    noun = {
        "light": "światła są wyłączone",
        "window": "okna są zamknięte",
        "door": "drzwi są zamknięte",
    }[kind]
    result = plan(f"Czy wszystkie {noun} w całym domu?", model)
    domain = "light" if kind == "light" else "binary_sensor"
    assert result.status == "ready"
    assert result.targets == tuple(
        f"{domain}.{tail}" for tail in ("observed", "unavailable", "unknown")
    )
    assert not result.calls and not result.area_ids


def test_group_is_selected_as_an_observed_entity_without_expanding_hidden_members():
    member = light("hidden_member", "kitchen", exposed=False)
    group = light("group", "living", state="on", attributes={"entity_id": [member.id]})
    result = plan("Czy gdzieś świeci się światło?", home((group, member)))
    assert result.targets == (group.id,) and result.status == "ready" and not result.calls


@pytest.mark.parametrize(
    "text", ["Czy jakieś światła są włączone?", "Czy wszystkie światła są wyłączone?"]
)
def test_missing_area_does_not_default_to_house(text):
    result = plan(text, home((light("living", "living"),)))
    assert result.reason == "missing_area" and not result.calls and not result.targets


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Czy w garażu świeci się światło?", "unknown_location"),
        ("Czy wszystkie światła są wyłączone na pustym piętrze?", "empty_floor"),
    ],
)
def test_unknown_or_empty_explicit_place_cannot_fall_back_to_source(text, reason):
    result = plan(text, home((light("living", "living"),)), source_area="living")
    assert result.reason == reason and not result.targets and not result.calls


def test_location_alias_conflict_remains_ambiguous():
    model = HomeModel(
        areas=(Area("a", "Room A", ("salon",)), Area("b", "Room B", ("salon",))),
        entities=(light("living", "a"),),
    )
    result = plan("Czy świeci się światło w salonie?", model, source_area="a")
    assert result.reason == "ambiguous_location" and not result.targets and not result.calls


def test_explicit_house_precedes_missing_or_invalid_source_area():
    model = home((light("unassigned", None),))
    result = plan("Czy gdzieś świeci się światło?", model, source_area="removed")
    assert result.status == "ready" and result.targets == ("light.unassigned",)
    assert not result.calls
    local = plan("Czy jakieś światła są włączone?", model, source_area="removed")
    assert local.reason == "invalid_source_area" and not local.targets


def test_no_readable_lights_gives_a_useful_message_without_all_off_claim():
    result = plan(
        "Czy gdzieś świeci się światło?", home((light("hidden", "living", exposed=False),))
    )
    assert result.reason == "no_targets" and not result.targets and not result.calls
    assert "świateł do sprawdzenia" in result.speech and "wyłączone" not in result.speech


@pytest.mark.parametrize(
    "cap,reason",
    [
        (Capability("state", "light", kind="on", scope="everywhere"), "invalid_scope"),
        (Capability("state", "light", kind="on", scope=None), "invalid_scope"),
        (
            Capability("state", "light", kind="on", scope="house", location="kuchnia"),
            "conflicting_scope",
        ),
        (Capability("state", "light", kind="on", scope="house", name="named"), "unsupported_scope"),
        (
            Capability("state", "light", kind="on", query="all_on", scope="house"),
            "unsupported_scope",
        ),
        (Capability("state", "light", kind="off", scope="house"), "unsupported_scope"),
        (Capability("state", "switch", kind="on", scope="house"), "unsupported_scope"),
        (
            Capability("state", "binary_sensor", kind="opening", query="closed", scope="house"),
            "unsupported_scope",
        ),
        (
            Capability("state", "binary_sensor", kind="door", query="locked", scope="house"),
            "unsupported_scope",
        ),
        (Capability("routine", "script", scope="house"), "unsupported_scope"),
        (Capability("todo_add", "todo", payload="mleko", scope="house"), "unsupported_scope"),
        (
            Capability("announce", "assist_satellite", payload="hej", scope="house"),
            "unsupported_scope",
        ),
        (Capability("media_play", "media_player", scope="house"), "unsupported_scope"),
        (Capability("turn_off", "light", scope="house"), "unsupported_scope"),
        (Capability("maintenance", scope="house"), "unsupported_scope"),
        (Capability("time", scope="house"), "unsupported_scope"),
        (Capability("date", scope="house"), "unsupported_scope"),
    ],
)
def test_invented_scope_slots_never_widen_another_operation_or_create_calls(cap, reason):
    result = plan_capability(
        cap,
        Request("untrusted structured slot", source_area="living"),
        home((light("living", "living"),)),
    )
    assert result.status in {"blocked", "clarify"} and result.reason == reason
    assert not result.targets and not result.calls and not result.completed
