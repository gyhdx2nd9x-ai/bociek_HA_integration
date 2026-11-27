"""Reviewable writes to native registries. This module never calls device services."""

import logging
from dataclasses import dataclass

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from ..core.home_model.role_catalog import CATALOG, COLLECTIVE, GUARD_ROLES
from .registry_sync import snapshot

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Record:
    entity: object
    label_ids: frozenset[str]
    direct: frozenset[str]
    inherited: tuple
    stamp: tuple


def read_roles(hass, model=None):
    if model is None:
        model = snapshot(hass)
    labels = {label.label_id: label.name for label in lr.async_get(hass).async_list_labels()}
    devices, areas = dr.async_get(hass), ar.async_get(hass)
    records = {}
    for entry in er.async_get(hass).entities.values():
        entity = model.entities[entry.entity_id]
        device = devices.async_get(entry.device_id) if entry.device_id else None
        parent_id = getattr(device, "parent_device_id", None)
        parent_device = devices.async_get(parent_id) if parent_id else None
        parent_name = (parent_device.name_by_user or parent_device.name) if parent_device else None
        area = areas.async_get_area(entity.area_id) if entity.area_id else None
        parents = []
        for parent, name in (
            (device, entity.device_name),
            (parent_device, parent_name),
            (area, area.name if area else None),
        ):
            if parent:
                roles = tuple(
                    sorted(labels[i] for i in parent.labels if labels.get(i) in GUARD_ROLES)
                )
                if roles:
                    parents.append((name or parent.id, roles))
        ids = frozenset(entry.labels)
        direct = frozenset(labels[i] for i in ids if labels.get(i) in CATALOG)
        members = entity.attributes.get("entity_id", ())
        if isinstance(members, str):
            members = (members,)
        stamp = (
            ids,
            entry.disabled_by,
            entity.exposed,
            entity.area_id,
            entity.device_id,
            tuple(parents),
            tuple(sorted(members or ())),
            entity.name,
            entity.device_name,
        )
        records[entity.id] = Record(entity, ids, direct, tuple(parents), stamp)
    return records, tuple(sorted(labels.items()))


@dataclass
class RolePlan:
    baseline: dict[str, Record]
    label_stamp: tuple
    desired: dict[str, frozenset[str]]

    @property
    def changes(self):
        return {
            key: roles for key, roles in self.desired.items() if roles != self.baseline[key].direct
        }

    def conflicts(self):
        for key, roles in self.changes.items():
            inherited = {role for _, roles in self.baseline[key].inherited for role in roles}
            if roles & COLLECTIVE and (roles | inherited) & {
                "voice_block",
                "voice_exclude_all",
                "voice_high_risk",
            }:
                return True
        return False


def apply_roles(hass, plan):
    """Validate the complete displayed scope, then write without yielding the event loop.

    HA has no registry transaction. Return exact completed IDs on partial failure;
    never overwrite later user edits and never claim a partial write was atomic.
    """
    current, labels = read_roles(hass)
    if labels != plan.label_stamp or any(
        key not in current or current[key].stamp != record.stamp
        for key, record in plan.baseline.items()
    ):
        return [], "voice_stale"
    if plan.conflicts():
        return [], "voice_conflict"
    label_registry, registry = lr.async_get(hass), er.async_get(hass)
    needed = set().union(*plan.changes.values()) if plan.changes else set()
    # HA names are case-insensitively unique; PCV roles require exact canonical names.
    for role in needed:
        existing = label_registry.async_get_label_by_name(role)
        if existing and existing.name != role:
            return [], "voice_label_name"
    completed = []
    try:
        role_ids = {}
        for role in sorted(needed):
            label = label_registry.async_get_label_by_name(role)
            if label is None:
                label = label_registry.async_create(
                    role, icon="mdi:microphone", description=CATALOG[role].description
                )
            role_ids[role] = label.label_id
        names = dict(labels)
        for key, desired in plan.changes.items():
            unrelated = {i for i in plan.baseline[key].label_ids if names.get(i) not in CATALOG}
            registry.async_update_entity(
                key, labels=unrelated | {role_ids[role] for role in desired}
            )
            completed.append(key)
    except Exception:  # Report actual partial progress; a fresh review is required to retry.
        _LOGGER.exception("Voice role editor stopped after updating %s entities", len(completed))
        return completed, "voice_write_failed"
    return completed, None
