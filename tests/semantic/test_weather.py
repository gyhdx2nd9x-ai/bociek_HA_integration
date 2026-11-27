from datetime import datetime, timedelta

import pytest

from custom_components.polish_context_voice.core.weather import (
    UNKNOWN_END,
    current_weather,
    daily_forecast,
    precipitation_comment,
    rest_of_day,
    temperature_range,
    temperature_speech,
    wind_description,
)

NOW = datetime.fromisoformat("2026-10-13T10:00:00+01:00")


@pytest.mark.parametrize(
    "low,high,expected",
    [
        (12.2, 18.7, "od około 12 do 19 stopni"),
        (None, 18.7, "około 19 stopni"),
        (True, 18.7, "około 19 stopni"),
        (21, 18.7, "około 19 stopni"),
        (18.5, 18.8, "około 19 stopni"),
        (-7.2, -1.5, "od około -7 do -2 stopni"),
    ],
)
def test_forecast_range_only_from_real_ordered_extrema(low, high, expected):
    assert temperature_range(low, high, "°C") == expected


def test_forecast_range_keeps_fahrenheit_units():
    assert temperature_range(50, 70, "°F") == "od około 50 do 70 stopni Fahrenheita"


def test_overview_does_not_use_past_hours_or_tomorrow_for_rest_of_today():
    points = [item(-4, condition="sunny"), item(1, condition="cloudy"), item(15, condition="sunny")]
    assert rest_of_day(points, NOW, "cloudy") == ""
    assert "rozpogodzić" in rest_of_day([item(2, condition="sunny")], NOW, "cloudy")


def test_daily_range_preserves_provider_data():
    source = {"condition": "cloudy", "temperature": 19.7, "templow": 10.3}
    speech = daily_forecast(source, {"temperature_unit": "°C"}, NOW.date(), today=NOW.date())
    assert "od około 10 do 20 stopni" in speech and source["templow"] == 10.3


def item(hours=1, **data):
    return {"datetime": (NOW + timedelta(hours=hours)).isoformat(), **data}


@pytest.mark.parametrize(
    "value,expected",
    [(17.7, 18), (17.2, 17), (17.5, 18), (18.5, 19), (-1.5, -2), (-0.4, 0), (-17.7, -18), (0, 0)],
)
def test_temperature_rounding_including_negative_halves(value, expected):
    speech = temperature_speech(value, "°C")
    assert speech == f"około {expected} stopni"


@pytest.mark.parametrize("value", [None, True, "17.7", float("inf"), float("nan")])
def test_invalid_temperature_is_not_spoken(value):
    assert not temperature_speech(value, "°C")


def test_temperature_units_and_singular():
    assert temperature_speech(0.9, "°C") == "około 1 stopnia"
    assert temperature_speech(63.8, "°F") == "około 64 stopni Fahrenheita"
    assert temperature_speech(289.7, "K") == "około 290 K"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("sunny", "Jest słonecznie"),
        ("cloudy", "Jest pochmurno"),
        ("rainy", "Pada deszcz"),
        ("pouring", "Pada ulewny deszcz"),
        ("snowy", "Pada śnieg"),
    ],
)
def test_current_condition_and_precise_source_unchanged(state, expected):
    attrs = {"temperature": 17.7, "temperature_unit": "°C"}
    assert (
        current_weather(state, attrs)
        == f"{expected} i {'mamy' if expected.startswith('Jest') else 'jest'} około 18 stopni."
    )
    assert attrs["temperature"] == 17.7


@pytest.mark.parametrize(
    "unit,speed",
    [("km/h", 20), ("m/s", 6), ("mi/h", 13), ("ft/s", 19), ("kn", 11), ("Beaufort", 4)],
)
def test_wind_thresholds_use_entity_units(unit, speed):
    assert wind_description({"wind_speed_unit": unit, "wind_speed": speed}) == "wietrznie"


@pytest.mark.parametrize(
    "attrs",
    [
        {},
        {"wind_speed": 99},
        {"wind_speed": 99, "wind_speed_unit": "invalid"},
        {"wind_speed": 3, "wind_speed_unit": "km/h"},
        {"wind_speed": -99, "wind_speed_unit": "km/h"},
        {"wind_speed": float("inf"), "wind_speed_unit": "km/h"},
    ],
)
def test_no_guessed_wind_or_noise_for_light_breeze(attrs):
    assert wind_description(attrs) == ""


def test_gusts_and_windy_condition():
    assert "silne podmuchy" in wind_description({"wind_speed_unit": "km/h", "wind_gust_speed": 50})
    assert "silne podmuchy" in wind_description(
        {"wind_speed_unit": "km/h", "wind_speed": 22, "wind_gust_speed": 50}
    )
    assert wind_description({"wind_speed_unit": "m/s", "wind_speed": 3}, condition="windy")


def test_hourly_window_sorted_and_local_time():
    data = [
        item(-1, condition="rainy"),
        item(8, condition="rainy"),
        item(4, condition="rainy"),
        item(2, condition="rainy", precipitation_probability=80),
    ]
    assert precipitation_comment(data, "hourly", NOW) == (
        "Według prognozy około 12 przewiduje się deszcz."
    )


def test_hourly_timezone_and_midnight():
    now = datetime.fromisoformat("2026-10-13T23:30:00+02:00")
    data = [{"datetime": "2026-10-13T23:00:00Z", "condition": "snowy"}]
    assert "jutro około 1" in precipitation_comment(data, "hourly", now)
    assert "śnieg" in precipitation_comment(data, "hourly", now)


@pytest.mark.parametrize(
    "data",
    [
        [],
        None,
        [None],
        [{}],
        [{"datetime": "bad", "condition": "rainy"}],
        [{"datetime": "2026-10-13T12:00:00", "condition": "rainy"}],
        [item(-1, condition="rainy")],
        [item(7, condition="rainy")],
        [item(condition="sunny")],
        [item(precipitation_probability=101)],
        [item(precipitation_probability=20)],
        [item(precipitation_probability=True)],
    ],
)
def test_absent_invalid_past_or_low_risk_forecast_never_implies_dry_weather(data):
    assert precipitation_comment(data, "hourly", NOW) == ""


def test_daily_does_not_claim_a_start_time_or_use_tomorrow():
    assert not precipitation_comment([item(24, condition="rainy")], "daily", NOW)
    speech = precipitation_comment([item(-3, condition="rainy")], "daily", NOW)
    assert speech == "W dzisiejszej prognozie przewiduje się deszcz."


def test_probability_or_amount_without_condition_does_not_invent_rain_type():
    for data in ({"precipitation_probability": 40}, {"precipitation": 1.5}):
        assert "możliwe są opady" in precipitation_comment([item(**data)], "hourly", NOW)


def test_daily_forecast_temperature_rain_probability_and_wind():
    source = item(
        condition="rainy",
        temperature=17.7,
        precipitation_probability=80,
        wind_speed=25,
        wind_gust_speed=45,
    )
    speech = daily_forecast(
        source, {"temperature_unit": "°C", "wind_speed_unit": "km/h"}, NOW.date()
    )
    assert "około 18 stopni" in speech
    assert "deszcz" in speech and "silne podmuchy wiatru" in speech
    assert source["temperature"] == 17.7


def test_user_rain_and_26_kmh_does_not_invent_gusts():
    speech = current_weather(
        "rainy",
        {
            "temperature": 18.7,
            "temperature_unit": "°C",
            "wind_speed": 26,
            "wind_speed_unit": "km/h",
        },
    )
    assert speech == "Pada deszcz, jest wietrznie i mamy około 19 stopni."
    assert "podmuch" not in speech and "kilometr" not in speech


def test_gusts_only_when_measured_and_celsius_omitted():
    speech = current_weather(
        "rainy",
        {
            "temperature": 18.7,
            "temperature_unit": "°C",
            "wind_gust_speed": 45,
            "wind_speed_unit": "km/h",
        },
    )
    assert speech == "Pada deszcz z silnymi podmuchami wiatru i jest około 19 stopni."


def test_exact_user_tomorrow_answer():
    data = {"condition": "partlycloudy", "temperature": 17.7}
    attrs = {"temperature_unit": "°C"}
    assert daily_forecast(data, attrs, NOW.date() + timedelta(days=1), today=NOW.date()) == (
        "Jutro przewiduje się częściowe zachmurzenie i około 18 stopni."
    )
    assert daily_forecast(data, attrs, NOW.date(), today=NOW.date()).startswith("Dzisiaj")


def test_fahrenheit_is_never_silently_presented_as_celsius():
    assert "Fahrenheita" in current_weather("sunny", {"temperature": 60, "temperature_unit": "°F"})


def test_current_rain_replaced_by_end_time_not_repeated_start():
    data = [
        item(1, condition="rainy"),
        item(2, condition="rainy"),
        item(3, condition="cloudy"),
        item(4, condition="sunny"),
    ]
    assert precipitation_comment(data, "hourly", NOW, "rainy") == (
        "Według prognozy powinno przestać padać około 13."
    )


def test_first_hour_dry_also_requires_next_dry_period():
    data = [item(1, condition="cloudy"), item(2, condition="cloudy")]
    assert "około 11" in precipitation_comment(data, "hourly", NOW, "rainy")
    assert precipitation_comment(data[:1], "hourly", NOW, "rainy") == UNKNOWN_END


@pytest.mark.parametrize(
    "data",
    [
        [],
        [item(1)],
        [item(1, condition="rainy")],
        [item(1, condition="rainy"), item(2, condition="cloudy")],
        [item(2, condition="cloudy"), item(3, condition="cloudy")],
        [item(1, condition="rainy"), item(3, condition="cloudy"), item(4, condition="cloudy")],
        [item(1), item(2, condition="cloudy"), item(3, condition="cloudy")],
        [
            item(1, condition="rainy"),
            item(2, condition="cloudy", precipitation_probability=70),
            item(3, condition="cloudy"),
        ],
        [
            item(1, condition="rainy"),
            item(2, condition="sunny", precipitation=1),
            item(3, condition="sunny"),
        ],
        [
            item(1, condition="rainy"),
            item(2, condition="sunny", precipitation_probability=None),
            item(3, condition="sunny"),
        ],
        [item(1, condition="sunny"), item(1, condition="rainy"), item(2, condition="sunny")],
    ],
)
def test_missing_conflicting_sparse_or_uncertain_data_never_fabricates_end(data):
    assert precipitation_comment(data, "hourly", NOW, "rainy") == UNKNOWN_END


def test_end_after_six_hours_but_within_day_and_continuous_data():
    data = [item(h, condition="rainy") for h in range(1, 8)]
    data += [item(8, condition="cloudy"), item(9, condition="cloudy")]
    assert "około 18" in precipitation_comment(data, "hourly", NOW, "rainy")


def test_future_start_and_end_in_one_summary():
    data = [
        item(1, condition="cloudy"),
        item(2, condition="rainy"),
        item(3, condition="cloudy"),
        item(4, condition="cloudy"),
    ]
    assert precipitation_comment(data, "hourly", NOW, "cloudy") == (
        "Według prognozy około 12 przewiduje się deszcz. Opady powinny ustąpić około 13."
    )


def test_rain_return_is_a_break_not_permanent_end():
    data = [
        item(1, condition="rainy"),
        item(2, condition="cloudy"),
        item(3, condition="cloudy"),
        item(4, condition="rainy"),
    ]
    speech = precipitation_comment(data, "hourly", NOW, "rainy")
    assert "przerwa w opadach" in speech and "około 14 mogą wrócić" in speech


def test_daily_cannot_give_rain_end_even_with_daily_dry_entry():
    speech = precipitation_comment([item(1, condition="sunny")], "daily", NOW, "rainy")
    assert "tylko prognozę dzienną" in speech and "godziny końca" in speech


def test_rain_end_question_without_data_acknowledges_unknown():
    assert precipitation_comment([], "hourly", NOW, "sunny", focus_end=True) == UNKNOWN_END


def test_midnight_end_uses_tomorrow():
    now = datetime.fromisoformat("2026-10-13T23:30:00+01:00")
    data = [
        {"datetime": "2026-10-14T00:00:00+01:00", "condition": "cloudy"},
        {"datetime": "2026-10-14T01:00:00+01:00", "condition": "cloudy"},
    ]
    assert "jutro około północy" in precipitation_comment(data, "hourly", now, "rainy")


def test_dst_fall_back_hour_keeps_real_chronology():
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 25, 0, 30, tzinfo=ZoneInfo("Europe/London"))
    data = [
        {"datetime": "2026-10-25T00:00:00Z", "condition": "rainy"},
        {"datetime": "2026-10-25T01:00:00Z", "condition": "cloudy"},
        {"datetime": "2026-10-25T02:00:00Z", "condition": "cloudy"},
    ]
    assert "przestać padać około 1" in precipitation_comment(data, "hourly", now, "rainy")
