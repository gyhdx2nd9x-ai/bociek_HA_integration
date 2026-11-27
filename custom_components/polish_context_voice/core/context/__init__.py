"""Trusted source context. Explicit utterance location always takes precedence."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    areas: tuple[str, ...] = ()
    explicit: bool = False
    error: str | None = None


def resolve_scope(proposal, request, model):
    if proposal.location is not None:
        matches = model.location_index.get(model.location_key(proposal.location), set())
        if len(matches) != 1:
            return Scope(error="ambiguous_location" if matches else "unknown_location")
        kind, identifier = next(iter(matches))
        if kind == "area":
            return Scope((identifier,), True)
        areas = tuple(sorted(a.id for a in model.areas.values() if a.floor_id == identifier))
        return Scope(areas, True, None if areas else "empty_floor")
    area = request.source_area
    if area is None and request.satellite_id:
        area = model.satellites.get(request.satellite_id)
    if area is None and request.device_id:
        area = model.devices.get(request.device_id)
    if area is not None and area not in model.areas:
        return Scope(error="invalid_source_area")
    return Scope((area,) if area else ())
