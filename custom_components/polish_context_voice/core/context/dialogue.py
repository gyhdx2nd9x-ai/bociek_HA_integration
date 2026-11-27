"""Bounded, source-bound dialogue state. No transcript or external model required."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..nlu.normalize import has_foreign_letters
from ..types import Proposal
from .light_sides import LightSideAnchor, LightSideReference

CONFIRMATIONS = {
    "tak",
    "potwierdzam",
    "tak potwierdzam",
    "dobrze",
    "ok",
    "okej",
    "zgoda",
    "tak prosze",
}
REJECTIONS = {"nie", "anuluj", "anuluj to", "nie potwierdzam", "nie dziekuje", "rezygnuje"}


def reply_words(text: str) -> str:
    if not text or len(text) > 160:
        return ""
    folded = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
    return " ".join(
        "".join(
            " " if unicodedata.category(char).startswith("P") else char
            for char in folded
            if not unicodedata.combining(char)
        ).split()
    )


def classify_reply(text: str, *, confirmations=CONFIRMATIONS, rejections=REJECTIONS) -> bool | None:
    """Classify a whole reply, including up to three identical affirmatives.

    Keep foreign letters and every semantic word: stripping non-Latin text could
    turn an STT error such as ``tak да`` into authorization. Callers still require
    an unexpired, source-bound proposal and fresh permission checks.
    """
    words = reply_words(text)
    if words in rejections:
        return False
    if words in confirmations or any(
        words == f"{phrase} {phrase}" or words == f"{phrase} {phrase} {phrase}"
        for phrase in confirmations
    ):
        return True
    return None


def is_confirmation_reply(text: str) -> bool:
    return classify_reply(text) is True


ORDINAL_POSITIONS = {
    word: index
    for index, forms in enumerate((
        ("pierwszy", "pierwsza", "pierwsze", "pierwszego"),
        ("drugi", "druga", "drugie", "drugiego"),
        ("trzeci", "trzecia", "trzecie", "trzeciego"),
        ("czwarty", "czwarta", "czwarte", "czwartego"),
        ("piaty", "piata", "piate", "piatego"),
    ))
    for word in forms
}
NUMBERED_POSITIONS = {"jeden": 0, "dwa": 1, "trzy": 2, "cztery": 3, "piec": 4}
CHOICE_CORRECTION = re.compile(r"^nie(?: to)?\s*,\s*(?=(?:t[aąeę]|ten|to)\s)", re.I)
ROOM_CORRECTION = re.compile(
    r"^nie(?:\s+(?:tutaj|tu|tam)\s+tylko\s+|(?:\s+(?:tutaj|tu|tam))?\s*,\s*(?:tylko\s+)?)"
    r"(?=(?:w|we|na)\s)", re.I,
)


ROOM_REPEAT = re.compile(
    r"(?:a )?(?:w|we|na) (.+?) (?:tez|rowniez|tak samo)|a (?:w|we|na) (.+)"
)


def repeated_room(words: str) -> str | None:
    """Recognize a short reply that carries only the destination of the action."""
    if len(words) <= 160 and (match := ROOM_REPEAT.fullmatch(words)):
        return match[1] or match[2]
    return None


def corrected_room(text):
    """Recognize an explicit correction, retaining the spoken room for the reply."""
    if not 1 <= len(text) <= 160 or not (start := ROOM_CORRECTION.match(text.strip())):
        return None
    return " ".join(text.strip()[start.end():].rstrip(" .!?").split())


def ordinal_reply(text: str, *, suffixes=()) -> tuple[int | None, bool]:
    """Return a whole reply's zero-based position and whether it attempted a choice."""
    if len(text) > 160:
        return None, False
    text = CHOICE_CORRECTION.sub("", text.strip())
    words = reply_words(text).split()
    if words and words[0] in {"ta", "ten", "te", "to"}:
        words = words[1:]
    numbered = bool(words and words[0] == "numer")
    if numbered:
        words = words[1:]
    if not words:
        return None, numbered
    head, *tail = words
    index = (NUMBERED_POSITIONS if numbered else ORDINAL_POSITIONS).get(head)
    if (numbered or len(head) == 1) and head.isascii() and head.isdecimal():
        index = int(head) - 1
    if index is None:
        return None, numbered
    if index < 0 or (tail and not (len(tail) == 1 and tail[0] in suffixes)) or re.search(
        r"[-−]\s*\d", text
    ):
        return None, True
    return index, True


def is_choice_correction(text):
    return bool(CHOICE_CORRECTION.match(text.strip()))


@dataclass(frozen=True)
class Command:
    proposal: Proposal
    query: Proposal | None = None
    selected: tuple[str, ...] = ()
    expected_targets: tuple[str, ...] | None = None
    expected_areas: tuple[str, ...] | None = None
    expected_structure: tuple | None = None
    category_fallback: bool = False
    selected_leaves: tuple[str, ...] = ()
    light_side_reference: LightSideReference | None = None


@dataclass(frozen=True)
class Turn:
    command: Command
    expires_at: float
    candidates: tuple[str, ...] = ()
    missing_area: bool = False
    missing_target: bool = False
    missing_category: bool = False
    plans: dict = field(default_factory=dict)
    light_choice: LightChoice | None = None
    preferred_light: str | None = None
    light_side_anchor: LightSideAnchor | None = None


@dataclass(frozen=True)
class LightChoice:
    question: Turn
    completed_options: frozenset[str] = frozenset()


def light_category_reply(text):
    """Accept a complete offered category, retaining every meaningful word."""
    words = reply_words(text)
    roles = {
        "robocze": "voice_task",
        "dekoracyjne": "voice_ambient",
        "ozdobne": "voice_ambient",
        "nastrojowe": "voice_ambient",
    }
    if words.startswith("swiatla "):
        words = words.removeprefix("swiatla ")
    return roles.get(words)


class Dialogue:
    def __init__(self, ttl=60, clarification_ttl=30, limit=512):
        self.ttl, self.clarification_ttl, self.limit = ttl, clarification_ttl, limit
        self.questions = {}
        self.memory = {}

    def prune(self, now):
        for mapping in (self.questions, self.memory):
            for key, turn in list(mapping.items()):
                if turn.expires_at <= now:
                    mapping.pop(key, None)

    def put(self, mapping, key, turn):
        mapping.pop(key, None)
        if len(mapping) >= self.limit:
            mapping.pop(next(iter(mapping)))
        mapping[key] = turn

    def discard(self, key):
        self.questions.pop(key, None)
        self.memory.pop(key, None)

    def clear(self):
        self.questions.clear()
        self.memory.clear()


def location_reply(text, model):
    text = re.sub(r"^(?:a )?(?:w|we|na) ", "", text)
    return text if model.location_key(text) in model.location_index else None


def preferred_light_reply(words):
    return re.fullmatch(
        r"(?:ta |te |ten |to )?(?:domysln|preferowan)[aey](?: lamp[ae]| swiatlo)?", words,
    ) is not None


def target_reply(text, candidates, model):
    text = re.sub(r"^(?:ta|ten|te|to) ", "", text)
    location = location_reply(text, model)
    if location:
        locations = model.location_index[model.location_key(location)]
        if len(locations) == 1:
            kind, identifier = next(iter(locations))
            areas = (
                {identifier}
                if kind == "area"
                else {a.id for a in model.areas.values() if a.floor_id == identifier}
            )
            return tuple(
                i for i in candidates if i in model.entities and model.entities[i].area_id in areas
            )
    matches = model.match_entity_name(text)
    return tuple(i for i in candidates if i in matches)


def reference_reply(text, candidates, model):
    """A missing reference accepts a complete HA name/alias, never a fragment."""
    if not text or len(text) > 160 or has_foreign_letters(text):
        return ()
    matches = model.match_entity_name(text)
    return tuple(identifier for identifier in candidates if identifier in matches)
