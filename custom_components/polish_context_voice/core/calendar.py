"""Bounded Polish calendar grammar and dates; no model, network or HA imports."""

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta

from .nlu.numbers import number
from .spoken_time import spoken_time

WEEKDAYS = {
    "poniedzialek": 0,
    "wtorek": 1,
    "srode": 2,
    "sroda": 2,
    "czwartek": 3,
    "piatek": 4,
    "sobote": 5,
    "sobota": 5,
    "niedziele": 6,
    "niedziela": 6,
}
DAY = (
    r"(?:dzisiaj|dzis|jutro|pojutrze|\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}(?:\.\d{4})?"
    r"|(?:w |we )?(?:(?:najblizszy|najblizsza|przyszly|przyszla) )?(?:" + "|".join(WEEKDAYS) + r"))"
)
OVERVIEW_PERIOD = (
    rf"(?:{DAY}|na {DAY}|(?:w )?(?:tym|przyszlym) tygodniu|(?:na )?przyszly tydzien)"
)
ORDINALS = dict(
    zip(
        "zerowej pierwszej drugiej trzeciej czwartej piatej szostej siodmej osmej dziewiatej "
        "dziesiatej jedenastej dwunastej trzynastej czternastej pietnastej szesnastej "
        "siedemnastej osiemnastej dziewietnastej dwudziestej".split(),
        range(21),
        strict=True,
    )
)
ORDINALS.update(
    {"dwudziestej pierwszej": 21, "dwudziestej drugiej": 22, "dwudziestej trzeciej": 23}
)
ORDINALS.update(
    dict(
        zip(
            "zerowa pierwsza druga trzecia czwarta piata szosta siodma osma dziewiata dziesiata "
            "jedenasta dwunasta trzynasta czternasta pietnasta szesnasta siedemnasta osiemnasta "
            "dziewietnasta dwudziesta".split(),
            range(21),
            strict=True,
        )
    )
)
ORDINALS.update({"dwudziesta pierwsza": 21, "dwudziesta druga": 22, "dwudziesta trzecia": 23})
CLOCK = r"(?:\d{1,2}(?:[:.]\d{2})?|" + "|".join(sorted(ORDINALS, key=len, reverse=True)) + r")"


def fold(text):
    """Keep punctuation and offsets, so titles retain Polish spelling and case."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower().replace("ł", "l"))
        if not unicodedata.combining(c)
    )


def kiosk_key(text):
    return "".join(c for c in fold(text) if c.isalnum())


def kiosk_conflict(name, others):
    key = kiosk_key(name)
    return not key or any(
        (other := kiosk_key(value)) and (key in other or other in key) for value in others
    )


@dataclass(frozen=True)
class CalendarCommand:
    operation: str
    title: str = ""
    when: str = ""
    calendar: str = ""
    duration: int | None = None
    new_when: str = ""
    view: str = "day"
    all_day: bool = False
    error: str = ""


def duration_minutes(text):
    value = fold(text.strip())
    if value == "pol godziny":
        return 30
    if value in {"godzine", "godzina"}:
        return 60
    match = re.fullmatch(r"(.+?) (minut|minuty|minute|godzin|godziny|godzine)", value)
    if not match:
        return None
    words = {"jedna": "jeden", "jedno": "jeden", "dwie": "dwa"}
    amount = number(" ".join(words.get(w, w) for w in match[1].split()))
    if amount is None or amount != int(amount):
        return None
    minutes = int(amount) * (60 if match[2].startswith("godzin") else 1)
    return minutes if 1 <= minutes <= 1440 else None


def _event_parts(text):
    value = fold(text)
    adjective = re.match(r"(jutrzejsz(?:y|a|e|ego|ej)|dzisiejsz(?:y|a|e|ego|ej)) (.+)", value)
    if adjective:
        return text[adjective.start(2) :], "jutro" if value.startswith("jutr") else "dzis"
    # A day before the title is as explicit as a day after it. Keep the original
    # title offsets and require the whole time phrase; never discard STT tails.
    leading = re.fullmatch(rf"(?:na )?({DAY}) (.+)", value)
    if leading:
        day = text[leading.start(1) : leading.end(1)]
        body = text[leading.start(2) :]
        body_value = fold(body)
        if body_value.startswith("o "):
            timed = re.fullmatch(rf"o ({CLOCK}) (.+)", body_value)
            if timed:
                return body[timed.start(2) :], day + " o " + body[timed.start(1) : timed.end(1)]
            # The date remains invalid so HA asks for clarification instead of
            # treating an unsupported hour as a title with an otherwise valid day.
            return body, day + " " + body
        timed = re.fullmatch(r"(.+?) o (.+)", body_value)
        if timed:
            return body[: timed.end(1)], day + " o " + body[timed.start(2) :]
        return body, day
    match = re.fullmatch(rf"(.+?) (?:na )?({DAY}(?: o .+)?)", value)
    if match:
        return text[: match.end(1)], text[match.start(2) :]
    return text, ""


def _overview_when(raw):
    """Match an entire overview question with at most one bounded date slot."""
    before = rf"(?: (?P<before>{OVERVIEW_PERIOD}))?"
    after = rf"(?: (?P<after>{OVERVIEW_PERIOD}))?"
    patterns = (
        rf"co (?:mam|mamy|jest){after}",
        rf"jakie (?:mam|mamy){before} (?:plany|wydarzenia){after}",
        rf"jakie (?:plany|wydarzenia) (?:mam|mamy){after}",
        rf"jakie sa (?:plany|wydarzenia){after}",
        rf"jaki (?:jest|mam|mamy){before} plan{after}",
        rf"co (?:mam|mamy){before} zaplanowane{after}",
        rf"co (?:jest|mam|mamy){before} w planie{after}",
        rf"co (?P<before>{OVERVIEW_PERIOD}) (?:mam|mamy) (?:w planie|zaplanowane)",
    )
    value = fold(raw)
    for pattern in patterns:
        if match := re.fullmatch(pattern, value):
            dates = [
                raw[match.start(name) : match.end(name)]
                for name in ("before", "after")
                if name in match.groupdict() and match[name]
            ]
            # Two dates are not a supported range or a correction. Keep the
            # question unresolved instead of selecting one silently.
            return dates[0] if len(dates) == 1 else ("" if not dates else None)
    return None


def _overview_calendar_tail(raw):
    """Split a calendar selector and date without defaulting an invalid date."""
    value = fold(raw)
    if re.search(
        r"\b(?:i|a) (?:wlacz|wylacz|dodaj|usun|odwolaj|ustaw|zapisz|odlicz)\b", value
    ):
        return None
    if re.fullmatch(OVERVIEW_PERIOD, value):
        return raw, ""
    for pattern in (
        rf"(?P<when>{OVERVIEW_PERIOD}) (?P<calendar>.+)",
        rf"(?P<calendar>.+?) (?P<when>{OVERVIEW_PERIOD})",
    ):
        if match := re.fullmatch(pattern, value):
            calendar = raw[match.start("calendar") : match.end("calendar")]
            # A second command, unsupported hour or second date is never part
            # of a valid date slot, nor a reason to read today's whole house.
            if re.search(
                rf"\b(?:o|na|za|dzis|dzisiaj|jutro|pojutrze|tygodniu)\b|\b{DAY}\b"
                r"|\b(?:i|a) (?:wlacz|wylacz|dodaj|usun|odwolaj|ustaw|zapisz)\b",
                fold(calendar),
            ):
                return None
            return raw[match.start("when") : match.end("when")], calendar
    if re.match(
        rf"(?:\d|o (?:okolo |rowno )?{CLOCK}\b"
        r"|(?:w|we|na) (?:tym|ten|przyszlym|przyszly|najblizszym|najblizszy)\b"
        r"|(?:za|po|przed) .+ (?:dni|dzien|godziny?|godzin|tygodnie|tygodni)\b)",
        value,
    ):
        return None
    if re.search(rf"\b{DAY}\b (?:o|i|a|lub)\b", value):
        return None
    # Preserve an explicit unknown alias; HA must report no matching calendar,
    # not fall back to an unqualified overview.
    return "", raw


def _parse_overview(raw):
    value = fold(raw)
    if not re.match(r"(?:co|jakie|jaki)\b", value):
        return None
    scope = re.search(
        r" w (?:(?:(?:moim|naszym) )?kalendarzu|(?:(?:moich|naszych) )?kalendarzach)"
        r"(?: (.+))?$",
        value,
    )
    body = raw[: scope.start()] if scope else raw
    when = _overview_when(body)
    if when is not None:
        calendar = ""
        if scope and scope[1]:
            tail = raw[scope.start(1) :]
            if when:
                # A date before an explicit selector makes its entire name
                # unambiguous, including aliases such as "Jutro".
                calendar = tail
            elif selected := _overview_calendar_tail(tail):
                when, calendar = selected
            else:
                return CalendarCommand("help", error="overview_syntax")
        return CalendarCommand("find", when=when or "dzis", calendar=calendar)
    if re.match(
        r"(?:co (?:mam|mamy|jest)\b|jakie (?:mam|mamy|plany|wydarzenia|sa)\b"
        r"|jaki (?:jest|mam|mamy)\b)",
        value,
    ):
        if re.search(r"\b(?:kalendarz\w*|plan|planie|plany|wydarzenia|zaplanowane)\b", value):
            return CalendarCommand("help", error="overview_syntax")
    return None


def parse_calendar(text):
    raw = " ".join(text.strip().rstrip("?.!").split())
    if len(raw) > 500:
        return None
    if overview := _parse_overview(raw):
        return overview
    value = fold(raw)
    calendar = ""
    suffix = re.search(r" (?:do kalendarza|w kalendarzu) (.+)$", value)
    if suffix:
        calendar = raw[suffix.start(1) :]
        raw, value = raw[: suffix.start()], value[: suffix.start()]
    match = re.fullmatch(r"kiedy (?:mam |mamy |jest )?(.+)", value)
    if match and (value.startswith("kiedy jest ") or calendar):
        title, when = _event_parts(raw[match.start(1) :])
        return CalendarCommand("find", title=title, when=when, calendar=calendar)
    match = re.fullmatch(r"pokaz (?:mi )?(?:kalendarz|kalendarze)(?: (.+))?", value)
    if match:
        return CalendarCommand(
            "show",
            when=match[1] or "dzis",
            calendar=calendar,
            view="week" if "tydzien" in (match[1] or "") else "day",
        )
    match = re.fullmatch(r"(?:przeloz|przesun) (.+) na (.+)", value)
    if match:
        title, when = _event_parts(raw[match.start(1) : match.end(1)])
        return CalendarCommand(
            "change", title=title, when=when, calendar=calendar, new_when=raw[match.start(2) :]
        )
    match = re.fullmatch(r"(?:odwolaj|usun|anuluj) (.+)", value)
    if match and (calendar or re.search(rf"\b{DAY}\b", match[1]) or "wydarzenie" in value):
        body = re.sub(r"^wydarzenie ", "", raw[match.start(1) :], flags=re.IGNORECASE)
        title, when = _event_parts(body)
        title = re.sub(r"^wydarzenie ", "", title, flags=re.IGNORECASE)
        return CalendarCommand("cancel", title=title, when=when, calendar=calendar)
    match = re.fullmatch(r"(?:dodaj|zapisz) (.+)", value)
    if match and (calendar or re.search(rf"\b{DAY}\b", match[1]) or "wydarzenie" in value):
        body = raw[match.start(1) :]
        body = re.sub(r"^wydarzenie ", "", body, flags=re.IGNORECASE)
        duration = None
        all_day = fold(body).endswith(" caly dzien")
        if all_day:
            body = body[: -len(" caly dzien")]
        for lasting in re.finditer(r",? (?:na|przez) ", fold(body)):
            if (minutes := duration_minutes(body[lasting.end() :])) is not None:
                duration = minutes
                body = body[: lasting.start()]
                break
        title, when = _event_parts(body)
        return CalendarCommand(
            "add", title=title, when=when, calendar=calendar, duration=duration, all_day=all_day
        )
    if "kalendarz" in value and re.match(
        r"(?:dodaj|zapisz|przeloz|przesun|odwolaj|usun|pokaz|co )", value
    ):
        return CalendarCommand("help", error="syntax")
    return None


def clock_time(text):
    value = fold(text.strip()).removeprefix("o ")
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?", value)
    if match:
        hour, minute = int(match[1]), int(match[2] or 0)
    elif value in ORDINALS:
        hour, minute = ORDINALS[value], 0
    else:
        return None
    return time(hour, minute) if 0 <= hour < 24 and 0 <= minute < 60 else None


def day_date(text, now):
    value = fold(text).removeprefix("na ")
    if value in {"dzis", "dzisiaj", "jutro", "pojutrze"}:
        return now.date() + timedelta(days={"jutro": 1, "pojutrze": 2}.get(value, 0))
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if re.fullmatch(r"\d{1,2}\.\d{1,2}(?:\.\d{4})?", value):
            bits = [int(x) for x in value.split(".")]
            return date(bits[2] if len(bits) == 3 else now.year, bits[1], bits[0])
    except ValueError:
        return None
    value = re.sub(r"^(?:w|we) ", "", value)
    future = value.startswith(("przyszly ", "przyszla "))
    value = re.sub(r"^(?:najblizszy|najblizsza|przyszly|przyszla) ", "", value)
    if value not in WEEKDAYS:
        return None
    offset = (
        7 - now.weekday() + WEEKDAYS[value] if future else (WEEKDAYS[value] - now.weekday()) % 7
    )
    return now.date() + timedelta(days=offset)


def moment(text, now, *, base=None):
    """Return (local time, time specified). DST gaps and folds require clarification."""
    value = fold(text.strip()).removeprefix("na ")
    pieces = value.split(" o ", 1)
    day = day_date(pieces[0], now)
    clock = clock_time(pieces[1]) if len(pieces) == 2 else None
    if day is None and (clock := clock_time(value)) is not None and base is not None:
        day = base.date()
    if day is None or (len(pieces) == 2 and clock is None):
        return None
    naive = datetime.combine(day, clock or time.min)
    at = naive.replace(tzinfo=now.tzinfo)
    if at.astimezone(UTC).astimezone(now.tzinfo).replace(tzinfo=None) != naive:
        return None
    if at.utcoffset() != at.replace(fold=1).utcoffset():
        return None
    return at, clock is not None


def period(text, now):
    value = fold(text)
    if value in {"w przyszlym tygodniu", "przyszly tydzien", "na przyszly tydzien"}:
        start = now.date() + timedelta(days=7 - now.weekday())
        days = 7
    elif value in {"w tym tygodniu", "tym tygodniu"}:
        start, days = now.date() - timedelta(days=now.weekday()), 7
    elif parsed := moment(value or "dzis", now):
        start, days = parsed[0].date(), 1
    else:
        return None
    return (
        datetime.combine(start, time.min, now.tzinfo),
        datetime.combine(start + timedelta(days=days), time.min, now.tzinfo),
    )


def answer_slot(command, stage, text):
    raw = " ".join(text.strip().rstrip("?.!").split())
    if stage == "when":
        return replace(command, when=raw)
    if stage == "time":
        if fold(raw) == "caly dzien":
            return replace(command, all_day=True)
        if clock_time(raw) is not None:
            return replace(command, when=command.when + " o " + raw.removeprefix("o "))
    if stage == "calendar":
        return replace(command, calendar=raw)
    return None


def event_day(at, now):
    """A local date without a numeric clock or a synthetic midnight."""
    day = (at.date() - now.date()).days
    if day in (0, 1, 2):
        return {0: "dzisiaj", 1: "jutro", 2: "pojutrze"}[day]
    months = (
        "stycznia",
        "lutego",
        "marca",
        "kwietnia",
        "maja",
        "czerwca",
        "lipca",
        "sierpnia",
        "września",
        "października",
        "listopada",
        "grudnia",
    )
    return f"{at.day} {months[at.month - 1]}" + (f" {at.year} roku" if at.year != now.year else "")


def event_when(at, now, *, conversational=False):
    if not isinstance(at, datetime):
        at = datetime.combine(at, time.min, now.tzinfo)
        return f"{event_day(at, now)}, przez cały dzień"
    style = "conversational" if conversational else "exact"
    return f"{event_day(at, now)} {spoken_time(at, style=style)}"


def event_overview(events, now):
    """Speak an already ordered list; source metadata stays with the caller.

    Day headings are repeated only when the local day changes. Equal titles are
    kept separate: two calendars can contain two distinct appointments.
    """
    parts, previous_day = [], None
    for title, start in events:
        local_day = start.date() if isinstance(start, datetime) else start
        if isinstance(start, datetime):
            when = spoken_time(start, style="conversational")
        else:
            when = "przez cały dzień"
        if local_day != previous_day:
            at = datetime.combine(local_day, time.min, now.tzinfo)
            when = f"{event_day(at, now)} {when}"
        parts.append(f"{when[0].upper()}{when[1:]} — {title}")
        previous_day = local_day
    return ". ".join(parts) + ("." if parts else "")
