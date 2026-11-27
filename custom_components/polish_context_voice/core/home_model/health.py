"""Read-only health findings. Never assign roles or change exposure automatically."""

from dataclasses import asdict, dataclass

ROLES = {
    "voice_default_on",
    "voice_main",
    "voice_task",
    "voice_ambient",
    "voice_accent",
    "voice_all_off",
    "voice_safe_all",
    "voice_exclude_all",
    "voice_primary",
    "voice_block",
    "voice_confirm",
    "voice_high_risk",
    "voice_media",
    "voice_shopping",
    "voice_routine",
    "voice_announce",
}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    ids: tuple[str, ...]
    message: str
    new: bool = False


def check(model, previous_ids=frozenset()):
    findings = []
    for entity in model.entities.values():
        if entity.exposed and entity.domain in {
            "light",
            "switch",
            "cover",
            "climate",
            "media_player",
        }:
            if not entity.labels & ROLES:
                findings.append(
                    Finding(
                        "missing_semantic_labels",
                        "info",
                        (entity.id,),
                        "Opcjonalna rola głosowa nie jest ustawiona. "
                        "Dodaj ją w kreatorze tylko jeśli encja ma należeć do zestawu "
                        "lub mieć specjalną rolę.",
                        entity.id not in previous_ids,
                    )
                )
            if entity.area_id not in model.areas:
                findings.append(
                    Finding(
                        "missing_area",
                        "warning",
                        (entity.id,),
                        "Encja Assist nie ma poprawnego Area.",
                    )
                )
            if entity.labels & {"voice_default_on", "voice_all_off", "voice_safe_all"} and (
                entity.labels & {"voice_block", "voice_exclude_all"}
            ):
                findings.append(
                    Finding(
                        "conflicting_labels",
                        "warning",
                        (entity.id,),
                        "Etykieta wykluczenia blokuje rolę zbiorczą.",
                    )
                )
    for alias, ids in sorted(model.entity_index.items()):
        areas = {}
        for entity_id in sorted(ids):
            entity = model.entities[entity_id]
            if entity.exposed:
                areas.setdefault(entity.area_id, []).append(entity_id)
        for same_area in areas.values():
            if len(same_area) > 1:
                findings.append(
                    Finding(
                        "alias_conflict",
                        "warning",
                        tuple(same_area),
                        f"Alias '{alias}' jest niejednoznaczny w jednym Area.",
                    )
                )
    for alias, matches in sorted(model.location_index.items()):
        if len(matches) > 1:
            findings.append(
                Finding(
                    "location_alias_conflict",
                    "warning",
                    tuple(sorted(i for _, i in matches)),
                    f"Konflikt aliasu lokalizacji '{alias}'.",
                )
            )
    for satellite, area in model.satellites.items():
        if area not in model.areas:
            findings.append(
                Finding(
                    "satellite_without_area",
                    "error",
                    (satellite,),
                    "Satelita nie ma poprawnego Area.",
                )
            )
    return [asdict(finding) for finding in findings]
