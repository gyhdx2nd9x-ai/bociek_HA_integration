"""Real HA options, media selector schema and entity services; virtual audio only."""

from copy import deepcopy

from homeassistant.components import media_player
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr

from .capability_scenarios import Player


class MusicPlayer(Player):
    _attr_supported_features = media_player.MediaPlayerEntityFeature(
        1 | 4 | 8 | 16 | 32 | 512 | 1024 | 4096 | 16384 | 524288
    )
    _attr_state = "idle"
    _attr_media_content_type = "music"

    async def async_play_media(self, media_type, media_id, **kwargs):
        self.operations.append((media_type, media_id))
        self._attr_state = "playing"
        self.async_write_ha_state()

    async def async_media_stop(self):
        self.operations.append("stop")
        self._attr_state = "idle"
        self.async_write_ha_state()


async def exercise_music(hass, entry, client, agent, report, case):
    domain = "polish_context_voice"
    original = deepcopy(dict(entry.options))
    areas = ar.async_get(hass)
    attic = areas.async_create("Poddasze testowe", aliases={"na poddaszu", "poddaszu"})
    workshop = areas.async_create("Pracownia testowa", aliases={"pracowni", "pracownia"})
    registry = er.async_get(hass)
    players = [MusicPlayer("Lab Attic Music"), MusicPlayer("Lab Workshop Music")]
    component = hass.data["media_player"]
    await component.async_add_entities(players)
    for player, area in zip(players, (attic, workshop), strict=True):
        registry.async_update_entity(player.entity_id, area_id=area.id)
        exposed_entities.async_expose_entity(hass, "conversation", player.entity_id, True)
    await hass.async_block_till_done()

    async def form(step):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert flow["type"] == "menu", flow
        return await hass.config_entries.options.async_configure(
            flow["flow_id"], user_input={"next_step_id": step}
        )

    async def submit(flow, data):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=data)
        await hass.async_block_till_done()
        return result

    async def command(text, **kwargs):
        result = await client.call(domain + "/process", text=text, **kwargs)
        await hass.async_block_till_done()
        return result

    media = {
        "entity_id": players[0].entity_id,
        "media_content_id": "media-source://test/curated-playlist",
        "media_content_type": "playlist",
    }
    shortcut = {
        "name": "Moja playlista YouTube",
        "aliases": "moja muzyka YouTube\nYouTube na wieczór",
        "backend": "home_assistant",
        "media": media,
    }
    try:
        with case(report, "music_options_add_shortcut_via_native_media_selector"):
            flow = await form("music_shortcuts")
            flow = await submit(flow, {"shortcut": "__new__"})
            result = await submit(flow, shortcut)
            assert result["type"] == "create_entry", result
            identifier = next(iter(entry.options["music_shortcuts"]))
            assert len(entry.options["music_shortcuts"][identifier]["aliases"]) == 2
            assert entry.options.get("quiet_start") == original.get("quiet_start")

        with case(report, "music_options_reject_colliding_alias_and_youtube_web_page"):
            flow = await form("music_shortcuts")
            flow = await submit(flow, {"shortcut": "__new__"})
            result = await submit(flow, {**shortcut, "name": "Inny skrót"})
            assert result["errors"]["base"] == "duplicate_music_alias", result
            result = await submit(
                flow,
                {
                    **shortcut,
                    "name": "Inny skrót",
                    "aliases": "",
                    "media": {
                        **media,
                        "media_content_id": "https://youtube.com/playlist?list=test",
                    },
                },
            )
            assert result["errors"]["base"] == "youtube_page", result
            hass.config_entries.options.async_abort(flow["flow_id"])

        with case(report, "music_options_rooms_from_unrelated_house_and_general_preservation"):
            for player, area in zip(players, (attic, workshop), strict=True):
                flow = await form("music_rooms")
                flow = await submit(flow, {"area": area.id})
                result = await submit(
                    flow, {"player": player.entity_id, "default_shortcut": identifier}
                )
                assert result["type"] == "create_entry", result
            flow = await form("general")
            result = await submit(flow, {"quiet_start": "21:00"})
            assert result["type"] == "create_entry", result
            assert len(entry.options["music_rooms"]) == 2
            assert identifier in entry.options["music_shortcuts"]

        with case(report, "music_options_empty_player_saves_satellite_fallback_and_keeps_shortcut"):
            flow = await form("music_rooms")
            flow = await submit(flow, {"area": attic.id})
            result = await submit(flow, {"default_shortcut": identifier})
            assert result["type"] == "create_entry", result
            assert entry.options["music_rooms"][attic.id] == {
                "player": "",
                "default_shortcut": identifier,
            }
            flow = await form("music_rooms")
            flow = await submit(flow, {"area": attic.id})
            result = await submit(
                flow, {"player": players[0].entity_id, "default_shortcut": identifier}
            )
            assert result["type"] == "create_entry", result

        with case(report, "music_preview_has_exact_uri_and_no_playback"):
            result = await command("odpal moją playlistę YouTube", source_area=attic.id)
            assert result["status"] == "dry_run", result
            assert result["targets"] == [players[0].entity_id]
            assert result["calls"][0]["data"]["media_content_id"] == media["media_content_id"]
            assert not any(p.operations for p in players)

        with case(report, "music_aliases_and_explicit_area_use_real_entity_service"):
            result = await command(
                "odpal moją muzykę YouTube w pracowni", source_area=attic.id, dry_run=False
            )
            assert result["status"] == "executed", result
            assert result["targets"] == [players[1].entity_id]
            assert players[1].operations[-1] == ("playlist", media["media_content_id"])
            assert not players[0].operations

        with case(report, "music_voice_start_and_stop_on_configured_player"):
            response = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="puść muzykę na poddaszu",
            )
            assert response["response"]["response_type"] == "action_done", response
            result = await command("wyłącz muzykę", source_area=attic.id, dry_run=False)
            assert result["status"] == "executed", result
            assert players[0].operations[-1] == "stop"
            assert hass.states.get(players[1].entity_id).state == "playing"

        with case(report, "music_confirmation_is_source_bound_and_single_use"):
            first = await command(
                "odpal YouTube na wieczór", source_area=workshop.id, dry_run=False
            )
            assert first["status"] == "confirm", first
            before = len(players[1].operations)
            foreign = await command(
                "dobrze",
                source_area=attic.id,
                dry_run=False,
                conversation_id=first["conversation_id"],
            )
            assert foreign["status"] != "executed", foreign
            malformed = await command(
                "tak да",
                source_area=workshop.id,
                dry_run=False,
                conversation_id=first["conversation_id"],
            )
            assert malformed["status"] != "executed" and len(players[1].operations) == before
            first = await command(
                "odpal YouTube na wieczór", source_area=workshop.id, dry_run=False
            )
            assert first["status"] == "confirm", first
            result = await command(
                "Dobrze. Dobrze",
                source_area=workshop.id,
                dry_run=False,
                conversation_id=first["conversation_id"],
            )
            assert result["status"] == "executed", result
            assert len(players[1].operations) == before + 1

        with case(report, "music_exposure_revocation_and_health"):
            exposed_entities.async_expose_entity(hass, "conversation", players[0].entity_id, False)
            await hass.async_block_till_done()
            before = len(players[0].operations)
            result = await command("puść muzykę", source_area=attic.id, dry_run=False)
            assert result["reason"] == "music_player_hidden", result
            assert len(players[0].operations) == before
            health = await client.call(domain + "/health")
            assert any(f["code"] == "music_player_unavailable" for f in health["findings"])
            exposed_entities.async_expose_entity(hass, "conversation", players[0].entity_id, True)
            await hass.async_block_till_done()

        with case(report, "music_ma_backend_rejects_non_ma_player"):
            runtime = next(iter(hass.data[domain].values()))
            changed = deepcopy(runtime.music_settings)
            changed["music_shortcuts"][identifier].update(
                backend="music_assistant", config_entry_id="unrelated-ma"
            )
            runtime.music_settings = changed
            result = await command("puść muzykę", source_area=attic.id, dry_run=False)
            assert result["reason"] == "music_backend_mismatch", result
            runtime.music_settings = {**entry.data, **entry.options}

        with case(report, "music_options_delete_shortcut_clears_room_defaults"):
            flow = await form("music_shortcuts")
            flow = await submit(flow, {"shortcut": identifier})
            result = await submit(flow, {**shortcut, "delete": True})
            assert result["type"] == "create_entry", result
            assert not entry.options["music_shortcuts"]
            assert all(not r["default_shortcut"] for r in entry.options["music_rooms"].values())

        with case(report, "music_short_words_use_live_room_state_across_new_voice_conversations"):
            voice = registry.async_get_or_create("assist_satellite", domain, "music_context_voice")
            registry.async_update_entity(voice.entity_id, area_id=attic.id)
            hass.states.async_set(voice.entity_id, "idle")
            for player in players:
                player._attr_state = "playing"
                player.async_write_ha_state()
            await hass.async_block_till_done()
            before = [len(player.operations) for player in players]
            for text in ("następny", "następna", "dalej", "pomiń", "następna piosenka"):
                result = await client.call(
                    "conversation/process",
                    agent_id=agent,
                    language="pl",
                    text=text,
                    satellite_id=voice.entity_id,
                )
                assert result["response"]["response_type"] == "action_done", result
            assert len(players[0].operations) == before[0] + 5
            assert players[0].operations[-5:] == ["next"] * 5
            assert len(players[1].operations) == before[1]

        with case(report, "music_continue_after_pause_resumes_and_idle_never_controls_remote_room"):
            await command("pauza", source_area=attic.id, dry_run=False)
            result = await command("dalej", source_area=attic.id, dry_run=False)
            assert result["status"] == "executed" and players[0].operations[-1] == "play", result
            players[0]._attr_state = "idle"
            players[0].async_write_ha_state()
            await hass.async_block_till_done()
            before = [len(player.operations) for player in players]
            result = await command("dalej", source_area=attic.id, dry_run=False)
            assert result["reason"] == "music_not_playing", result
            assert before == [len(player.operations) for player in players]

        with case(report, "music_shared_group_one_next_per_queue_from_either_room_and_preview"):
            members = [player.entity_id for player in players]
            for player in players:
                player._attr_state = "playing"
                player._attr_group_members = members
                player.async_write_ha_state()
            await hass.async_block_till_done()
            before = [len(player.operations) for player in players]
            preview = await command("następna", source_area=workshop.id)
            assert preview["status"] == "dry_run" and set(preview["targets"]) == set(members), (
                preview
            )
            assert len(preview["calls"]) == 1, preview
            assert preview["calls"][0]["entity_ids"] == [players[0].entity_id], preview
            assert set(preview["calls"][0]["guard_entity_ids"]) == set(members), preview
            assert before == [len(player.operations) for player in players]
            for area in (attic, workshop):
                result = await command("następny", source_area=area.id, dry_run=False)
                assert result["status"] == "executed" and set(result["completed"]) == set(members)
            assert len(players[0].operations) == before[0] + 2
            assert len(players[1].operations) == before[1]

        with case(report, "music_shared_group_hidden_or_unavailable_member_blocks_whole_command"):
            before = [len(player.operations) for player in players]
            exposed_entities.async_expose_entity(hass, "conversation", players[1].entity_id, False)
            await hass.async_block_till_done()
            result = await command("następny", source_area=attic.id, dry_run=False)
            assert result["reason"] == "music_session_blocked", result
            exposed_entities.async_expose_entity(hass, "conversation", players[1].entity_id, True)
            players[1]._attr_available = False
            players[1].async_write_ha_state()
            await hass.async_block_till_done()
            result = await command("następny", source_area=attic.id, dry_run=False)
            assert result["reason"] == "music_session_unavailable", result
            assert before == [len(player.operations) for player in players]
            players[1]._attr_available = True
            for player in players:
                player._attr_group_members = None
                player.async_write_ha_state()
            await hass.async_block_till_done()

        with case(
            report, "music_ambiguous_player_reply_and_memory_use_native_registry_and_context"
        ):
            runtime = next(iter(hass.data[domain].values()))
            previous_settings = runtime.music_settings
            runtime.music_settings = {}
            runtime.music.memory.clear()
            registry.async_update_entity(players[1].entity_id, area_id=attic.id)
            await hass.async_block_till_done()
            try:
                first = await command("następny", source_area=attic.id)
                assert first["reason"] == "ambiguous_music_session", first
                # Preview must not arm the question.
                first = await command("następny", source_area=attic.id, dry_run=False)
                result = await command(
                    "Lab Workshop Music",
                    source_area=attic.id,
                    dry_run=False,
                    conversation_id=first["conversation_id"],
                )
                assert result["status"] == "executed" and result["targets"] == [
                    players[1].entity_id
                ]
                result = await command("następna", source_area=attic.id, dry_run=False)
                assert (
                    result["targets"] == [players[1].entity_id] and result["status"] == "executed"
                )
            finally:
                runtime.music_settings = previous_settings
                registry.async_update_entity(players[1].entity_id, area_id=workshop.id)
                await hass.async_block_till_done()
        with case(report, "music_short_word_does_not_consume_pending_confirmation"):
            label_registry = lr.async_get(hass)
            label = next(
                (
                    item
                    for item in label_registry.async_list_labels()
                    if item.name == "voice_confirm"
                ),
                None,
            )
            if label is None:
                label = label_registry.async_create("voice_confirm")
            current = registry.async_get(players[0].entity_id)
            previous_labels = set(current.labels)
            registry.async_update_entity(
                current.entity_id, labels=previous_labels | {label.label_id}
            )
            await hass.async_block_till_done()
            try:
                first = await command("następny utwór", source_area=attic.id, dry_run=False)
                assert first["status"] == "confirm", first
                before = len(players[0].operations)
                result = await command(
                    "dalej",
                    source_area=attic.id,
                    dry_run=False,
                    conversation_id=first["conversation_id"],
                )
                assert result["reason"] == "pending_question" and result["continue_conversation"]
                assert len(players[0].operations) == before
                result = await command(
                    "tak",
                    source_area=attic.id,
                    dry_run=False,
                    conversation_id=first["conversation_id"],
                )
                assert result["status"] == "executed" and len(players[0].operations) == before + 1
            finally:
                registry.async_update_entity(current.entity_id, labels=previous_labels)
                await hass.async_block_till_done()
    finally:
        hass.config_entries.async_update_entry(entry, options=original)
        await hass.async_block_till_done()
