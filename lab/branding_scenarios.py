"""Verify the supplied icon through HA's authenticated local Brands API."""

import hashlib
from pathlib import Path

from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component

DOMAIN = "polish_context_voice"


async def exercise_branding(hass, client, session, token, report, case):
    assert await async_setup_component(hass, "brands", {})
    integration = await async_get_integration(hass, DOMAIN)
    expected = {}
    root = Path(integration.file_path) / "brand"
    for name in ("icon.png", "icon@2x.png"):
        expected[name] = await hass.async_add_executor_job((root / name).read_bytes)

    with case(report, "brand_local_png_and_dark_logo_fallback_without_cdn"):
        assert integration.has_branding
        assets = {}
        for name in (
            "icon.png",
            "icon@2x.png",
            "dark_icon.png",
            "dark_icon@2x.png",
            "logo.png",
            "logo@2x.png",
            "dark_logo.png",
            "dark_logo@2x.png",
        ):
            url = f"http://127.0.0.1:18123/api/brands/integration/{DOMAIN}/{name}"
            async with session.get(url, headers={"Authorization": "Bearer " + token}) as response:
                content = await response.read()
                assert response.status == 200, name
                assert response.content_type == "image/png", name
                source = (
                    "icon@2x.png" if name in {"icon@2x.png", "dark_icon@2x.png"} else "icon.png"
                )
                assert content == expected[source], name
                assets[name] = {
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
        report["bundled_brand_images"] = assets

    with case(report, "brand_access_token_works_and_anonymous_request_is_denied"):
        url = f"http://127.0.0.1:18123/api/brands/integration/{DOMAIN}/icon.png"
        async with session.get(url) as response:
            assert response.status == 403
        brand_token = (await client.call("brands/access_token"))["token"]
        async with session.get(url, params={"token": brand_token}) as response:
            assert response.status == 200
            assert await response.read() == expected["icon.png"]
