"""Read-only preview action for HA Developer Tools; identity comes from HA Context."""

import probatio as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service

from ..const import DOMAIN
from ..core.types import Request

SCHEMA = vol.Schema(
    {
        vol.Required("text"): vol.All(str, vol.Length(min=1, max=2000)),
        vol.Optional("source_area"): vol.All(str, vol.Length(min=1)),
        vol.Optional("satellite_id"): vol.All(str, vol.Length(min=1)),
        vol.Optional("device_id"): vol.All(str, vol.Length(min=1)),
        vol.Optional("conversation_id"): vol.All(str, vol.Length(min=1)),
    }
)


@callback
def register(hass):
    async def preview(call):
        current = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if current is None:
            raise ServiceValidationError("HA Polish Context Voice is not loaded")
        result = await current.process(
            Request(**call.data, user_id=call.context.user_id, dry_run=True), call.context
        )
        return result.to_dict()

    # Registered at integration setup, including while its config entry is unloaded.
    # Resolving runtime per call keeps the service valid after a reload.
    hass.services.async_register(
        DOMAIN, "preview", preview, schema=SCHEMA, supports_response=SupportsResponse.ONLY
    )

    async def self_test(call):
        from .self_test import async_run_self_test

        user = (
            await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
        )
        if user is None or not user.is_active or not user.is_admin:
            raise ServiceValidationError("Test Boćka wymaga konta administratora HA.")
        current = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if current is None:
            raise ServiceValidationError("HA Polish Context Voice is not loaded")
        return await async_run_self_test(hass, current)

    async_register_admin_service(
        hass,
        DOMAIN,
        "self_test",
        self_test,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
