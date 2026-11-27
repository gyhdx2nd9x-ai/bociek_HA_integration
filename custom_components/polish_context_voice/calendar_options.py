"""Calendar preferences and an optional, explicitly addressed Kiosk display."""

import probatio as vol
from homeassistant.helpers import selector

from .core.calendar import kiosk_conflict


class CalendarOptions:
    async def async_step_calendar(self, user_input=None):
        if user_input is not None and user_input.get("back"):
            return await self.async_step_init()
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    **{k: v for k, v in user_input.items() if k != "back"},
                    "calendar_default": user_input.get("calendar_default") or None,
                },
            )
        values = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="calendar",
            last_step=False,
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional(
                        "calendar_default",
                        **(
                            {"default": values["calendar_default"]}
                            if values.get("calendar_default")
                            else {}
                        ),
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar")),
                    vol.Optional(
                        "calendar_write_enabled",
                        default=values.get("calendar_write_enabled", False),
                    ): bool,
                    vol.Optional(
                        "calendar_default_duration",
                        default=values.get("calendar_default_duration", 60),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=1440,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                }
            ),
        )

    async def async_step_calendar_kiosk(self, user_input=None):
        from .ha.registry_sync import snapshot

        values = {**self.config_entry.data, **self.config_entry.options}
        mappings = dict(values.get("calendar_kiosks", {}))
        issues = {}
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()
            satellite = user_input.get("satellite", "")
            name = user_input.get("kiosk_name", "").strip()
            if not satellite:
                issues["base"] = "calendar_satellite_required"
            elif user_input.get("delete"):
                mappings.pop(satellite, None)
            elif not name or len(name) > 100:
                issues["base"] = "calendar_kiosk_name"
            elif kiosk_conflict(name, [v for k, v in mappings.items() if k != satellite]):
                issues["base"] = "calendar_kiosk_ambiguous"
            else:
                mappings[satellite] = name
            if not issues:
                return self.async_create_entry(
                    title="",
                    data={**self.config_entry.options, "calendar_kiosks": mappings},
                )
        model = snapshot(self.hass)
        current = (
            "\n".join(
                f"• {model.entities[key].name if key in model.entities else key}: {value}"
                for key, value in mappings.items()
            )
            or "—"
        )
        return self.async_show_form(
            step_id="calendar_kiosk",
            last_step=False,
            errors=issues,
            description_placeholders={"mappings": current},
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional("satellite"): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="assist_satellite")
                    ),
                    vol.Optional("kiosk_name", default=""): str,
                    vol.Optional("delete", default=False): bool,
                }
            ),
        )
