"""F1 scoped state questions against synthetic native HA registries and permissions."""

import json

from homeassistant.auth.models import Group
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

DOMAIN = "polish_context_voice"


async def exercise_state_scope(hass, entry, client, agent, report, case):
    # Import the installed lab copy only after its integration has been loaded.
    from custom_components.polish_context_voice.core.types import Request

    runtime = hass.data[DOMAIN][entry.entry_id]
    registry, areas = er.async_get(hass), ar.async_get(hass)
    floors, labels = fr.async_get(hass), lr.async_get(hass)
    floor = floors.async_create("Kondygnacja F1", aliases={"kondygnacji f1"})
    alpha = areas.async_create(
        "Komora alfa F1", aliases={"komorze alfa f1"}, floor_id=floor.floor_id
    )
    beta = areas.async_create(
        "Komora beta F1", aliases={"komorze beta f1"}, floor_id=floor.floor_id
    )
    outside = areas.async_create("Komora poza F1", aliases={"komorze poza f1"})
    created, created_labels, calls = [], [], []
    reader = None

    def create(domain, suffix, *, room=None, state="off", kind=None, role=None, exposed=True):
        item = registry.async_get_or_create(
            domain, DOMAIN, "lab_f1_" + suffix, suggested_object_id="lab_f1_" + suffix
        )
        created.append(item.entity_id)
        options = {"area_id": room, "name": "F1 " + suffix}
        if role:
            label = labels.async_get_label_by_name(role)
            if label is None:
                label = labels.async_create(role)
                created_labels.append(label.label_id)
            options["labels"] = {label.label_id}
        registry.async_update_entity(item.entity_id, **options)
        attributes = {"friendly_name": "F1 " + suffix}
        if kind:
            attributes["device_class"] = kind
        hass.states.async_set(item.entity_id, state, attributes)
        exposed_entities.async_expose_entity(hass, "conversation", item.entity_id, exposed)
        return item.entity_id

    local = create("light", "local", room=alpha.id)
    remote = create("light", "remote", room=beta.id, state="on")
    unassigned = create("light", "unassigned", state="on")
    beyond_floor = create("light", "beyond_floor", room=outside.id, state="on")
    hidden = create("light", "hidden", room=alpha.id, state="on", exposed=False)
    blocked = create("light", "blocked", room=alpha.id, state="on", role="voice_block")
    excluded = create("light", "excluded", room=alpha.id, state="on", role="voice_exclude_all")
    window = create("binary_sensor", "window", room=alpha.id, kind="window")
    door = create("binary_sensor", "door", room=beta.id, kind="door")
    secret_window = create(
        "binary_sensor", "secret_window", room=alpha.id, kind="window", state="on", exposed=False
    )
    satellite = create("assist_satellite", "satellite", room=alpha.id, state="idle")
    permitted = {local, remote, unassigned, window, door}

    @callback
    def capture(event):
        calls.append(event)

    unsubscribe = hass.bus.async_listen("call_service", capture)

    async def command(text, **kwargs):
        result = await client.call(
            DOMAIN + "/process", text=text, satellite_id=satellite, dry_run=False, **kwargs
        )
        await hass.async_block_till_done()
        return result

    async def read_as_reader(text):
        result = await runtime.process(
            Request(
                text,
                satellite_id=satellite,
                user_id=reader.id,
                conversation_id="lab_f1_restricted_reader",
            ),
            Context(user_id=reader.id),
        )
        await hass.async_block_till_done()
        return result.to_dict()

    def no_service(result):
        # Runtime tuples and WebSocket arrays are deliberately tested by emptiness.
        assert not result["calls"] and not result["completed"] and not calls, result
        recent = runtime.diagnostics.snapshot()["recent_requests"]
        row = next(row for row in recent if row["request_id"] == result["request_id"])
        assert row["ha_service_calls"] == {
            "attempted": 0,
            "returned": 0,
            "failed": 0,
            "interrupted": 0,
        }, row

    try:
        await hass.async_block_till_done()
        calls.clear()

        with case(
            report, "f1_native_local_all_off_uses_satellite_not_other_rooms_or_role_defaults"
        ):
            result = await command("Czy wszystkie światła są wyłączone?")
            assert result["status"] == "answered", result
            assert result["targets"] == [local] and result["area_ids"] == [alpha.id], result
            assert result["speech"] == "Tak, sprawdzone światła są wyłączone.", result
            assert hass.states.is_state(remote, "on")
            assert all(item not in json.dumps(result) for item in (hidden, blocked, excluded)), (
                result
            )
            no_service(result)

        with case(report, "f1_native_group_reads_own_state_without_expanding_hidden_members"):
            group_attributes = {
                "friendly_name": "F1 local",
                "entity_id": [hidden, "light.lab_f1_member_without_reading"],
            }
            hass.states.async_set(local, "off", group_attributes)
            await hass.async_block_till_done()
            result = await command("Czy wszystkie światła są wyłączone?")
            assert result["targets"] == [local] and result["speech"] == (
                "Tak, sprawdzone światła są wyłączone."
            ), result
            assert hass.states.is_state(hidden, "on")
            assert "hidden" not in result["speech"] and "member_without_reading" not in str(result)
            no_service(result)
            hass.states.async_set(local, "unknown", group_attributes)
            await hass.async_block_till_done()
            result = await command("Czy wszystkie światła są wyłączone?")
            assert "Nie mam aktualnego odczytu: F1 local." in result["speech"], result
            assert not result["speech"].startswith("Tak,") and "wyłączone" not in result["speech"]
            no_service(result)
            hass.states.async_set(local, "off", {"friendly_name": "F1 local"})
            await hass.async_block_till_done()

        with case(report, "f1_native_explicit_area_wins_over_satellite_context"):
            result = await command("Czy w komorze beta f1 świeci się światło?")
            assert result["status"] == "answered", result
            assert result["targets"] == [remote] and result["area_ids"] == [beta.id], result
            assert "F1 remote" in result["speech"] and "włączone" in result["speech"], result
            no_service(result)

        with case(report, "f1_native_explicit_floor_keeps_unassigned_and_other_floors_out"):
            result = await command("Czy wszystkie światła są wyłączone na kondygnacji f1?")
            assert result["status"] == "answered", result
            assert set(result["targets"]) == {local, remote}, result
            assert set(result["area_ids"]) == {alpha.id, beta.id}, result
            assert result["speech"].startswith("Nie,") and "F1 remote" in result["speech"], result
            assert unassigned not in result["targets"] and beyond_floor not in result["targets"]
            no_service(result)

        with case(report, "f1_native_explicit_house_includes_exposed_unassigned_lights"):
            result = await command("Czy gdzieś świeci się światło?")
            assert result["status"] == "answered" and not result["area_ids"], result
            assert {local, remote, unassigned, beyond_floor}.issubset(result["targets"]), result
            assert all(item not in result["targets"] for item in (hidden, blocked, excluded)), (
                result
            )
            no_service(result)

        with case(report, "f1_native_house_and_contacts_respect_restricted_ha_read_permissions"):
            reader = await hass.auth.async_create_user("Lab F1 restricted reader")
            reader.groups = [
                Group(
                    "Only selected synthetic F1 states may be read",
                    {"entities": {"entity_ids": {item: {"read": True} for item in permitted}}},
                )
            ]
            result = await read_as_reader("Czy gdzieś świeci się światło?")
            assert result["status"] == "answered", result
            assert set(result["targets"]) == {local, remote, unassigned}, result
            serialized = json.dumps(result, ensure_ascii=False)
            assert all(
                item not in serialized for item in (hidden, blocked, excluded, beyond_floor)
            ), result
            no_service(result)
            for text, target, expected in (
                ("Czy wszystkie okna są zamknięte w całym domu?", window, "okna"),
                ("Czy wszystkie drzwi są zamknięte w całym domu?", door, "drzwi"),
            ):
                result = await read_as_reader(text)
                assert result["status"] == "answered" and set(result["targets"]) == {target}, result
                assert not result["area_ids"] and result["speech"] == (
                    f"Tak, sprawdzone {expected} są zamknięte."
                ), result
                assert (
                    secret_window not in json.dumps(result)
                    and "secret_window" not in result["speech"]
                )
                no_service(result)

        with case(
            report, "f1_native_unavailable_light_and_contact_never_assert_safe_universal_state"
        ):
            for identifier in (local, window):
                original = hass.states.get(identifier)
                hass.states.async_set(identifier, "unavailable", original.attributes)
            await hass.async_block_till_done()
            for text in ("Czy wszystkie światła są wyłączone?", "Czy okna są zamknięte?"):
                result = await read_as_reader(text)
                assert result["status"] == "answered", result
                assert "Nie mam aktualnego odczytu" in result["speech"], result
                assert (
                    not result["speech"].startswith("Tak,")
                    and "są wyłączone" not in result["speech"]
                )
                no_service(result)
            hass.states.async_set(local, "off", {"friendly_name": "F1 local"})
            hass.states.async_set(
                window, "off", {"device_class": "window", "friendly_name": "F1 window"}
            )
            await hass.async_block_till_done()

        with case(
            report, "f1_native_exposure_changes_are_applied_before_next_read_without_service"
        ):
            exposed_entities.async_expose_entity(hass, "conversation", remote, False)
            await hass.async_block_till_done()
            result = await read_as_reader("Czy gdzieś świeci się światło?")
            assert result["status"] == "answered" and set(result["targets"]) == {
                local,
                unassigned,
            }, result
            assert remote not in json.dumps(result) and "F1 remote" not in result["speech"], result
            no_service(result)
            exposed_entities.async_expose_entity(hass, "conversation", remote, True)
            await hass.async_block_till_done()

        with case(report, "f1_native_compound_question_does_not_run_trailing_control"):
            result = await command("Czy wszystkie światła są wyłączone i włącz światła?")
            assert result["status"] in {"unsupported", "clarify", "blocked"}, result
            assert not result["targets"], result
            no_service(result)

        with case(report, "f1_native_conversation_preserves_query_answer_transport"):
            result = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="Czy wszystkie światła są wyłączone?",
                satellite_id=satellite,
            )
            await hass.async_block_till_done()
            assert result["response"]["response_type"] == "query_answer", result
            assert result["response"]["speech"]["plain"]["speech"] == (
                "Tak, sprawdzone światła są wyłączone."
            ), result
            assert not result["continue_conversation"] and not calls, result

        report["f1_state_scope"] = {
            "synthetic_native_ha_states_only": True,
            "satellite_area_explicit_area_floor_and_house_checked": True,
            "house_includes_readable_unassigned_entities": True,
            "native_read_permissions_exposure_block_and_exclusion_checked": True,
            "unavailable_state_is_not_off_or_closed": True,
            "group_reads_own_cached_state_without_expanding_members": True,
            "zero_device_service_calls_for_queries": True,
            "conversation_query_transport_preserved": True,
            "physical_devices_audio_and_n5105_not_tested": True,
        }
    finally:
        unsubscribe()
        runtime.dialogue.clear()
        runtime.pending.clear()
        for identifier in reversed(created):
            hass.states.async_remove(identifier)
            if registry.async_get(identifier):
                registry.async_remove(identifier)
        for identifier in (alpha.id, beta.id, outside.id):
            areas.async_delete(identifier)
        floors.async_delete(floor.floor_id)
        for identifier in created_labels:
            labels.async_delete(identifier)
        if reader:
            await hass.auth.async_remove_user(reader)
        await hass.async_block_till_done()
