"""Optional content sources; accounts and music speakers stay in their own settings."""

import probatio as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector


class ContentOptions:
    async def async_step_content(self, user_input=None):
        if user_input is not None and user_input.get("back"):
            return await self.async_step_init()
        values = {**self.config_entry.data, **self.config_entry.options}
        issues = {}
        if user_input is not None:
            entity_id = (user_input.get("stoic_entity_id") or "").strip()
            raw_username = user_input.get("media_search_username") or ""
            username = raw_username.strip()
            if entity_id and (
                not entity_id.startswith("sensor.")
                or (
                    self.hass.states.get(entity_id) is None
                    and er.async_get(self.hass).async_get(entity_id) is None
                )
            ):
                issues["stoic_entity_id"] = "content_sensor_missing"
            if len(username) > 128 or any(ord(char) < 32 for char in raw_username):
                issues["media_search_username"] = "content_username_invalid"
            if not issues:
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        "stoic_entity_id": entity_id or None,
                        "media_search_enabled": user_input.get("media_search_enabled", True),
                        "media_search_library_first": user_input.get(
                            "media_search_library_first", False
                        ),
                        "media_search_username": username or None,
                    },
                )
            values = {**values, **user_input}
        return self.async_show_form(
            step_id="content",
            last_step=False,
            errors=issues,
            data_schema=vol.Schema(
                {
                    vol.Optional("back", default=False): bool,
                    vol.Optional(
                        "stoic_entity_id",
                        description={"suggested_value": values.get("stoic_entity_id")},
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        "media_search_enabled", default=values.get("media_search_enabled", True)
                    ): bool,
                    vol.Optional(
                        "media_search_library_first",
                        default=values.get("media_search_library_first", False),
                    ): bool,
                    vol.Optional(
                        "media_search_username",
                        description={"suggested_value": values.get("media_search_username") or ""},
                    ): str,
                }
            ),
        )
