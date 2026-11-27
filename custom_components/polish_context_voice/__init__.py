"""Home Assistant entry point; pure core remains importable without HA installed."""

from .const import DOMAIN, PLATFORMS


async def async_setup(hass, config):
    from homeassistant.helpers.translation import async_get_translations

    from .ha.frontend import register as register_frontend
    from .ha.services import register

    # Load the actual default catalog before other locales request this integration.
    # Uses HA's public loader; no cache edits or HA Core patches.
    await async_get_translations(hass, "en", "options", {DOMAIN})
    register(hass)
    register_frontend(hass)
    return True


async def async_setup_entry(hass, entry):
    from homeassistant.helpers import llm

    from .core.home_model.light_profiles import PRESETS_OPTION
    from .ha.llm_api import SemanticAPI
    from .ha.runtime import Runtime
    from .ha.websocket import register

    runtime = Runtime(hass, entry)
    loaded_data = entry.data
    loaded_options = {key: value for key, value in entry.options.items() if key != PRESETS_OPTION}

    async def reload_runtime(hass, updated_entry):
        options = {
            key: value for key, value in updated_entry.options.items() if key != PRESETS_OPTION
        }
        if updated_entry.data != loaded_data or options != loaded_options:
            await hass.config_entries.async_reload(updated_entry.entry_id)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.async_on_unload(runtime.subscribe())
    entry.async_on_unload(llm.async_register_api(hass, SemanticAPI(hass, runtime)))
    entry.async_on_unload(entry.add_update_listener(reload_runtime))
    register(hass)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    return True


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        return True
    return False
