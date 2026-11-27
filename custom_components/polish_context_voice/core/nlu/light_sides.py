"""Read explicit left/right words without inferring geometry or positions."""

import re
from typing import NamedTuple


class SideForm(NamedTuple):
    side: str
    genders: frozenset[str]


# Normalization folds „prawą” into „prawa” and „lewą” into „lewa”.
SIDE_FORMS = {
    prefix + ending: SideForm(side, frozenset(genders))
    for prefix, side in (("lew", "left"), ("praw", "right"))
    for ending, genders in (
        ("y", "m"), ("a", "f"), ("e", "n"), ("ego", "mn"), ("ej", "f"), ("ym", "mn"),
    )
}
_SIDE_REPLY = re.compile(
    rf"(?:a )?(?:ten |ta |te |to )?({'|'.join(SIDE_FORMS)}) (?:tez|rowniez|tak samo)"
    r"(?: (?:prosze|poprosze))?"
)


def side_reply(words: str) -> SideForm | None:
    """A complete request to apply the previous operation to a specific side."""
    if match := _SIDE_REPLY.fullmatch(words):
        return SIDE_FORMS[match[1]]
    return None


def side_name(key: str) -> tuple[SideForm, str] | None:
    """The other words in a registered name or alias must match as a whole."""
    words = key.split()
    sides = [word for word in words if word in SIDE_FORMS]
    if len(sides) != 1 or len(words) < 2:
        return None
    return SIDE_FORMS[sides[0]], " ".join(word for word in words if word != sides[0])
