"""Resolve a requested peer from registered light names and optional HA groups."""

from dataclasses import dataclass

from ..home_model import Entity, HomeModel
from ..home_model.role_catalog import GUARD_ROLES
from ..nlu.light_sides import SideForm


@dataclass(frozen=True)
class LightSideAnchor:
    entity_id: str
    area_id: str
    side: str
    names: frozenset[tuple[SideForm, str]]


@dataclass(frozen=True)
class LightSideReference:
    anchor: LightSideAnchor
    requested: SideForm


def light_side_anchor(home: HomeModel, entity: Entity) -> LightSideAnchor | None:
    names = home.light_side_names.get(entity.id)
    if not names or not entity.area_id or entity.members or entity.platform == "group":
        return None
    sides = {form.side for form, _ in names}
    if len(sides) != 1:
        return None
    return LightSideAnchor(
        entity.id, entity.area_id, next(iter(sides)), names,
    )


def matching_light_sides(home: HomeModel, reference: LightSideReference) -> frozenset[str]:
    anchor, requested = reference.anchor, reference.requested
    current = home.entities.get(anchor.entity_id)
    if (
        requested.side == anchor.side or current is None
        or not current.exposed or not current.available
        or current.labels & {"voice_block", "voice_high_risk"}
        or light_side_anchor(home, current) != anchor
    ):
        return frozenset()

    matches = set()
    for form, name in anchor.names:
        genders = form.genders & requested.genders
        if not genders:
            continue
        for identifier in home.light_side_index.get((anchor.area_id, requested.side, name), ()):
            candidate = home.entities.get(identifier)
            if (
                candidate is not None and candidate.area_id == anchor.area_id
                and (peer := light_side_anchor(home, candidate)) is not None
                and peer.side == requested.side
                and any(
                    other_name == name and other_form.genders & genders
                    for other_form, other_name in peer.names
                )
            ):
                # Keep hidden, blocked and unavailable peers in the match set.
                # Their presence must not silently make another peer the sole match.
                matches.add(identifier)
    if len(matches) <= 1:
        return frozenset(matches)

    # A visible two-member group is explicit evidence when names are reused.
    # Guarded groups cannot confer authority to choose a member.
    paired = set()
    for group in home.entities.values():
        if (
            group.domain != "light" or group.area_id != anchor.area_id
            or not group.exposed or not group.available or group.labels & GUARD_ROLES
            or len(group.members) != 2 or anchor.entity_id not in group.members
        ):
            continue
        paired.update(matches.intersection(group.members))
    return frozenset(paired if len(paired) == 1 else matches)
