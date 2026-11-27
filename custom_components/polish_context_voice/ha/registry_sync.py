"""Read canonical metadata; retain only state attributes used by the engine."""

from dataclasses import replace

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from ..core.home_model import Area, Entity, Floor, HomeModel

GUARDS = {"voice_block", "voice_exclude_all", "voice_confirm", "voice_high_risk"}
STATE_ATTRIBUTES = {
    "entity_id",
    "supported_color_modes",
    "supported_features",
    "min_temp",
    "max_temp",
    "temperature_unit",
    "unit_of_measurement",
    "device_class",
    "friendly_name",
    "temperature",
    "wind_speed",
    "wind_gust_speed",
    "wind_speed_unit",
    "group_members",
    "active_queue",
    "media_content_id",
    "media_content_type",
    "media_title",
    "media_artist",
}


def state_attributes(hass, entity_id, state):
    attributes = (
        {key: value for key, value in state.attributes.items() if key in STATE_ATTRIBUTES}
        if state
        else {}
    )
    if entity_id.startswith("climate."):
        attributes["temperature_unit"] = hass.config.units.temperature_unit
    return attributes


def update_state(hass, cache, event):
    """Ordinary measurements do not invalidate registry/alias indexes."""
    if cache.model is None or cache.dirty:
        return
    entity_id = event.data["entity_id"]
    current = cache.model.entities.get(entity_id)
    state = event.data.get("new_state")
    if current is None or state is None:
        cache.invalidate()
        return
    attributes = state_attributes(hass, entity_id, state)
    if any(
        attributes.get(key) != current.attributes.get(key)
        for key in ("friendly_name", "device_class")
    ):
        cache.invalidate()
        return
    cache.update_entity(
        replace(
            current,
            state=state.state,
            available=state.state not in {"unknown", "unavailable"},
            attributes=attributes,
        )
    )


def snapshot(hass):
    entity_registry, device_registry = er.async_get(hass), dr.async_get(hass)
    labels = {label.label_id: label.name for label in lr.async_get(hass).async_list_labels()}

    def semantic(ids):
        # IDs may be suffixed or UUID-like; use exact canonical label name, never a slug guess.
        return frozenset(labels[label_id] for label_id in ids if label_id in labels)

    areas = [
        Area(a.id, a.name, tuple(a.aliases), a.floor_id, semantic(a.labels))
        for a in ar.async_get(hass).async_list_areas()
    ]
    area_map = {a.id: a for a in areas}
    floors = [
        Floor(f.floor_id, f.name, tuple(f.aliases)) for f in fr.async_get(hass).async_list_floors()
    ]
    entities, satellites, devices = [], {}, {}
    registered = dict(entity_registry.entities)
    # Include legacy state-only entities for exposure and explicitly named control.
    ids = set(registered) | {s.entity_id for s in hass.states.async_all()}
    for entity_id in sorted(ids):
        entry = registered.get(entity_id)
        state = hass.states.get(entity_id)
        device = device_registry.async_get(entry.device_id) if entry and entry.device_id else None
        area_id = er.async_get_effective_area_id(hass, entry) if entry else None
        if device:
            devices[device.id] = dr.async_get_effective_area_id(hass, device)
        roles = semantic(entry.labels) if entry else frozenset()
        # Role labels belong to entities. Parent/device/area only propagate restrictions.
        if device:
            roles |= semantic(device.labels) & GUARDS
            parent_id = getattr(device, "parent_device_id", None)
            if parent_id and (parent := device_registry.async_get(parent_id)):
                roles |= semantic(parent.labels) & GUARDS
        if area_id in area_map:
            roles |= area_map[area_id].labels & GUARDS
        attrs = state_attributes(hass, entity_id, state)
        enabled = entry is None or entry.disabled_by is None
        entity = Entity(
            id=entity_id,
            name=attrs.get("friendly_name")
            or (entry.name or entry.original_name if entry else None)
            or entity_id,
            area_id=area_id,
            # 2026.10 aliases may contain COMPUTED_NAME, not just strings.
            aliases=tuple(er.async_get_entity_aliases(hass, entry)) if entry else (),
            labels=roles,
            device_class=(entry.device_class if entry else None)
            or attrs.get("device_class")
            or (entry.original_device_class if entry else None),
            exposed=enabled
            and exposed_entities.async_should_expose(hass, "conversation", entity_id),
            available=enabled
            and state is not None
            and state.state not in {"unavailable", "unknown"},
            state=state.state if state else "unavailable",
            attributes=attrs,
            device_id=entry.device_id if entry else None,
            device_name=(device.name_by_user or device.name) if device else None,
            platform=getattr(entry, "platform", None),
            config_entry_id=getattr(entry, "config_entry_id", None),
            unique_id=getattr(entry, "unique_id", None),
        )
        entities.append(entity)
        if entity.domain == "assist_satellite" and enabled:
            satellites[entity_id] = area_id
    return HomeModel(areas, floors, entities, satellites, devices)
