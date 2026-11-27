"""Small Polish reading grammar and immutable views of existing Daily Stoic text."""

import re
from calendar import isleap
from dataclasses import dataclass
from datetime import date

from .nlu.normalize import normalize

DEFAULT_ENTITY_ID = "sensor.stoic_quote_of_the_day"
MAX_SPEECH_CHARS = 24000
SNAPSHOT_TTL = 300
MAX_SNAPSHOTS = 64


@dataclass(frozen=True)
class StoicCommand:
    section: str
    today: bool = False
    followup: bool = False


@dataclass(frozen=True)
class StoicEntry:
    quote: str
    author: str
    title: str
    commentary: str
    date_status: str


@dataclass(frozen=True)
class StoicSnapshot:
    entity_id: str
    entry: StoicEntry
    expires_at: float


def parse_stoic(text):
    """Accept a whole reading request, never a quote mentioned in another command."""
    words = normalize(text)
    words = re.sub(r"^(?:czy )?(?:mozesz|moglbys|moglabys) (?:mi )?", "", words)
    words = re.sub(r"^prosze(?: mi)? ", "", words)
    words = re.sub(r"(?: prosze)$", "", words)
    if words in {"a omowienie", "a komentarz", "a tytul"}:
        return StoicCommand("title" if words == "a tytul" else "commentary", followup=True)
    direct = re.fullmatch(
        r"(?:(dzis|dzisiaj|dzisiejsz(?:y|a|e)) )?"
        r"(medytacja|medytacji|cytat|cytatu)"
        r"(?: (?:na )?(dzis|dzisiaj))?",
        words,
    )
    if direct and (direct[1] or direct[3]):
        return StoicCommand("full" if direct[2].startswith("medytac") else "quote", today=True)
    prefix = re.fullmatch(
        r"(?:przeczytaj|odczytaj|czytaj|podaj|pokaz|powiedz)(?: mi)? (.+)", words
    )
    query = re.fullmatch(
        r"(?:jaki(?: jest| mamy)?|jaka(?: jest)?|jakie(?: jest| mamy)?|"
        r"jak brzmi|co to (?:jest )?za|co zawiera) (.+)",
        words,
    )
    content = prefix.group(1) if prefix else query.group(1) if query else None
    if content is None:
        return None
    today_word = r"(?:dzis|dzisiaj|dzisiejs(?:zy|za|ze|zego|zej|zym))"
    today = bool(
        re.search(rf"\b{today_word}\b", content)
    )
    content = re.sub(r"\bna (?:dzis|dzisiaj)\b", "", content)
    content = re.sub(rf"\b{today_word}\b", "", content)
    content = " ".join(content.split())
    content = re.sub(r"^(?:tresc|zawartosc) ", "", content)
    content = re.sub(r"^na ", "", content)
    content = re.sub(r" (?:dnia|dzis|dzisiaj)$", "", content).strip()
    if content in {
        "stoica",
        "stoic",
        "daily stoic",
        "medytacja",
        "medytacji",
        "medytacje",
        "medytacja stoicka",
        "medytacje stoicka",
        "medytacji stoickiej",
        "tekst ze stoica",
        "tekst medytacji",
        "dzisiejsza medytacja",
        "dzisiejszej medytacji",
        "medytacje ze stoica",
        "wpis stoicki",
        "wpis dnia",
        "cytat i omowienie",
    }:
        return StoicCommand("full", today)
    if content in {
        "cytat",
        "cytatu",
        "cytat dnia",
        "cytat stoicki",
        "stoicki cytat",
        "cytat stoica",
        "cytat ze stoica",
    }:
        return StoicCommand("quote", today)
    if content in {
        "omowienie",
        "omowienia",
        "komentarz",
        "komentarza",
        "omowienie cytatu",
        "omowienia cytatu",
        "komentarz do cytatu",
        "omowienie stoica",
        "omowienie medytacji",
        "omowienia medytacji",
        "komentarz stoica",
        "omowienie ze stoica",
        "komentarz ze stoica",
    }:
        return StoicCommand("commentary", today)
    if content in {
        "tytul medytacji",
        "tytul cytatu",
        "tytul stoica",
        "tytul dzisiejszej medytacji",
    }:
        return StoicCommand("title", today)
    return None


def _text(attributes, *keys):
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def date_status(attributes, today):
    """Validate explicit dates; localized date labels outrank source-specific ordinals."""
    checks = []
    localized_date = None
    ordinal = attributes.get("day_of_year")
    leap = attributes.get("is_leap_year")
    if leap is not None and type(leap) is not bool:
        return "invalid"
    if ordinal is not None:
        if type(ordinal) is not int or not 1 <= ordinal <= (365 if leap is False else 366):
            return "invalid"
        checks.append(ordinal == today.timetuple().tm_yday)
        if leap is not None:
            checks.append(leap == isleap(today.year))
    key = attributes.get("dateKey", attributes.get("date_key"))
    if key is not None:
        if not isinstance(key, str) or not re.fullmatch(r"\d{2}-\d{2}", key):
            return "invalid"
        try:
            date.fromisoformat("2000-" + key)
        except ValueError:
            return "invalid"
        checks.append(key == today.strftime("%m-%d"))
    for name in ("date", "date_iso"):
        value = attributes.get(name)
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", value):
            try:
                checks.append(date.fromisoformat(value[:10]) == today)
            except ValueError:
                return "invalid"
        elif name == "date" and isinstance(value, str):
            match = re.fullmatch(r"\s*(\d{1,2})\s+([\wąćęłńóśźż]+)\s*", value.casefold())
            if match:
                months = {
                    "stycznia": 1,
                    "lutego": 2,
                    "marca": 3,
                    "kwietnia": 4,
                    "maja": 5,
                    "czerwca": 6,
                    "lipca": 7,
                    "sierpnia": 8,
                    "września": 9,
                    "wrzesnia": 9,
                    "października": 10,
                    "pazdziernika": 10,
                    "listopada": 11,
                    "grudnia": 12,
                }
                month = months.get(match[2])
                day = int(match[1])
                if month is not None:
                    try:
                        localized_date = date(today.year, month, day)
                    except ValueError:
                        return "invalid"
    if localized_date is not None:
        # Daily Stoic sources can use a one-based book index for day_of_year.
        # The localized calendar date is the better freshness signal when present.
        checks = [check for check in checks if check]
        checks.append(localized_date == today)
    return "stale" if False in checks else "current" if checks else "unknown"


def entry_from_attributes(attributes, today):
    author = _text(attributes, "author_and_source", "authorAndSource") or " · ".join(
        text for text in (_text(attributes, "author"), _text(attributes, "source")) if text
    )
    return StoicEntry(
        _text(attributes, "quote"),
        author,
        _text(attributes, "title"),
        _text(attributes, "commentary"),
        date_status(attributes, today),
    )


def _sentence(text):
    return text if text.rstrip('”"’').endswith((".", "!", "?", "…")) else text + "."


def reading(entry, section):
    """Return text, reason and missing sections without shortening the source."""
    values = {
        "quote": entry.quote,
        "title": entry.title,
        "commentary": entry.commentary,
    }
    unavailable = {
        "quote": "Cytat nie jest dostępny w danych Daily Stoic.",
        "title": "Tytuł medytacji nie jest dostępny w danych Daily Stoic.",
        "commentary": "Omówienie nie jest dostępne w danych Daily Stoic.",
    }
    if section == "full":
        if not entry.quote:
            return unavailable["quote"], "stoic_missing_quote", ("quote",)
        missing = tuple(key for key in ("title", "commentary") if not values[key])
        parts = [entry.quote, entry.author, entry.title, entry.commentary]
        parts.extend(unavailable[key] for key in missing)
    elif section in values:
        if not values[section]:
            return unavailable[section], "stoic_missing_" + section, (section,)
        missing = ()
        parts = [values[section], entry.author] if section == "quote" else [values[section]]
    else:
        return "Nie ma takiej części medytacji.", "stoic_invalid_section", ()
    speech = " ".join(_sentence(value) for value in parts if value)
    if len(speech) > MAX_SPEECH_CHARS:
        return (
            "Ta treść jest zbyt długa do jednego odczytu. Poproś osobno o cytat lub omówienie.",
            "stoic_text_too_long",
            (),
        )
    return speech, "stoic_partial" if missing else "", missing
