"""Small, deterministic role catalog for onboarding; no guessed device permissions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    title: str
    description: str
    domains: frozenset[str] = frozenset()


LIGHT = frozenset({"light"})
CATALOG = {
    "voice_default_on": Role("Włączane domyślnie", "Zapal światła — tylko wybrane lampy.", LIGHT),
    "voice_all_off": Role("Wyłączane razem", "Wyłącz światła — wybrane lampy pokoju.", LIGHT),
    "voice_main": Role(
        "Światło główne", "Na polecenie główne światła; osobno od domyślnego włączania.", LIGHT
    ),
    "voice_task": Role("Światło robocze", "Oświetlenie do pracy.", LIGHT),
    "voice_ambient": Role(
        "Światło dekoracyjne i nastrojowe", "Jedna kategoria dekoracji i nastroju.", LIGHT
    ),
    "voice_accent": Role(
        "Dawna etykieta dekoracyjna",
        "Zgodność ze starszymi ustawieniami; jak voice_ambient.",
        LIGHT,
    ),
    "voice_safe_all": Role(
        "Zestaw: wyłącz wszystko",
        "Osobna zgoda na zbiorcze wyłączanie; nadal obowiązuje potwierdzenie.",
        frozenset({"light", "switch", "media_player"}),
    ),
    "voice_primary": Role(
        "Preferowany odczyt / cel",
        "Wybiera lampę na ogólne „włącz lampę” lub domyślny cel odczytu.",
        frozenset(
            {"light", "sensor", "binary_sensor", "climate", "weather", "calendar", "todo",
             "media_player"}
        ),
    ),
    "voice_shopping": Role("Lista zakupów", "Domyślna lista zakupów.", frozenset({"todo"})),
    "voice_routine": Role(
        "Rutyna głosowa",
        "Dopuszcza scenę/skrypt po potwierdzeniu. Skrypt może sterować całym domem.",
        frozenset({"script", "scene"}),
    ),
    "voice_announce": Role(
        "Odbiorca komunikatów",
        "Zezwala na komunikaty z innego satelity. Nie jest potrzebne do słuchania ani minutników.",
        frozenset({"assist_satellite"}),
    ),
    "voice_media": Role(
        "Multimedia (opis)",
        "Etykieta opisowa; nie wybiera odtwarzacza.",
        frozenset({"media_player"}),
    ),
    "voice_exclude_all": Role(
        "Pomiń w zestawach",
        "Pomija polecenia zbiorcze i raporty. Jawne wywołanie po nazwie pozostaje możliwe.",
    ),
    "voice_confirm": Role(
        "Pytaj przed zmianą", "Wymaga potwierdzenia zmiany; nie blokuje zwykłych odczytów."
    ),
    "voice_high_risk": Role(
        "Zablokuj sterowanie",
        "Blokuje sterowanie urządzeniem wysokiego ryzyka. Niektóre odczyty nadal są możliwe.",
    ),
    "voice_block": Role(
        "Wyklucz z tego asystenta",
        "Blokuje wybór encji przez PCV. "
        "Nie wyłącza mikrofonu, automatyzacji ani innych agentów HA.",
    ),
}
GUARD_ROLES = frozenset({"voice_block", "voice_exclude_all", "voice_confirm", "voice_high_risk"})
LIGHT_ROLES = frozenset({"voice_default_on", "voice_all_off"})
COLLECTIVE = LIGHT_ROLES | {"voice_safe_all"}


def allowed_roles(domain, existing=()):
    """Keep obsolete/wrong-domain assignments visible so users can remove them."""
    return frozenset(
        key
        for key, role in CATALOG.items()
        if not role.domains or domain in role.domains or key in existing
    )


def suggest_lights(entities):
    """Explicit opt-in suggestions, preserving configured sets. Never use room names."""
    current = {
        role: {entity.id for entity in entities if role in entity.labels} for role in LIGHT_ROLES
    }
    eligible = [
        entity
        for entity in entities
        if entity.domain == "light"
        and entity.exposed
        and entity.available
        and not entity.labels & {"voice_block", "voice_exclude_all", "voice_high_risk"}
    ]
    current["voice_all_off"].update(entity.id for entity in eligible)
    if not current["voice_default_on"]:
        main = [entity for entity in eligible if "voice_main" in entity.labels]
        if len(main) == 1:
            current["voice_default_on"].add(main[0].id)
        elif len(eligible) == 1:
            current["voice_default_on"].add(eligible[0].id)
    return {role: sorted(ids) for role, ids in current.items()}
