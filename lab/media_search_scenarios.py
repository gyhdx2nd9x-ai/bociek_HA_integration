"""Native HA boundary with simulated MA catalogs; no server, streams or audio."""

from time import monotonic
from types import MappingProxyType, SimpleNamespace

import probatio as vol
from homeassistant.auth.models import Group
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, SupportsResponse
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

DOMAIN = "polish_context_voice"


async def exercise_media_search(hass, entry, client, agent, user, report, case):
    from custom_components.polish_context_voice.core.types import Request

    runtime = hass.data[DOMAIN][entry.entry_id]
    registry, areas = er.async_get(hass), ar.async_get(hass)
    original_music, original_search = runtime.music_settings, runtime.media_search_settings
    ma = ConfigEntry(
        version=1,
        minor_version=1,
        domain="music_assistant",
        title="Synthetic MA catalog",
        data={},
        options={},
        source="user",
        unique_id="lab_media_search",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    # Test-only registration, as in HA config-entry fixtures. async_add also sets
    # up the integration, which would import a real MA client and contact a server.
    # This entry supplies registry identity only; all responses below are simulated.
    hass.config_entries._entries[ma.entry_id] = ma
    rooms = [
        areas.async_create("Komora muzyki alfa", aliases={"komorze muzyki alfa"}),
        areas.async_create("Komora muzyki beta", aliases={"komorze muzyki beta"}),
    ]
    players = []
    for index, room in enumerate(rooms):
        player = registry.async_get_or_create(
            "media_player",
            "music_assistant",
            f"lab_media_search_{index}",
            config_entry=ma,
            suggested_object_id=f"lab_media_search_{index}",
        )
        registry.async_update_entity(player.entity_id, area_id=room.id, name=f"Lab MA {index}")
        hass.states.async_set(player.entity_id, "idle", {"supported_features": 512})
        exposed_entities.async_expose_entity(hass, "conversation", player.entity_id, True)
        players.append(player.entity_id)
    runtime.music_settings = {
        **original_music,
        "music_rooms": {
            **original_music.get("music_rooms", {}),
            **{room.id: {"player": player} for room, player in zip(rooms, players, strict=True)},
        },
    }
    runtime.media_search_settings = {
        **original_search,
        "media_search_enabled": True,
        "media_search_library_first": False,
        "media_search_config_entry_id": ma.entry_id,
        "media_search_username": "lab-profile",
    }
    searches, plays = [], []
    response = {"tracks": [{"name": "Laboratorium", "uri": "library://track/lab-exact"}]}
    restricted = None
    source_entities = []
    source_device = None
    search_schema = vol.Schema(
        {
            vol.Required("config_entry_id"): vol.In({ma.entry_id}),
            vol.Required("name"): str,
            vol.Required("media_type"): [vol.In({"track", "album", "artist", "audiobook"})],
            vol.Required("limit"): vol.All(int, vol.Range(min=1, max=5)),
            vol.Required("library_only"): bool,
            vol.Optional("artist"): str,
            vol.Optional("album"): str,
            vol.Optional("username"): str,
        }
    )
    play_schema = vol.Schema(
        {
            vol.Required("entity_id"): [vol.In(set(players))],
            vol.Required("media_id"): str,
            vol.Required("media_type"): vol.In({"track", "album", "artist", "audiobook"}),
            vol.Required("enqueue"): vol.In({"replace"}),
            vol.Optional("username"): str,
        }
    )

    async def search(call):
        searches.append((dict(call.data), call.context.user_id))
        return response

    async def play(call):
        plays.append((dict(call.data), call.context.user_id))

    assert not hass.services.has_service("music_assistant", "search")
    assert not hass.services.has_service("music_assistant", "play_media")
    hass.services.async_register(
        "music_assistant",
        "search",
        search,
        schema=search_schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("music_assistant", "play_media", play, schema=play_schema)

    async def command(text, *, room=None, conversation_id="media_search_native", dry_run=False):
        result = await client.call(
            DOMAIN + "/process",
            text=text,
            source_area=room or rooms[0].id,
            conversation_id=conversation_id,
            dry_run=dry_run,
        )
        await hass.async_block_till_done()
        return result

    async def reset():
        runtime.media_search_pending.clear()
        runtime.music.pending.clear()
        runtime.music.questions.clear()
        for player in players:
            hass.states.async_set(player, "idle", {"supported_features": 512})
            exposed_entities.async_expose_entity(hass, "conversation", player, True)
        await hass.async_block_till_done()
        searches.clear()
        plays.clear()

    try:
        await reset()
        with case(report, "media_search_native_preview_searches_exact_uri_without_playback"):
            result = await command("Puść utwór Laboratorium", dry_run=True)
            assert result["status"] == "dry_run" and result["route"] == "media_search", result
            assert result["calls"][0]["data"]["media_id"] == "library://track/lab-exact", result
            assert len(searches) == 1 and not plays, (searches, plays)
            assert searches[0][0]["username"] == "lab-profile"
            assert searches[0][1] == user.id

        await reset()
        with case(report, "media_search_native_exact_match_plays_selected_uri_once_with_context"):
            result = await command("Puść utwór Laboratorium")
            assert result["status"] == "executed" and result["completed"] == [players[0]], result
            assert len(searches) == len(plays) == 1, (searches, plays)
            assert plays[0] == (
                {
                    "entity_id": [players[0]],
                    "media_id": "library://track/lab-exact",
                    "media_type": "track",
                    "enqueue": "replace",
                    "username": "lab-profile",
                },
                user.id,
            ), plays

        await reset()
        response = {
            "tracks": [
                {"name": "Laboratorium", "uri": "library://track/lab-studio", "version": "Studio"},
                {"name": "Laboratorium", "uri": "library://track/lab-live", "version": "Live"},
            ]
        }
        with case(report, "media_search_native_version_choice_stays_with_source_room"):
            question = await command("Puść utwór Laboratorium")
            assert question["continue_conversation"] and question["status"] == "clarify", question
            assert not plays
            other = await command("druga", room=rooms[1].id)
            assert other["status"] != "executed" and not plays, other
            invalid = await command("druga да")
            assert invalid["continue_conversation"] and not plays, invalid
            result = await command("numer dwa")
            assert result["status"] == "executed" and len(plays) == 1, result
            assert plays[0][0]["media_id"] == "library://track/lab-live"
            assert plays[0][0]["entity_id"] == [players[0]]

        await reset()
        with case(report, "media_search_native_revoked_player_exposure_prevents_choice_playback"):
            question = await command("Puść utwór Laboratorium")
            assert question["continue_conversation"], question
            exposed_entities.async_expose_entity(hass, "conversation", players[0], False)
            await hass.async_block_till_done()
            result = await command("druga")
            assert result["status"] != "executed" and not plays, result
            assert not runtime.media_search_pending

        await reset()
        response = {"audiobooks": [{"name": "Laboratorium", "uri": "library://audiobook/lab"}]}
        with case(report, "media_search_native_unknown_book_author_requires_explicit_choice"):
            question = await command("Puść audiobook Laboratorium autora Ada Autor")
            assert question["continue_conversation"] and question["status"] == "clarify", question
            assert "nie podaje tu autora" in question["speech"] and not plays, question
            assert "artist" not in searches[0][0]
            result = await command("tak")
            assert result["status"] == "executed" and len(plays) == 1, result
            assert plays[0][0]["media_id"] == "library://audiobook/lab"

        await reset()
        response = {"tracks": []}
        with case(report, "media_search_native_empty_catalog_is_conversation_query_answer"):
            result = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="Puść utwór Laboratorium w komorze muzyki alfa",
            )
            await hass.async_block_till_done()
            assert result["response"]["response_type"] == "query_answer", result
            assert "Nie ma pasujących" in result["response"]["speech"]["plain"]["speech"], result
            assert not result["continue_conversation"] and not plays, result

        await reset()
        response = {"tracks": [{"name": "Laboratorium", "uri": "library://track/lab-exact"}]}
        with case(report, "media_search_native_explicit_search_abandons_calendar_draft"):
            key = Request(
                "",
                source_area=rooms[0].id,
                conversation_id="media_calendar_draft",
                user_id=user.id,
            ).session_key
            runtime.pending_calendars[key] = SimpleNamespace(expires_at=monotonic() + 60)
            result = await command(
                "Puść utwór Laboratorium", conversation_id="media_calendar_draft", dry_run=False
            )
            assert result["status"] == "executed" and key not in runtime.pending_calendars, result
            assert len(plays) == 1

        await reset()
        with case(report, "media_search_native_configured_shortcut_wins_over_title_search"):
            runtime.music_settings = {
                **runtime.music_settings,
                "music_shortcuts": {
                    "lab_favorite": {
                        "name": "Laboratorium",
                        "aliases": [],
                        "backend": "home_assistant",
                        "media": {
                            "media_content_id": "library://track/lab-favorite",
                            "media_content_type": "track",
                        },
                    }
                },
            }
            result = await command("Puść Laboratorium", dry_run=True)
            assert result["status"] == "dry_run" and result["route"] == "music", result
            assert result["calls"][0]["data"]["media_content_id"] == "library://track/lab-favorite"
            assert not searches and not plays

        await reset()
        with case(report, "media_search_native_read_only_player_cannot_start_playback"):
            restricted = await hass.auth.async_create_user("Media search restricted reader")
            restricted.groups = [
                Group(
                    "Read MA player only",
                    {
                        "entities": {"entity_ids": {players[0]: {"read": True}}},
                    },
                )
            ]
            result = await runtime.process(
                Request(
                    "Puść utwór Laboratorium",
                    source_area=rooms[0].id,
                    conversation_id="media_restricted",
                    user_id=restricted.id,
                ),
                Context(user_id=restricted.id),
            )
            assert result.status != "executed" and not searches and not plays, result

        # Source fallback is linked by native HA registry identity, not room/name.
        source_device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "lab_music_source")},
            name="Laboratoryjny satelita muzyki",
        )
        dr.async_get(hass).async_update_device(source_device.id, area_id=rooms[0].id)
        satellite = registry.async_get_or_create(
            "assist_satellite",
            DOMAIN,
            "lab_music_source",
            config_entry=entry,
            device_id=source_device.id,
        )
        raw = registry.async_get_or_create(
            "media_player",
            DOMAIN,
            "lab_music_source",
            config_entry=entry,
            device_id=source_device.id,
        )
        source_entities.extend([satellite.entity_id, raw.entity_id])
        hass.states.async_set(satellite.entity_id, "idle")
        hass.states.async_set(raw.entity_id, "idle", {"supported_features": 512})
        # HA Music Assistant entities prefix a Home Assistant source player ID
        # with "mass_" when using the Home Assistant player provider.
        registry.async_update_entity(
            players[0], new_unique_id=f"mass_{raw.entity_id}", area_id=None
        )
        runtime.music_settings["music_rooms"].pop(rooms[0].id)

        async def source_command(text, **kwargs):
            return await client.call(
                DOMAIN + "/process",
                text=text,
                satellite_id=satellite.entity_id,
                device_id=source_device.id,
                conversation_id="source_fallback",
                dry_run=False,
                **kwargs,
            )

        previous_room_defaults = runtime.music_settings["music_rooms"]
        runtime.music_settings["music_rooms"] = {
            area_id: value
            for area_id, value in previous_room_defaults.items()
            if area_id not in {rooms[0].id, rooms[1].id}
        }
        registry.async_update_entity(players[1], area_id=rooms[0].id)
        await hass.async_block_till_done()
        await reset()
        response = {"audiobooks": [{"name": "Laboratorium", "uri": "library://audiobook/lab"}]}
        with case(report, "ha_area_soundbar_preferred_over_linked_satellite_player"):
            result = await source_command("Puść audiobook Laboratorium")
            assert result["status"] == "executed" and result["targets"] == [players[1]], result
            assert plays[0][0]["entity_id"] == [players[1]]
        registry.async_update_entity(players[1], area_id=rooms[1].id)
        runtime.music_settings["music_rooms"] = previous_room_defaults
        await hass.async_block_till_done()

        await reset()
        response = {"tracks": [{"name": "Laboratorium", "uri": "library://track/lab-exact"}]}
        with case(report, "source_player_native_ma_registry_link_plays_on_requesting_satellite"):
            result = await source_command("Puść utwór Laboratorium")
            assert result["status"] == "executed" and result["targets"] == [players[0]], result
            assert len(plays) == 1 and plays[0][0]["entity_id"] == [players[0]]
            assert registry.async_get(players[0]).area_id is None

        await reset()
        response = {"audiobooks": [{"name": "Laboratorium", "uri": "library://audiobook/lab"}]}
        with case(
            report, "source_player_native_audiobook_uses_same_satellite_with_no_room_default"
        ):
            result = await source_command("Puść audiobook Laboratorium")
            assert result["status"] == "executed" and result["targets"] == [players[0]], result
            assert plays[0][0]["media_id"] == "library://audiobook/lab"

        await reset()
        with case(report, "source_player_native_other_room_default_overrides_requesting_satellite"):
            result = await source_command("Puść audiobook Laboratorium w komorze muzyki beta")
            assert result["status"] == "executed" and result["targets"] == [players[1]], result
            assert len(plays) == 1

        await reset()
        runtime.music_settings["music_rooms"].pop(rooms[1].id)
        room_source_device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "lab_area_link_beta")},
            name="Laboratoryjny głośnik w drugim pokoju",
        )
        dr.async_get(hass).async_update_device(room_source_device.id, area_id=rooms[1].id)
        room_source = registry.async_get_or_create(
            "media_player",
            DOMAIN,
            "lab_area_link_beta",
            config_entry=entry,
            device_id=room_source_device.id,
        )
        source_entities.append(room_source.entity_id)
        hass.states.async_set(room_source.entity_id, "idle", {"supported_features": 512})
        # The MA proxy itself has no HA Area; its official provider ID links it
        # to a normal HA player whose effective Area comes from the device.
        registry.async_update_entity(
            players[1], new_unique_id=f"mass_{room_source.entity_id}", area_id=None
        )
        await hass.async_block_till_done()
        with case(report, "ha_area_player_finds_linked_ma_proxy_without_bociek_room_mapping"):
            result = await source_command("Puść audiobook Laboratorium w komorze muzyki beta")
            assert result["status"] == "executed" and result["targets"] == [players[1]], result
            assert len(plays) == 1 and plays[0][0]["entity_id"] == [players[1]]
            assert plays[0][0]["media_id"] == "library://audiobook/lab"

        await reset()
        old_area = dr.async_get(hass).async_get(room_source.device_id).area_id
        dr.async_get(hass).async_update_device(room_source.device_id, area_id=None)
        await hass.async_block_till_done()
        with case(report, "missing_area_player_never_falls_back_across_explicit_room"):
            result = await source_command("Puść audiobook Laboratorium w komorze muzyki beta")
            assert result["reason"] == "music_satellite_wrong_area" and not plays, result
            assert not searches
        dr.async_get(hass).async_update_device(room_source.device_id, area_id=old_area)
        await hass.async_block_till_done()

        await reset()
        with case(report, "source_player_native_hidden_ma_proxy_never_falls_back_to_other_speaker"):
            exposed_entities.async_expose_entity(hass, "conversation", players[0], False)
            await hass.async_block_till_done()
            result = await source_command("Puść audiobook Laboratorium")
            assert result["reason"] == "music_player_hidden" and not searches and not plays, result

        report["media_search"] = {
            "real_native_ha_registry_permissions_websocket_and_services": True,
            "music_assistant_search_and_play_handlers_are_simulated": True,
            "strict_public_action_fields_and_unchanged_user_context_checked": True,
            "preview_searches_but_never_plays": True,
            "exact_uri_single_play_and_isolated_version_choice_checked": True,
            "fresh_exposure_and_control_permissions_checked": True,
            "missing_author_disclosed_before_playback": True,
            "calendar_draft_abandonment_and_shortcut_priority_checked": True,
            "no_real_ma_providers_audio_resume_or_n5105_benchmark": True,
        }
    finally:
        runtime.music_settings, runtime.media_search_settings = original_music, original_search
        runtime.media_search_pending.clear()
        runtime.music.clear()
        hass.services.async_remove("music_assistant", "search")
        hass.services.async_remove("music_assistant", "play_media")
        for player in players:
            hass.states.async_remove(player)
            if registry.async_get(player):
                registry.async_remove(player)
        for identifier in source_entities:
            hass.states.async_remove(identifier)
            registry.async_remove(identifier)
        if source_device:
            dr.async_get(hass).async_remove_device(source_device.id)
        for room in rooms:
            areas.async_delete(room.id)
        del hass.config_entries._entries[ma.entry_id]
        if restricted:
            await hass.auth.async_remove_user(restricted)
        await hass.async_block_till_done()
