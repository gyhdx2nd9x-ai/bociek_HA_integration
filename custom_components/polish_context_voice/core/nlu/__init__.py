"""Whole-utterance grammar. Unconsumed words never become silent actions."""

import re

from ..types import Action, Proposal
from .normalize import has_foreign_letters, normalize
from .numbers import number

ON = r"(?:wlacz|wlaczyc|wlaczysz|wlaczcie|zapal|zapalic|zapalisz|zaswiec|zaswiecic)"
OFF = r"(?:wylacz|wylaczyc|wylaczysz|wylaczcie|zgas|zgasic|zgasisz|powygaszaj)"
LIGHTS = {"swiatla", "swiatlo", "oswietlenie", "lampy", "lampki"}
SINGULAR = {"lampe", "lampa", "lampke", "lampka"}
QUALIFIERS = {
    "glowne": "voice_main",
    "robocze": "voice_task",
    "nastrojowe": "voice_ambient",
    "dekoracyjne": "voice_ambient",
    "ozdobne": "voice_ambient",
}
EXCLUSIVE = {"tylko", "jedynie", "wylacznie"}
LIT_WORDS = {"zapalone", "wlaczone", "swiecace"}
LIT_CLAUSE = re.compile(r" ktore (?:sie swieca|sa (?:wlaczone|zapalone))$")
LIT_KINDS = {"lit_lights", "all_lit_lights"}
COVERS = {"rolety", "zaluzje", "zaslony"}
SINGLE_COVERS = {"roleta", "rolete", "zaluzja", "zaslona", "zaslone"}
FANS = {"wentylator", "wiatrak"}
OPEN = r"(?:otworz|otworzyc)"
CLOSE = r"(?:zamknij|zamknac)"
VERB = rf"(?:{ON}|{OFF}|{OPEN}|{CLOSE}|ustaw|przyciemnij|sciemnij|rozjasnij)"
IMMEDIATE_PREFIX = re.compile(rf"^(?:a )?teraz (?=(?:{VERB}|ja|je|go|to)\b)")
PERCENT_UNIT = r"(?:%|procent|proc)"
BRIGHTNESS_REPLY = re.compile(rf"(?:i )?jeszcze (?:na|do) (.+?)\s*{PERCENT_UNIT}")
LIGHT_PURPOSE_ROLES = {
    "do czytania": "voice_task",
    "do pracy": "voice_task",
    "dla nastroju": "voice_ambient",
}
PURPOSE_REQUEST = re.compile(r"(potrzebuje|poprosze(?: o)?) (.+)")
LIGHT_OUTCOME_ON = re.compile(
    r"(?:zrob|zrobisz) jasno|(?:daj|zrob|poprosze(?: o)?) (?:swiatlo|oswietlenie)"
    r"|potrzebuje zapalonego swiatla"
)
LIGHT_OUTCOME_OFF = re.compile(r"(?:zrob|zrobisz) ciemno")
DARK_ROOM_REMARK = re.compile(r"ale (?:(tu|tutaj) )?(?:jest )?ciemno")
OUTCOME_ROOM_FIRST = re.compile(
    r"(?:w|we|na) (.+?) ((?:zrob|zrobisz|daj|poprosze|potrzebuje) .+)"
)
OUTCOME_ROOM_MIDDLE = re.compile(
    r"(zrob|zrobisz|daj|poprosze(?: o)?) (?:w|we|na) (.+?)"
    r" (jasno|ciemno|swiatlo|oswietlenie)"
)
OUTCOME_STARTS = (
    "zrob ", "zrobisz ", "daj ", "poprosze ", "potrzebuje ", "w ", "we ", "na ",
    "tu ", "tutaj ", "niech zgasnie ", "czy dasz rade zgasic ", "ale ",
)
LIGHT_ONLY_REQUEST = re.compile(r"(?:niech zgasnie|czy dasz rade zgasic) (.+)")
HERE_WORDS = re.compile(r"\b(?:mi|tutaj|tu)\b")
_GENERIC_NOUNS = "|".join(sorted(LIGHTS | SINGULAR | COVERS | SINGLE_COVERS | FANS))
_PURPOSE_NOUNS = "|".join(sorted(LIGHTS | SINGULAR | {"cos"}))
_PURPOSE_PHRASES = "|".join(LIGHT_PURPOSE_ROLES)
ROOM_FIRST_OBJECT = re.compile(
    rf"({VERB}) ((?:w|we|na) .+) ((?:{_PURPOSE_NOUNS}) (?:{_PURPOSE_PHRASES})|{_GENERIC_NOUNS})"
)


def canonical_order(value: str) -> str:
    """Move one explicit verb/location, retaining every non-politeness word."""
    value = re.sub(r"\bprosze\b", "", value)
    value = " ".join(value.split())
    verbs = list(re.finditer(rf"\b{VERB}\b", value))
    if len(verbs) != 1:
        return value
    verb = verbs[0]
    before, after = value[: verb.start()].strip(), value[verb.end() :].strip()
    if before:
        tail = f"{after} {before}" if re.match(r"^(w|we|na) ", before) else f"{before} {after}"
        value = f"{verb[0]} {tail.strip()}"
    # Fronted location before a generic object; named aliases stay intact.
    if match := ROOM_FIRST_OBJECT.fullmatch(value):
        value = f"{match[1]} {match[3]} {match[2]}"
    return value


def unsafe_for_fallback(text: str) -> bool:
    """Unsupported control structure must not become an immediate action."""
    text = normalize(text)
    return bool(
        re.search(
            r"\b(nie|nigdy|jezeli|jesli|potem|pozniej|jutro|oprocz|bez|oraz|ale|lub|"
            r"tylko|jedynie|wylacznie)\b"
            r"|\bza \d|\bi\b",
            text,
        )
    )


def split_location(value: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"(.+) (?:w|we|na) (.+)", value)
    if match:
        numeric = re.sub(r"\s*(?:%|procent|proc|stopni)$", "", match[2])
        if number(numeric) is None:
            return match[1], match[2]
    return value, None


def parse(text: str) -> Proposal | None:
    value = normalize(text)
    # Negation, conditionals, sequence, schedules: refuse fast-path interpretation.
    if re.search(r"\b(nie|nigdy|jezeli|jesli|chyba|potem|pozniej|jutro|za|oprocz|bez)\b", value):
        return None
    value = re.sub(r"^(?:czy )?(?:mozesz|moglbys|moglabys) (?:mi )?", "", value)
    value = re.sub(r"^prosze (?:o )?", "", value)
    value = re.sub(r" prosze$", "", value)
    if (immediate := IMMEDIATE_PREFIX.match(value)) or "tutejsz" in value:
        if has_foreign_letters(text):
            return None
        if immediate:
            value = value[immediate.end():]
    if match := BRIGHTNESS_REPLY.fullmatch(value):
        numeric = number(match[1])
        if numeric is not None:
            return Proposal(Action.BRIGHTNESS, "reference", value=numeric)
        return None
    if value.startswith(OUTCOME_STARTS) and (outcome := _light_outcome(value, text)):
        return outcome
    value = canonical_order(value)
    # The last location marker leaves entity aliases like 'lampa przy stole' intact.
    value, location = split_location(value)
    value = HERE_WORDS.sub("", value)
    value = " ".join(value.split())
    if re.fullmatch(r"(?:jaka (?:jest )?temperatura|ile (?:jest )?stopni)", value):
        return Proposal(Action.TEMPERATURE, "sensor", location=location)
    match = re.fullmatch(r"ustaw (?:temperature (?:na )?)?(.+) stopni", value)
    if match:
        numeric = number(match[1])
        if numeric is not None:
            return Proposal(Action.SET_TEMPERATURE, "climate", location=location, value=numeric)
        return None
    if match := re.fullmatch(rf"(?:{OPEN}|ustaw) (.+?) do polowy", value):
        if match[1] in COVERS | SINGLE_COVERS:
            return _target(Action.COVER_POSITION, match[1], location, 50)
    match = re.fullmatch(
        rf"(ustaw|przyciemnij|sciemnij|rozjasnij|{OPEN}) (.+?) (?:na|do) (.+?)"
        rf"\s*{PERCENT_UNIT}", value,
    )
    if match:
        verb, target, numeric = match.groups()
        if (numeric := number(numeric)) is None:
            return None
        if target in COVERS | SINGLE_COVERS and (verb == "ustaw" or re.fullmatch(OPEN, verb)):
            return _target(Action.COVER_POSITION, target, location, numeric)
        if target in FANS and verb == "ustaw":
            return _target(Action.FAN_SPEED, target, location, numeric)
        if not re.fullmatch(OPEN, verb):
            return _target(Action.BRIGHTNESS, target, location, numeric)
        return None
    if request := PURPOSE_REQUEST.fullmatch(value):
        if has_foreign_letters(text):
            return None
        return _purpose_light(
            request[2], Action.ON, location, genitive=request[1] == "potrzebuje",
        )
    for expression, action in (
        (ON, Action.ON),
        (OFF, Action.OFF),
        (OPEN, Action.OPEN),
        (CLOSE, Action.CLOSE),
    ):
        match = re.fullmatch(expression + r" (.+)", value)
        if match:
            if action in {Action.ON, Action.OFF}:
                allow_indefinite = match[0].startswith(("zapal", "zaswiec"))
                purpose = _purpose_light(match[1], action, location, allow_indefinite)
                if purpose is not None:
                    return None if has_foreign_letters(text) else purpose
            if action == Action.OFF and (proposal := _lit_lights(match[1], location)):
                return proposal
            return _target(action, match[1], location)
    return None


def _light_outcome(value, original):
    """Translate a complete request for room lighting to an ordinary light action."""
    if value.startswith("ale "):
        body, location = split_location(value)
        if (
            (remark := DARK_ROOM_REMARK.fullmatch(body))
            and (remark[1] or location)
            and not original.rstrip().endswith("?")
            and not has_foreign_letters(original)
        ):
            if location in {"tym pokoju", "tym pomieszczeniu"}:
                location = None
            return Proposal(Action.ON, "light", location=location, prefer_single_light=True)
        return None
    value = " ".join(HERE_WORDS.sub("", value).split())
    if match := OUTCOME_ROOM_FIRST.fullmatch(value):
        location, body = match.groups()
    elif match := OUTCOME_ROOM_MIDDLE.fullmatch(value):
        verb, location, target = match.groups()
        body = f"{verb} {target}"
    elif value.startswith(("w ", "we ", "na ")):
        return None
    else:
        body, location = split_location(value)
    if location in {"tym pokoju", "tym pomieszczeniu"}:
        location = None
    if match := LIGHT_ONLY_REQUEST.fullmatch(body):
        proposal = _target(Action.OFF, match[1], location)
        if (
            proposal is not None
            and proposal.kind in {"light", "single_light", "all_lights"}
            and not has_foreign_letters(original)
        ):
            return proposal
        return None
    if LIGHT_OUTCOME_ON.fullmatch(body):
        action = Action.ON
    elif LIGHT_OUTCOME_OFF.fullmatch(body):
        action = Action.OFF
    else:
        return None
    if has_foreign_letters(original):
        return None
    return Proposal(
        action, "light", location=location, prefer_single_light=action == Action.ON,
    )


def _purpose_light(target, action, location, allow_indefinite=False, *, genitive=False):
    noun, _, purpose = target.partition(" ")
    if (role := LIGHT_PURPOSE_ROLES.get(purpose)) is None:
        return None
    indefinite = allow_indefinite and noun == "cos"
    if not (indefinite or noun in LIGHTS or noun in SINGULAR):
        return None
    if location in {"tym pokoju", "tym pomieszczeniu"}:
        location = None
    return Proposal(
        action, "purpose_light", name=None if indefinite else target,
        location=location, qualifier=role,
        prefer_single_light=(
            indefinite or noun in SINGULAR or noun == "swiatlo" or (genitive and noun == "swiatla")
        ),
    )


def _lit_lights(target, location):
    target = LIT_CLAUSE.sub(" swiecace", target)
    words = target.split()
    all_lights = bool(words and words[0] == "wszystkie")
    if words and (all_lights or words[0] in EXCLUSIVE):
        words.pop(0)
    state_words = [word for word in words if word in LIT_WORDS]
    if len(state_words) != 1:
        return None
    words.remove(state_words[0])
    role = [word for word in words if word in QUALIFIERS]
    if len(role) > 1:
        return None
    if role:
        words.remove(role[0])
    if words and (len(words) != 1 or words[0] not in LIGHTS):
        return None
    if location in {"tym pokoju", "tym pomieszczeniu"}:
        location = None
    return Proposal(
        Action.OFF, "all_lit_lights" if all_lights else "lit_lights",
        location=location, qualifier=QUALIFIERS[role[0]] if role else None,
    )


def _target(action, target, location, value=None):
    if re.search(r"\b(i|oraz|a|ale|lub)\b", target):
        return None
    if action in {Action.ON, Action.OFF}:
        modifier, _, remainder = target.partition(" ")
        if modifier in EXCLUSIVE and remainder:
            restricted = _classify_target(action, remainder, location, value)
            if (
                restricted
                and restricted.kind in {"light", "all_lights"}
                and restricted.qualifier
            ):
                return restricted
    return _classify_target(action, target, location, value)


def _classify_target(action, target, location, value):
    if location is None:
        adjective, _, noun = target.partition(" ")
        if (adjective == "tutejsze" and noun in LIGHTS) or (
            adjective == "tutejsza" and noun in SINGULAR
        ):
            target = noun
    if target == "wszystko":
        return Proposal(action, "safe_all", location=location) if action == Action.OFF else None
    if target in {"je", "ja", "go", "to"}:
        return Proposal(action, "reference", location=location, value=value)
    qualifier = None
    words = target.split()
    all_lights = bool(words and words[0] == "wszystkie")
    if all_lights:
        words.pop(0)
    if words and words[0] in QUALIFIERS:
        qualifier = QUALIFIERS[words.pop(0)]
    elif words and words[-1] in QUALIFIERS:
        qualifier = QUALIFIERS[words.pop()]
    target = " ".join(words)
    if target in LIGHTS or (qualifier and not target):
        return Proposal(
            action,
            "all_lights" if all_lights else "light",
            location=location,
            qualifier=qualifier,
            value=value,
            prefer_single_light=(
                action == Action.ON and target == "swiatlo" and not (all_lights or qualifier)
            ),
        )
    if all_lights:
        return None
    if target in SINGULAR:
        return Proposal(action, "single_light", location=location, value=value)
    if target in COVERS | SINGLE_COVERS and action in {
        Action.OPEN, Action.CLOSE, Action.COVER_POSITION,
    }:
        kind = "cover" if target in COVERS else "single_cover"
        return Proposal(action, kind, location=location, value=value)
    if target in FANS and action in {Action.ON, Action.OFF, Action.FAN_SPEED}:
        return Proposal(action, "fan", location=location, value=value)
    return Proposal(
        action, "named", name=target, location=location, qualifier=qualifier, value=value
    )
