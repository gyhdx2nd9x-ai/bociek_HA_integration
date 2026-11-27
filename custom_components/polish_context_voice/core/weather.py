"""Compose short weather answers from facts, with cautious hourly trend inference."""

from dataclasses import dataclass
from datetime import datetime

from .speech import around_time, number, rounded, temperature_speech

CONDITIONS = {
    "clear-night": "jest bezchmurnie",
    "cloudy": "jest pochmurno",
    "fog": "jest mgliście",
    "hail": "pada grad",
    "lightning": "jest burza",
    "lightning-rainy": "jest burza z deszczem",
    "partlycloudy": "jest częściowe zachmurzenie",
    "pouring": "pada ulewny deszcz",
    "rainy": "pada deszcz",
    "snowy": "pada śnieg",
    "snowy-rainy": "pada deszcz ze śniegiem",
    "sunny": "jest słonecznie",
    "windy": "jest wietrznie",
    "windy-variant": "jest pochmurno i wietrznie",
    "exceptional": "są nietypowe warunki",
}
FORECAST_CONDITIONS = {
    "clear-night": "bezchmurne niebo",
    "cloudy": "zachmurzenie",
    "fog": "mgłę",
    "hail": "grad",
    "lightning": "burzę",
    "lightning-rainy": "burzę z deszczem",
    "partlycloudy": "częściowe zachmurzenie",
    "pouring": "ulewny deszcz",
    "rainy": "deszcz",
    "snowy": "śnieg",
    "snowy-rainy": "deszcz ze śniegiem",
    "sunny": "słoneczną pogodę",
    "windy": "wietrzną pogodę",
    "windy-variant": "zachmurzenie i wiatr",
    "exceptional": "nietypowe warunki",
}
PRECIPITATION = {
    "rainy": "deszcz",
    "pouring": "ulewny deszcz",
    "lightning-rainy": "burzę z deszczem",
    "snowy": "śnieg",
    "snowy-rainy": "deszcz ze śniegiem",
    "hail": "grad",
}
DRY_CONDITIONS = {"clear-night", "cloudy", "fog", "partlycloudy", "sunny", "windy", "windy-variant"}
# Conversational thresholds only, not meteorological warnings.
WIND_KMH = 20
GUST_KMH = 40
PRECIPITATION_PROBABILITY = 30
SPEED_FACTORS = {"km/h": 1, "m/s": 3.6, "mi/h": 1.609344, "ft/s": 1.09728, "kn": 1.852}
UNKNOWN_END = "Nie mam wystarczających danych, żeby określić, kiedy przestanie padać."


def unavailable_weather_speech(operation, kind=None):
    """Describe the missing answer without guessing why the provider is unavailable."""
    if kind == "rain_end":
        return "Nie mogę teraz sprawdzić, kiedy przestanie padać. Prognoza jest niedostępna."
    if operation == "forecast":
        return "Nie mam teraz dostępu do prognozy pogody."
    return "Nie mogę teraz sprawdzić pogody. Dane pogodowe są niedostępne."


def wind_description(attributes, condition=None):
    unit = attributes.get("wind_speed_unit")
    factor = SPEED_FACTORS.get(unit)
    if factor is None and unit != "Beaufort":
        return "wietrznie" if condition in {"windy", "windy-variant"} else ""
    speed, gust = attributes.get("wind_speed"), attributes.get("wind_gust_speed")
    speed = speed if number(speed) and speed >= 0 else None
    gust = gust if number(gust) and gust >= 0 else None
    if gust is not None and (gust * factor >= GUST_KMH if factor else gust >= 6):
        return "silne podmuchy wiatru"
    if (
        speed is not None
        and (speed * factor >= WIND_KMH if factor else speed >= 4)
        or condition in {"windy", "windy-variant"}
    ):
        return "wietrznie"
    return ""


def current_weather(state, attributes):
    condition = CONDITIONS.get(state)
    temperature = temperature_speech(
        attributes.get("temperature"), attributes.get("temperature_unit", "")
    )
    wind = wind_description(attributes, state)
    if condition is None:
        return (f"Jest {temperature}. " if temperature else "") + "Nie mam opisu obecnej pogody."
    if wind == "silne podmuchy wiatru":
        condition += (
            " z silnymi podmuchami wiatru"
            if state in PRECIPITATION
            else (", wieje porywisty wiatr")
        )
    elif wind and state not in {"windy", "windy-variant"}:
        condition += ", jest wietrznie"
    if temperature:
        condition += f" i {'mamy' if 'jest ' in condition else 'jest'} {temperature}"
    return condition[0].upper() + condition[1:] + "."


def probability(forecast):
    value = forecast.get("precipitation_probability")
    return value if number(value) and 0 <= value <= 100 else None


def wet_status(item):
    """True: precipitation signal, False: explicit dry conditions, None: insufficient data."""
    condition = item.get("condition")
    chance, amount = probability(item), item.get("precipitation")
    if (
        condition in PRECIPITATION
        or (chance is not None and chance >= PRECIPITATION_PROBABILITY)
        or (number(amount) and amount > 0)
    ):
        return True
    if (
        condition in DRY_CONDITIONS
        and ("precipitation_probability" not in item or chance is not None)
        and ("precipitation" not in item or (number(amount) and amount >= 0))
    ):
        return False
    return None


def forecast_time(forecast):
    try:
        value = datetime.fromisoformat(forecast.get("datetime", ""))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo is not None and value.utcoffset() is not None else None


@dataclass(frozen=True)
class ForecastPoint:
    at: datetime
    item: dict
    wet: bool | None


def timeline(forecasts, now, hours=24):
    """Keep gaps and uncertain/duplicate points visible; order by absolute time across DST."""
    points = {}
    if not isinstance(forecasts, list):
        return []
    for item in forecasts[:500]:
        if not isinstance(item, dict) or (at := forecast_time(item)) is None:
            continue
        stamp = at.timestamp()
        if not now.timestamp() <= stamp <= now.timestamp() + hours * 3600:
            continue
        point = ForecastPoint(at.astimezone(now.tzinfo), item, wet_status(item))
        if stamp in points and points[stamp].item != item:
            point = ForecastPoint(point.at, {}, None)
        points[stamp] = point
    return [points[stamp] for stamp in sorted(points)]


def dry_transition(points, start):
    """Require continuous known hourly data and two dry points before suggesting a break."""
    previous = start.timestamp()
    for index, point in enumerate(points):
        gap = point.at.timestamp() - previous
        if gap > 90 * 60 or point.wet is None:
            return None
        if point.wet is False:
            following = points[index + 1] if index + 1 < len(points) else None
            if (
                following is not None
                and following.wet is False
                and 30 * 60 <= following.at.timestamp() - point.at.timestamp() <= 90 * 60
            ):
                return index
            return None
        previous = point.at.timestamp()
    return None


def precipitation_comment(forecasts, kind, now, current_state=None, *, focus_end=False):
    raining = current_state in PRECIPITATION
    if kind not in {"hourly", "daily"}:
        return UNKNOWN_END if focus_end else ""
    if kind != "hourly":
        if focus_end or raining:
            return "Mam tylko prognozę dzienną, więc nie określę godziny końca opadów."
        if not isinstance(forecasts, list):
            return ""
        for item in forecasts[:500]:
            if not isinstance(item, dict) or (at := forecast_time(item)) is None:
                continue
            if at.astimezone(now.tzinfo).date() == now.date() and wet_status(item) is True:
                weather = PRECIPITATION.get(item.get("condition"), "opady")
                return f"W dzisiejszej prognozie przewiduje się {weather}."
        return ""
    points = timeline(forecasts, now)
    if raining:
        stop = dry_transition(points, now)
        if stop is None:
            return UNKNOWN_END
        at = around_time(points[stop].at, now)
        again = next((p for p in points[stop + 2 :] if p.wet is True), None)
        if again:
            return (
                f"Według prognozy {at} powinna być przerwa w opadach, "
                f"ale {around_time(again.at, now)} mogą wrócić."
            )
        return f"Według prognozy powinno przestać padać {at}."
    first = next(
        (
            i
            for i, p in enumerate(points)
            if p.wet is True and p.at.timestamp() <= now.timestamp() + 6 * 3600
        ),
        None,
    )
    if first is None:
        return UNKNOWN_END if focus_end else ""
    point = points[first]
    weather = PRECIPITATION.get(point.item.get("condition"), "opady")
    when = around_time(point.at, now)
    speech = f"Według prognozy {when} możliwe są opady."
    if point.item.get("condition") in PRECIPITATION:
        speech = f"Według prognozy {when} przewiduje się {weather}."
    stop = dry_transition(points[first + 1 :], point.at)
    if stop is not None:
        end = points[first + 1 + stop]
        again = next((p for p in points[first + stop + 3 :] if p.wet is True), None)
        if again:
            speech += (
                f" Możliwa jest przerwa {around_time(end.at, now)}, "
                f"ale {around_time(again.at, now)} opady mogą wrócić."
            )
        else:
            speech += f" Opady powinny ustąpić {around_time(end.at, now)}."
    elif focus_end:
        speech += " " + UNKNOWN_END
    return speech


def temperature_range(low, high, unit):
    """Speak a range only when the provider actually supplies ordered extrema."""
    if not (number(low) and number(high)) or low > high:
        return temperature_speech(high, unit)
    if rounded(low) == rounded(high):
        return temperature_speech(high, unit)
    scale = " stopni" if unit in {"°C", "°F"} else f" {unit}".rstrip()
    if unit == "°F":
        scale += " Fahrenheita"
    return f"od około {rounded(low)} do {rounded(high)}{scale}"


def rest_of_day(forecasts, now, current_state):
    """One useful change, using only future hours of the same local day."""
    points = [p for p in timeline(forecasts, now) if p.at.date() == now.date()]
    if not points:
        return ""
    rain = precipitation_comment([p.item for p in points], "hourly", now, current_state)
    if rain:
        return rain
    for point in points:
        condition = point.item.get("condition")
        if condition in {"sunny", "clear-night"} and current_state in {"cloudy", "partlycloudy"}:
            return f"Według prognozy {around_time(point.at, now)} powinno się rozpogodzić."
        if condition in {"lightning", "lightning-rainy"} and current_state != condition:
            return f"Według prognozy {around_time(point.at, now)} możliwa jest burza."
    return ""


def daily_forecast(forecast, attributes, date, *, today=None):
    when = (
        "Jutro"
        if today is not None and (date - today).days == 1
        else ("Dzisiaj" if today == date else f"Na {date:%d.%m}")
    )
    condition = FORECAST_CONDITIONS.get(forecast.get("condition"))
    temperature = temperature_range(
        forecast.get("templow"),
        forecast.get("temperature"),
        attributes.get("temperature_unit", ""),
    )
    facts = [part for part in (condition, temperature) if part]
    speech = (
        f"{when} przewiduje się {' i '.join(facts)}."
        if facts
        else ("W prognozie brakuje opisu warunków i temperatury.")
    )
    wind = wind_description(
        {**forecast, "wind_speed_unit": attributes.get("wind_speed_unit")},
        forecast.get("condition"),
    )
    if wind == "silne podmuchy wiatru":
        speech += " Możliwe są też silne podmuchy wiatru."
    elif wind and forecast.get("condition") not in {"windy", "windy-variant"}:
        speech += " Będzie też wietrznie."
    if forecast.get("condition") not in PRECIPITATION and wet_status(forecast) is True:
        speech += " Możliwe są też opady."
    return speech
