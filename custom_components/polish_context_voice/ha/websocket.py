"""Authenticated HA-native API for the optional standalone HTTP gateway."""

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback

from ..const import DOMAIN
from ..core.types import Request
from .command_trial import preview
from .lighting_panel import lighting


def runtime(hass):
    return next(iter(hass.data.get(DOMAIN, {}).values()), None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "polish_context_voice/process",
        vol.Required("text"): vol.All(str, vol.Length(min=1, max=2000)),
        vol.Optional("source_area"): str,
        vol.Optional("satellite_id"): str,
        vol.Optional("device_id"): str,
        vol.Optional("conversation_id"): str,
        vol.Optional("dry_run", default=True): bool,
    }
)
@websocket_api.async_response
async def process(hass, connection, msg):
    if (current := runtime(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    request = Request(
        **{k: v for k, v in msg.items() if k not in {"id", "type"}}, user_id=connection.user.id
    )
    result = await current.process(request, connection.context(msg))
    connection.send_result(msg["id"], result.to_dict())


@websocket_api.websocket_command({vol.Required("type"): "polish_context_voice/health"})
@websocket_api.require_admin
@callback
def health(hass, connection, msg):
    if (current := runtime(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    connection.send_result(msg["id"], {"findings": current.health()})


@websocket_api.websocket_command({
    vol.Required("type"): "polish_context_voice/trial",
    vol.Optional("text"): vol.All(str, str.strip, vol.Length(min=1, max=400)),
    vol.Optional("area"): str,
})
@websocket_api.require_admin
@websocket_api.async_response
async def command_trial(hass, connection, msg):
    if (current := runtime(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    model = current.visible_model(connection.user)
    if "text" not in msg:
        connection.send_result(msg["id"], {"areas": [
            {"id": area.id, "name": area.name}
            for area in sorted(model.areas.values(), key=lambda item: item.name.casefold())
        ]})
        return
    area = msg.get("area")
    if area is not None and area not in model.areas:
        connection.send_error(msg["id"], "invalid_area", "Area is not available")
        return
    connection.send_result(msg["id"], await preview(model, msg["text"], area))


@callback
def register(hass):
    # Re-register on reload; commands resolve the currently loaded runtime at call time.
    websocket_api.async_register_command(hass, process)
    websocket_api.async_register_command(hass, health)
    websocket_api.async_register_command(hass, export_snapshot)
    websocket_api.async_register_command(hass, command_trial)
    websocket_api.async_register_command(hass, lighting)


@websocket_api.websocket_command({vol.Required("type"): "polish_context_voice/snapshot"})
@websocket_api.require_admin
@callback
def export_snapshot(hass, connection, msg):
    if (current := runtime(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Integration not loaded")
        return
    connection.send_result(msg["id"], current.cache.get(force=True).export())
