"""Single-instance local integration, with an optional semantic LLM endpoint."""

import re
from urllib.parse import urlparse

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .calendar_options import CalendarOptions
from .command_trial_options import CommandTrialOptions
from .const import DOMAIN
from .content_options import ContentOptions
from .core.maintenance import DEFAULT_BATTERY_THRESHOLD, valid_threshold
from .music_options import MusicOptions
from .navigation import NavigationOptions
from .profile_options import ProfileOptions
from .role_options import RoleOptions
from .self_test_options import SelfTestOptions


def schema(values=None, *, communication=False):
    values = values or {}
    fields = {
        vol.Optional("llm_endpoint", default=values.get("llm_endpoint", "")): str,
        vol.Optional("llm_model", default=values.get("llm_model", "")): str,
    }
    if communication:
        fields = {vol.Optional("back", default=False): bool, **fields}
        fields.update(
            {
                vol.Optional(
                    "maintenance_battery_threshold",
                    default=values.get("maintenance_battery_threshold", DEFAULT_BATTERY_THRESHOLD),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=50,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="%",
                    )
                ),
                vol.Optional(
                    "quiet_ack_enabled", default=values.get("quiet_ack_enabled", False)
                ): bool,
                vol.Optional("quiet_start", default=values.get("quiet_start", "20:00")): str,
                vol.Optional("quiet_end", default=values.get("quiet_end", "08:00")): str,
                vol.Optional(
                    "quiet_areas", default=values.get("quiet_areas", [])
                ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                vol.Optional(
                    "quiet_signals", default=values.get("quiet_signals", [])
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["input_boolean", "binary_sensor"], multiple=True
                    )
                ),
            }
        )
    return vol.Schema(fields)


def errors(data):
    if "maintenance_battery_threshold" in data and not valid_threshold(
        data["maintenance_battery_threshold"]
    ):
        return {"base": "invalid_battery_threshold"}
    for key in ("quiet_start", "quiet_end"):
        if key in data and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", data[key]):
            return {"base": "invalid_quiet_time"}
    endpoint = data.get("llm_endpoint", "")
    if not endpoint:
        return {}
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not data.get("llm_model", "").strip()
    ):
        return {"base": "invalid_endpoint"}
    return {}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        issues = errors(user_input) if user_input is not None else {}
        if user_input is not None and not issues:
            return self.async_create_entry(title="HA Polish Context Voice", data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(), errors=issues)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(
    ContentOptions,
    CommandTrialOptions,
    SelfTestOptions,
    CalendarOptions,
    ProfileOptions,
    RoleOptions,
    MusicOptions,
    NavigationOptions,
    config_entries.OptionsFlow,
):
    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "voice_roles",
                "voice_trial",
                "self_test",
                "general",
                "calendar",
                "calendar_kiosk",
                "content",
                "music_shortcuts",
                "music_rooms",
            ],
        )

    async def async_step_general(self, user_input=None):
        if user_input is not None and user_input.get("back"):
            return await self.async_step_init()
        issues = errors(user_input) if user_input is not None else {}
        if user_input is not None and not issues:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    **{k: v for k, v in user_input.items() if k != "back"},
                },
            )
        values = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="general",
            last_step=False,
            data_schema=schema(values, communication=True),
            errors=issues,
        )
