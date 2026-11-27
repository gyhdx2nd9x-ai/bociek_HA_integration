"""Stable links between Music Assistant entities and Home Assistant players."""


def linked_ha_player_ids(model, entity):
    """Return HA media players with an explicit registry identity link to an MA entity.

    The Music Assistant HA integration creates media-player unique IDs as
    ``mass_<MA player id>``. Its Home Assistant player provider uses the source
    HA entity ID as that player ID. Some integrations also share one HA device
    registry entry. Both are identity links; display-name similarity is not.
    """
    if entity.domain != "media_player" or entity.platform != "music_assistant":
        return frozenset()

    identities = set()
    unique_id = entity.unique_id
    if unique_id:
        identities.add(unique_id)
        if unique_id.startswith("mass_"):
            identities.add(unique_id.removeprefix("mass_"))

    exact_matches = frozenset(
        source.id
        for source in model.entities.values()
        if source.domain == "media_player"
        and source.platform != "music_assistant"
        and (source.id in identities or source.unique_id in identities)
    )
    if exact_matches:
        return exact_matches
    if entity.device_id is None:
        return frozenset()
    return frozenset(
        source.id
        for source in model.entities.values()
        if source.domain == "media_player"
        and source.platform != "music_assistant"
        and source.device_id == entity.device_id
    )


def player_area(model, entity):
    """Resolve an effective HA Area, inheriting only from linked HA identities."""
    if entity.area_id:
        return entity.area_id
    if entity.platform != "music_assistant":
        return None

    linked_areas = {
        area
        for identifier in linked_ha_player_ids(model, entity)
        if (source := model.entities.get(identifier))
        for area in (source.area_id or model.devices.get(source.device_id),)
        if area
    }
    if linked_areas:
        return next(iter(linked_areas)) if len(linked_areas) == 1 else None
    return model.devices.get(entity.device_id) if entity.device_id else None
