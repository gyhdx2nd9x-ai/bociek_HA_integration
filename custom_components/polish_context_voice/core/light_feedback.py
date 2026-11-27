"""Conservative speech based on HA's cached light state before execution.

This does not suppress an idempotent service call or verify physical execution.
The snapshot is useful only after the same complete plan executed successfully.
"""

from dataclasses import dataclass

from .home_model import Entity, HomeModel
from .nlu.light_sides import SIDE_FORMS
from .nlu.morphology import light_name_key
from .nlu.normalize import normalize
from .types import Action, Proposal, Result

# Use a known singular noun instead of trying to decline arbitrary HA aliases.
_LIGHT_SUBJECTS = {
    "kinkiet": ("Kinkiet", "był", "y"),
    "reflektor": ("Reflektor", "był", "y"),
    "zyrandol": ("Żyrandol", "był", "y"),
    "lampa": ("Lampa", "była", "a"),
    "lampka": ("Lampka", "była", "a"),
    "zarowka": ("Żarówka", "była", "a"),
    "tasma": ("Taśma", "była", "a"),
    "listwa": ("Listwa", "była", "a"),
    "girlanda": ("Girlanda", "była", "a"),
    "swiatlo": ("Światło", "było", "e"),
    "oswietlenie": ("Oświetlenie", "było", "e"),
}
_GENERIC_LIGHT = ("To światło", "było", "e")


def _known_light_subject(home: HomeModel, name: str):
    first, _, rest = normalize(name).partition(" ")
    if first in SIDE_FORMS:
        first = rest.partition(" ")[0]
    if first in {"lampy", "lampki", "swiatla"}:
        return None
    noun = home.key(first)
    return _LIGHT_SUBJECTS.get(light_name_key(noun) or noun)


def _light_subject(home: HomeModel, proposal: Proposal, entity: Entity):
    if not proposal.name:
        return _GENERIC_LIGHT
    if subject := _known_light_subject(home, proposal.name):
        return subject
    choices = {
        known for name in (entity.name, *entity.aliases)
        if (known := _known_light_subject(home, name))
    }
    return next(iter(choices)) if len(choices) == 1 else _GENERIC_LIGHT


@dataclass(frozen=True)
class LightStateSnapshot:
    targets: tuple[str, ...]
    calls: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]
    speech: str


def _pure_calls(result: Result, service: str):
    if not result.calls:
        return None
    calls = []
    called = []
    for call in result.calls:
        if call.domain != "light" or call.service != service or call.data != {}:
            return None
        if not call.entity_ids or any(not item.startswith("light.") for item in call.entity_ids):
            return None
        called.extend(call.entity_ids)
        calls.append((call.domain, call.service, call.entity_ids, call.guard_entity_ids))
    if sorted(called) != sorted(result.targets):
        return None
    return tuple(calls)


def snapshot_light_state(
    proposal: Proposal, home: HomeModel, result: Result
) -> LightStateSnapshot | None:
    """Read resolved light leaves once, immediately before the existing executor."""
    if (
        proposal.action not in {Action.ON, Action.OFF}
        or proposal.value is not None
        or result.status not in {"ready", "confirm"}
        or not result.targets
        or len(set(result.targets)) != len(result.targets)
    ):
        return None
    calls = _pure_calls(result, proposal.action.value)
    if calls is None:
        return None
    requested_state = "on" if proposal.action == Action.ON else "off"
    for identifier in result.targets:
        entity = home.entities.get(identifier)
        if (
            entity is None
            or entity.domain != "light"
            or not entity.available
            or not entity.exposed
            or entity.members
            or entity.platform == "group"
            or entity.state != requested_state
        ):
            return None
    state_word = "włączon" if requested_state == "on" else "wyłączon"
    subject, copula, ending = (
        _light_subject(home, proposal, home.entities[result.targets[0]]) if len(result.targets) == 1
        else ("Te światła", "były", "e")
    )
    speech = f"{subject} {copula} już {state_word}{ending}."
    return LightStateSnapshot(result.targets, calls, speech)


def light_state_feedback(snapshot: LightStateSnapshot | None, result: Result) -> str | None:
    """Never replace an error, partial execution, preview or changed plan's speech."""
    if (
        snapshot is None
        or result.status != "executed"
        or result.targets != snapshot.targets
        or sorted(result.completed) != sorted(snapshot.targets)
        or _pure_calls(result, snapshot.calls[0][1]) != snapshot.calls
    ):
        return None
    return snapshot.speech
