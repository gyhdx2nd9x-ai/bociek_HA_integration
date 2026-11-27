"""Small, whole-sentence grammar for native HA capabilities; no model required."""

import re
from dataclasses import dataclass
from typing import Literal

from . import split_location
from .normalize import normalize


@dataclass(frozen=True)
class Capability:
    operation: str
    domain: str = ""
    name: str | None = None
    location: str | None = None
    value: int | None = None
    payload: str = ""
    day: int = 0
    kind: str = ""
    query: str = ""
    scope: Literal["", "house"] = ""


def parse_capability(text: str) -> Capability | None:
    if not 0 < len(text) <= 600:
        return None
    original = text.strip().rstrip(".!?")
    # Payload is data, never reparsed as a command. Keep accents and case.
    match = re.fullmatch(r"dodaj (.{1,120}) do listy (.{1,80})", original, re.I)
    if not match:
        match = re.fullmatch(r"dodaj (.{1,120}) do (zakupów|zakupow)", original, re.I)
    if match:
        name = normalize(match[2])
        return Capability(
            "todo_add",
            "todo",
            name=None if name == "zakupow" else name,
            payload=match[1],
            kind="shopping" if name == "zakupow" else "",
        )
    match = re.fullmatch(
        r"(ogłoś|oglos|zapytaj) (?:w|we|na) ([^:]{1,80}):\s*(.{1,250})", original, re.I
    )
    if not match:
        match = re.fullmatch(
            r"(ogłoś|oglos) (?:w|we|na) (.{1,80}?) (?:że|ze) (.{1,250})", original, re.I
        )
    if not match:
        match = re.fullmatch(r"(zapytaj) (?:w|we|na) (.{1,80}?) (czy .{1,245})", original, re.I)
    if match:
        return Capability(
            "ask" if normalize(match[1]) == "zapytaj" else "announce",
            "assist_satellite",
            location=normalize(match[2]),
            payload=match[3],
        )
    value = normalize(original)
    value = re.sub(r"^prosze ", "", value)
    if value in {"ktora godzina", "ktora jest godzina", "jaka jest godzina"}:
        return Capability("time")
    if value in {"jaki dzisiaj dzien", "jaki dzis dzien", "jaka jest data", "jaka dzisiaj data"}:
        return Capability("date")
    # No stripping of unknown suffixes or compound/negated commands.
    media_name = None
    media_match = re.fullmatch(r"(.+) na odtwarzaczu (.+)", value)
    media_body = value
    if media_match:
        media_body, media_name = media_match.groups()
    body, location = split_location(media_body)
    media = {
        "wstrzymaj muzyke": "media_pause",
        "zatrzymaj muzyke": "media_pause",
        "wznow muzyke": "media_play",
        "nastepny utwor": "media_next_track",
        "poprzedni utwor": "media_previous_track",
        "glosniej": "volume_up",
        "ciszej": "volume_down",
        "wycisz muzyke": "mute",
        "przywroc dzwiek": "unmute",
    }
    if body in media:
        return Capability(media[body], "media_player", name=media_name, location=location)
    match = re.fullmatch(r"ustaw glosnosc na (\d{1,3})\s*(?:%|procent)", body)
    if match:
        return Capability(
            "volume_set", "media_player", name=media_name, location=location, value=int(match[1])
        )
    if media_match:
        return None
    maintenance = {
        "czy wszystkie urzadzenia sa sprawne": "health",
        "czy wszystkie urzadzenia dzialaja": "health",
        "czy urzadzenia dzialaja poprawnie": "health",
        "czy jakies urzadzenie ma problem": "health",
        "czy jakies urzadzenia maja problem": "health",
        "czy sa jakies problemy z urzadzeniami": "health",
        "czy sa problemy z urzadzeniami": "health",
        "sprawdz stan urzadzen": "health",
        "sprawdz urzadzenia": "health",
        "czy trzeba gdzies wymienic baterie": "battery",
        "czy trzeba wymienic baterie": "battery",
        "gdzie trzeba wymienic baterie": "battery",
        "ktore urzadzenia maja slaba baterie": "battery",
        "czy sa slabe baterie": "battery",
        "czy jakas bateria jest slaba": "battery",
        "sprawdz baterie": "battery",
    }
    if body in maintenance:
        return Capability("maintenance", location=location, kind=maintenance[body])
    state_query = _parse_state_query(value)
    if state_query:
        return state_query
    if value.startswith(("czy ", "ktore ")) and re.search(r"\bgdzies\b|\bcalym domu\b", value):
        # A malformed global question must not fall through as a legacy Area alias.
        return None
    match = re.fullmatch(r"(czy|ktore) (wszystkie )?(okna|drzwi) sa (otwarte|zamkniete)", body)
    if match and not (match[1] == "ktore" and match[2]):
        # "Na klucz" describes the lock, not an area named "klucz".
        if location == "klucz":
            return None
        query = "open" if match[4] == "otwarte" else "closed"
        if match[1] == "ktore":
            query = "which_" + query
        elif match[2] and query == "open":
            query = "all_open"
        return Capability(
            "state",
            "binary_sensor",
            location=location,
            kind="window" if match[3] == "okna" else "door",
            query=query,
        )
    if body in {"ktore swiatla sa wlaczone", "czy swiatla sa wlaczone"}:
        return Capability("state", "light", location=location, kind="on")
    if body == "jaka jest wilgotnosc":
        return Capability("sensor", "sensor", location=location, kind="humidity")
    match = re.fullmatch(r"jaki (?:jest )?stan (.+)", body)
    if match:
        return Capability("state", name=match[1], location=location)
    if value in {
        "co jest na liscie zakupow",
        "co zostalo na liscie zakupow",
        "przeczytaj liste zakupow",
    }:
        return Capability("todo_read", "todo", kind="shopping")
    match = re.fullmatch(r"(?:przeczytaj liste|co jest na liscie) (.+)", value)
    if match:
        return Capability("todo_read", "todo", name=match[1])
    weather = _parse_weather(value)
    if weather:
        return weather
    match = re.fullmatch(r"co mam(?: (dzis|dzisiaj|jutro))? w kalendarzu(?: (.+))?", value)
    if match:
        return Capability("calendar", "calendar", name=match[2], day=int(match[1] == "jutro"))
    match = re.fullmatch(r"uruchom (scene|skrypt|rutyne) (.+)", value)
    if match:
        return Capability("routine", "scene" if match[1] == "scene" else "script", name=match[2])
    return None


def _parse_state_query(value: str) -> Capability | None:
    """One bounded read question; house scope is never inferred from 'all'."""
    if not value.startswith(("czy ", "ktore ")) or len(value) > 256 or len(value.split()) > 40:
        return None
    if value in {"czy gdzies swieci sie swiatlo", "czy gdzies swieca sie swiatla"}:
        return Capability("state", "light", kind="on", query="any_on", scope="house")
    scope: Literal["", "house"] = ""
    suffix = " w calym domu"
    if value.endswith(suffix):
        value, scope = value[: -len(suffix)], "house"
    body, location = split_location(value)
    front = re.fullmatch(
        r"czy (?P<preposition>w|we|na) (?P<location>.{1,80}?) "
        r"(?P<question>swieci sie swiatlo|swieca sie swiatla)",
        value,
    )
    if front:
        body = "czy " + front["question"]
        location = front["location"]
        if front["preposition"] == "w" and location == "calym domu":
            if scope:
                return None
            scope, location = "house", None
    if scope and location is not None:
        return None
    if location is not None and (
        len(location) > 80
        or re.search(
            r"\b(nie|nigdy|jezeli|jesli|potem|pozniej|jutro|oprocz|bez|oraz|ale|lub|i|"
            r"zamiast|jednak|tylko|gdzies)\b|\bcalym domu\b",
            location,
        )
    ):
        return None
    light_queries = {
        "czy swieci sie swiatlo": "any_on",
        "czy swieca sie swiatla": "any_on",
        "czy jakies swiatla sa wlaczone": "any_on",
        "czy wszystkie swiatla sa wylaczone": "all_off",
    }
    query = light_queries.get(body)
    if not query and (scope or front):
        # Existing unscoped questions retain their legacy, empty query slot.
        query = {
            "czy swiatla sa wlaczone": "any_on",
            "ktore swiatla sa wlaczone": "which_on",
        }.get(body)
    if query:
        return Capability("state", "light", location=location, kind="on", query=query, scope=scope)
    if scope:
        match = re.fullmatch(r"(czy|ktore) (wszystkie )?(okna|drzwi) sa (otwarte|zamkniete)", body)
        if match and not (match[1] == "ktore" and match[2]):
            query = "open" if match[4] == "otwarte" else "closed"
            if match[1] == "ktore":
                query = "which_" + query
            elif match[2] and query == "open":
                query = "all_open"
            return Capability(
                "state",
                "binary_sensor",
                kind="window" if match[3] == "okna" else "door",
                query=query,
                scope=scope,
            )
    return None


def _parse_weather(value: str) -> Capability | None:
    """Allow Polish word order while consuming the entire weather question."""
    current = re.fullmatch(
        r"jaka (?:jest )?(?:teraz|w tej chwili|w tym momencie) pogoda"
        r"|jaka (?:jest )?pogoda (?:teraz|w tej chwili|w tym momencie)",
        value,
    )
    if current:
        return Capability("weather", "weather", kind="current")
    if re.fullmatch(
        r"(?:kiedy|o ktorej) przestanie padac(?: deszcz)?|do ktorej bedzie padac", value
    ):
        return Capability("weather", "weather", kind="rain_end")
    day = r"(?P<day>dzis|dzisiaj|jutro)"
    for pattern in (
        rf"jaka (?:(?P<tense>jest|bedzie) )?pogoda(?: {day})?",
        rf"jaka (?:(?P<tense>jest|bedzie) )?{day} pogoda",
        rf"jaka {day} (?P<tense>jest|bedzie) pogoda",
        rf"jaka pogoda (?P<tense>jest|bedzie)(?: {day})?",
        rf"jaka mamy pogode(?: {day})?",
        rf"jaka mamy {day} pogode",
        rf"jaka {day} mamy pogode",
    ):
        match = re.fullmatch(pattern, value)
        if match:
            slots = match.groupdict()
            tomorrow = slots.get("day") == "jutro"
            future = tomorrow or slots.get("tense") == "bedzie"
            return Capability("forecast" if future else "weather", "weather", day=int(tomorrow))
    for pattern in (
        rf"czy bedzie padac(?: {day})?",
        rf"czy {day} bedzie padac",
        rf"czy bedzie {day} padac",
    ):
        match = re.fullmatch(pattern, value)
        if match:
            return Capability("forecast", "weather", day=int(match["day"] == "jutro"))
    return None
