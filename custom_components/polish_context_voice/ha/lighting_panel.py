"""Read, preview and apply room lighting roles through the native role editor."""

import json
from collections import Counter
from dataclasses import replace
from hashlib import sha256

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.helpers import area_registry as ar

from ..const import DOMAIN
from ..core.home_model.light_profiles import (
    DEFAULT_PROFILES,
    LIGHT_KINDS,
    PROFILE_ROLES,
    VISIBLE_KINDS,
    matches_light_role,
    options_with_profile,
    profiles_for,
    room_profile_overrides,
)
from ..core.home_model.role_catalog import GUARD_ROLES, LIGHT_ROLES
from .command_trial import preview
from .registry_sync import snapshot
from .role_editor import RolePlan, apply_roles, read_roles

COMMANDS = {
    "on": "Włącz światło",
    "off": "Wyłącz światła",
    "voice_main_on": "Włącz światła główne",
    "voice_main_off": "Wyłącz światła główne",
    "voice_task_on": "Włącz światła robocze",
    "voice_task_off": "Wyłącz światła robocze",
    "voice_ambient_on": "Włącz światła dekoracyjne",
    "voice_ambient_off": "Wyłącz światła dekoracyjne",
    "preference": "Włącz lampę",
}
ON = "voice_default_on"
OFF = "voice_all_off"
PREFERRED = "voice_primary"
EDITABLE_PROFILE_ROLES = PROFILE_ROLES - {"voice_accent"}
EDITABLE_LIGHT_ROLES = PROFILE_ROLES | {PREFERRED}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_room(hass, area, profiles):
    model = snapshot(hass)
    if area not in model.areas:
        raise ValueError("invalid_area")
    records, labels = read_roles(hass, model)
    lights = sorted(
        (e for e in model.entities.values() if e.domain == "light" and e.area_id == area),
        key=lambda entity: (entity.name.casefold(), entity.id),
    )
    name_counts = Counter(light.name for light in lights)
    scoped = {light.id: records[light.id] for light in lights if light.id in records}
    rows = [
        {
            "id": light.id,
            "name": light.name if name_counts[light.name] == 1 else f"{light.name} ({light.id})",
            "exposed": light.exposed,
            "available": light.available,
            "editable": light.id in records,
            "on": ON in records[light.id].direct if light.id in records else False,
            "off": OFF in records[light.id].direct if light.id in records else False,
            "preferred": PREFERRED in records[light.id].direct if light.id in records else False,
            "categories": [
                role for role in VISIBLE_KINDS if matches_light_role(light.labels, role)
            ],
            "guards": sorted(light.labels & GUARD_ROLES),
        }
        for light in lights
    ]
    revision = digest({
        "area": area,
        "rows": rows,
        "labels": labels,
        "roles": {key: sorted(record.direct) for key, record in scoped.items()},
        "inherited": {key: record.inherited for key, record in scoped.items()},
        "profiles": profiles,
    })
    return model, RolePlan(scoped, labels, {}), rows, revision


async def preview_room(model, area, names, commands):
    return {
        key: await preview(model, command, area, names=names) for key, command in commands.items()
    }


def desired_roles(direct, change):
    selected = {role for key, role in (("on", ON), ("off", OFF)) if change[key]}
    roles = (direct - LIGHT_ROLES) | selected
    if "preferred" in change:
        roles = (roles - {PREFERRED}) | ({PREFERRED} if change["preferred"] else set())
    if "categories" not in change:
        return frozenset(roles)
    categories = set(change["categories"])
    # Keep the old decorative label while its owner keeps the category selected.
    if "voice_ambient" in categories and "voice_accent" in direct:
        categories.add("voice_accent")
        if "voice_ambient" not in direct:
            categories.remove("voice_ambient")
    return frozenset((roles - LIGHT_KINDS) | categories)


def save_profile(hass, entry, connection, msg, revision):
    update = msg.get("profile")
    if update is None or "changes" in msg or "review" in msg:
        connection.send_error(msg["id"], "voice_invalid_profile", "Invalid profile update")
        return
    if msg.get("revision") != revision:
        connection.send_error(msg["id"], "voice_profile_stale", "Room presets changed")
        return
    roles = update["roles"]
    if roles is not None and len(set(roles)) != len(roles):
        connection.send_error(msg["id"], "voice_invalid_profile", "Duplicate profile roles")
        return
    hass.config_entries.async_update_entry(
        entry, options=options_with_profile(entry.options, msg["area"], update["key"], roles),
    )
    connection.send_result(msg["id"])


@websocket_api.websocket_command({
    vol.Required("type"): DOMAIN + "/lighting",
    vol.Required("area"): vol.All(str, vol.Length(min=1, max=255)),
    vol.Optional("action", default="read"): vol.In(("read", "preview", "save", "save_profile")),
    vol.Optional("revision"): vol.All(str, vol.Length(min=64, max=64)),
    vol.Optional("review"): vol.All(str, vol.Length(min=64, max=64)),
    vol.Optional("choice_command"): vol.All(str, str.strip, vol.Length(min=1, max=400)),
    vol.Optional("profile"): {
        vol.Required("key"): vol.In(tuple(DEFAULT_PROFILES)),
        vol.Required("roles"): vol.Any(
            None,
            vol.All([vol.In(EDITABLE_PROFILE_ROLES)], vol.Length(min=1, max=5)),
        ),
    },
    vol.Optional("changes"): vol.All([
        {
            vol.Required("id"): str, vol.Required("on"): bool, vol.Required("off"): bool,
            vol.Optional("preferred"): bool,
            vol.Optional("categories"): vol.All(
                [vol.In(VISIBLE_KINDS)], vol.Length(max=len(VISIBLE_KINDS)),
            ),
        }
    ], vol.Length(min=1, max=500)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def lighting(hass, connection, msg):
    area, action = msg["area"], msg["action"]
    active = hass.data.get(DOMAIN, {})
    entry = hass.config_entries.async_get_entry(next(iter(active))) if active else None
    if entry is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    options = {**entry.data, **entry.options}
    overrides = room_profile_overrides(options, area)
    profile_revision = digest({"area": area, "overrides": overrides})
    if action == "save_profile":
        if ar.async_get(hass).async_get_area(area) is None:
            connection.send_error(msg["id"], "invalid_area", "Room not found")
            return
        save_profile(hass, entry, connection, msg, profile_revision)
        return
    profiles = {
        key: sorted(roles)
        for key, roles in profiles_for(options, area).items()
    }
    try:
        model, plan, rows, revision = read_room(hass, area, profiles)
    except ValueError:
        connection.send_error(msg["id"], "invalid_area", "Room not found")
        return
    names = {row["id"]: row["name"] for row in rows}
    commands = {**COMMANDS, "preference": msg.get("choice_command", COMMANDS["preference"])}
    if action == "read":
        connection.send_result(msg["id"], {
            "room": model.areas[area].name, "lights": rows, "revision": revision,
            "plans": await preview_room(model, area, names, commands), "commands": commands,
            "profiles": profiles, "profile_revision": profile_revision,
            "profile_defaults": {key: sorted(roles) for key, roles in DEFAULT_PROFILES.items()},
            "custom_profiles": [
                key for key in profiles
                if key in overrides or (key == "ambient" and "accent" in overrides)
            ],
        })
        return
    changes = msg.get("changes", [])
    ids = {row["id"] for row in changes}
    if (
        not changes or len(ids) != len(changes) or not ids <= plan.baseline.keys()
        or any(len(set(cats)) != len(cats) for row in changes if (cats := row.get("categories")))
    ):
        connection.send_error(msg["id"], "voice_invalid_selection", "Invalid lighting selection")
        return
    if msg.get("revision") != revision:
        connection.send_error(msg["id"], "voice_stale", "Lighting setup changed")
        return
    plan.desired.update({
        row["id"]: desired_roles(plan.baseline[row["id"]].direct, row) for row in changes
    })
    if plan.conflicts():
        connection.send_error(msg["id"], "voice_conflict", "Lighting roles conflict with a guard")
        return
    changed = plan.changes
    draft = model.with_entities(
        replace(
            entity,
            labels=(entity.labels - EDITABLE_LIGHT_ROLES)
            | (changed[entity.id] & EDITABLE_LIGHT_ROLES),
        )
        if entity.id in changed else entity
        for entity in model.entities.values()
    )
    before = await preview_room(model, area, names, commands)
    after = await preview_room(draft, area, names, commands)
    review = digest({
        "revision": revision, "before": before, "after": after,
        "commands": commands,
        "changes": {key: sorted(roles) for key, roles in changed.items()},
    })
    if action == "preview":
        connection.send_result(msg["id"], {
            "before": before, "after": after, "review": review, "commands": commands,
        })
        return
    if msg.get("review") != review:
        connection.send_error(msg["id"], "voice_review_changed", "Review the updated lighting plan")
        return
    completed, error = apply_roles(hass, plan)
    connection.send_result(msg["id"], {"completed": completed, "error": error})
