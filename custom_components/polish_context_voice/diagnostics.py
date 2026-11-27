"""Support report for installation/localization without credentials or home inventory."""

import json
from pathlib import Path

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .core.capability_catalog import catalog_snapshot
from .ha.frontend import DATA_FRONTEND
from .ha.self_test import report_state


async def async_get_config_entry_diagnostics(hass, entry):
    root = Path(__file__).parent

    def files():
        manifest = json.loads((root / "manifest.json").read_text())
        return manifest["version"], {
            language: (root / "translations" / (language + ".json")).is_file()
            for language in ("en", "en-GB", "pl")
        }

    version, catalogs = await hass.async_add_executor_job(files)
    resources = {}
    for language in catalogs:
        strings = await async_get_translations(hass, language, "options", {DOMAIN})
        resources[language] = {
            "resource_count": len(strings),
            "menu_title": strings.get(f"component.{DOMAIN}.options.step.init.title"),
        }
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return {
        "integration_version": version,
        "ha_core": HA_VERSION,
        "ha_language": hass.config.language,
        "bundled_navigation": hass.data.get(DATA_FRONTEND, {"registered": False}),
        "translation_files_present": catalogs,
        "loaded_options": resources,
        "request_diagnostics": runtime.diagnostics.snapshot() if runtime else None,
        "model_cache_metrics": runtime.cache.metrics() if runtime else None,
        "self_test": report_state(runtime),
        "capability_catalog": catalog_snapshot(),
    }
