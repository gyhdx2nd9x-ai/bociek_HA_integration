"""Named media shortcuts and room preferences. No provider credentials or streaming."""

import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from .capabilities import MEDIA_FEATURES
from .communication import ACTION_FAILED_SPEECH
from .context import resolve_scope
from .home_model.health import Finding
from .nlu.normalize import normalize
from .playback import PlaybackError, is_music_active, playback_scope, select_active_player
from .player_identity import linked_ha_player_ids, player_area
from .satellite_player import failure_speech, source_player
from .types import Call, Result, Risk

MAX_SHORTCUTS = 64
MEDIA_TYPES = {"music", "playlist", "album", "artist", "track", "radio", "podcast", "audiobook"}
CONTROLS = {
    "wstrzymaj muzyke": "media_pause",
    "zatrzymaj muzyke": "media_pause",
    "pauza": "media_pause",
    "wznow muzyke": "media_play",
    "wznow odtwarzanie": "media_play",
    "zakoncz odtwarzanie": "media_stop",
    "wylacz muzyke": "media_stop",
    "nastepny utwor": "media_next_track",
    "poprzedni utwor": "media_previous_track",
    "ciszej": "volume_down",
    "glosniej": "volume_up",
    "wycisz muzyke": "mute",
    "przywroc dzwiek": "unmute",
}
SHORT_CONTROLS = {
    "dalej": "continue",
    "pomin": "media_next_track",
    "przeskocz": "media_next_track",
    "pauza": "media_pause",
    "stop": "media_stop",
    "wznow": "media_play",
    "graj dalej": "media_play",
    "odtwarzaj dalej": "media_play",
}


def alias_key(text):
    text = re.sub(r"^(?:moja|moje|moj|mojej) ", "", normalize(text))
    text = re.sub(r"^(?:playliste|playlisty)\b", "playlista", text)
    return re.sub(r"^(?:muzyke|muzyki)\b", "muzyka", text)


def aliases(shortcut):
    return {alias_key(s) for s in (shortcut["name"], *shortcut.get("aliases", []))}


def validate_shortcut(value, existing, identifier=None):
    """Validate normalized form input; identifiers must refer to concrete media."""
    name = value.get("name", "").strip()
    names = [name, *value.get("aliases", [])]
    if any(not isinstance(n, str) or not alias_key(n) or len(n) > 100 for n in names):
        return "invalid_music_name"
    if len(names) > 17 or len(existing) >= MAX_SHORTCUTS and identifier not in existing:
        return "music_limit"
    keys = {alias_key(n) for n in names}
    reserved = {"muzyke", "muzyka", "muzyki", "radio", "playliste", "playlista"}
    if keys & reserved or keys & (set(CONTROLS) | set(SHORT_CONTROLS)):
        return "reserved_music_name"
    if any(keys & aliases(s) for key, s in existing.items() if key != identifier):
        return "duplicate_music_alias"
    media = value.get("media", {})
    uri, kind = media.get("media_content_id", ""), media.get("media_content_type", "")
    if not isinstance(uri, str) or not isinstance(kind, str):
        return "invalid_music_media"
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return "invalid_music_media"
    if (
        not uri
        or len(uri) > 2048
        or any(c.isspace() for c in uri)
        or "://" not in uri
        or not parsed.scheme
        or parsed.username
        or parsed.password
        or not kind
        or len(kind) > 100
    ):
        return "invalid_music_media"
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube_page"
    if value.get("backend") not in {"music_assistant", "home_assistant"}:
        return "invalid_music_backend"
    if value["backend"] == "music_assistant" and (
        kind not in MEDIA_TYPES or not value.get("config_entry_id")
    ):
        return "invalid_ma_media"
    return None


@dataclass(frozen=True)
class MusicCommand:
    operation: str
    shortcut: str | None = None
    location: str | None = None
    player_name: str | None = None
    value: int | None = None
    contextual: bool = False
    preferred_player: str | None = None
    selected_player: str | None = None
    backend: str | None = None
    config_entry_id: str | None = None


def parse_music(text, settings):
    """Exact whole utterances; never remove unrecognized trailing speech."""
    value = re.sub(r"^prosze ", "", normalize(text))
    shortcuts = settings.get("music_shortcuts", {})
    index = {}
    for identifier, shortcut in shortcuts.items():
        for name in aliases(shortcut):
            index.setdefault(name, []).append(identifier)

    def body_command(body):
        if body in SHORT_CONTROLS:
            return MusicCommand(SHORT_CONTROLS[body], contextual=True)
        track = re.fullmatch(
            r"(?:(?:pusc|wlacz|odtworz) )?"
            r"(nastepn[ay]|kolejn[ay]|poprzedni|poprzednia)"
            r"(?: (utwor|piosenka|piosenke|kawalek))?",
            body,
        )
        if track:
            operation = (
                "media_previous_track" if track[1].startswith("poprzed") else "media_next_track"
            )
            return MusicCommand(operation, contextual=track[2] is None)
        if body in CONTROLS:
            return MusicCommand(CONTROLS[body])
        if m := re.fullmatch(r"ustaw glosnosc na (\d{1,3})\s*(?:%|procent)", body):
            return MusicCommand("volume_set", value=int(m[1]))
        m = re.fullmatch(r"(?:odpal|puszcz|pusc|odtworz|wlacz|zagraj|graj) (.+)", body)
        if not m:
            return None
        name = alias_key(m[1])
        if name in {"muzyke", "muzyka"}:
            return MusicCommand("start")
        if name in index:
            return (
                MusicCommand("start", index[name][0])
                if len(index[name]) == 1
                else (MusicCommand("ambiguous_shortcut"))
            )
        return None

    # A complete configured title can itself contain a room name.
    if command := body_command(value):
        return command
    if m := re.fullmatch(r"(.+) na odtwarzaczu (.+)", value):
        if command := body_command(m[1]):
            return replace(command, player_name=m[2])
    for m in re.finditer(r" (?:w|we|na) ", value):
        if command := body_command(value[: m.start()]):
            return replace(command, location=value[m.end() :])
    # Recognizable media requests fail closed instead of becoming LLM service calls.
    if re.search(r"\b(?:muzyke|muzyka|playliste|playlista|odtwarzanie)\b", value) and (
        re.match(r"(?:odpal|pusc|odtworz|wlacz|graj|zagraj|przenies|zatrzymaj|wylacz)\b", value)
    ):
        return MusicCommand("unknown_shortcut")
    if any(
        re.match(rf"(?:odpal|pusc|odtworz|wlacz|graj|zagraj) (?:moja )?{re.escape(k)}\b", value)
        for k in index
    ):
        return MusicCommand("unknown_shortcut")
    return None


def room_players(model, area_id, *, backend=None, config_entry_id=None):
    """Find exposed room players from HA's effective Area assignment.

    A configured Boćek room choice remains authoritative. This discovery path is
    only used when that choice is empty, so ordinary HA Areas work without a
    duplicate ``music_rooms`` mapping. When an MA proxy and its HA player are
    both assigned to the Area, prefer the MA representation unless a backend
    was explicitly selected.
    """
    candidates = [
        entity
        for entity in model.entities.values()
        if entity.domain == "media_player"
        and player_area(model, entity) == area_id
        and entity.exposed
        and "voice_block" not in entity.labels
    ]
    if backend == "music_assistant":
        candidates = [
            entity
            for entity in candidates
            if entity.platform == "music_assistant"
            and (not config_entry_id or entity.config_entry_id == config_entry_id)
        ]
    elif backend == "home_assistant":
        candidates = [entity for entity in candidates if entity.platform != "music_assistant"]
    else:
        ma_players = [entity for entity in candidates if entity.platform == "music_assistant"]
        if ma_players:
            linked_ids = {
                identifier
                for entity in ma_players
                for identifier in linked_ha_player_ids(model, entity)
            }
            linked_devices = {entity.device_id for entity in ma_players if entity.device_id}
            candidates = ma_players + [
                entity
                for entity in candidates
                if entity.platform != "music_assistant"
                and entity.id not in linked_ids
                and (not entity.device_id or entity.device_id not in linked_devices)
            ]
    available = [entity for entity in candidates if entity.available]
    return available or candidates


def plan_music(command, request, model, settings):
    base = dict(
        route="music",
        confidence=0.99,
        confidence_class="high",
        conversation_id=request.conversation_id,
    )

    def fail(reason, speech, status="clarify"):
        return Result(status, speech, reason=reason, **base)

    if command.operation in {"unknown_shortcut", "ambiguous_shortcut"}:
        return fail(
            command.operation,
            "Nie rozpoznaję skrótu muzycznego. Podaj nazwę zapisaną w ustawieniach muzyki.",
        )
    scope = resolve_scope(command, request, model)
    if scope.error:
        return fail(scope.error, "Nie rozpoznaję jednoznacznie tego pomieszczenia.")
    rooms = settings.get("music_rooms", {})
    room = rooms.get(scope.areas[0], {}) if len(scope.areas) == 1 else {}
    if command.selected_player:
        entity = model.entities.get(command.selected_player)
        if entity is None or entity.area_id not in scope.areas:
            return fail("music_selection_changed", "Zmienił się wybór głośnika. Powtórz polecenie.")
        room = rooms.get(entity.area_id, {})
    elif command.player_name:
        ids = model.entity_index.get(model.key(command.player_name), set())
        candidates = [
            e
            for e in model.entities.values()
            if e.id in ids and e.domain == "media_player" and e.exposed
        ]
        if scope.explicit:
            candidates = [e for e in candidates if e.area_id in scope.areas]
        if len(candidates) != 1:
            return fail("ambiguous_player", "Podaj jednoznaczną nazwę odtwarzacza.")
        entity = candidates[0]
        room = rooms.get(entity.area_id, {})
    else:
        if len(scope.areas) != 1:
            return fail(
                "missing_music_area", "Podaj pomieszczenie, na przykład: puść muzykę w salonie."
            )
        configured = room.get("player")
        configured_room_player = bool(configured)
        entity = model.entities.get(configured)
        fallback = False
        if not configured:
            shortcut = (
                settings.get("music_shortcuts", {}).get(
                    command.shortcut or room.get("default_shortcut"), {}
                )
                if command.operation == "start"
                else {}
            )
            backend = command.backend or shortcut.get("backend")
            config_entry_id = command.config_entry_id or shortcut.get("config_entry_id")
            if command.operation == "start":
                # Room-assigned speakers outrank the satellite's own player so
                # a soundbar in the same HA Area can be the music destination.
                candidates = room_players(
                    model,
                    scope.areas[0],
                    backend=backend,
                    config_entry_id=config_entry_id,
                )
                satellite_player, satellite_reason = source_player(
                    request,
                    model,
                    scope.areas[0],
                    backend=backend,
                    config_entry_id=config_entry_id,
                )
                if satellite_player:
                    independent = [
                        e
                        for e in candidates
                        if e.id != satellite_player.id
                        and not (
                            e.device_id
                            and satellite_player.device_id
                            and e.device_id == satellite_player.device_id
                        )
                        and not (
                            e.platform == "music_assistant"
                            and satellite_player.id in linked_ha_player_ids(model, e)
                        )
                        and not (
                            satellite_player.platform == "music_assistant"
                            and e.id in linked_ha_player_ids(model, satellite_player)
                        )
                    ]
                    if independent:
                        candidates = independent
                if len(candidates) == 1:
                    entity = candidates[0]
                    reason = ""
                elif len(candidates) > 1:
                    return fail(
                        "ambiguous_room_music_player",
                        "W tym pomieszczeniu jest kilka głośników. Wybierz domyślny "
                        "w ustawieniach muzyki Boćka.",
                    )
                else:
                    entity, reason = satellite_player, satellite_reason
                    fallback = entity is not None and player_area(model, entity) is None
                if entity is None:
                    entity, reason = None, satellite_reason
            else:
                # Controls target the active playback session. Do not replace
                # that resolution with every idle speaker found in the Area.
                entity, reason = source_player(
                    request,
                    model,
                    scope.areas[0],
                    backend=backend,
                    config_entry_id=config_entry_id,
                )
            if not fallback and command.operation == "start" and entity is None:
                return fail(reason, failure_speech(reason))
        if (
            command.operation != "start"
            and (command.contextual or entity is None)
            and not (fallback and is_music_active(entity, configured=True))
        ):
            entity, candidates = select_active_player(
                model, scope.areas[0], configured, command.preferred_player
            )
            if candidates:
                names = " czy ".join(model.entities[i].name[:70] for i in candidates[:4])
                result = fail("ambiguous_music_session", f"Na którym głośniku: {names}?")
                result.response_data = {"player_candidates": candidates}
                result.continue_conversation = len(candidates) <= 4
                return result
            if entity is None:
                return fail("music_not_playing", "Tutaj nic teraz nie gra.")
        if entity is None:
            return fail(
                "missing_music_player",
                "Wybierz domyślny głośnik tego pokoju w ustawieniach muzyki integracji.",
            )
        resolved_area = player_area(model, entity)
        if resolved_area != scope.areas[0] and not (
            (fallback or configured_room_player) and resolved_area is None
        ):
            return fail(
                "music_player_moved",
                "Głośnik zmienił pomieszczenie. Popraw jego przypisanie w ustawieniach muzyki.",
            )
    if entity.domain != "media_player" or not entity.exposed or "voice_block" in entity.labels:
        return fail(
            "music_player_hidden", "Ten głośnik nie jest dostępny dla asystenta.", "blocked"
        )
    if not entity.available:
        return fail("music_player_unavailable", ACTION_FAILED_SPEECH)
    if "voice_high_risk" in entity.labels:
        return fail(
            "high_risk_disabled", "Ten odtwarzacz jest zablokowany przez politykę.", "blocked"
        )
    if command.contextual and not is_music_active(entity, configured=True):
        return fail("music_not_playing", "Tutaj nic teraz nie gra.")
    if command.operation == "continue":
        command = replace(
            command, operation="media_play" if entity.state == "paused" else "media_next_track"
        )
    try:
        playback = playback_scope(entity, model, command.operation)
    except PlaybackError as error:
        return fail(error.reason, error.speech, "blocked")
    shortcut = None
    if command.operation == "start":
        identifier = command.shortcut or room.get("default_shortcut")
        shortcut = settings.get("music_shortcuts", {}).get(identifier)
        if not shortcut:
            return fail(
                "missing_music_shortcut",
                "Podaj nazwę zapisanej playlisty albo wybierz domyślny skrót muzyczny dla pokoju.",
            )
        if validate_shortcut(shortcut, {identifier: shortcut}, identifier):
            return fail("invalid_music_config", "Popraw konfigurację tego skrótu muzycznego.")
        required, service = 512, "play_media"
        data = {k: shortcut["media"][k] for k in ("media_content_id", "media_content_type")}
        speech = f"Uruchamiam: {shortcut['name']}."
    else:
        required = {**MEDIA_FEATURES, "media_stop": 4096}[command.operation]
        service, data = command.operation, {}
        speech = "Gotowe."
        if command.operation == "volume_set":
            if command.value is None or not 0 <= command.value <= 100:
                return fail("range", "Głośność musi wynosić od 0 do 100 procent.", "blocked")
            data = {"volume_level": command.value / 100}
        elif command.operation in {"mute", "unmute"}:
            service, data = "volume_mute", {"is_volume_muted": command.operation == "mute"}
    coordinator = model.entities[playback.coordinator]
    if not coordinator.attributes.get("supported_features", 0) & required:
        return fail("music_capability", "Głośnik nie obsługuje tej czynności.", "blocked")
    base.update(
        targets=playback.members,
        area_ids=tuple(
            sorted(
                {model.entities[i].area_id for i in playback.members if model.entities[i].area_id}
                or set(scope.areas)
            )
        ),
        calls=(
            Call(
                "media_player",
                service,
                (coordinator.id,),
                data,
                playback.members if len(playback.members) > 1 else (),
            ),
        ),
    )
    conflict = command.operation == "start" and entity.state in {"playing", "buffering"}
    confirmation = conflict or any(
        "voice_confirm" in model.entities[i].labels for i in playback.members
    )
    return Result(
        "confirm" if confirmation else "ready",
        ("Głośnik już odtwarza. Zastąpić muzykę?" if conflict else "Wykonać tę czynność?")
        if confirmation
        else speech,
        risk=Risk.MEDIUM if confirmation else Risk.LOW,
        reason="confirmation_required" if confirmation else "",
        response_data={
            "shortcut": command.shortcut or room.get("default_shortcut")
            if command.operation == "start"
            else None,
            "backend": shortcut["backend"] if shortcut else "home_assistant",
            "config_entry_id": shortcut.get("config_entry_id") if shortcut else None,
            "success_speech": speech,
            "current_content": entity.attributes.get("media_content_id") if conflict else None,
            "playback_key": playback.key,
            "selected_player": entity.id,
        },
        **base,
    )


def music_health(model, settings):
    findings = []
    for area_id, room in settings.get("music_rooms", {}).items():
        player = room.get("player", "")
        entity = model.entities.get(player)
        if area_id not in model.areas or player and (entity is None or entity.area_id != area_id):
            findings.append(
                Finding(
                    "music_room_mapping",
                    "warning",
                    (area_id, player),
                    "Popraw przypisanie głośnika muzycznego do pokoju.",
                )
            )
        elif entity is not None and (
            not entity.exposed or "voice_block" in entity.labels or not entity.available
        ):
            findings.append(
                Finding(
                    "music_player_unavailable",
                    "warning",
                    (player,),
                    "Domyślny głośnik jest niedostępny lub niewystawiony do Assist.",
                )
            )
        default = room.get("default_shortcut")
        if default and default not in settings.get("music_shortcuts", {}):
            findings.append(
                Finding(
                    "music_shortcut_missing",
                    "warning",
                    (area_id,),
                    "Domyślny skrót muzyczny już nie istnieje.",
                )
            )
    return findings
