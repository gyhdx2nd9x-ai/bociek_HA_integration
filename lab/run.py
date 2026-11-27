"""Real HA Core, registries, HTTP/WebSocket API and synthetic template devices only.

Run with .ha-venv/bin/python -m lab.run. Never reads .env or a production config.
"""

import argparse
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import platform
import shutil
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiohttp
import yaml
from homeassistant import bootstrap, config_entries, loader
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from homeassistant.runner import create_event_loop
from homeassistant.setup import async_setup_component

from scripts.artifact_io import component_tree_sha256
from scripts.check_ha_stable import core_pin

from .scenarios import exercise

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "polish_context_voice"
PORT = 18123
DENIED_NETWORK = []


def loopback_only(event, args):
    """Process-local audit guard: no sockets to real devices or internet at runtime."""
    if event in {"socket.connect", "socket.bind", "socket.sendto"}:
        sock, address = args[0], args[-1]
        if sock.family not in {socket.AF_INET, socket.AF_INET6}:
            return
        host = address[0]
    elif event == "socket.getaddrinfo":
        host = args[0]
    else:
        return
    try:
        allowed = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        DENIED_NETWORK.append(event)
        raise PermissionError("Lab network guard: only loopback is allowed")


def prepare():
    folder = ROOT / ".ha-lab" / ("run-" + uuid4().hex[:12])
    folder.mkdir(parents=True, mode=0o700)
    (folder / "custom_components").mkdir()
    shutil.copytree(
        ROOT / "custom_components" / DOMAIN,
        folder / "custom_components" / DOMAIN,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    config = yaml.safe_load((ROOT / "lab/configuration.yaml").read_text())
    config["homeassistant"]["auth_mfa_modules"] = []
    structure = json.loads((ROOT / "lab/household.json").read_text())
    # Six other rooms each get one additional synthetic main light.
    for area in structure["areas"]:
        if area["id"] in {"living_room", "kitchen"}:
            continue
        key = "lab_" + area["id"]
        config["input_boolean"][key] = {"initial": False}
        config["template"][0]["light"].append(
            {
                "name": key.replace("_", " ").title() + " Light",
                "unique_id": "pcv_" + key,
                "state": "{{ is_state('input_boolean." + key + "', 'on') }}",
                "turn_on": {
                    "action": "input_boolean.turn_on",
                    "target": {"entity_id": "input_boolean." + key},
                },
                "turn_off": {
                    "action": "input_boolean.turn_off",
                    "target": {"entity_id": "input_boolean." + key},
                },
            }
        )
    (folder / "configuration.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
    return folder, config, structure


async def setup(hass, config):
    # Select actual integrations explicitly. Frontend is optional; no discovery/cloud boot bundle,
    # no mocked HA services or monkeypatches to the production component.
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass.config_entries = config_entries.ConfigEntries(hass, config)
    await loader.async_get_custom_components(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    await async_process_ha_core_config(hass, config["homeassistant"])
    for domain in (
        "homeassistant",
        "persistent_notification",
        "http",
        "api",
        "websocket_api",
        "config",
        "conversation",
        "input_boolean",
        "template",
    ):
        assert await async_setup_component(hass, domain, config), "setup failed: " + domain
    if "frontend" in config:
        assert await async_setup_component(hass, "frontend", config), "setup failed: frontend"
    await hass.async_start()
    await hass.async_block_till_done()
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={})
    assert flow["type"] == "create_entry", flow
    await hass.async_block_till_done()
    entry = flow["result"]
    assert entry.state.value == "loaded", f"{entry.state}: {entry.reason}"
    return entry


async def household(hass, entry, structure):
    from homeassistant.components.homeassistant import exposed_entities

    floors = {}
    for item in structure["floors"]:
        floor = fr.async_get(hass).async_create(item["name"], aliases=set(item["aliases"]))
        floors[item["id"]] = floor.floor_id
    areas, devices = {}, {}
    for item in structure["areas"]:
        area = ar.async_get(hass).async_create(
            item["name"], aliases=set(item["aliases"]), floor_id=floors.get(item["floor_id"])
        )
        assert area.id == item["id"], (area.id, item["id"])
        areas[item["id"]] = area.id
        devices[item["id"]] = []
        for index in range(item["devices"]):
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, f"lab-{area.id}-{index}")},
                name=f"Virtual {item['name']} {index + 1}",
                manufacturer="Synthetic lab",
            )
            dr.async_get(hass).async_update_device(device.id, area_id=area.id)
            devices[item["id"]].append(device.id)
    labels = {
        name: lr.async_get(hass).async_create(name).label_id
        for name in (
            "voice_default_on",
            "voice_all_off",
            "voice_main",
            "voice_task",
            "voice_ambient",
            "voice_primary",
            "voice_safe_all",
            "voice_block",
        )
    }
    registry = er.async_get(hass)
    specifications = [
        (
            "light.lab_main_lights",
            "living_room",
            ["voice_default_on", "voice_all_off", "voice_main"],
            [er.COMPUTED_NAME, "główne oświetlenie"],
            True,
        ),
        (
            "light.lab_table_light",
            "living_room",
            ["voice_all_off", "voice_task"],
            ["lampa przy stole"],
            True,
        ),
        (
            "light.lab_ambient_light",
            "living_room",
            ["voice_all_off", "voice_ambient"],
            ["taśma led"],
            True,
        ),
        ("light.lab_hidden_light", "living_room", ["voice_default_on", "voice_all_off"], [], False),
        ("light.lab_kitchen_light", "kitchen", ["voice_default_on", "voice_all_off"], [], True),
        ("sensor.lab_temperature", "living_room", ["voice_primary"], [], True),
        ("switch.lab_test_switch", "living_room", ["voice_primary"], ["gniazdo testowe"], True),
    ]
    for area in structure["areas"]:
        if area["id"] not in {"living_room", "kitchen"}:
            specifications.append(
                (
                    "light.lab_" + area["id"] + "_light",
                    area["id"],
                    ["voice_default_on", "voice_all_off"],
                    [],
                    True,
                )
            )
    for state in hass.states.async_all():
        exposed_entities.async_expose_entity(hass, "conversation", state.entity_id, False)
    for identifier, area, roles, aliases, exposed in specifications:
        assert registry.async_get(identifier) is not None, (identifier, list(registry.entities))
        registry.async_update_entity(
            identifier,
            device_id=devices[area][0],
            aliases=aliases,
            labels={labels[name] for name in roles},
        )
        exposed_entities.async_expose_entity(hass, "conversation", identifier, exposed)
    # Satellite entry exercises real device/area registry lookup without audio hardware.
    satellite = registry.async_get_or_create(
        "assist_satellite",
        DOMAIN,
        "lab_satellite",
        suggested_object_id="lab_living_room",
        device_id=devices["living_room"][1],
    )
    hass.states.async_set(satellite.entity_id, "idle", {"friendly_name": "Virtual satellite"})
    await hass.async_block_till_done()
    return {"areas": areas, "devices": devices, "satellite": satellite.entity_id}


class Client:
    def __init__(self, ws):
        self.ws, self.identifier = ws, 0

    async def call(self, kind, *, expected_error=None, **data):
        self.identifier += 1
        await self.ws.send_json({"id": self.identifier, "type": kind, **data})
        async with asyncio.timeout(15):
            while True:
                reply = await self.ws.receive_json()
                if reply.get("id") == self.identifier:
                    if expected_error:
                        assert not reply.get("success"), reply
                        assert reply["error"]["code"] == expected_error, reply
                        return reply["error"]
                    assert reply.get("success"), reply
                    return reply["result"]


async def run(args):
    folder, config, structure = prepare()
    if args.frontend:
        config["frontend"] = {}
        config["homeassistant"]["auth_providers"] = [
            {
                "type": "trusted_networks",
                "trusted_networks": ["127.0.0.1/32"],
                "allow_bypass_login": True,
            }
        ]
        storage = folder / ".storage"
        storage.mkdir(exist_ok=True)
        (storage / "onboarding").write_text(
            json.dumps(
                {
                    "version": 4,
                    "minor_version": 1,
                    "key": "onboarding",
                    "data": {"done": ["user", "core_config", "integration", "analytics"]},
                }
            )
        )
    sys.path.insert(0, str(folder))
    sys.addaudithook(loopback_only)
    hass = HomeAssistant(str(folder))
    report = {
        "created_utc": datetime.now(UTC).isoformat(),
        "cases": [],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "synthetic_devices_only": True,
        "network": "loopback only",
    }
    completed = False
    try:
        entry = await setup(hass, config)
        component = sys.modules["custom_components." + DOMAIN]
        assert Path(component.__file__).is_relative_to(folder)
        report["component_version"] = json.loads(
            (folder / "custom_components" / DOMAIN / "manifest.json").read_text()
        )["version"]
        report["component_tree_sha256"] = component_tree_sha256(
            folder / "custom_components" / DOMAIN
        )
        fixture = await household(hass, entry, structure)
        user = await hass.auth.async_create_user("Lab administrator", group_ids=[GROUP_ID_ADMIN])
        refresh = await hass.auth.async_create_refresh_token(user, client_id="http://127.0.0.1")
        token = hass.auth.async_create_access_token(refresh)
        async with aiohttp.ClientSession() as session:
            if args.frontend:
                url = f"http://127.0.0.1:{PORT}" + hass.data[DOMAIN + "_frontend"]["url"]
                async with session.get(url) as response:
                    module = await response.read()
                    assert response.status == 200
                    assert (
                        module
                        == (
                            folder / "custom_components" / DOMAIN / "frontend/navigation.js"
                        ).read_bytes()
                    )
                    report["bundled_frontend_http"] = {
                        "status": response.status,
                        "bytes": len(module),
                        "sha256": hashlib.sha256(module).hexdigest(),
                    }
            async with session.ws_connect(f"http://127.0.0.1:{PORT}/api/websocket") as ws:
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                client = Client(ws)
                info = await client.call("get_config")
                snapshot = await client.call("polish_context_voice/snapshot")
                print(
                    json.dumps(
                        {
                            "ha_version": info["version"],
                            "integration": entry.state.value,
                            "areas": len(snapshot["areas"]),
                            "devices": sum(len(v) for v in fixture["devices"].values()),
                        }
                    )
                )
                report.update(
                    ha_version=info["version"],
                    areas=len(snapshot["areas"]),
                    floors=len(snapshot["floors"]),
                    devices=sum(len(area_devices) for area_devices in fixture["devices"].values()),
                )
                if not args.ui_only:
                    async with asyncio.timeout(90):
                        await exercise(hass, entry, fixture, client, session, token, user, report)
        completed = True
        if args.serve:
            credentials = ROOT / ".ha-lab/access.json"
            credentials.write_text(json.dumps({"url": "http://127.0.0.1:18123", "token": token}))
            credentials.chmod(0o600)
            print(
                f"Local synthetic HA: http://127.0.0.1:18123 "
                f"({'frontend + API' if args.frontend else 'API only'}). "
                f"Temporary credentials (0600): {credentials}",
                flush=True,
            )
            await asyncio.Event().wait()
    finally:
        await hass.async_stop()
        if args.serve and "credentials" in locals():
            credentials.unlink(missing_ok=True)
        report["blocked_network_attempts"] = len(DENIED_NETWORK)
        report["passed"] = (
            None
            if args.ui_only
            else completed and bool(report["cases"]) and all(c["passed"] for c in report["cases"])
        )
        if args.ui_only:
            report["session_only"] = True  # Manual browser results live in frontend-<version>.json.
        report_file = args.output or ROOT / (
            "lab/ui-session.json" if args.ui_only else "lab/latest.json"
        )
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Report: {report_file}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Write the report to a separate file")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--frontend", action="store_true", help="Render the official HA UI locally")
    parser.add_argument("--ui-only", action="store_true", help="Frontend session; separate report")
    args = parser.parse_args()
    if args.frontend and not args.serve:
        parser.error("--frontend requires --serve (local synthetic HA only)")
    if args.ui_only and not args.frontend:
        parser.error("--ui-only requires --frontend")
    expected_core = core_pin((ROOT / "lab/requirements.txt").read_text())
    if HA_VERSION != expected_core:
        parser.error(f"Installed HA Core is {HA_VERSION}; the lab requires {expected_core}.")
    os.umask(0o077)
    logging.basicConfig(level=logging.WARNING)
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        try:
            runner.run(run(args))
        except KeyboardInterrupt:
            print("Lab stopped.")


if __name__ == "__main__":
    main()
