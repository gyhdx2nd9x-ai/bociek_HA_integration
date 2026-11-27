"""Bounded light-command prefixes; candidates are never permission to execute.

This runs only after the ordinary whole-utterance interpretation failed. The
engine must resolve the original complete request first, validate each prefix,
and ask for fresh, source-bound consent. No text is sent to an external model.
"""

import re
from dataclasses import dataclass

from ..types import Action, Proposal
from . import OFF, ON, VERB, parse
from .normalize import normalize

MAX_TEXT_LENGTH = 256
MAX_WORDS = 32
MAX_BOUNDARIES = 12
_START = re.compile(
    rf"^(?:(?:czy )?(?:mozesz|moglbys|moglabys)(?: mi)? |prosze(?: o)? )?(?:{ON}|{OFF})\b"
)
_QUOTED_SPEECH = re.compile(
    r"[\"'„”“«»‘’]|\b(?:powiedzial\w*|mowil\w*|krzykn\w*|cyt\w*|napis\w*|brzm\w*)\b"
)
# A modifier/second action is meaningful, even when it is not understood. Do
# not discard it as background. Conservative refusals are preferable to a
# proposal that loses an explicit restriction or changes the requested action.
_MEANINGFUL_TAIL = re.compile(
    rf"\b(?:{VERB}|nie|nigdy|zadn\w*|jednak|zamiast|raczej|stop|czekaj|poczekaj|"
    r"anul\w*|rezygn\w*|cofn\w*|przerwij|ale|tylko|jedynie|wylacznie|wszystk\w*|"
    r"jezeli|jesli|gdy|kiedy|chyba|zanim|dopiero|dopoki|podczas|oprocz|poza|bez|wyj\w*|"
    r"i|oraz|a|lub|albo|potem|pozniej|nastepnie|najpierw|rownoczesnie|"
    r"o|po|przed|od|az|zaraz|za|jutro|dzis\w*|dzisiaj|teraz|wiecz\w*|rano|noc\w*|godzin\w*|minut\w*|sekund\w*|"
    r"w|we|na|do|z|ze|u|przy|nad|pod|obok|miedzy|tutaj|tam|tu|wsz\w*|"
    r"moc\w*|slab\w*|jasn\w*|ciem\w*|sciem\w*|przygas\w*|rozjas\w*|procent\w*|"
    r"mniej|bardziej|lekko|troche|minimal\w*|maksymal\w*|pel\w*|"
    r"kolor\w*|barw\w*|ciepl\w*|zimn\w*|czerw\w*|ziel\w*|niebies\w*|"
    r"bial\w*|zol\w*|fiolet\w*|rozow\w*|pomarancz\w*|turkus\w*|"
    r"polow\w*|pol|cwierc|jeden|jedna|dwa|dwie|trzy|cztery|piec|szesc|siedem|"
    r"osiem|dziewiec|dziesiec|sto|zero|dwadziescia|trzydziesci|\w*nascie|\w*dziesiat|\w*set|"
    r"pierwsz\w*|drug\w*|inn\w*|tamte\w*|ostatn\w*|kolejn\w*|wybran\w*|konkretn\w*|"
    r"glown\w*|robocz\w*|dekor\w*|ozdob\w*|nastroj\w*|tyln\w*|przedn\w*|gorn\w*|doln\w*|"
    r"zapalon\w*|wlaczon\w*|swiec\w*|ktore\w*|"
    r"boczn\w*|sufit\w*|podlog\w*|stol\w*|biurk\w*|kuchenn\w*|salon\w*|sypial\w*|"
    r"biurow\w*|lazien\w*|ogrod\w*|taras\w*|korytarz\w*|garaz\w*|"
    r"pogle\w*|poglos\w*|scisz\w*|graj|zagraj|pusc|odtw\w*|zatrzym\w*|"
    r"odlicz\w*|timer\w*|minutnik\w*|alarm\w*|muzyk\w*|piosenk\w*|utwor\w*|"
    r"dodaj|usun\w*|odwol\w*|przenies\w*|uruch\w*|wlacz\w*|wylacz\w*|"
    r"zapal\w*|zgas\w*|gas\w*|otwor\w*|zamkn\w*|rolet\w*|zaluz\w*|ogrzew\w*|"
    r"temperatur\w*|klimat\w*|nawil\w*|odkur\w*|wentyl\w*|zawor\w*|"
    r"drzwi\w*|zamek\w*|bram\w*|skrypt\w*|scen\w*|automat\w*)\b|[\d%]"
)


@dataclass(frozen=True)
class LightPrefix:
    proposal: Proposal
    prefix_words: int
    trailing_words: int
    unsafe_tail: bool


def light_prefixes(text):
    """Return at most twelve longest prefixes, preserving every trailing word.

    Semantic eligibility belongs to the engine. A named prefix can still be a
    switch, cover, hidden alias, or unknown location and must never be assumed
    to be a light. ``unsafe_tail`` is retained so a known light prefix cannot
    leak into a more permissive fallback after its restriction was rejected.
    """
    if not text or len(text) > MAX_TEXT_LENGTH:
        return ()
    value = normalize(text)
    words = value.split()
    if not 3 <= len(words) <= MAX_WORDS or not _START.match(value):
        return ()
    candidates = []
    for boundary in range(len(words) - 1, max(1, len(words) - MAX_BOUNDARIES - 1), -1):
        proposal = parse(" ".join(words[:boundary]))
        if proposal is None or proposal.action not in {Action.ON, Action.OFF}:
            continue
        if proposal.kind not in {"light", "single_light", "named"}:
            continue
        tail = " ".join(words[boundary:])
        candidates.append(
            LightPrefix(
                proposal,
                boundary,
                len(words) - boundary,
                bool(
                    _MEANINGFUL_TAIL.search(tail)
                    or _QUOTED_SPEECH.search(text.casefold())
                    or _QUOTED_SPEECH.search(value)
                ),
            )
        )
    return tuple(candidates)
