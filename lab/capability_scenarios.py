"""Native entity services on virtual providers. Satellite audio/STT are simulated."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import yaml
from homeassistant.auth.const import GROUP_ID_READ_ONLY
from homeassistant.components import (
    assist_satellite,
    calendar,
    cover,
    fan,
    media_player,
    todo,
    weather,
)
from homeassistant.components.assist_satellite import AssistSatelliteAnnouncement
from homeassistant.components.homeassistant import exposed_entities
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .contact_scenarios import exercise_contacts
from .maintenance_scenarios import exercise_maintenance


class Player(media_player.MediaPlayerEntity):
    _attr_should_poll = False
    _attr_supported_features = media_player.MediaPlayerEntityFeature(
        1 | 4 | 8 | 16 | 32 | 1024 | 16384
    )
    _attr_state = "playing"
    _attr_volume_level = 0.5

    def __init__(self, name):
        self._attr_name = name
        self._attr_unique_id = name
        self.operations = []

    async def async_media_pause(self):
        self.operations.append("pause")
        self._attr_state = "paused"
        self.async_write_ha_state()

    async def async_media_play(self):
        self.operations.append("play")
        self._attr_state = "playing"
        self.async_write_ha_state()

    async def async_media_next_track(self):
        self.operations.append("next")

    async def async_media_previous_track(self):
        self.operations.append("previous")

    async def async_set_volume_level(self, volume):
        self.operations.append(volume)
        self._attr_volume_level = volume
        self.async_write_ha_state()

    async def async_volume_up(self):
        await self.async_set_volume_level(min(1, self.volume_level + 0.1))

    async def async_volume_down(self):
        await self.async_set_volume_level(max(0, self.volume_level - 0.1))

    async def async_mute_volume(self, mute):
        self.operations.append(mute)


class Blind(cover.CoverEntity):
    _attr_should_poll = False
    _attr_name = "Lab Kitchen Blind"
    _attr_unique_id = "lab_kitchen_blind"
    _attr_supported_features = cover.CoverEntityFeature.SET_POSITION
    _attr_current_cover_position = 20
    _attr_is_closed = False
    _attr_device_class = cover.CoverDeviceClass.BLIND

    async def async_set_cover_position(self, **kwargs):
        self._attr_current_cover_position = kwargs["position"]
        self._attr_is_closed = self._attr_current_cover_position == 0
        self.async_write_ha_state()


class Fan(fan.FanEntity):
    _attr_should_poll = False
    _attr_name = "Lab Living Fan"
    _attr_unique_id = "lab_living_fan"
    _attr_supported_features = (
        fan.FanEntityFeature.TURN_ON | fan.FanEntityFeature.TURN_OFF
        | fan.FanEntityFeature.SET_SPEED
    )
    _attr_percentage = 0

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        await self.async_set_percentage(percentage if percentage is not None else 100)

    async def async_turn_off(self, **kwargs):
        await self.async_set_percentage(0)

    async def async_set_percentage(self, percentage):
        self._attr_percentage = percentage
        self.async_write_ha_state()


class ListEntity(todo.TodoListEntity):
    _attr_should_poll = False
    _attr_name = "Lab Shopping"
    _attr_unique_id = "lab_shopping"
    _attr_supported_features = todo.TodoListEntityFeature(1)

    def __init__(self):
        self.items = []

    @property
    def todo_items(self):
        return self.items

    async def async_create_todo_item(self, item):
        self.items.append(replace(item, uid=uuid4().hex))
        self.async_write_ha_state()


class WeatherEntity(weather.WeatherEntity):
    _attr_should_poll = False
    _attr_name = "Lab Weather"
    _attr_unique_id = "lab_weather"
    _attr_condition = "sunny"
    _attr_native_temperature = 17.7
    _attr_native_wind_speed = 25
    _attr_native_wind_gust_speed = 45
    _attr_native_wind_speed_unit = "km/h"
    _attr_native_temperature_unit = "°C"
    _attr_supported_features = weather.WeatherEntityFeature(1)

    async def async_forecast_daily(self):
        return [
            {
                "datetime": (dt_util.now() + timedelta(days=day)).replace(hour=12).isoformat(),
                "condition": "rainy",
                "native_temperature": 15.4,
                "precipitation_probability": 80,
            }
            for day in (0, 1)
        ]

    async def async_forecast_hourly(self):
        return [
            {
                "datetime": (dt_util.now() + timedelta(hours=2)).isoformat(),
                "condition": "rainy",
                "precipitation_probability": 80,
            }
        ]


class CalendarEntity(calendar.CalendarEntity):
    _attr_should_poll = False
    _attr_name = "Lab Calendar"
    _attr_unique_id = "lab_calendar"

    @property
    def event(self):
        return None

    async def async_get_events(self, hass, start_date, end_date):
        local = dt_util.as_local(start_date).replace(hour=10)
        return [calendar.CalendarEvent(local, local + timedelta(hours=1), "Wizyta testowa")]


class Satellite(assist_satellite.AssistSatelliteEntity):
    _attr_name = "Lab Kitchen Voice"
    _attr_unique_id = "lab_kitchen_voice"
    _attr_supported_features = assist_satellite.AssistSatelliteEntityFeature(3)

    def __init__(self):
        self.messages = []

    async def async_get_configuration(self):
        return assist_satellite.AssistSatelliteConfiguration([], [], 0)

    async def async_set_configuration(self, config):
        pass

    def on_pipeline_event(self, event):
        pass

    async def _resolve_announcement_media_id(self, message, media_id, **kwargs):
        # Only transport synthesis is simulated; HA service, schema, state machine,
        # ask-question matching and production adapter are real.
        return AssistSatelliteAnnouncement(message, "lab://audio", "lab://audio", None, "url")

    async def async_announce(self, announcement):
        self.messages.append(announcement.message)

    async def async_start_conversation(self, announcement):
        self.messages.append(announcement.message)
        self._ask_question_future.set_result("tak")


async def exercise_capabilities(hass, fixture, client, agent, user, report, case):
    domain = "polish_context_voice"
    from custom_components.polish_context_voice.core.types import Request

    runtime = next(iter(hass.data[domain].values()))
    registry = er.async_get(hass)
    labels = {}
    for name in (
        "voice_shopping",
        "voice_announce",
        "voice_routine",
        "voice_primary",
        "voice_block",
    ):
        label = next(
            (item for item in lr.async_get(hass).async_list_labels() if item.name == name), None
        )
        labels[name] = label.label_id if label else lr.async_get(hass).async_create(name).label_id
    blind, desk_fan = Blind(), Fan()
    providers = {
        "media_player": [Player("Lab Living Player"), Player("Lab Kitchen Player")],
        "todo": [ListEntity()],
        "weather": [WeatherEntity()],
        "calendar": [CalendarEntity()],
        "assist_satellite": [Satellite()],
        "cover": [blind],
        "fan": [desk_fan],
    }
    for name, entities in providers.items():
        assert await async_setup_component(hass, name, {}), name
        await hass.data[name].async_add_entities(entities)
    await hass.async_block_till_done()
    player, kitchen_player = providers["media_player"]
    shopping = providers["todo"][0]
    forecast = providers["weather"][0]
    diary = providers["calendar"][0]
    satellite = providers["assist_satellite"][0]
    for entity, area, roles, aliases in (
        (player, "living_room", ["voice_primary"], ["muzyka"]),
        (kitchen_player, "kitchen", ["voice_primary"], []),
        (shopping, None, ["voice_shopping", "voice_primary"], ["zakupy"]),
        (forecast, None, ["voice_primary"], []),
        (diary, None, ["voice_primary"], ["rodzina"]),
        (satellite, "kitchen", ["voice_announce"], []),
        (blind, "kitchen", [], []),
        (desk_fan, "living_room", [], []),
    ):
        registry.async_update_entity(
            entity.entity_id, area_id=area, labels={labels[n] for n in roles}, aliases=aliases
        )
        exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, True)
    for name, value, exposed in (
        ("lab_window", "on", True),
        ("lab_window_unknown", "unavailable", True),
        ("lab_secret_window", "on", False),
    ):
        entity = registry.async_get_or_create(
            "binary_sensor", domain, name, suggested_object_id=name
        )
        registry.async_update_entity(entity.entity_id, area_id="living_room")
        hass.states.async_set(
            entity.entity_id, value, {"device_class": "window", "friendly_name": name}
        )
        exposed_entities.async_expose_entity(hass, "conversation", entity.entity_id, exposed)
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "lab_voice_routine": {
                    "alias": "Lab voice routine",
                    "sequence": [{"event": "pcv_lab_routine"}],
                }
            }
        },
    )
    await hass.async_block_till_done()
    script = registry.async_get("script.lab_voice_routine")
    registry.async_update_entity(
        script.entity_id, aliases=["dobranoc"], labels={labels["voice_routine"]}
    )
    exposed_entities.async_expose_entity(hass, "conversation", script.entity_id, True)
    await hass.async_block_till_done()

    async def command(text, **kwargs):
        result = await client.call(domain + "/process", text=text, **kwargs)
        await hass.async_block_till_done()
        return result

    async def execute(text, **kwargs):
        first = await command(text, dry_run=False, **kwargs)
        assert first["status"] == "confirm", first
        result = await command(
            "potwierdzam", dry_run=False, conversation_id=first["conversation_id"], **kwargs
        )
        assert result["status"] in {"executed", "answered"}, result
        return result

    with case(report, "cover_position_uses_explicit_room_and_waits_for_confirmation"):
        preview = await command("Otwórz rolety do połowy w kuchni", source_area="living_room")
        assert preview["status"] == "confirm" and preview["targets"] == [blind.entity_id], preview
        assert blind.current_cover_position == 20
        assert preview["speech"].endswith("na 50 procent?")
        await execute("Ustaw rolety na 35 procent w kuchni", source_area="living_room")
        assert blind.current_cover_position == 35

    with case(report, "fan_uses_satellite_room_and_applies_speed_directly"):
        for text, percentage in (
            ("Włącz wiatrak tutaj", 100),
            ("Ustaw wentylator na pięćdziesiąt procent", 50),
            ("Wyłącz wentylator", 0),
        ):
            result = await command(text, satellite_id=fixture["satellite"], dry_run=False)
            assert result["status"] == "executed", result
            assert result["targets"] == [desk_fan.entity_id], result
            assert desk_fan.percentage == percentage

    with case(report, "capabilities_time_date_in_ha_timezone"):
        result = await command("która godzina", dry_run=False)
        assert (
            result["status"] == "answered" and dt_util.now().strftime("%H:%M") in result["speech"]
        ), result
        result = await command("jaki dzisiaj dzień", dry_run=False)
        assert (
            result["status"] == "answered"
            and dt_util.now().strftime("%d.%m.%Y") in result["speech"]
        )

    with case(report, "capabilities_state_unknown_and_hidden_scope"):
        result = await command("czy okna są otwarte", source_area="living_room", dry_run=False)
        assert result["status"] == "answered", result
        assert "lab_window" in result["speech"] and "Nie mam aktualnego odczytu" in result["speech"]
        assert "secret" not in str(result)
        assert len(result["targets"]) == 2

    await exercise_contacts(hass, fixture, client, agent, report, case)
    await exercise_maintenance(hass, fixture, client, agent, report, case)

    with case(report, "capabilities_media_preview_confirmation_and_explicit_area"):
        preview = await command("wstrzymaj muzykę", source_area="living_room")
        assert preview["status"] == "dry_run" and preview["targets"] == [player.entity_id], preview
        assert not player.operations and not runtime.pending_capabilities
        invalid = await command(
            "potwierdzam",
            source_area="living_room",
            dry_run=False,
            conversation_id=preview["conversation_id"],
        )
        assert invalid["status"] == "clarify" and not player.operations
        await execute("wstrzymaj muzykę w kuchni", source_area="living_room")
        assert kitchen_player.operations == ["pause"] and not player.operations
        for text in (
            "wstrzymaj muzykę",
            "wznów muzykę",
            "następny utwór",
            "poprzedni utwór",
            "ustaw głośność na 30 procent",
            "głośniej",
            "ciszej",
            "wycisz muzykę",
            "przywróć dźwięk",
        ):
            await execute(text, satellite_id=fixture["satellite"])
        assert player.operations[:5] == ["pause", "play", "next", "previous", 0.3], (
            player.operations
        )

    with case(report, "capabilities_confirmation_identity_expiry_cancel_and_revalidation"):
        for mode in ("expire", "cancel", "interrupt", "hidden", "label", "user", "source"):
            before = len(player.operations)
            first = await command("ciszej", source_area="living_room", dry_run=False)
            cid = first["conversation_id"]
            key = next(k for k in runtime.pending_capabilities if k[1] == cid)
            if mode == "expire":
                runtime.pending_capabilities[key].expires_at = 0
            if mode in {"cancel", "interrupt"}:
                await command(
                    "nie" if mode == "cancel" else "która godzina",
                    source_area="living_room",
                    dry_run=False,
                    conversation_id=cid,
                )
            if mode == "hidden":
                exposed_entities.async_expose_entity(hass, "conversation", player.entity_id, False)
            if mode == "label":
                registry.async_update_entity(player.entity_id, labels={labels["voice_block"]})
            await hass.async_block_till_done()
            try:
                if mode == "user":
                    reader = await hass.auth.async_create_user(
                        "Other capability user", group_ids=[GROUP_ID_READ_ONLY]
                    )
                    result = (
                        await runtime.process(
                            Request(
                                "potwierdzam",
                                source_area="living_room",
                                conversation_id=cid,
                                user_id=reader.id,
                            ),
                            Context(user_id=reader.id),
                        )
                    ).to_dict()
                else:
                    result = await command(
                        "potwierdzam",
                        source_area="kitchen" if mode == "source" else "living_room",
                        dry_run=False,
                        conversation_id=cid,
                    )
                assert result["status"] not in {"executed", "answered"}, (mode, result)
                assert len(player.operations) == before
            finally:
                exposed_entities.async_expose_entity(hass, "conversation", player.entity_id, True)
                registry.async_update_entity(player.entity_id, labels={labels["voice_primary"]})
                runtime.pending_capabilities.clear()
                await hass.async_block_till_done()

    with case(report, "capabilities_shopping_native_add_read_and_single_use"):
        first = await command(
            "dodaj Żółty ser do zakupów", dry_run=False, source_area="living_room"
        )
        assert first["status"] == "confirm" and not shopping.items
        cid = first["conversation_id"]
        done = await command(
            "potwierdzam", dry_run=False, source_area="living_room", conversation_id=cid
        )
        assert done["status"] == "executed" and shopping.items[0].summary == "Żółty ser", done
        await command("potwierdzam", dry_run=False, source_area="living_room", conversation_id=cid)
        assert len(shopping.items) == 1
        result = await command("przeczytaj listę zakupów", dry_run=False)
        assert result["status"] == "answered" and "Żółty ser" in result["speech"], result

    with case(report, "capabilities_weather_today_tomorrow_and_calendar_native_responses"):
        result = await command("jaka jest pogoda", dry_run=False)
        assert result["status"] == "answered" and "18" in result["speech"], result
        result = await command("czy jutro będzie padać", dry_run=False)
        assert (
            result["status"] == "answered"
            and result["response_data"]["precipitation_probability"] == 80
            and "deszcz" in result["speech"]
        ), result
        assert (
            result["response_data"]["date"]
            == (dt_util.now().date() + timedelta(days=1)).isoformat()
        )
        result = await command("co mam jutro w kalendarzu rodzina", dry_run=False)
        assert (
            result["status"] == "answered"
                and "o dziesiątej" in result["speech"]
            and "Wizyta testowa" in result["speech"]
        ), result

    with case(report, "weather_exact_user_transcript_through_conversation"):
        before = {s.entity_id: s.state for s in hass.states.async_all("light")}
        result = await client.call(
            "conversation/process",
            agent_id=agent,
            language="pl",
            text="Jaka dziś pogoda?",
            satellite_id=fixture["satellite"],
        )
        response = result["response"]
        assert response["response_type"] == "query_answer", result
        assert "słonecznie" in response["speech"]["plain"]["speech"], result
        assert "18" in response["speech"]["plain"]["speech"], result
        assert not result["continue_conversation"]
        assert before == {s.entity_id: s.state for s in hass.states.async_all("light")}

    with case(report, "weather_natural_forecast_today_tomorrow_and_preview"):
        events = []
        unsub = hass.bus.async_listen("call_service", lambda event: events.append(event))
        try:
            preview = await command("Czy dziś będzie padać?", satellite_id=fixture["satellite"])
            assert preview["status"] == "dry_run", preview
            assert preview["calls"][0]["service"] == "get_forecasts", preview
            assert not events, events
            for text, day in (
                ("Jaka będzie dziś pogoda?", 0),
                ("Czy będzie dzisiaj padać?", 0),
                ("Jaka jutro pogoda?", 1),
                ("Jaka jutro będzie pogoda?", 1),
            ):
                result = await command(text, dry_run=False, satellite_id=fixture["satellite"])
                assert result["status"] == "answered", result
                assert (
                    result["response_data"]["date"]
                    == (dt_util.now().date() + timedelta(days=day)).isoformat()
                ), result
                assert "15" in result["speech"] and "deszcz" in result["speech"], result
            assert len(events) == 4
            assert all(
                event.data["domain"] == "weather" and event.data["service"] == "get_forecasts"
                for event in events
            )
        finally:
            unsub()

    with case(report, "weather_unknown_or_unavailable_has_specific_speech_without_provider_calls"):
        events = []
        unsub = hass.bus.async_listen("call_service", lambda event: events.append(event))
        try:
            for available, condition in ((False, "sunny"), (True, None)):
                forecast._attr_available = available
                forecast._attr_condition = condition
                forecast.async_write_ha_state()
                await hass.async_block_till_done()
                assert hass.states.get(forecast.entity_id).state in {"unavailable", "unknown"}
                for text, expected in (
                    (
                        "Jaka jest pogoda?",
                        "Nie mogę teraz sprawdzić pogody. Dane pogodowe są niedostępne.",
                    ),
                    ("Jaka będzie jutro pogoda?", "Nie mam teraz dostępu do prognozy pogody."),
                    (
                        "Kiedy przestanie padać?",
                        "Nie mogę teraz sprawdzić, kiedy przestanie padać. "
                        "Prognoza jest niedostępna.",
                    ),
                ):
                    result = await command(text, dry_run=False)
                    assert result["reason"] == "unavailable", result
                    assert result["speech"] == expected, result
                    assert not result["calls"], result
                result = await client.call(
                    "conversation/process",
                    agent_id=agent,
                    language="pl",
                    text="Jaka jest pogoda?",
                    satellite_id=fixture["satellite"],
                )
                assert result["response"]["response_type"] == "error", result
                assert result["response"]["speech"]["plain"]["speech"] == (
                    "Nie mogę teraz sprawdzić pogody. Dane pogodowe są niedostępne."
                ), result
            assert not events, events
        finally:
            unsub()
            forecast._attr_available = True
            forecast._attr_condition = "sunny"
            forecast.async_write_ha_state()
            await hass.async_block_till_done()

    with case(report, "weather_unexposed_provider_has_specific_speech_and_keeps_exposure_boundary"):
        exposed_entities.async_expose_entity(hass, "conversation", forecast.entity_id, False)
        await hass.async_block_till_done()
        try:
            result = await command("Jaka jest pogoda?", dry_run=False)
            assert result["status"] == "clarify" and result["reason"] == "no_targets", result
            assert result["speech"] == (
                "Nie mogę teraz sprawdzić pogody. Dane pogodowe są niedostępne."
            ), result
            assert not result["targets"] and not result["calls"], result
        finally:
            exposed_entities.async_expose_entity(hass, "conversation", forecast.entity_id, True)
            await hass.async_block_till_done()

    with case(report, "weather_current_daily_rain_wind_rounding_and_preview"):
        events = []
        unsub = hass.bus.async_listen("call_service", lambda e: events.append(e))
        try:
            result = await command("jaka jest pogoda")
            assert result["status"] == "dry_run" and not events, result
            assert result["calls"][0]["data"]["type"] == "daily", result
            result = await command("jaka jest pogoda", dry_run=False)
            assert result["status"] == "answered", result
            speech = result["speech"]
            assert "około 18 stopni" in speech and "17.7" not in speech, result
            assert "wieje porywisty wiatr" in speech, result
            assert "W dzisiejszej prognozie przewiduje się deszcz" in speech, result
            assert len(events) == 1 and events[0].context.user_id == user.id
            assert hass.states.get(forecast.entity_id).attributes["temperature"] == 17.7
        finally:
            unsub()

    with case(report, "weather_hourly_preferred_and_current_rain"):
        forecast._attr_supported_features = weather.WeatherEntityFeature(3)
        forecast._attr_condition = "rainy"
        forecast.async_write_ha_state()
        await hass.async_block_till_done()
        try:
            result = await command("jaka dziś pogoda", dry_run=False)
            assert result["status"] == "answered", result
            assert result["response_data"]["forecast_type"] == "hourly", result
            assert "Pada deszcz" in result["speech"], result
            assert "kiedy przestanie padać" in result["speech"], result
        finally:
            forecast._attr_supported_features = weather.WeatherEntityFeature(1)
            forecast._attr_condition = "sunny"
            forecast.async_write_ha_state()
            await hass.async_block_till_done()

    with case(report, "weather_now_skips_forecast_and_daily_forecast_speaks_range"):
        events = []
        off = hass.bus.async_listen("call_service", lambda event: events.append(event))
        original_daily = forecast.async_forecast_daily

        async def daily_with_range():
            return [
                {**row, "native_templow": 10.2, "native_temperature": 19.7}
                for row in await original_daily()
            ]

        try:
            result = await command("jaka jest teraz pogoda", dry_run=False)
            assert result["status"] == "answered" and not result["calls"], result
            assert result["response_data"]["forecast_status"] == "not_requested", result
            assert not [event for event in events if event.data.get("domain") == "weather"]
            forecast.async_forecast_daily = daily_with_range
            result = await command("jaka będzie dziś pogoda", dry_run=False)
            assert "od około 10 do 20 stopni" in result["speech"], result
            assert result["response_data"]["temperature_low"] == 10.2
        finally:
            forecast.async_forecast_daily = original_daily
            off()

    with case(report, "weather_optional_forecast_error_timeout_and_malformed"):
        from custom_components.polish_context_voice.ha import capabilities as caps

        original = forecast.async_forecast_daily

        async def failure():
            raise HomeAssistantError("Synthetic provider offline")

        async def slow():
            await asyncio.sleep(1)
            return await original()

        async def missing():
            return None

        try:
            for handler in (failure, slow, missing):
                forecast.async_forecast_daily = handler
                with patch.object(caps, "WEATHER_FORECAST_TIMEOUT", 0.01):
                    result = await command("jaka jest pogoda", dry_run=False)
                assert result["status"] == "answered", result
                assert "około 18" in result["speech"], result
                assert result["response_data"]["forecast_status"] == "unavailable", result
            forecast.async_forecast_daily = failure
            result = await command("jaka jutro pogoda", dry_run=False)
            assert result["status"] == "error", result
            assert result["reason"] == "native_capability_failed", result
            assert result["speech"] == "Nie mam teraz dostępu do prognozy pogody.", result
        finally:
            forecast.async_forecast_daily = original

    with case(report, "weather_revalidates_exposure_after_forecast_await"):
        original = forecast.async_forecast_daily

        async def revoke():
            exposed_entities.async_expose_entity(hass, "conversation", forecast.entity_id, False)
            return await original()

        try:
            forecast.async_forecast_daily = revoke
            result = await command("jaka jest pogoda", dry_run=False)
            assert result["status"] != "answered" and "18" not in result["speech"], result
        finally:
            forecast.async_forecast_daily = original
            exposed_entities.async_expose_entity(hass, "conversation", forecast.entity_id, True)
            await hass.async_block_till_done()

    with case(report, "weather_read_only_current_state_survives_native_service_denial"):
        reader = await hass.auth.async_create_user("Weather reader", group_ids=[GROUP_ID_READ_ONLY])
        result = await runtime.process(
            Request("jaka jest pogoda", user_id=reader.id), Context(user_id=reader.id)
        )
        assert result.status == "answered" and "około 18" in result.speech, result
        assert result.response_data["forecast_status"] == "unavailable", result
        result = await runtime.process(
            Request("Jaka będzie jutro pogoda?", user_id=reader.id), Context(user_id=reader.id)
        )
        assert result.status == "blocked" and result.reason == "ha_permission_denied", result
        assert result.speech == "Nie mam teraz dostępu do prognozy pogody.", result

    with case(report, "weather_state_only_provider_needs_no_forecast_service"):
        forecast._attr_supported_features = weather.WeatherEntityFeature(0)
        forecast.async_write_ha_state()
        await hass.async_block_till_done()
        events = []
        unsub = hass.bus.async_listen("call_service", lambda e: events.append(e))
        try:
            result = await command("jaka jest pogoda", dry_run=False)
            assert result["status"] == "answered" and not events, result
            assert result["response_data"]["forecast_status"] == "unsupported", result
            assert "około 18" in result["speech"], result
        finally:
            unsub()
            forecast._attr_supported_features = weather.WeatherEntityFeature(1)
            forecast.async_write_ha_state()
            await hass.async_block_till_done()

    with case(report, "weather_natural_current_and_rain_end_native_hourly"):
        original = forecast.async_forecast_hourly
        forecast._attr_supported_features = weather.WeatherEntityFeature(3)
        forecast._attr_condition = "rainy"
        forecast.async_write_ha_state()
        await hass.async_block_till_done()
        base = dt_util.now().replace(minute=0, second=0, microsecond=0)
        points = [
            {
                "datetime": (base + timedelta(hours=h)).isoformat(),
                "condition": "rainy" if h < 3 else "cloudy",
            }
            for h in (1, 2, 3, 4)
        ]

        async def hourly():
            return points

        try:
            forecast.async_forecast_hourly = hourly
            result = await command("Kiedy przestanie padać?", dry_run=False)
            assert result["status"] == "answered", result
            assert "Według prognozy powinno przestać padać" in result["speech"], result
            assert "Prognoza na" not in result["speech"] and "Celsjusza" not in result["speech"]
            answer = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="Jaka jest pogoda?",
                satellite_id=fixture["satellite"],
            )
            speech = answer["response"]["speech"]["plain"]["speech"]
            assert "Pada deszcz z silnymi podmuchami wiatru i jest około 18 stopni." in speech
            # The general overview stops at local midnight. When this lab runs
            # late in the evening, the second dry hour belongs to tomorrow and
            # must not be used to promise an end to today's rain. The explicit
            # rain-end question above intentionally retains its longer horizon.
            today = dt_util.now().date()
            local_points = [
                p for p in points if dt_util.parse_datetime(p["datetime"]).date() == today
            ]
            if all(dt_util.parse_datetime(p["datetime"]).date() == today for p in points[-2:]):
                assert "powinno przestać padać" in speech, answer
            elif local_points:
                assert "Nie mam wystarczających danych" in speech, answer
            else:
                assert "Według prognozy" not in speech, answer
            assert "godzinę" not in speech
            events = []
            unsub = hass.bus.async_listen("call_service", lambda e: events.append(e))
            try:
                preview = await command("Kiedy przestanie padać?")
                assert preview["status"] == "dry_run" and not events
                assert preview["calls"][0]["data"]["type"] == "hourly"
            finally:
                unsub()
        finally:
            forecast.async_forecast_hourly = original
            forecast._attr_supported_features = weather.WeatherEntityFeature(1)
            forecast._attr_condition = "sunny"
            forecast.async_write_ha_state()
            await hass.async_block_till_done()

    with case(report, "weather_rain_end_when_currently_dry_is_short_without_forecast_calls"):
        events = []
        unsub = hass.bus.async_listen("call_service", lambda event: events.append(event))
        try:
            for state in ("sunny", "partlycloudy", "cloudy"):
                forecast._attr_condition = state
                forecast.async_write_ha_state()
                await hass.async_block_till_done()
                result = await command("Kiedy przestanie padać?", dry_run=False)
                assert result["status"] == "answered", result
                assert result["speech"] == "Przecież teraz nie pada.", result
                assert not result["calls"], result
            assert not [e for e in events if e.data.get("domain") == "weather"]
        finally:
            unsub()
            forecast._attr_condition = "sunny"
            forecast.async_write_ha_state()
            await hass.async_block_till_done()

    with case(report, "weather_user_tomorrow_transcript_natural_speech_precise_data"):
        original = forecast.async_forecast_daily

        async def partlycloudy():
            return [
                {
                    "datetime": (dt_util.now() + timedelta(days=1)).isoformat(),
                    "condition": "partlycloudy",
                    "native_temperature": 17.7,
                }
            ]

        try:
            forecast.async_forecast_daily = partlycloudy
            answer = await client.call(
                "conversation/process",
                agent_id=agent,
                language="pl",
                text="Jaka będzie jutro pogoda?",
                satellite_id=fixture["satellite"],
            )
            assert answer["response"]["response_type"] == "query_answer", answer
            assert answer["response"]["speech"]["plain"]["speech"] == (
                "Jutro przewiduje się częściowe zachmurzenie i około 18 stopni."
            ), answer
            result = await command("Jaka będzie jutro pogoda?", dry_run=False)
            assert result["response_data"]["temperature"] == 17.7, result
        finally:
            forecast.async_forecast_daily = original

    with case(report, "weather_rain_end_daily_and_empty_hourly_do_not_invent_time"):
        forecast._attr_condition = "rainy"
        forecast.async_write_ha_state()
        await hass.async_block_till_done()
        original = forecast.async_forecast_hourly

        async def empty_hourly():
            return []

        try:
            result = await command("O której przestanie padać?", dry_run=False)
            assert "tylko prognozę dzienną" in result["speech"], result
            forecast._attr_supported_features = weather.WeatherEntityFeature(3)
            forecast.async_forecast_hourly = empty_hourly
            forecast.async_write_ha_state()
            await hass.async_block_till_done()
            result = await command("O której przestanie padać?", dry_run=False)
            assert "Prognoza opadów jest teraz niedostępna" in result["speech"], result
            assert "powinno przestać padać" not in result["speech"], result
        finally:
            forecast.async_forecast_hourly = original
            forecast._attr_condition = "sunny"
            forecast._attr_supported_features = weather.WeatherEntityFeature(1)
            forecast.async_write_ha_state()
            await hass.async_block_till_done()

    with case(report, "capabilities_routine_opt_in_native_script"):
        events = []
        unsub = hass.bus.async_listen("pcv_lab_routine", lambda e: events.append(e))
        try:
            await execute("uruchom rutynę dobranoc")
            assert len(events) == 1 and events[0].context.user_id == user.id
        finally:
            unsub()

    with case(report, "capabilities_satellite_announce_and_question_native_service"):
        result = await execute("ogłoś w kuchni że Obiad gotowy", satellite_id=fixture["satellite"])
        assert result["completed"] == [satellite.entity_id]
        assert satellite.messages == ["Obiad gotowy"]
        result = await execute(
            "zapytaj w kuchni czy obiad gotowy", satellite_id=fixture["satellite"]
        )
        assert result["response_data"]["id"] == "yes" and "tak" in result["speech"], result
        assert len(satellite.messages) == 2
        blocked = await command(
            "zapytaj w kuchni czy obiad gotowy", satellite_id=satellite.entity_id, dry_run=False
        )
        assert blocked["reason"] == "same_satellite", blocked

    with case(report, "capabilities_read_only_states_and_native_service_permissions"):
        reader = await hass.auth.async_create_user(
            "Capability reader", group_ids=[GROUP_ID_READ_ONLY]
        )
        context = Context(user_id=reader.id)
        request = Request(
            "wstrzymaj muzykę",
            source_area="living_room",
            user_id=reader.id,
            conversation_id="cap-reader",
        )
        first = await runtime.process(request, context)
        assert first.status == "confirm"
        before = len(player.operations)
        result = await runtime.process(replace(request, text="potwierdzam"), context)
        assert result.status == "error" and len(player.operations) == before
        result = await runtime.process(replace(request, text="co mam jutro w kalendarzu"), context)
        # CalendarEntity reads use POLICY_READ, just like HA's calendar REST view.
        assert result.status == "answered" and "Wizyta testowa" in result.speech, result
        result = await runtime.process(replace(request, text="czy okna są otwarte"), context)
        assert result.status == "answered", result

    with case(report, "capabilities_conversation_output_and_compound_rejection"):
        result = await client.call(
            "conversation/process", text="która godzina", agent_id=agent, language="pl"
        )
        assert result["response"]["response_type"] == "query_answer", result
        before = len(player.operations)
        result = await command(
            "wstrzymaj muzykę i włącz światła", source_area="living_room", dry_run=False
        )
        assert result["status"] == "unsupported" and len(player.operations) == before
    with case(report, "capabilities_packaged_preview_yaml_no_side_effects"):
        before = (len(player.operations), len(shopping.items), len(satellite.messages))
        specs = Path(__file__).resolve().parents[1] / "deployment/testy"
        paths = sorted(specs.glob("2[0-3]-*.yaml"))
        assert len(paths) == 4
        for path in paths:
            spec = yaml.safe_load(path.read_text())
            result = await client.call(
                "call_service",
                domain=domain,
                service="preview",
                service_data=spec["data"],
                return_response=True,
            )
            assert result["response"]["status"] == "dry_run", result
        assert before == (len(player.operations), len(shopping.items), len(satellite.messages))

    with case(report, "capabilities_latency_100_correct_previews"):
        samples = []
        cases = [
            ("która godzina", [], None),
            ("wstrzymaj muzykę", [player.entity_id], "media_pause"),
            ("dodaj mleko do zakupów", [shopping.entity_id], "add_item"),
            ("co mam jutro w kalendarzu", [diary.entity_id], None),
            ("jaka będzie pogoda jutro", [forecast.entity_id], "get_forecasts"),
        ]
        for _ in range(20):
            for text, targets, service in cases:
                started = perf_counter()
                result = await command(text, source_area="living_room")
                samples.append((perf_counter() - started) * 1000)
                assert result["status"] == "dry_run" and result["targets"] == targets, result
                if service:
                    assert result["calls"][0]["service"] == service, result
                elif "kalendarzu" in text:
                    assert result["response_data"]["operation"] == "find", result
                    assert result["response_data"]["needs_lookup"] is True and not result["calls"]
        samples.sort()
        report["capability_latency_ms"] = {
            "samples": 100,
            "correct": 100,
            "p50": round(samples[49], 3),
            "p95": round(samples[94], 3),
            "p99": round(samples[98], 3),
            "scope": "Local WebSocket previews, warm cache, five capability types; "
            "no audio/provider network",
        }

    report["capability_providers"] = (
        "Native HA entity services with virtual media/todo/weather/calendar/script; "
        "satellite synthesis and captured answer simulated"
    )
