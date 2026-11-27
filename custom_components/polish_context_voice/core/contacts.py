"""Describe only selected, readable HA contact sensors, without inferring missing states."""

from .home_model import Entity


def _names(entities: tuple[Entity, ...]) -> str:
    names = [entity.name[:100] for entity in entities[:5]]
    text = ", ".join(names[:-1]) + " i " + names[-1] if len(names) > 1 else names[0]
    if len(entities) > 5:
        text += f" oraz jeszcze {len(entities) - 5}"
    return text


def contact_speech(kind: str, query: str, entities: tuple[Entity, ...]) -> str:
    """HA window/door: on=open, off=closed; all other readings remain unknown."""
    noun = "okna" if kind == "window" else "drzwi"
    if not entities:
        return f"Nie mam dostępnych czujników, żeby sprawdzić {noun}."
    opened = tuple(e for e in entities if e.available and e.state == "on")
    closed = tuple(e for e in entities if e.available and e.state == "off")
    unknown = tuple(e for e in entities if not e.available or e.state not in {"on", "off"})
    speech = ""
    if query in {"closed", "all_open"}:
        expected, opposite = (
            ("zamknięte", "otwarte") if query == "closed" else ("otwarte", "zamknięte")
        )
        contrary = opened if query == "closed" else closed
        if contrary:
            speech = f"Nie, {opposite}: {_names(contrary)}."
        elif not unknown:
            speech = f"Tak, sprawdzone {noun} są {expected}."
        elif opened or closed:
            speech = f"{noun.capitalize()} z dostępnym odczytem są {expected}."
    else:
        selected = closed if query == "which_closed" else opened
        label = "Zamknięte" if query == "which_closed" else "Otwarte"
        opposite = "otwarte" if query == "which_closed" else "zamknięte"
        if selected:
            speech = f"{label}: {_names(selected)}."
        elif not unknown:
            speech = f"Sprawdzone {noun} są {opposite}."
        elif opened or closed:
            speech = f"{noun.capitalize()} z dostępnym odczytem są {opposite}."
    if unknown:
        speech += f" Nie mam aktualnego odczytu: {_names(unknown)}."
    return speech.strip()
