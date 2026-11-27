"""Resolve the requesting satellite's player from HA identity, never its name."""

from .communication import ACTION_FAILED_SPEECH
from .player_identity import linked_ha_player_ids


def source_player(request, model, area_id, *, backend=None, config_entry_id=None):
    """Return one physical source's player; callers retain exposure/risk checks.

    MA's Home Assistant player provider uses the original media_player entity ID
    as its player ID. Other providers need a common HA device or explicit room
    preference; similar names are not identity.
    """
    satellite = model.entities.get(request.satellite_id)
    device_id = request.device_id
    if request.satellite_id:
        if satellite is None or satellite.domain != "assist_satellite" or not satellite.device_id:
            return None, "music_satellite_unknown"
        if device_id and satellite.device_id != device_id:
            return None, "music_satellite_mismatch"
        device_id = satellite.device_id
    elif device_id:
        satellites = [
            entity
            for entity in model.entities.values()
            if entity.domain == "assist_satellite" and entity.device_id == device_id
        ]
        if len(satellites) != 1:
            return None, "music_satellite_unknown"
        satellite = satellites[0]
    else:
        return None, "missing_music_player"
    source_area = satellite.area_id or model.devices.get(device_id)
    if not satellite.available:
        return None, "music_satellite_unavailable"
    if source_area != area_id:
        # An explicit request for another room must never fall back here.
        return None, "music_satellite_wrong_area"
    physical = [
        entity
        for entity in model.entities.values()
        if entity.domain == "media_player" and entity.device_id == device_id
    ]
    physical_ids = {entity.id for entity in physical if entity.platform != "music_assistant"}
    ma = [
        entity
        for entity in model.entities.values()
        if entity.domain == "media_player"
        and entity.platform == "music_assistant"
        and (
            entity.device_id == device_id
            or bool(linked_ha_player_ids(model, entity) & physical_ids)
        )
    ]
    if backend == "music_assistant":
        candidates = [e for e in ma if not config_entry_id or e.config_entry_id == config_entry_id]
        if not candidates:
            return None, "music_satellite_ma_missing"
    elif backend == "home_assistant":
        candidates = [e for e in physical if e.platform != "music_assistant"] or ma
    else:
        # Queue controls prefer MA's representation of this same physical device.
        candidates = ma or physical
    if not candidates:
        return None, "music_satellite_player_missing"
    if len(candidates) != 1:
        return None, "music_satellite_player_ambiguous"
    entity = candidates[0]
    if entity.area_id not in (None, area_id):
        return None, "music_satellite_wrong_area"
    return entity, ""


def failure_speech(reason):
    return {
        "music_satellite_unknown": "Nie mogę ustalić odtwarzacza satelity, która przyjęła prośbę.",
        "music_satellite_mismatch": (
            "Dane satelity i urządzenia nie są zgodne. Sprawdź ich konfigurację."
        ),
        "music_satellite_unavailable": ACTION_FAILED_SPEECH,
        "music_satellite_wrong_area": (
            "W tym pomieszczeniu nie ma domyślnego głośnika. "
            "Wybierz go w ustawieniach muzyki Boćka."
        ),
        "music_satellite_ma_missing": (
            "Odtwarzacz tej satelity nie jest dostępny w Music Assistant. "
            "Dodaj go tam lub wybierz domyślny głośnik pokoju w Boćku."
        ),
        "music_satellite_player_missing": (
            "Ta satelita nie udostępnia odtwarzacza muzyki w HA. "
            "Wybierz domyślny głośnik pokoju w Boćku."
        ),
        "music_satellite_player_ambiguous": (
            "Z tą satelitą powiązano kilka odtwarzaczy. "
            "Wybierz domyślny głośnik pokoju w Boćku."
        ),
    }.get(reason, "Wybierz domyślny głośnik pokoju albo wydaj polecenie przez satelitę.")
