"""Native Home Assistant contextual names and inherited areas for child devices."""

from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr


async def exercise_registry_context(hass, entry, command, report, case):
    devices = dr.async_get(hass)
    if not hasattr(devices, "async_get_or_create_child"):
        return
    entities = er.async_get(hass)
    labels = lr.async_get(hass)
    block = labels.async_get_label_by_name("voice_block")
    if block is None:
        block = labels.async_create("voice_block")
    parent = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, "lab_power_strip")},
        name="Listwa testowa",
    )
    devices.async_update_device(parent.id, area_id="living_room")
    child = devices.async_get_or_create_child(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, "lab_power_strip_socket")},
        parent_device_id=parent.id,
        name="Gniazdo testowe listwy",
    )
    registered = entities.async_get_or_create(
        "switch", entry.domain, "lab_power_strip_socket_entity",
        config_entry=entry, device_id=child.id, original_name="Wyjście testowe",
        has_entity_name=True,
    )
    entity_id = registered.entity_id
    entities.async_update_entity(entity_id, aliases=[er.COMPUTED_NAME, "Stare gniazdo listwy"])
    hass.states.async_set(entity_id, "off", {})
    exposed_entities.async_expose_entity(hass, "conversation", entity_id, True)
    await hass.async_block_till_done()
    try:
        with case(report, "ha_registry_child_device_names_and_area_used_in_bociek"):
            registered = entities.async_get(entity_id)
            name = er.async_get_full_entity_name(hass, registered)
            assert "Wyjście testowe" in name and "Gniazdo testowe listwy" in name, name
            assert registered.area_id is None and child.area_id is None
            for alias in (name, "Stare gniazdo listwy"):
                plan = await command(f"włącz {alias} w salonie", dry_run=True)
                assert plan["status"] == "confirm" and plan["targets"] == [entity_id], plan
                assert plan["area_ids"] == ["living_room"] and not plan["completed"], plan

            devices.async_update_child_device(child.id, area_id="kitchen")
            await hass.async_block_till_done()
            registered = entities.async_get(entity_id)
            assert er.async_get_effective_area_id(hass, registered) == "kitchen"
            # The context-generated name can change when the child gets its own room.
            current_name = er.async_get_full_entity_name(hass, registered)
            for alias in (current_name, "Stare gniazdo listwy"):
                plan = await command(f"włącz {alias} w kuchni", dry_run=True)
                assert plan["status"] == "confirm" and plan["targets"] == [entity_id], plan
                assert plan["area_ids"] == ["kitchen"] and not plan["completed"], plan
            old_room = await command(f"włącz {current_name} w salonie")
            assert not old_room["calls"], old_room

        with case(report, "ha_registry_parent_block_label_applies_to_child_device"):
            devices.async_update_device(parent.id, labels={block.label_id})
            await hass.async_block_till_done()
            result = await command("włącz Stare gniazdo listwy w kuchni", dry_run=False)
            assert result["status"] in {"blocked", "clarify"} and not result["calls"], result
            assert hass.states.is_state(entity_id, "off")
    finally:
        exposed_entities.async_expose_entity(hass, "conversation", entity_id, False)
        hass.states.async_remove(entity_id)
        entities.async_remove(entity_id)
        devices.async_remove_device(parent.id)
        await hass.async_block_till_done()
