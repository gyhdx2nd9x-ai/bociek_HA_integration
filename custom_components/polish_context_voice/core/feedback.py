"""Versioned, deliberately manual feedback for synthetic offline replay."""

import re
from hashlib import sha256

from .home_model import Area, Entity, HomeModel

FORMAT = "bociek-feedback-1"
FIXTURE = "feedback_lights_v1"
AREAS = (Area("kitchen", "Kuchnia"), Area("living_room", "Salon"))
ON_OFF = frozenset({"voice_default_on", "voice_all_off", "voice_main"})
LIGHTS = (
    Entity("light.kitchen_main", "Sufit w kuchni", "kitchen", ("światło sufitowe",), ON_OFF),
    Entity("light.kitchen_task", "Lampka przy blacie", "kitchen", ("lampka",),
           frozenset({"voice_all_off", "voice_task"})),
    Entity("light.living_main", "Sufit w salonie", "living_room", ("żyrandol",), ON_OFF),
    Entity("light.living_table", "Lampka przy stole", "living_room", ("lampka", "nad stołem"),
           frozenset({"voice_all_off", "voice_confirm"})),
)
TARGETS = {light.id: light for light in LIGHTS}
STATUSES = ("dry_run", "confirm", "clarify", "unsupported")
DIALOGUE_STATUSES = (*STATUSES, "answered", "cancelled")
MAX_TURNS = 6
OPERATIONS = ("turn_on", "turn_off", "brightness")
AREA_IDS = {area.id for area in AREAS}
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?\Z")


class FeedbackError(ValueError):
    def __init__(self, field):
        super().__init__(field)
        self.field = field


def fixture():
    return HomeModel(
        AREAS,
        entities=[
            Entity(
                light.id, light.name, light.area_id, light.aliases, light.labels,
                exposed=True, state="on", attributes={"supported_color_modes": ["brightness"]},
            )
            for light in LIGHTS
        ],
    ).export()


def text_field(value, field, minimum):
    if not isinstance(value, str):
        raise FeedbackError(field)
    value = value.strip()
    if not minimum <= len(value) <= 400:
        raise FeedbackError(field)
    return value


def _source_area(value):
    if value is not None and (not isinstance(value, str) or value not in AREA_IDS):
        raise FeedbackError("source_area")
    return value


def _targets(value, field):
    if value is None:
        return []
    if (
        not isinstance(value, list) or len(value) > len(TARGETS)
        or any(not isinstance(item, str) or item not in TARGETS for item in value)
        or len(set(value)) != len(value)
    ):
        raise FeedbackError(field)
    return sorted(value)


def _expectation(values, statuses):
    text = text_field(values.get("text"), "text", 1)
    status = values.get("expected_status")
    if status not in statuses:
        raise FeedbackError("expected_status")
    operation = values.get("operation")
    targets = _targets(values.get("targets"), "targets")
    brightness = values.get("brightness_pct")
    if status in {"dry_run", "confirm"}:
        if operation not in OPERATIONS:
            raise FeedbackError("operation")
        if not targets:
            raise FeedbackError("targets")
    else:
        if operation is not None:
            raise FeedbackError("operation")
        if targets:
            raise FeedbackError("targets")
    if operation == "brightness":
        if type(brightness) not in {int, float} or not 1 <= brightness <= 100:
            raise FeedbackError("brightness_pct")
        if brightness != int(brightness):
            raise FeedbackError("brightness_pct")
        brightness = int(brightness)
    elif brightness is not None:
        raise FeedbackError("brightness_pct")
    return {
        "text": text,
        "expected_status": status,
        "operation": operation,
        "targets": targets,
        "brightness_pct": brightness,
    }


def example_fields(values):
    expectation = _expectation(values, STATUSES)
    return {
        "fixture": FIXTURE, "text": expectation.pop("text"),
        "source_area": _source_area(values.get("source_area")), **expectation,
    }


def dialogue_turn(values):
    turn = _expectation(values, DIALOGUE_STATUSES)
    on = _targets(values.get("lights_on"), "lights_on")
    off = _targets(values.get("lights_off"), "lights_off")
    if set(on) & set(off):
        raise FeedbackError("lights_off")
    seconds = values.get("advance_seconds", 0)
    if type(seconds) not in {int, float} or not 0 <= seconds <= 600 or seconds != int(seconds):
        raise FeedbackError("advance_seconds")
    dialogue = values.get("followup")
    if dialogue not in (None, "yes", "no"):
        raise FeedbackError("followup")
    return {
        **turn, "lights_on": on, "lights_off": off, "advance_seconds": int(seconds),
        "followup": dialogue,
    }


def dialogue_fields(values):
    area = _source_area(values.get("source_area"))
    turns = values.get("turns")
    if not isinstance(turns, list) or not 2 <= len(turns) <= MAX_TURNS:
        raise FeedbackError("turns")
    if any(not isinstance(turn, dict) for turn in turns):
        raise FeedbackError("turns")
    return {"fixture": FIXTURE, "source_area": area, "turns": [dialogue_turn(t) for t in turns]}


def document(kind, values, *, bociek, ha_core):
    if any(not isinstance(v, str) or not VERSION.fullmatch(v) for v in (bociek, ha_core)):
        raise FeedbackError("versions")
    envelope = {"format": FORMAT, "kind": kind, "versions": {"bociek": bociek, "ha_core": ha_core}}
    if kind == "example":
        return {**envelope, "example": example_fields(values)}
    if kind == "dialogue":
        return {**envelope, "dialogue": dialogue_fields(values)}
    if kind == "idea":
        return {**envelope, "idea": text_field(values.get("idea"), "idea", 10)}
    raise FeedbackError("kind")


def corpus_case(report):
    """Translate a reviewed example or dialogue into a read-only corpus test."""
    if (
        not isinstance(report, dict)
        or report.get("format") != FORMAT or report.get("kind") not in ("example", "dialogue")
        or set(report) != {"format", "kind", "versions", report["kind"]}
        or not isinstance(report.get("versions"), dict)
        or set(report["versions"]) != {"bociek", "ha_core"}
        or not isinstance(report.get(report["kind"]), dict)
    ):
        raise FeedbackError("format")
    kind = report["kind"]
    raw = report[kind]
    payload = document(kind, raw, **report["versions"])[kind]
    if raw != payload:
        raise FeedbackError(kind)
    identifier = "FB-" + sha256(repr(payload).encode()).hexdigest()[:12]
    return {
        "id": identifier,
        "home": FIXTURE,
        "split": "regression",
        "layer": "engine",
        "family": identifier,
        "category": "continuation" if kind == "dialogue" else "command",
        "turns": [_corpus_turn(turn, payload["source_area"]) for turn in (
            payload["turns"] if kind == "dialogue" else [payload]
        )],
    }


def _corpus_turn(turn, source_area):
    service = turn["operation"]
    data = {}
    if service == "brightness":
        service, data = "turn_on", {"brightness_pct": turn["brightness_pct"]}
    calls = [
        {"domain": "light", "service": service, "entity_ids": [target], "data": data}
        for target in turn["targets"]
    ]
    result = {
        "request": {"text": turn["text"], "source_area": source_area},
        "expected": {
            "status": turn["expected_status"], "targets": turn["targets"],
            "calls": calls, "executor_calls": [],
        },
    }
    if turn.get("advance_seconds"):
        result["advance_seconds"] = turn["advance_seconds"]
    if turn.get("followup") is not None:
        result["expected"]["continue_conversation"] = turn["followup"] == "yes"
    mutations = [
        {"entity_id": identifier, "changes": {"state": state}}
        for state in ("on", "off") for identifier in turn.get("lights_" + state, [])
    ]
    if mutations:
        result["mutations"] = mutations
    return result
