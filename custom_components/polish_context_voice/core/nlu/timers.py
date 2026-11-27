"""Cheap timer routing; device control continues through the semantic resolver."""

import re

from ..context.dialogue import CONFIRMATIONS, classify_reply
from ..speech import duration_number
from .normalize import normalize


def is_timer_request(text: str) -> bool:
    return bool(re.search(r"\b(odlicz\w*|minutnik\w*|timer\w*|stoper\w*)\b", normalize(text)))


def canonical_timer_text(text: str) -> str:
    # Only an opening imperative is an alias. HA must still match the whole sentence,
    # including its duration, so negation/extra commands cannot become a timer.
    text = re.sub(
        r"^(?:prosz[eę]\s+)?odlicz\s+(?:mi\s+)?",
        "ustaw minutnik na ",
        text.strip(),
        flags=re.IGNORECASE,
    )
    # Preserve the entire selector/tail: HA resolves names, durations and ambiguity.
    text = re.sub(
        r"^(?:prosz[eę]\s+)?(przerwij|anuluj|zako[nń]cz|wstrzymaj|wzn[oó]w)\s+"
        r"(odliczanie|minutnik|timer|stoper)\b",
        lambda match: (
            {
                "przerwij": "anuluj",
                "anuluj": "anuluj",
                "zakoncz": "anuluj",
                "wstrzymaj": "wstrzymaj",
                "wznow": "wznów",
            }[normalize(match[1])]
            + " minutnik"
        ),
        text,
        flags=re.IGNORECASE,
    )
    # Polish often omits "one": "na minutę i sekundę". Limit this to duration
    # positions, keeping named timers and the rest of the utterance intact.
    duration, *name = re.split(r"\b(o nazwie|zwany|nazwany)\b", text, maxsplit=1)
    duration = re.sub(
        r"\b(na|i)\s+(godzinę|minutę|sekundę)\b",
        r"\1 1 \2",
        duration,
        flags=re.IGNORECASE,
    )
    return duration + "".join(name)


TIMER_CONFIRMATIONS = CONFIRMATIONS | {"potwierdzam minutnik", "tak potwierdzam minutnik"}
TIMER_REJECTIONS = frozenset(
    {
        "nie",
        "nie dziekuje",
        "nie uruchamiaj",
        "anuluj",
        "anuluj to",
        "rezygnuje",
    }
)
TIMER_NO_MATCH_SPEECH = "Nie rozumiem polecenia minutnika. Powtórz je proszę."


def timer_reply(text: str) -> bool | None:
    return classify_reply(text, confirmations=TIMER_CONFIRMATIONS, rejections=TIMER_REJECTIONS)


def is_timer_followup(text: str) -> bool:
    """Whole replies only. Only route a short reply to an existing proposal."""
    return timer_reply(text) is not None


def is_timer_response_echo(text: str) -> bool:
    """Suppress two known error sentences, not arbitrary overheard conversation."""
    return normalize(text.replace(".", " ")) in {
        normalize(TIMER_NO_MATCH_SPEECH.replace(".", " ")),
        "nie rozumiem polecenia minutnika powiedz na przyklad odlicz dziesiec sekund",
        "nadal nie rozumiem polecenia minutnika",
    }


def timer_prefix_candidates(text: str) -> list[str]:
    """Suggest bounded opening durations for confirmation, never for execution.

    Do not extract from the middle of conversation or discard a second command,
    alternative duration or immediate correction. Native HA validates each prefix.
    """
    if len(text) > 2000 or not re.match(
        r"^(?:prosze )?(?:odlicz\b|(?:ustaw|uruchom|nastaw) minutnik\b)",
        normalize(text),
    ):
        return []
    endings = list(
        re.finditer(
            r"\b(?:sekund(?:a|ę|y)?|minut(?:a|ę|y)?|godzin(?:a|ę|y)?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    candidates = []
    for ending in endings[:6]:
        if ending.end() > 160:
            break
        tail = normalize(text[ending.end() :])
        if not tail or re.match(
            r"^(?:i|a|ale|nie|jednak|albo|lub|zamiast|raczej|czyli|to znaczy|o nazwie|nazwany)\b",
            tail,
        ):
            continue
        if re.search(
            r"\b(?:\d+|sekund\w*|minut\w*|godzin\w*|odlicz\w*|timer\w*|stoper\w*|"
            r"wlacz\w*|wylacz\w*|zapal\w*|zgas\w*|ustaw\w*|uruchom\w*|nastaw\w*|"
            r"anuluj\w*|przerwij\w*|zakoncz\w*|zatrzymaj\w*|potwierdz\w*|"
            r"wstrzymaj\w*|wznow\w*|otworz\w*|zamknij\w*)\b",
            tail,
        ):
            continue
        candidates.append(text[: ending.end()].strip())
    return list(reversed(candidates))  # Prefer complete composite durations.


def spoken_duration(slots: dict, *, words: bool = False) -> str:
    parts = []
    for key, forms in (
        ("hours", ("godzinę", "godziny", "godzin")),
        ("minutes", ("minutę", "minuty", "minut")),
        ("seconds", ("sekundę", "sekundy", "sekund")),
    ):
        value = int(slots.get(key) or 0)
        if value:
            form = (
                forms[0]
                if value == 1
                else (
                    forms[1]
                    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14)
                    else forms[2]
                )
            )
            parts.append(
                (form if value == 1 else f"{duration_number(value)} {form}")
                if words
                else f"{value} {form}"
            )
    if words and len(parts) > 1:
        return ", ".join(parts[:-1]) + " i " + parts[-1]
    return " i ".join(parts)
