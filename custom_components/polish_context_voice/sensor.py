"""Read-only Voice Health Check sensor; updates even without voice requests."""

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .core.health_attributes import sensor_attributes

SCAN_INTERVAL = timedelta(seconds=60)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([VoiceHealth(entry, hass.data[DOMAIN][entry.entry_id])], True)


class VoiceHealth(SensorEntity):
    _attr_name = "Voice Health Check"
    _attr_icon = "mdi:microphone-message"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "issues"

    def __init__(self, entry, runtime):
        self.runtime = runtime
        self._attr_unique_id = entry.entry_id + "_health"

    async def async_update(self):
        issues = self.runtime.health()
        self._attr_native_value = sum(item["severity"] in {"warning", "error"} for item in issues)
        self._attr_extra_state_attributes = sensor_attributes(
            issues,
            llm_enabled=self.runtime.fallback is not None,
            cache=self.runtime.cache.metrics(),
            active_requests=len(self.runtime.admission.active),
        )
