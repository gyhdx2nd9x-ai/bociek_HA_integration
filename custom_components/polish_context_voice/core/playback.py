"""Resolve live playback using HA groups and instance-scoped MA queue identities."""

from dataclasses import dataclass

from .communication import ACTION_FAILED_SPEECH
from .player_identity import player_area

SESSION_CONTROLS = {
    "media_next_track",
    "media_previous_track",
    "media_pause",
    "media_play",
    "media_stop",
}
ACTIVE_STATES = {"playing", "buffering", "paused"}


class PlaybackError(Exception):
    def __init__(self, reason, speech):
        self.reason, self.speech = reason, speech


@dataclass(frozen=True)
class Playback:
    coordinator: str
    members: tuple[str, ...]
    key: tuple


def queue_key(entity):
    queue = entity.attributes.get("active_queue")
    if (
        entity.platform == "music_assistant"
        and entity.config_entry_id
        and isinstance(queue, str)
        and 0 < len(queue) <= 256
    ):
        return ("ma", entity.config_entry_id, queue)
    return None


def group_members(entity):
    members = entity.attributes.get("group_members", ())
    if members is None:
        members = ()
    if (
        not isinstance(members, (list, tuple))
        or len(members) > 64
        or any(
            not isinstance(member, str) or not member.startswith("media_player.")
            for member in members
        )
    ):
        raise PlaybackError("music_group_invalid", "Nie mogę ustalić składu grupy głośników.")
    return tuple(dict.fromkeys(members))


def playback_scope(entity, model, operation):
    if entity.members:
        raise PlaybackError(
            "music_group_unsupported", "Ten rodzaj grupy nie udostępnia wspólnej kolejki muzyki."
        )
    group = group_members(entity)
    key = queue_key(entity)
    peers = (
        {
            other.id
            for other in model.entities.values()
            if other.domain == "media_player" and queue_key(other) == key
        }
        if key
        else set()
    )
    members = peers | set(group) | {entity.id}
    for identifier in peers:
        members.update(group_members(model.entities[identifier]))
    if len(members) > 64:
        raise PlaybackError("music_group_invalid", "Grupa głośników jest zbyt duża.")
    if len(members) > 1 and operation not in SESSION_CONTROLS:
        raise PlaybackError(
            "music_group_unsupported",
            "Ta czynność wymaga pojedynczego głośnika. Sterowanie kolejką grupy jest dostępne.",
        )
    coordinator = entity.id
    owners = (
        [model.entities[i] for i in peers if model.entities[i].unique_id == key[2]] if key else []
    )
    if len(owners) == 1:
        coordinator = owners[0].id
    elif group:
        if entity.id not in group:
            raise PlaybackError(
                "music_group_invalid", "Zmienił się skład grupy. Powtórz polecenie."
            )
        coordinator = group[0]
    elif key:
        coordinator = min(members)
    for identifier in sorted(members):
        member = model.entities.get(identifier)
        if (
            member is None
            or member.domain != "media_player"
            or not member.exposed
            or (member.labels & {"voice_block", "voice_high_risk"})
            or (len(members) > 1 and "voice_exclude_all" in member.labels)
        ):
            raise PlaybackError(
                "music_session_blocked", "Nie mam dostępu do wszystkich głośników tej sesji."
            )
        if not member.available:
            raise PlaybackError(
                "music_session_unavailable", ACTION_FAILED_SPEECH
            )
        declared = group_members(member)
        if (
            declared
            and not key
            and (set(declared) != set(group or declared) or (group and declared[0] != coordinator))
        ):
            raise PlaybackError(
                "music_group_invalid", "Zmienił się skład grupy. Powtórz polecenie."
            )
        if key and queue_key(member) != key:
            raise PlaybackError(
                "music_group_invalid", "Nie mogę potwierdzić wspólnej kolejki muzyki."
            )
    return Playback(coordinator, tuple(sorted(members)), key or ("ha", coordinator))


def is_music_active(entity, *, configured=False):
    if not entity.exposed or not entity.available or entity.state not in ACTIVE_STATES:
        return False
    if entity.labels & {"voice_block", "voice_high_risk"}:
        return False
    kind = entity.attributes.get("media_content_type", "")
    content = str(entity.attributes.get("media_content_id", ""))
    if kind in {"video", "movie", "tvshow", "channel", "url_tts"} or (
        "media-source://tts/" in content or "/api/tts_proxy/" in content
    ):
        return False
    return (
        configured
        or entity.platform == "music_assistant"
        or (
            entity.device_class == "speaker"
            or kind in {"music", "playlist", "track", "radio", "podcast"}
        )
    )


def select_active_player(model, area, configured=None, preferred=None):
    for identifier in (preferred, configured):
        entity = model.entities.get(identifier)
        if entity and player_area(model, entity) == area and is_music_active(
            entity, configured=True
        ):
            return entity, ()
    candidates = [
        entity
        for entity in model.entities.values()
        if entity.domain == "media_player"
        and player_area(model, entity) == area
        and is_music_active(entity)
    ]
    sessions = {}
    for entity in sorted(candidates, key=lambda item: item.id):
        # Invalid group metadata must not make an otherwise visible player disappear.
        try:
            key = playback_scope(entity, model, "media_next_track").key
        except PlaybackError:
            key = ("invalid", entity.id)
        sessions.setdefault(key, entity)
    players = tuple(sessions.values())
    return (players[0], ()) if len(players) == 1 else (None, tuple(e.id for e in players))
