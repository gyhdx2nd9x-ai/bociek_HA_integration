"""Public HA MA service boundary: exact URIs, authorization, choices and previews."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_native_boundary import load

from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.media_search import parse_media_search
from custom_components.polish_context_voice.core.music_session import MusicSession
from custom_components.polish_context_voice.core.types import Request


@pytest.fixture
def boundary(monkeypatch):
    allowed = {"read": True, "control": True, "exposed": True}
    user = NS(is_active=True, permissions=NS(check_entity=lambda i, policy: allowed[policy]))
    exports = {
        "homeassistant.auth.permissions.const": {
            "POLICY_CONTROL": "control",
            "POLICY_READ": "read",
        },
        "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
        "homeassistant.components.homeassistant.exposed_entities": {
            "async_should_expose": lambda *args: allowed["exposed"]
        },
    }
    load(monkeypatch, "intents", exports)
    home = HomeModel(
        areas=[Area("living", "Salon", ("salonie",)), Area("kitchen", "Kuchnia", ("kuchni",))],
        entities=[
            Entity(
                f"media_player.{area}",
                area,
                area,
                exposed=True,
                state="idle",
                platform="music_assistant",
                config_entry_id="ma",
                attributes={"supported_features": 65535},
            )
            for area in ("living", "kitchen")
        ],
    )
    registries = {i: NS(platform="music_assistant", config_entry_id="ma") for i in home.entities}
    module = load(
        monkeypatch,
        "media_search",
        {
            "homeassistant.helpers.entity_registry": {
                "async_get": lambda h: NS(async_get=registries.get)
            },
        },
    )
    response = {
        "tracks": [
            {
                "name": "X",
                "uri": "library://track/1",
                "media_type": "track",
                "version": "",
                "artists": [{"name": "Queen"}],
            }
        ]
    }
    calls = []

    async def invoke(domain, service, data, **kwargs):
        calls.append((domain, service, dict(data), kwargs))
        return response if service == "search" else None

    hass = NS(
        auth=NS(async_get_user=AsyncMock(return_value=user)),
        states=NS(get=lambda i: NS(state=home.entities[i].state) if i in home.entities else None),
        services=NS(has_service=lambda *a: True, async_call=AsyncMock(side_effect=invoke)),
    )
    runtime = NS(
        hass=hass,
        music=MusicSession(),
        media_search_pending={},
        music_settings={"music_rooms": {a: {"player": f"media_player.{a}"} for a in home.areas}},
        media_search_settings={},
        visible_model=lambda user: home.visible_to(lambda i: allowed["read"]),
    )
    request = Request(
        "Puść utwór X wykonawcy Queen", user_id="alice", source_area="living", conversation_id="c"
    )
    return NS(
        module=module,
        home=home,
        runtime=runtime,
        request=request,
        user=user,
        allowed=allowed,
        response=response,
        calls=calls,
        registries=registries,
    )


async def run(b, text=None, command=True, **changes):
    request = replace(b.request, text=text or b.request.text, **changes)
    parsed = parse_media_search(request.text, b.runtime.music_settings) if command else None
    return await b.module.process(b.runtime, request, "context", parsed, b.user)


async def test_exact_title_artist_uses_public_search_then_exact_uri(boundary):
    b = boundary
    result = await run(b)
    assert result.status == "executed"
    assert result.targets == ("media_player.living",)
    assert [call[1] for call in b.calls] == ["search", "play_media"]
    assert b.calls[0][2] == {
        "config_entry_id": "ma",
        "name": "X",
        "media_type": ["track"],
        "limit": 5,
        "library_only": False,
        "artist": "Queen",
    }
    assert b.calls[1][2]["media_id"] == "library://track/1"
    assert b.calls[1][2]["entity_id"] == ["media_player.living"]
    assert all(call[3]["context"] == "context" for call in b.calls)


async def test_missing_audiobook_title_prompts_and_searches_followup(boundary):
    b = boundary
    b.response["audiobooks"] = [
        {
            "name": "Diuna",
            "uri": "library://audiobook/1",
            "media_type": "audiobook",
            "authors": [{"name": "Frank Herbert"}],
        }
    ]
    first = await run(b, "Włącz mi audiobooka")
    assert first.status == "clarify" and first.continue_conversation
    assert first.speech == "Jaki audiobook mam wyszukać?"
    assert not b.calls

    second = await run(b, "Diuna autora Frank Herbert", command=False)
    assert second.status == "executed"
    assert b.calls[0][2]["name"] == "Diuna"
    assert b.calls[1][2]["media_id"] == "library://audiobook/1"
    assert b.calls[1][2]["entity_id"] == ["media_player.living"]


async def test_preview_reads_catalog_but_never_starts_player(boundary):
    b = boundary
    result = await run(b, dry_run=True)
    assert result.status == "dry_run" and result.calls[0].data["media_id"] == "library://track/1"
    assert [c[1] for c in b.calls] == ["search"]


@pytest.mark.parametrize("mode", ["read", "control", "exposed"])
async def test_hidden_or_unauthorized_player_cannot_search_or_play(boundary, mode):
    b = boundary
    b.allowed[mode] = False
    result = await run(b)
    assert result.status in {"blocked", "error"}
    assert not b.calls


async def test_multiple_versions_ask_then_play_only_selected_uri(boundary):
    b = boundary
    b.response["tracks"].append(
        {"name": "X", "uri": "library://track/2", "version": "live", "artists": [{"name": "Queen"}]}
    )
    first = await run(b)
    assert first.status == "clarify" and first.continue_conversation
    assert [c[1] for c in b.calls] == ["search"]
    for text in ("drugi да", "drugi albo trzeci", "drugi 😊"):
        rejected = await run(b, text, command=False)
        assert rejected.continue_conversation and [call[1] for call in b.calls] == ["search"]
        assert not rejected.response_data
    second = await run(b, "Nie, ten drugi", command=False)
    assert second.status == "executed"
    assert b.calls[-1][2]["media_id"] == "library://track/2"
    assert [c[1] for c in b.calls] == ["search", "play_media"]
    again = await run(b, "drugi", command=False)
    assert again.reason == "media_search_no_pending"


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "bob"},
        {"source_area": "kitchen"},
        {"satellite_id": "assist_satellite.other"},
        {"dry_run": True},
    ],
)
async def test_choice_cannot_cross_user_satellite_room_or_mode(boundary, changes):
    b = boundary
    b.response["tracks"].append(
        {"name": "X", "uri": "library://track/2", "version": "live", "artists": [{"name": "Queen"}]}
    )
    await run(b)
    result = await run(b, "drugi", command=False, **changes)
    assert result.reason == "media_search_no_pending"
    assert [c[1] for c in b.calls] == ["search"]


async def test_control_revocation_between_question_and_choice_blocks_playback(boundary):
    b = boundary
    b.response["tracks"].append(
        {"name": "X", "uri": "library://track/2", "artists": [{"name": "Queen"}]}
    )
    await run(b)
    b.allowed["control"] = False
    result = await run(b, "drugi", command=False)
    assert result.status == "error" and [c[1] for c in b.calls] == ["search"]


async def test_renamed_room_player_mapping_invalidates_choice(boundary):
    b = boundary
    b.response["tracks"].append(
        {"name": "X", "uri": "library://track/2", "artists": [{"name": "Queen"}]}
    )
    await run(b)
    b.runtime.music_settings["music_rooms"]["living"]["player"] = "media_player.kitchen"
    result = await run(b, "drugi", command=False)
    assert result.reason == "music_player_moved"
    assert [c[1] for c in b.calls] == ["search"]


async def test_expired_choice_does_not_execute(boundary):
    b = boundary
    b.response["tracks"].append(
        {"name": "X", "uri": "library://track/2", "artists": [{"name": "Queen"}]}
    )
    await run(b)
    key = b.request.session_key
    b.runtime.media_search_pending[key] = replace(b.runtime.media_search_pending[key], expires_at=0)
    result = await run(b, "drugi", command=False)
    assert result.reason == "media_search_no_pending" and [c[1] for c in b.calls] == ["search"]


async def test_source_is_not_invented_from_library_uri(boundary):
    b = boundary
    result = await run(b, "Puść utwór X z Plex")
    assert result.reason == "media_search_source_unverified"
    assert [c[1] for c in b.calls] == ["search"]


async def test_direct_source_uri_can_satisfy_explicit_source(boundary):
    b = boundary
    b.response["tracks"][0]["uri"] = "plex://track/test"
    result = await run(b, "Puść utwór X z Plex")
    assert result.status == "executed" and b.calls[-1][2]["media_id"] == "plex://track/test"
    assert "provider" not in b.calls[0][2]


async def test_no_result_is_specific(boundary):
    b = boundary
    b.response["tracks"] = []
    result = await run(b)
    assert result.reason == "media_search_no_results" and not result.calls


async def test_search_timeout_never_attempts_playback(boundary, monkeypatch):
    b = boundary

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    b.runtime.hass.services.async_call.side_effect = slow
    monkeypatch.setattr(b.module, "SEARCH_TIMEOUT", 0.001)
    result = await run(b)
    assert result.reason == "media_search_timeout"
    assert b.runtime.hass.services.async_call.await_count == 1


async def test_playback_conflict_requires_confirmation(boundary):
    b = boundary
    player = b.home.entities["media_player.living"]
    b.home.entities[player.id] = replace(
        player, state="playing", attributes={**player.attributes, "media_content_id": "old"}
    )
    first = await run(b)
    assert first.status == "confirm" and first.continue_conversation
    assert [c[1] for c in b.calls] == ["search"]
    second = await run(b, "Tak. Tak", command=False)
    assert second.status == "executed"
    assert [c[1] for c in b.calls] == ["search", "play_media"]


async def test_audiobook_title_and_explicit_profile(boundary):
    b = boundary
    b.runtime.media_search_settings["media_search_username"] = "books-user"
    b.response["audiobooks"] = [{"name": "Solaris", "uri": "library://audiobook/8"}]
    result = await run(b, "Wznów audiobook Solaris")
    assert result.status == "executed"
    assert b.calls[0][2]["username"] == "books-user"
    assert b.calls[-1][2]["username"] == "books-user"
    assert b.calls[-1][2]["media_type"] == "audiobook"


async def test_audiobook_author_absent_in_native_schema_requires_choice(boundary):
    b = boundary
    b.response["audiobooks"] = [{"name": "Solaris", "uri": "library://audiobook/8"}]
    first = await run(b, "Puść audiobook Solaris autora Stanisław Lem")
    assert first.continue_conversation and "nie podaje tu autora" in first.speech
    assert "artist" not in b.calls[0][2]
    assert [c[1] for c in b.calls] == ["search"]
    second = await run(b, "potwierdzam", command=False)
    assert second.status == "executed"


async def test_artist_search_can_play_artist_uri(boundary):
    b = boundary
    b.response["artists"] = [{"name": "Queen", "uri": "library://artist/9"}]
    result = await run(b, "Puść muzykę wykonawcy Queen")
    assert result.status == "executed" and b.calls[-1][2]["media_id"] == "library://artist/9"


async def test_full_title_colliding_with_explicit_room_never_redirects_playback(boundary):
    b = boundary
    b.response["tracks"] = [{"name": "Noc w kuchni", "uri": "library://track/3"}]
    result = await run(b, "Puść utwór Noc w kuchni")
    assert result.reason == "media_search_title_location_ambiguous"
    assert not result.calls and not result.continue_conversation
    assert not b.runtime.media_search_pending
    assert [c[1] for c in b.calls] == ["search"]
    assert b.calls[0][2]["name"] == "Noc w kuchni"


async def test_quoted_complete_title_can_keep_area_words_without_redirect(boundary):
    b = boundary
    b.response["tracks"] = [{"name": "Noc w kuchni", "uri": "library://track/3"}]
    result = await run(b, "Puść utwór „Noc w kuchni”")
    assert result.status == "executed" and result.targets == ("media_player.living",)
    assert b.calls[0][2]["name"] == "Noc w kuchni"


async def test_explicit_room_marker_cannot_be_reinterpreted_as_a_title(boundary):
    b = boundary
    result = await run(b, "Puść utwór X w pomieszczeniu kuchni")
    assert result.status == "executed" and result.targets == ("media_player.kitchen",)
    assert [c[2]["name"] for c in b.calls if c[1] == "search"] == ["X"]


async def test_explicit_room_overrides_satellite_after_title_probe(boundary):
    b = boundary
    result = await run(b, "Puść utwór X w kuchni")
    assert result.status == "executed" and result.targets == ("media_player.kitchen",)
    assert [c[2]["name"] for c in b.calls if c[1] == "search"] == ["X w kuchni", "X"]


async def test_existing_player_admission_prevents_racing_write(boundary):
    b = boundary
    with b.runtime.music.admission.enter(("ha", "media_player.living")):
        result = await run(b)
    assert result.status == "busy" and [c[1] for c in b.calls] == ["search"]


async def test_catalog_listing_needs_no_room_or_control_and_does_not_arm_playback(boundary):
    b = boundary
    b.allowed["control"] = False
    b.response["audiobooks"] = [{"name": "Solaris", "uri": "library://audiobook/8"}]
    first = await run(b, "Jakie mam audiobooki autora Stanisław Lem?", source_area=None)
    assert first.status == "answered" and not first.continue_conversation
    assert not b.runtime.media_search_pending
    assert [c[1] for c in b.calls] == ["search"]
    assert b.calls[0][2]["name"] == "Stanisław Lem" and "artist" not in b.calls[0][2]
    second = await run(b, "pierwsza", command=False, source_area=None)
    assert second.reason == "media_search_no_pending"
    assert [c[1] for c in b.calls] == ["search"]


async def test_read_denied_catalog_never_searches(boundary):
    b = boundary
    b.allowed["read"] = False
    result = await run(b, "Jakie mam audiobooki autora Lem?", source_area=None)
    assert result.reason == "media_search_catalog_instance" and not b.calls


@pytest.mark.parametrize("revoke", ["exposure", "active_user", "read_permission"])
async def test_catalog_rechecks_live_access_after_search_before_disclosure(boundary, revoke):
    b = boundary
    b.response["audiobooks"] = [{"name": "Private book title", "uri": "library://audiobook/8"}]

    async def revoke_during_search(*args, **kwargs):
        if revoke == "exposure":
            b.allowed["exposed"] = False
        elif revoke == "active_user":
            b.user.is_active = False
        else:
            b.allowed["read"] = False
        return b.response

    b.runtime.hass.services.async_call.side_effect = revoke_during_search
    result = await run(b, "Jakie mam audiobooki autora Lem?", source_area=None)
    assert result.reason in {"media_search_catalog_instance", "media_search_catalog_denied"}
    assert "Private book title" not in result.speech
    assert not result.continue_conversation and not b.runtime.media_search_pending
    assert b.runtime.hass.services.async_call.await_count == 1


async def test_known_wrong_author_never_autoplays_or_offers_wrong_book(boundary):
    b = boundary
    b.response["audiobooks"] = [
        {"name": "Solaris", "uri": "library://audiobook/8", "authors": ["Other author"]}
    ]
    result = await run(b, "Puść audiobook Solaris autora Stanisław Lem")
    assert result.reason == "media_search_no_results"
    assert not b.runtime.media_search_pending and [c[1] for c in b.calls] == ["search"]


async def test_dry_run_ambiguity_never_arms_a_choice(boundary):
    b = boundary
    b.response["tracks"].append(
        {"name": "X", "uri": "library://track/2", "version": "live", "artists": [{"name": "Queen"}]}
    )
    result = await run(b, dry_run=True)
    assert not result.continue_conversation and not b.runtime.media_search_pending
    assert [c[1] for c in b.calls] == ["search"]


async def test_disabled_search_never_contacts_ma(boundary):
    b = boundary
    b.runtime.media_search_settings["media_search_enabled"] = False
    result = await run(b)
    assert result.reason == "media_search_disabled" and not b.calls


def satellite_setup(b):
    b.runtime.music_settings["music_rooms"].pop("living")
    b.home.entities["assist_satellite.source"] = Entity(
        "assist_satellite.source", "Satelita", "living", device_id="voice", state="idle"
    )
    b.home.satellites["assist_satellite.source"] = "living"
    b.home.entities["media_player.raw"] = Entity(
        "media_player.raw", "Satelita audio", "living", device_id="voice", state="idle"
    )
    b.home.entities["media_player.living"] = replace(
        b.home.entities["media_player.living"],
        area_id=None,
        device_id="ma-voice",
        unique_id="media_player.raw",
    )
    b.request = replace(b.request, satellite_id="assist_satellite.source", device_id="voice")


@pytest.mark.parametrize("kind", ["track", "audiobook"])
async def test_satellite_fallback_search_and_exact_playback(boundary, kind):
    b = boundary
    satellite_setup(b)
    b.response.clear()
    b.response[kind + "s"] = [{"name": "X", "uri": f"library://{kind}/1"}]
    text = "Puść utwór X" if kind == "track" else "Puść audiobook X"
    result = await run(b, text)
    assert result.status == "executed" and result.targets == ("media_player.living",)
    assert [call[1] for call in b.calls] == ["search", "play_media"]
    assert b.calls[-1][2]["media_id"] == f"library://{kind}/1"


async def test_satellite_link_change_during_search_prevents_playback(boundary):
    b = boundary
    satellite_setup(b)
    previous = b.runtime.hass.services.async_call.side_effect

    async def changed(*args, **kwargs):
        response = await previous(*args, **kwargs)
        b.home.entities["media_player.living"] = replace(
            b.home.entities["media_player.living"], unique_id="unrelated"
        )
        return response

    b.runtime.hass.services.async_call.side_effect = changed
    result = await run(b)
    assert result.status != "executed" and [call[1] for call in b.calls] == ["search"]


async def test_satellite_without_ma_player_does_not_search_or_play(boundary):
    b = boundary
    satellite_setup(b)
    b.home.entities["media_player.living"] = replace(
        b.home.entities["media_player.living"], unique_id="unrelated"
    )
    result = await run(b)
    assert result.reason == "music_satellite_ma_missing" and not b.calls
