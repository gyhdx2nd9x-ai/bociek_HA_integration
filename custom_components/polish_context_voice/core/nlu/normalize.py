"""Normalize accents and punctuation without deleting semantic words."""

import re
import unicodedata


def normalize(text: str) -> str:
    text = text.casefold().replace("ł", "l")
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"[^a-z0-9_.% -]+", " ", text)
    return " ".join(text.strip(" .").split())


def has_foreign_letters(text):
    """Do not let the ASCII normalizer silently delete an extra foreign word."""
    folded = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
    return any(char.isalpha() and not "a" <= char <= "z" for char in folded)
