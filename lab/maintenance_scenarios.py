"""Synthetic maintenance data on native HA registries and conversation/read APIs."""

from time import perf_counter

from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


async def exercise_maintenance(hass, fixture, client, agent, report, case):
    from custom_components.polish_context_voice.core.types import Request

    domain = "polish_context_voice"
    runtime = next(iter(hass.data[domain].values()))
    registry, devices = er.async_get(hass), dr.async_get(hass)
    entry = hass.config_entries.async_entries(domain)[0]
    created, device_ids = [], []
    events = []
    unsubscribe = hass.bus.async_listen("call_service", lambda event: events.append(event))
    original_threshold = runtime.maintenance_battery_threshold

    async def command(text, **kwargs):
        return await client.call(
            domain + "/process",
            text=text,
            dry_run=False,
            satellite_id=fixture["satellite"],
            **kwargs,
        )

    try:
        for name, area in (("Czujnik okna", "living_room"), ("Sterownik kuchni", "kitchen")):
            device = devices.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(domain, "maintenance_" + area)},
                name=name,
            )
            devices.async_update_device(device.id, area_id=area)
            device_ids.append(device.id)
        for name, entity_domain, kind, state, area, device, exposed in (
            ("maint_low", "sensor", "battery", "10", "living_room", device_ids[0], True),
            (
                "maint_low_flag",
                "binary_sensor",
                "battery",
                "on",
                "living_room",
                device_ids[0],
                True,
            ),
            ("maint_hidden", "sensor", "battery", "1", "living_room", device_ids[0], False),
            ("maint_normal", "sensor", "battery", "55", "kitchen", None, True),
            ("maint_missing", "sensor", "battery", "unknown", "kitchen", None, True),
            ("maint_problem", "binary_sensor", "problem", "on", "kitchen", device_ids[1], True),
            (
                "maint_connection",
                "binary_sensor",
                "connectivity",
                "off",
                "kitchen",
                device_ids[1],
                True,
            ),
            ("maint_unavailable", "light", None, "unavailable", "kitchen", None, True),
        ):
            entity = registry.async_get_or_create(
                entity_domain,
                domain,
                name,
                suggested_object_id=name,
                original_name=name,
                has_entity_name=True,
                config_entry=entry,
                device_id=device,
            )
            created.append(entity.entity_id)
            if device is None:
                registry.async_update_entity(entity.entity_id, area_id=area)
            attrs = {"friendly_name": name, "device_class": kind}
            if entity_domain == "sensor":
                attrs["unit_of_measurement"] = "%"
            hass.states.async_set(entity.entity_id, state, attrs)
            exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, exposed)
        await hass.async_block_till_done()

        with case(report, "maintenance_exact_user_questions_via_conversation"):
            for text in (
                "Czy wszystkie urządzenia są sprawne?",
                "Czy jakieś urządzenie ma problem?",
                "Czy trzeba gdzieś wymienić baterie?",
            ):
                answer = await client.call(
                    "conversation/process",
                    agent_id=agent,
                    language="pl",
                    text=text,
                    satellite_id=fixture["satellite"],
                )
                assert answer["response"]["response_type"] == "query_answer", answer
                speech = answer["response"]["speech"]["plain"]["speech"]
                assert "słaba bateria" in speech and "maint_hidden" not in str(answer), answer
                if "baterie" not in text:
                    assert "Sterownik kuchni" in speech and "zgłoszony problem" in speech

        with case(report, "maintenance_spoken_findings_include_registry_rooms"):
            result = await command("Sprawdź baterie")
            assert "pomieszczenie: Living Room" in result["speech"], result
            assert "pomieszczenie: Kitchen" in result["speech"], result
            assert "brak aktualnego odczytu" in result["speech"], result

        with case(report, "maintenance_global_scope_device_grouping_and_permissions"):
            result = await command("Czy jakieś urządzenie ma problem?")
            data = result["response_data"]
            assert data["scope"] == "home" and not result["area_ids"], result
            assert "light.maint_unavailable" in result["targets"]
            assert "sensor.maint_hidden" not in str(result)
            device_group = next(g for g in data["groups"] if g["device_id"] == device_ids[0])
            assert device_group["name"] == "Czujnik okna" and len(device_group["findings"]) == 2
            reader = await hass.auth.async_create_user(
                "Maintenance reader", group_ids=[GROUP_ID_READ_ONLY]
            )
            answer = await runtime.process(
                Request("Sprawdź baterie", user_id=reader.id),
                Context(user_id=reader.id),
            )
            assert answer.status == "answered" and not answer.calls

        with case(report, "maintenance_explicit_area_and_unknown_place"):
            result = await command("Sprawdź baterie w salonie")
            assert set(result["targets"]) == {"sensor.maint_low", "binary_sensor.maint_low_flag"}
            assert (
                result["area_ids"] == ["living_room"]
                and result["response_data"]["scope"] == "areas"
            )
            unknown = await command("Sprawdź baterie w nieznanym pokoju")
            assert unknown["reason"] == "unknown_location" and not unknown["targets"], unknown

        with case(report, "maintenance_threshold_and_state_refresh"):
            low = hass.states.get("sensor.maint_low")
            flag = hass.states.get("binary_sensor.maint_low_flag")
            hass.states.async_set(flag.entity_id, "off", flag.attributes)
            hass.states.async_set(low.entity_id, "25", low.attributes)
            await hass.async_block_till_done()
            result = await command("Sprawdź baterie w salonie")
            assert not result["response_data"]["groups"], result
            runtime.maintenance_battery_threshold = 30
            result = await command("Sprawdź baterie w salonie")
            assert (
                "25 procent" in result["speech"]
                and result["response_data"]["battery_threshold_percent"] == 30
            )
            runtime.maintenance_battery_threshold = original_threshold
            hass.states.async_set(low.entity_id, "unknown", low.attributes)
            await hass.async_block_till_done()
            result = await command("Sprawdź baterie w salonie")
            assert result["response_data"]["missing_readings"] == 1
            assert "brak aktualnego odczytu" in result["speech"]
            hass.states.async_set(low.entity_id, low.state, low.attributes)
            hass.states.async_set(flag.entity_id, flag.state, flag.attributes)
            await hass.async_block_till_done()

        with case(report, "maintenance_exposure_changes_and_no_sources"):
            selected = ("sensor.maint_low", "binary_sensor.maint_low_flag")
            for identifier in selected:
                exposed_entities.async_expose_entity(hass, "conversation", identifier, False)
            await hass.async_block_till_done()
            try:
                result = await command("Sprawdź baterie w salonie")
                assert result["reason"] == "no_maintenance_sources" and not result["targets"], (
                    result
                )
                assert "nie widzę słabych" not in result["speech"]
            finally:
                for identifier in selected:
                    exposed_entities.async_expose_entity(hass, "conversation", identifier, True)
                await hass.async_block_till_done()

        with case(report, "maintenance_preview_and_reads_never_call_services"):
            preview = await client.call(
                domain + "/process",
                text="Czy wszystkie urządzenia są sprawne?",
                dry_run=True,
            )
            assert preview["status"] == "dry_run" and not preview["calls"]
            assert preview["response_data"] is None
            assert not events, events

        with case(report, "maintenance_latency_100_correct_read_reports"):
            samples = []
            for _ in range(50):
                for text, kind in (
                    ("Sprawdź baterie", "battery"),
                    ("Sprawdź urządzenia", "health"),
                ):
                    started = perf_counter()
                    result = await command(text)
                    samples.append((perf_counter() - started) * 1000)
                    data = result["response_data"]
                    assert result["status"] == "answered" and data["kind"] == kind
                    assert data["scope"] == "home" and "słaba bateria" in result["speech"]
                    assert "sensor.maint_hidden" not in str(result) and not result["calls"]
            samples.sort()
            report["maintenance_latency_ms"] = {
                "samples": 100,
                "correct": 100,
                "p50": round(samples[49], 3),
                "p95": round(samples[94], 3),
                "p99": round(samples[98], 3),
                "scope": "Native local HA WebSocket reads, planning and speech from cached "
                "synthetic states; no audio, LLM or external network",
            }
    finally:
        unsubscribe()
        runtime.maintenance_battery_threshold = original_threshold
        for identifier in created:
            hass.states.async_remove(identifier)
            registry.async_remove(identifier)
        for identifier in device_ids:
            devices.async_remove_device(identifier)
        await hass.async_block_till_done()
