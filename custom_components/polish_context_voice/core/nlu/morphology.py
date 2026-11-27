"""Conservative lightweight morphology; optional Morfeusz adapter.

No heuristic stemming of arbitrary entity names. HA aliases handle proper nouns.
Morfeusz is injectable, never installed/downloaded at runtime by the integration.
"""

from functools import lru_cache
from typing import Protocol

from .normalize import normalize

FORMS = {
    "salon": "salonu salonie salonem",
    "kuchnia": "kuchni kuchnie kuchnia",
    "biuro": "biura biurze biurem",
    "sypialnia": "sypialni sypialnie",
    "lazienka": "lazience lazienki lazienke",
    "korytarz": "korytarzu korytarzem",
    "pokoj": "pokoju pokojem",
    "parter": "parterze parteru",
    "pietro": "pietrze pietra",
    "garaz": "garazu garazem",
    "jadalnia": "jadalni jadalnie",
    "gabinet": "gabinecie gabinetu gabinetem",
    "pralnia": "pralni pralnie",
    "spizarnia": "spizarni spizarnie",
    "garderoba": "garderobie garderoby garderobe",
    "piwnica": "piwnicy piwnice",
    "poddasze": "poddaszu poddasza",
    "strych": "strychu strychem",
    "wiatrolap": "wiatrolapie wiatrolapu wiatrolapem",
    "przedpokoj": "przedpokoju przedpokojem",
    "taras": "tarasie tarasu tarasem",
    "balkon": "balkonie balkonu balkonem",
    "ogrod": "ogrodzie ogrodu ogrodem",
    "warsztat": "warsztacie warsztatu warsztatem",
    "pracownia": "pracowni pracownie",
    "kotlownia": "kotlowni kotlownie",
    "brama": "brame bramy bramie",
    "lampa": "lampe lampa lampy lamp",
    "lampka": "lampke lampka lampki lampek",
    "stol": "stole stolu stolem",
    "glowny": "glowne glowna glownego glownych",
}
LEMMAS = {form: lemma for lemma, forms in FORMS.items() for form in [lemma, *forms.split()]}


_LIGHT_ACCUSATIVES = {
    "tasme": "tasma",
    "listwe": "listwa",
    "zarowke": "zarowka",
    "girlande": "girlanda",
}
_LIGHT_NOUNS = _LIGHT_ACCUSATIVES.keys() | set(_LIGHT_ACCUSATIVES.values())


def light_name_key(key: str) -> str | None:
    """Fold a known light noun in an already normalized complete entity name."""
    noun, separator, remainder = key.partition(" ")
    if noun not in _LIGHT_NOUNS:
        return None
    return _LIGHT_ACCUSATIVES.get(noun, noun) + separator + remainder


class Morphology(Protocol):
    def key(self, text: str) -> str: ...


@lru_cache(maxsize=4096)
def _light_key(text: str) -> str:
    return " ".join(LEMMAS.get(token, token) for token in normalize(text).split())


class LightMorphology:
    def key(self, text: str) -> str:
        return _light_key(text)


class MorfeuszMorphology(LightMorphology):
    """Only adopt a lemma when all analyses agree. Ambiguity stays literal."""

    def __init__(self, analyser=None):
        if analyser is None:
            import morfeusz2

            analyser = morfeusz2.Morfeusz()
        self.analyser = analyser
        self._cache = {}

    def key(self, text: str) -> str:
        if text in self._cache:
            return self._cache[text]
        result = []
        for word in text.casefold().split():
            base = super().key(word)
            if normalize(word) in LEMMAS:
                result.append(base)
                continue
            analyses = self.analyser.analyse(word)
            lemmas = {normalize(item[2][1].split(":")[0]) for item in analyses}
            result.append(next(iter(lemmas)) if len(lemmas) == 1 else base)
        if len(self._cache) >= 4096:
            self._cache.clear()
        self._cache[text] = " ".join(result)
        return self._cache[text]
