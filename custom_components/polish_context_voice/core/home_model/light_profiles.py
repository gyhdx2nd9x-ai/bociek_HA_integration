"""Editable recipes, not a second inventory or a background auto-labeling system."""

from ..types import Action
from .role_catalog import LIGHT_ROLES

LIGHT_KINDS = frozenset({"voice_main", "voice_task", "voice_ambient", "voice_accent"})
VISIBLE_KINDS = ("voice_main", "voice_task", "voice_ambient")
PROFILE_ROLES = LIGHT_ROLES | LIGHT_KINDS
LIGHT_SEMANTIC_ROLES = PROFILE_ROLES | {"voice_safe_all"}
PRESETS_OPTION = "voice_room_presets"
DEFAULT_PROFILES = {
    "main": frozenset({"voice_main", "voice_default_on", "voice_all_off"}),
    "task": frozenset({"voice_task", "voice_all_off"}),
    "ambient": frozenset({"voice_ambient", "voice_all_off"}),
}


def canonical_roles(roles):
    """One visible mood/decorative category, while accepting the old label on input."""
    return frozenset("voice_ambient" if role == "voice_accent" else role for role in roles)


def matches_light_role(labels, role):
    if role in {"voice_ambient", "voice_accent"}:
        return bool(labels & {"voice_ambient", "voice_accent"})
    return role in labels


def has_light_semantic_roles(entities):
    """Whether this room has any explicit lighting roles/categories."""
    return any(
        entity.domain == "light" and entity.labels & LIGHT_SEMANTIC_ROLES for entity in entities
    )


def has_default_light(home, area):
    # An unavailable or unexposed configured main light is still configured.
    return any(
        entity.domain == "light" and entity.area_id == area and "voice_default_on" in entity.labels
        for entity in home.entities.values()
    )


def lights_for_command(entities, action, qualifier=None):
    """Choose light targets, with a bounded all-unlabelled generic-OFF fallback.

    A configured lighting role means the room has explicit semantics, so a
    missing ``voice_all_off`` remains a configuration issue. Only a room with
    no semantic light roles at all falls back to its exposed light entities.
    Guards such as ``voice_block`` and ``voice_exclude_all`` are applied by the
    caller after this selection and are never bypassed.
    """
    lights = [entity for entity in entities if entity.domain == "light"]
    if qualifier:
        return [entity for entity in lights if matches_light_role(entity.labels, qualifier)]

    role = "voice_all_off" if action == Action.OFF else "voice_default_on"
    selected = [entity for entity in lights if matches_light_role(entity.labels, role)]
    if action == Action.OFF and not selected and not has_light_semantic_roles(lights):
        return lights
    return selected


def valid_profile(roles):
    return (
        isinstance(roles, (list, tuple, set, frozenset))
        and bool(roles)
        and all(isinstance(role, str) and role in PROFILE_ROLES for role in roles)
    )


def room_profile_overrides(options, area):
    rooms = options.get(PRESETS_OPTION, {})
    overrides = rooms.get(area, {}) if isinstance(rooms, dict) else {}
    return overrides if isinstance(overrides, dict) else {}


def profiles_for(options, area):
    """Return independent sets; invalid saved data cannot grant extra permissions."""
    overrides = room_profile_overrides(options, area)
    result = {}
    for key, roles in DEFAULT_PROFILES.items():
        # Prefer an explicit ambient recipe; recover an older decorative recipe
        # when no ambient override exists. Keep stored overrides intact until editing.
        selected = overrides.get(key)
        if key == "ambient" and key not in overrides:
            selected = overrides.get("accent")
        result[key] = canonical_roles(selected) if valid_profile(selected) else roles
    return result


def options_with_profile(options, area, key, roles):
    """Update a room recipe. None restores the default for this recipe only."""
    if key not in DEFAULT_PROFILES or (roles is not None and not valid_profile(roles)):
        raise ValueError("Invalid profile")
    stored = options.get(PRESETS_OPTION, {})
    rooms = dict(stored) if isinstance(stored, dict) else {}
    recipes = dict(room_profile_overrides(options, area))
    if roles is None:
        recipes.pop(key, None)
        if key == "ambient":
            recipes.pop("accent", None)
    else:
        recipes[key] = sorted(set(roles))
    if recipes:
        rooms[area] = recipes
    else:
        rooms.pop(area, None)
    return {**options, PRESETS_OPTION: rooms}


def apply_profile(current, selected, roles, *, replace=False):
    """Change only explicitly selected lights, preserving guards and other labels."""
    if not selected or not set(selected) <= current.keys() or not valid_profile(roles):
        raise ValueError("Invalid profile selection")
    return {
        key: (current[key] - PROFILE_ROLES if replace else current[key]) | frozenset(roles)
        for key in selected
    }
