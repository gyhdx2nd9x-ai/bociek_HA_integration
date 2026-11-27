"""Small Polish command grammar for Dreame vacuum cleaning."""

import re
from dataclasses import dataclass

from .nlu.morphology import LightMorphology
from .nlu.normalize import normalize


@dataclass(frozen=True)
class VacuumCommand:
    room: str | None = None


START = re.compile(
    r"^(?:odkurz|posprzataj|uruchom odkurzacz|wlacz odkurzacz|"
    r"uruchom sprzatanie|wlacz sprzatanie)(?: (.+))?$"
)
_MORPHOLOGY = LightMorphology()


def match_command(text):
    """Recognize cleaning requests before loading the device registry."""
    value = normalize(text).strip(" .!?\t\n")
    value = re.sub(r"^(?:alexa|hej bociek)[, ]+", "", value)
    return START.fullmatch(value)


def resolve_command(match, mapped_rooms):
    """Resolve a matched request only against rooms on the active map."""
    tail = match[1]
    if not tail:
        return VacuumCommand()
    key = _MORPHOLOGY.key(re.sub(r"^(?:w|we|na) ", "", tail))
    for identifier, name in mapped_rooms.items():
        if key == _MORPHOLOGY.key(name):
            return VacuumCommand(room=identifier)
    return VacuumCommand(room="")


def parse(text, mapped_rooms):
    match = match_command(text)
    return resolve_command(match, mapped_rooms) if match else None


def mapped_rooms(state):
    """Read only the active map's room IDs from the Dreame vacuum state."""
    if state is None:
        return {}
    attributes = state.attributes
    rooms = attributes.get("rooms")
    if not isinstance(rooms, dict):
        return {}
    selected = attributes.get("selected_map")
    rows = rooms.get(selected)
    if not isinstance(rows, (list, tuple)):
        return {}
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        segment_id, name = row.get("id"), row.get("name")
        if (
            isinstance(segment_id, int)
            and not isinstance(segment_id, bool)
            and isinstance(name, str)
        ):
            if 1 <= segment_id <= 60 and name.strip():
                result[str(segment_id)] = name.strip()
    return result
