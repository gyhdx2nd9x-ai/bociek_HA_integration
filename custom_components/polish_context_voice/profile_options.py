"""Room light categories and editable recipes, using only native HA forms."""

import probatio as vol

from .core.home_model.light_profiles import (
    DEFAULT_PROFILES,
    LIGHT_KINDS,
    PROFILE_ROLES,
    VISIBLE_KINDS,
    apply_profile,
    matches_light_role,
    options_with_profile,
    profiles_for,
    valid_profile,
)
from .ha.role_editor import RolePlan
from .music_options import choices
from .role_options import UNASSIGNED, entity_select, role_select

PROFILE_NAMES = {
    "main": "Główne",
    "task": "Robocze",
    "ambient": "Dekoracyjne i nastrojowe",
}


class ProfileOptions:
    def _room_profiles(self):
        return profiles_for(self.values, self._voice_area or UNASSIGNED)

    def _read_lights(self):
        self._voice_read()
        return {
            key: record
            for key, record in self._voice_records.items()
            if record.entity.domain == "light"
        }

    async def async_step_voice_light_types(self, user_input=None):
        self._voice_review_cancel = "voice_lighting"
        if user_input is None and not getattr(self, "_voice_return", False):
            self._kind_records = self._read_lights()
            self._kind_draft = {
                role: sorted(
                    key
                    for key, record in self._kind_records.items()
                    if matches_light_role(record.direct, role)
                )
                for role in VISIBLE_KINDS
            }
        self._voice_return = False
        if not self._kind_records:
            return self.async_show_form(step_id="voice_empty", data_schema=vol.Schema({}))
        issues = {}
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_voice_lighting()
            draft = {role: user_input.get(role, []) for role in VISIBLE_KINDS}
            if any(not set(ids) <= self._kind_records.keys() for ids in draft.values()):
                issues["base"] = "voice_invalid_selection"
            else:
                self._kind_draft = draft
                desired = {
                    key: (record.direct - LIGHT_KINDS)
                    | frozenset(role for role in VISIBLE_KINDS if key in draft[role])
                    for key, record in self._kind_records.items()
                }
                self._voice_plan = RolePlan(self._kind_records, self._voice_labels, desired)
                if self._voice_plan.conflicts():
                    issues["base"] = "voice_conflict"
                else:
                    self._voice_back = "voice_light_types"
                    return await self.async_step_voice_review()
        return self.async_show_form(
            step_id="voice_light_types",
            last_step=False,
            errors=issues,
            description_placeholders={"room": self._voice_area_name},
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    **{
                        vol.Optional(role, default=self._kind_draft[role]): entity_select(
                            self._kind_records
                        )
                        for role in VISIBLE_KINDS
                    },
                }
            ),
        )

    async def async_step_voice_preset(self, user_input=None):
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_voice_lighting()
            self._profile_key = user_input.get("profile")
            if self._profile_key not in DEFAULT_PROFILES:
                return self.async_abort(reason="voice_invalid_selection")
            self._voice_return = False
            return await self.async_step_voice_preset_apply()
        return self.async_show_form(
            step_id="voice_preset",
            last_step=False,
            description_placeholders={"room": self._voice_area_name},
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional("profile", default="main"): choices(PROFILE_NAMES.items()),
                }
            ),
        )

    async def async_step_voice_preset_apply(self, user_input=None):
        self._voice_review_cancel = "voice_lighting"
        if user_input is None and not getattr(self, "_voice_return", False):
            self._profile_records = self._read_lights()
            self._profile_recipe = self._room_profiles()[self._profile_key]
            self._profile_draft = {"entities": [], "replace": False}
        self._voice_return = False
        if not self._profile_records:
            return self.async_show_form(step_id="voice_empty", data_schema=vol.Schema({}))
        issues = {}
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_voice_preset()
            if self._room_profiles()[self._profile_key] != self._profile_recipe:
                return self.async_abort(reason="voice_stale")
            self._profile_draft = {
                "entities": user_input.get("entities", []),
                "replace": user_input.get("replace", False),
            }
            try:
                desired = apply_profile(
                    {key: record.direct for key, record in self._profile_records.items()},
                    self._profile_draft["entities"],
                    self._profile_recipe,
                    replace=self._profile_draft["replace"],
                )
            except ValueError:
                issues["base"] = "voice_empty_selection"
            else:
                self._voice_plan = RolePlan(self._profile_records, self._voice_labels, desired)
                if self._voice_plan.conflicts():
                    issues["base"] = "voice_conflict"
                else:
                    self._voice_back = "voice_preset_apply"
                    return await self.async_step_voice_review()
        return self.async_show_form(
            step_id="voice_preset_apply",
            last_step=False,
            errors=issues,
            description_placeholders={
                "room": self._voice_area_name,
                "profile": PROFILE_NAMES[self._profile_key],
                "roles": ", ".join(sorted(self._profile_recipe)),
            },
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional(
                        "entities", default=self._profile_draft["entities"]
                    ): entity_select(self._profile_records),
                    vol.Optional("replace", default=self._profile_draft["replace"]): bool,
                }
            ),
        )

    async def async_step_voice_profiles(self, user_input=None):
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_voice_lighting()
            self._profile_key = user_input.get("profile")
            if self._profile_key not in DEFAULT_PROFILES:
                return self.async_abort(reason="voice_invalid_selection")
            return await self.async_step_voice_profile_edit()
        return self.async_show_form(
            step_id="voice_profiles",
            last_step=False,
            description_placeholders={"room": self._voice_area_name},
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional("profile", default="main"): choices(PROFILE_NAMES.items()),
                }
            ),
        )

    async def async_step_voice_profile_edit(self, user_input=None):
        issues = {}
        selected = sorted(self._room_profiles()[self._profile_key])
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_voice_profiles()
            selected = user_input.get("roles", [])
            if not user_input.get("reset") and not valid_profile(selected):
                issues["base"] = "voice_invalid_profile"
            else:
                area = self._voice_area or UNASSIGNED
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options=options_with_profile(
                        self.config_entry.options,
                        area,
                        self._profile_key,
                        None if user_input.get("reset") else selected,
                    ),
                )
                return await self.async_step_voice_profile_saved()
        return self.async_show_form(
            step_id="voice_profile_edit",
            last_step=False,
            errors=issues,
            description_placeholders={
                "room": self._voice_area_name,
                "profile": PROFILE_NAMES[self._profile_key],
            },
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional("roles", default=selected): role_select(PROFILE_ROLES),
                    vol.Optional("reset", default=False): bool,
                }
            ),
        )

    async def async_step_voice_profile_saved(self, user_input=None):
        return self.async_show_menu(
            step_id="voice_profile_saved",
            description_placeholders={
                "room": self._voice_area_name,
                "profile": PROFILE_NAMES[self._profile_key],
            },
            menu_options=["voice_preset", "voice_profiles", "voice_lighting", "init"],
        )
