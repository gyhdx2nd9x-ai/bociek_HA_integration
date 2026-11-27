"""Register the bundled Bociek panel and options-flow navigation in HA."""

import hashlib
from pathlib import Path

from homeassistant.setup import async_when_setup

from ..const import DOMAIN

DATA_FRONTEND = DOMAIN + "_frontend"


def register(hass):
    async_when_setup(hass, "frontend", _register)


async def _register(hass, _component):
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.panel_custom import async_register_panel
    from homeassistant.loader import async_get_integration

    if DATA_FRONTEND in hass.data:
        return
    integration = await async_get_integration(hass, DOMAIN)
    root = Path(__file__).parents[1]
    paths, urls = [], {}
    for key, file in {
        "navigation": root / "frontend/navigation.js",
        "panel": root / "frontend/panel.js",
        "icon": root / "brand/icon.png",
    }.items():
        content = await hass.async_add_executor_job(file.read_bytes)
        digest = hashlib.sha256(content).hexdigest()[:16]
        url = f"/{DOMAIN}/frontend/{integration.version}/{key}-{digest}{file.suffix}"
        paths.append(StaticPathConfig(url, str(file), True))
        urls[key] = url
    await hass.http.async_register_static_paths(paths)
    add_extra_js_url(hass, urls["navigation"])
    await async_register_panel(
        hass, "bociek", "bociek-settings-panel", sidebar_title="Bociek",
        sidebar_icon="mdi:microphone-outline", module_url=urls["panel"],
        config={"icon": urls["icon"]}, require_admin=True, trust_external=True,
    )
    hass.data[DATA_FRONTEND] = {
        "url": urls["navigation"], "panel": urls["panel"], "registered": True,
    }
