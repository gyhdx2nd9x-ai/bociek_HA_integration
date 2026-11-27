"""Room wizard using native HA forms; drafts are private to an options flow."""

from collections import Counter

import probatio as vol
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import selector

from .core.home_model.light_profiles import canonical_roles
from .core.home_model.role_catalog import CATALOG, LIGHT_ROLES, allowed_roles, suggest_lights
from .ha.role_editor import RolePlan, apply_roles, read_roles
from .music_options import choices

UNASSIGNED = "__unassigned__"


def action_select(key, options):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=key,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def role_select(roles):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                {"value": role, "label": f"{CATALOG[role].title} — {CATALOG[role].description}"}
                for role in CATALOG
                if role in roles and role != "voice_accent"
            ],
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def entity_select(records, *, multiple=True):
    names = Counter(record.entity.name for record in records.values())
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                {
                    "value": key,
                    "label": (
                        record.entity.name
                        + (f" · {key}" if names[record.entity.name] > 1 else "")
                        + (
                            f" · {record.entity.device_name}"
                            if not multiple
                            and record.entity.device_name
                            and record.entity.device_name != record.entity.name
                            else ""
                        )
                        + (" · poza Assist" if not record.entity.exposed else "")
                        + (" · ograniczenia z urządzenia/pokoju" if record.inherited else "")
                    ),
                }
                for key, record in sorted(
                    records.items(), key=lambda item: (item[1].entity.name, item[0])
                )
            ],
            multiple=multiple,
            mode=(
                selector.SelectSelectorMode.LIST
                if multiple and len(records) <= 12
                else selector.SelectSelectorMode.DROPDOWN
            ),
        )
    )


class RoleOptions:
    async def async_step_voice_roles(self, user_input=None):
        areas = list(ar.async_get(self.hass).async_list_areas())
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()
            area = user_input.get("area", "")
            if area != UNASSIGNED and area not in {a.id for a in areas}:
                return self.async_abort(reason="voice_stale")
            self._voice_area = None if area == UNASSIGNED else area
            self._voice_hidden = user_input.get("include_hidden", False)
            self._voice_area_name = next(
                (a.name for a in areas if a.id == area), "Bez pomieszczenia"
            )
            return await self.async_step_voice_room()
        return self.async_show_form(
            step_id="voice_roles",
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional("area"): choices(
                        [(a.id, a.name) for a in sorted(areas, key=lambda a: a.name)]
                        + [(UNASSIGNED, "Bez przypisanego pomieszczenia")]
                    ),
                    vol.Optional("include_hidden", default=False): bool,
                }
            ),
        )

    def _voice_read(self):
        records, self._voice_labels = read_roles(self.hass)
        self._voice_records = {
            key: record
            for key, record in records.items()
            if record.entity.area_id == self._voice_area
            and (record.entity.exposed or self._voice_hidden)
        }

    async def async_step_voice_room(self, user_input=None):
        self._voice_plan = None
        self._voice_return = False
        self._voice_read()
        return self.async_show_menu(
            step_id="voice_room",
            description_placeholders={
                "room": self._voice_area_name,
                "count": str(len(self._voice_records)),
                "on_count": str(
                    sum("voice_default_on" in r.direct for r in self._voice_records.values())
                ),
                "off_count": str(
                    sum("voice_all_off" in r.direct for r in self._voice_records.values())
                ),
            },
            menu_options=["voice_lighting", "voice_entity", "voice_roles", "init", "voice_done"],
        )

    async def async_step_voice_lighting(self, user_input=None):
        self._voice_plan = None
        return self.async_show_menu(
            step_id="voice_lighting",
            description_placeholders={"room": self._voice_area_name},
            menu_options=[
                "voice_lights",
                "voice_light_types",
                "voice_preset",
                "voice_profiles",
                "voice_room",
                "init",
            ],
        )

    async def async_step_voice_lights(self, user_input=None):
        self._voice_review_cancel = "voice_lighting"
        issues = {}
        if user_input is None and not getattr(self, "_voice_return", False):
            self._voice_read()
            self._voice_lights = {
                key: record
                for key, record in self._voice_records.items()
                if record.entity.domain == "light"
            }
            self._voice_light_draft = {
                role: sorted(
                    key for key, record in self._voice_lights.items() if role in record.direct
                )
                for role in LIGHT_ROLES
            }
        self._voice_return = False
        if not self._voice_lights:
            return self.async_show_form(step_id="voice_empty", data_schema=vol.Schema({}))
        if user_input is not None:
            action = user_input.get("action", "review")
            if action == "back":
                return await self.async_step_voice_lighting()
            draft = {role: user_input.get(role, []) for role in LIGHT_ROLES}
            if any(not set(ids) <= self._voice_lights.keys() for ids in draft.values()):
                issues["base"] = "voice_invalid_selection"
            else:
                self._voice_light_draft = draft
                if action == "suggest":
                    suggestions = suggest_lights(
                        [record.entity for record in self._voice_lights.values()]
                    )
                    # Only add suggestions to the current draft; a review still follows.
                    self._voice_light_draft = {
                        role: sorted(set(draft[role]) | set(suggestions[role]))
                        for role in LIGHT_ROLES
                    }
                else:
                    desired = {
                        key: (record.direct - LIGHT_ROLES)
                        | frozenset(role for role in LIGHT_ROLES if key in draft[role])
                        for key, record in self._voice_lights.items()
                    }
                    self._voice_plan = RolePlan(self._voice_lights, self._voice_labels, desired)
                    if self._voice_plan.conflicts():
                        issues["base"] = "voice_conflict"
                    else:
                        self._voice_back = "voice_lights"
                        return await self.async_step_voice_review()
        return self.async_show_form(
            step_id="voice_lights",
            last_step=False,
            errors=issues,
            description_placeholders={"room": self._voice_area_name},
            data_schema=vol.Schema(
                {
                    **{
                        vol.Optional(role, default=self._voice_light_draft[role]): entity_select(
                            self._voice_lights
                        )
                        for role in ("voice_default_on", "voice_all_off")
                    },
                    vol.Required("action", default="review"): action_select(
                        "voice_light_action", ["review", "suggest", "back"]
                    ),
                }
            ),
        )

    async def async_step_voice_entity(self, user_input=None):
        self._voice_review_cancel = "voice_room"
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_voice_room()
            key = user_input.get("entity", "")
            if key not in self._voice_records:
                return self.async_abort(reason="voice_invalid_selection")
            self._voice_entity = key
            self._voice_entity_draft = sorted(canonical_roles(self._voice_records[key].direct))
            return await self.async_step_voice_entity_roles()
        self._voice_read()
        if not self._voice_records:
            return self.async_show_form(step_id="voice_empty", data_schema=vol.Schema({}))
        return self.async_show_form(
            step_id="voice_entity",
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional("entity"): entity_select(self._voice_records, multiple=False),
                    vol.Optional("back", default=False): bool,
                }
            ),
        )

    async def async_step_voice_entity_roles(self, user_input=None):
        record = self._voice_records[self._voice_entity]
        allowed = allowed_roles(record.entity.domain, canonical_roles(record.direct))
        selected = self._voice_entity_draft
        issues = {}
        if user_input is not None:
            selected = user_input.get("roles", [])
            if user_input.get("back"):
                return await self.async_step_voice_entity()
            if not set(selected) <= allowed:
                issues["base"] = "voice_invalid_selection"
            else:
                self._voice_entity_draft = selected
                self._voice_plan = RolePlan(
                    {self._voice_entity: record},
                    self._voice_labels,
                    {self._voice_entity: frozenset(selected)},
                )
                if self._voice_plan.conflicts():
                    issues["base"] = "voice_conflict"
                else:
                    self._voice_back = "voice_entity_roles"
                    return await self.async_step_voice_review()
        inherited = (
            "\n".join(
                f"• {origin}: {', '.join(CATALOG[role].title for role in roles)}"
                for origin, roles in record.inherited
            )
            or "Brak."
        )
        return self.async_show_form(
            step_id="voice_entity_roles",
            last_step=False,
            errors=issues,
            description_placeholders={
                "entity": f"{record.entity.name} ({self._voice_entity})",
                "exposure": "Udostępniona Assist"
                if record.entity.exposed
                else "Poza Assist lub wyłączona",
                "inherited": inherited,
            },
            data_schema=vol.Schema(
                {
                    vol.Optional("roles", default=selected): role_select(allowed),
                    vol.Optional("back", default=False): bool,
                }
            ),
        )

    async def async_step_voice_review(self, user_input=None):
        plan = self._voice_plan
        if plan is None:
            return self.async_abort(reason="voice_stale")
        if user_input is not None:
            action = user_input["action"]
            if action == "edit":
                self._voice_return = True
                return await getattr(self, f"async_step_{self._voice_back}")()
            if action == "cancel":
                return await getattr(self, "async_step_" + self._voice_review_cancel)()
            if action != "apply":
                return self.async_abort(reason="voice_invalid_selection")
            completed, error = apply_roles(self.hass, plan)
            self._voice_plan = None  # Single use, including partial failure.
            self._voice_completed = completed
            if error:
                return self.async_abort(
                    reason=error,
                    description_placeholders={"completed": ", ".join(completed) or "brak"},
                )
            return await self.async_step_voice_saved()
        lines = []
        for key, desired in plan.changes.items():
            before = plan.baseline[key]
            added = ", ".join(CATALOG[r].title for r in sorted(desired - before.direct)) or "brak"
            removed = ", ".join(CATALOG[r].title for r in sorted(before.direct - desired)) or "brak"
            lines.append(
                f"**{before.entity.name}** (`{key}`)\n\n- Dodaj: {added}\n- Usuń: {removed}"
            )
        return self.async_show_form(
            step_id="voice_review",
            last_step=False,
            description_placeholders={"changes": "\n\n".join(lines) or "Brak zmian."},
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="edit"): action_select(
                        "voice_review_action", ["apply", "edit", "cancel"]
                    ),
                }
            ),
        )

    async def async_step_voice_saved(self, user_input=None):
        if user_input is not None:
            if user_input["action"] == "done":
                return await self.async_step_voice_done()
            return await self.async_step_voice_room()
        return self.async_show_form(
            step_id="voice_saved",
            last_step=False,
            description_placeholders={
                "completed": ", ".join(self._voice_completed) or "Brak zmian"
            },
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="room"): action_select(
                        "voice_saved_action", ["room", "done"]
                    ),
                }
            ),
        )

    async def async_step_voice_empty(self, user_input=None):
        return await self.async_step_voice_room()

    async def async_step_voice_done(self, user_input=None):
        # Roles already live in HA registries, not a duplicate config entry inventory.
        return self.async_create_entry(title="", data=dict(self.config_entry.options))
