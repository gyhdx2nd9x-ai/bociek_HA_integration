"""A manual test and report in HA's existing admin-only options flow."""

import probatio as vol
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .ha.self_test import async_run_self_test


class SelfTestOptions:
    async def async_step_self_test(self, user_input=None):
        if user_input is not None and user_input.get("back"):
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            if runtime is None:
                errors["base"] = "self_test_unavailable"
            else:
                try:
                    result = await async_run_self_test(self.hass, runtime)
                except ServiceValidationError:
                    errors["base"] = "self_test_busy"
                else:
                    self._self_test_counts = {
                        key: str(result["summary"][key]) for key in ("pass", "fail", "warn", "skip")
                    }
                    return await self.async_step_self_test_result()
        return self.async_show_form(
            step_id="self_test",
            last_step=False,
            data_schema=vol.Schema({vol.Optional("back", default=False): bool}),
            errors=errors,
        )

    async def async_step_self_test_result(self, user_input=None):
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="self_test_result",
            last_step=False,
            data_schema=vol.Schema({vol.Optional("back", default=False): bool}),
            description_placeholders=self._self_test_counts,
        )
