"""Bounded answers from selected, readable HA light states; never infer missing data."""

from .home_model import Entity


def _names(entities: tuple[Entity, ...]) -> str:
    names = [entity.name[:100] for entity in entities[:8]]
    text = ", ".join(names[:-1]) + " i " + names[-1] if len(names) > 1 else names[0]
    if len(entities) > 8:
        text += f" oraz jeszcze {len(entities) - 8}"
    return text


def light_state_speech(query: str, entities: tuple[Entity, ...]) -> str:
    """Describe entity states, including groups' own state, without expanding members."""
    if not entities:
        return "Nie mam dostępnych świateł, żeby sprawdzić ich stan."
    active = tuple(e for e in entities if e.available and e.state == "on")
    inactive = tuple(e for e in entities if e.available and e.state == "off")
    unknown = tuple(e for e in entities if not e.available or e.state not in {"on", "off"})
    speech = ""
    if active:
        prefix = (
            "Nie, włączone"
            if query == "all_off"
            else ("Tak, włączone" if query == "any_on" else "Włączone")
        )
        speech = f"{prefix}: {_names(active)}."
    elif not unknown:
        prefix = (
            "Tak, sprawdzone"
            if query == "all_off"
            else ("Nie, sprawdzone" if query == "any_on" else "Sprawdzone")
        )
        speech = f"{prefix} światła są wyłączone."
    elif inactive:
        speech = "Światła z dostępnym odczytem są wyłączone."
    if unknown:
        speech += f" Nie mam aktualnego odczytu: {_names(unknown)}."
    return speech.strip()
