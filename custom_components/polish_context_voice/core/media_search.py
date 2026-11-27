"""Bounded Polish media queries; catalog data remains in Music Assistant."""

import re
from dataclasses import dataclass, replace
from time import monotonic
from urllib.parse import urlsplit

from .context.dialogue import classify_reply, is_confirmation_reply, ordinal_reply, reply_words
from .music import parse_music
from .nlu.normalize import normalize

MAX_RESULTS = 5
MAX_CHOICES = 3
QUERY_LIMIT = 240
PENDING_LIMIT = 128
CHOICE_TTL = 45
CHOICE_SUFFIXES = ("utwor", "piosenka", "wersja", "pozycja", "audiobook", "album")
BUCKETS = {"track": "tracks", "artist": "artists", "album": "albums", "audiobook": "audiobooks"}
SOURCE_ALIASES = {"plex": "plex", "monochrome": "monochrome", "audiobookshelf": "audiobookshelf"}


@dataclass(frozen=True)
class MediaSearchCommand:
    media_type: str
    query: str
    creator: str | None = None
    location: str | None = None
    player_name: str | None = None
    source: str | None = None
    library_only: bool = False
    list_only: bool = False
    resume: bool = False
    quoted: bool = False
    explicit_location: bool = False


@dataclass(frozen=True)
class MediaCandidate:
    uri: str
    name: str
    media_type: str
    version: str = ""
    creators: tuple[str, ...] = ()
    album: str = ""
    authors: tuple[str, ...] = ()

    @property
    def description(self):
        details = [self.name, ", ".join(self.creators or self.authors), self.version]
        return " — ".join(part[:100] for part in details if part)


@dataclass(frozen=True)
class MediaSearchTurn:
    command: MediaSearchCommand
    candidates: tuple[MediaCandidate, ...]
    target_signature: tuple
    expires_at: float
    stage: str = "choice"
    plan_fingerprint: tuple | None = None
    confirmation_prompt: str = ""


def _fold(text):
    # Keep punctuation and original spans; unlike normalize this must not delete titles.
    import unicodedata

    value = text.lower().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def parse_media_search(text, settings=None):
    """Parse bounded Polish media requests; existing named shortcuts keep priority."""
    settings = settings or {}
    raw = " ".join(text.strip().strip(".!?").split())
    if len(raw) > 600:
        return None
    raw = re.sub(r"^prosz[eę][, ]+", "", raw, flags=re.I)
    raw = re.sub(
        r"^(?:czy\s+)?(?:możesz|mozesz|mógłbyś|moglbys|mogłabyś|moglabys)\s+(?:mi\s+)?",
        "",
        raw,
        flags=re.I,
    )
    value = _fold(raw)
    verbs = (
        r"(?:puszcz|pusc(?:ic)?|odtworz(?:yc)?|odpal(?:ic)?|zagraj|zagrac|"
        r"graj|grac|wlacz(?:yc)?|wznow(?:ic)?)"
    )
    start = re.match(rf"^{verbs}(?: mi)?(?: teraz)? ", value)
    # Ordinary home commands should not rebuild the named-shortcut index.
    if not start and not re.match(r"^(?:jakie mam|jakie sa|pokaz|wyszukaj|znajdz) ", value):
        return None
    if start and start[0].split()[0] in {"wlacz", "wlaczyc"}:
        # A plain "włącz muzykę" and a named room shortcut belong to the usual route.
        tail = value[start.end() :]
        explicit_item = re.match(
            r"(?:utwor(?:u)?|piosenk(?:e|a|i)|kawal(?:ek|ka)|album(?:u)?|"
            r"audiobook(?:a|i|ow)?|ksiazk(?:e|a|i) audio)\b",
            tail,
        )
        named_creator = re.match(
            r"(?:muzyke|muzyki|utwory|piosenki|wykonawce|wykonawca|"
            r"zespol|artyste|artysta) \S",
            tail,
        )
        if not (explicit_item or named_creator):
            return None
    existing = parse_music(text, settings)
    if existing and existing.operation != "unknown_shortcut":
        return None
    listing = re.fullmatch(
        r"(?:jakie mam|jakie sa|pokaz|wyszukaj|znajdz) "
        r"(?:audiobooki|ksiazki audio)(?: (?:autora|autorki|napisane przez))? (.+)",
        value,
    )
    if listing:
        return MediaSearchCommand("audiobook", raw[listing.start(1) :], list_only=True)
    match = re.fullmatch(rf"({verbs})(?: mi)?(?: teraz)? (.+)", value)
    if not match:
        return None
    body = raw[match.start(2) :]
    folded = _fold(body)
    kind, creator, quoted = None, None, False
    patterns = (
        (
            "audiobook",
            r"(?:audiobook(?:a|i|ow)?|ksiazk(?:e|a|i) audio)(?: (.*))?",
        ),
        (
            "artist",
            r"(?:muzyke|muzyki|utwory|piosenki) "
            r"(?:(?:wykonawcy|zespolu|artysty|artystki) )?(.*)",
        ),
        ("artist", r"(?:wykonawce|wykonawca|zespol|artyste|artysta) (.+)"),
        ("album", r"album(?:u)?(?: (.*))?"),
        ("track", r"(?:utwor(?:u)?|piosenk(?:e|a)|kawal(?:ek|ka))(?: (.*))?"),
    )
    for candidate, pattern in patterns:
        if typed := re.fullmatch(pattern, folded):
            kind = candidate
            body = body[typed.start(1) :] if typed[1] is not None else ""
            break
    if kind is None:
        # 'Włącz lampę' and generic/default music must stay on their native routes.
        if match[1] not in {
            "pusc",
            "puscic",
            "odtworz",
            "odtworzyc",
            "odpal",
            "odpalic",
            "zagraj",
            "zagrac",
            "graj",
            "grac",
        } or re.search(
            r"\b(?:muzyk[aei]|playliste|radio|odtwarzanie|swiatla|swiatlo|lampe|dalej)\b",
            folded,
        ):
            return None
        kind = "track"
    if kind == "artist" and not body:
        # "Puść muzykę" keeps its configured default; it is not an incomplete search.
        return None
    if trailing := re.fullmatch(r"(.+) na odtwarzaczu (.+)", _fold(body)):
        body, player = (
            body[: trailing.start(2)].removesuffix("na odtwarzaczu ").strip(),
            body[trailing.start(2) :],
        )
    else:
        player = None
    source, library_only = None, False
    aliases = {**SOURCE_ALIASES, **settings.get("media_search_source_aliases", {})}
    for alias, identifier in sorted(aliases.items(), key=lambda item: -len(item[0])):
        suffix = " z " + _fold(alias)
        if _fold(body).endswith(suffix):
            source, body = identifier, body[: -len(suffix)].strip()
            break
    if _fold(body).endswith(" z mojej biblioteki"):
        library_only, body = True, body[: -len(" z mojej biblioteki")].strip()
    if kind in {"track", "album", "audiobook"}:
        by = (
            r" (?:autora|autorki|autorstwa|napisanego przez|napisanej przez) (.+)$"
            if kind == "audiobook"
            else r" (?:wykonawcy|zespolu|artysty|artystki) (.+)$"
        )
        if authored := re.search(by, _fold(body)):
            creator, body = body[authored.start(1) :], body[: authored.start()].strip()
    pairs = (("„", "”"), ('"', '"'), ("«", "»"))
    for left, right in pairs:
        if body.startswith(left) and right in body[1:]:
            end = body.index(right, 1)
            remainder = body[end + 1 :].strip()
            if not remainder:
                body, quoted = body[1:end], True
            elif re.match(r"^(?:w|we|na) ", _fold(remainder)):
                # An explicit suffix outside quotation marks cannot be part of the title.
                return MediaSearchCommand(
                    kind,
                    body[1:end],
                    creator=creator,
                    location=re.sub(r"^(?:w|we|na) ", "", remainder, flags=re.I),
                    player_name=player,
                    source=source,
                    library_only=library_only,
                    resume=match[1] == "wznow",
                    quoted=True,
                )
    if len(body) > QUERY_LIMIT or re.search(
        r"\b(?:i|a potem) (?:wlacz|wylacz|zamknij|otworz)\b", _fold(body)
    ):
        return None
    return MediaSearchCommand(
        kind,
        body,
        creator=creator,
        player_name=player,
        source=source,
        library_only=library_only,
        resume=match[1] in {"wznow", "wznowic"},
        quoted=quoted,
    )


def with_location(command, model):
    """Resolve only known trailing Area/Floor aliases, leaving title contents intact."""
    if command.location or command.list_only:
        return command
    for field in ("creator", "query"):
        if command.quoted and field == "query":
            continue
        value = getattr(command, field)
        if not value:
            continue
        if _fold(value).endswith(" tutaj"):
            command = replace(command, **{field: value[:-6].strip()})
            break
        for match in re.finditer(r" (?:w|we|na) ", _fold(value)):
            location = value[match.end() :]
            explicit = _fold(location).startswith("pomieszczeniu ")
            if explicit:
                location = location[len("pomieszczeniu ") :]
            if explicit or model.location_index.get(model.location_key(location)):
                command = replace(
                    command,
                    **{field: value[: match.start()].strip()},
                    location=location,
                    explicit_location=explicit,
                )
                break
        if command.location:
            break
    # Source suffix may precede the room suffix; process it after resolving that room.
    for field in ("creator", "query"):
        value = getattr(command, field)
        if not value:
            continue
        if _fold(value).endswith(" z mojej biblioteki"):
            command = replace(
                command,
                **{field: value[: -len(" z mojej biblioteki")].strip()},
                library_only=True,
            )
            continue
        for alias, source in SOURCE_ALIASES.items():
            suffix = " z " + alias
            if _fold(value).endswith(suffix):
                command = replace(command, **{field: value[: -len(suffix)].strip()}, source=source)
                break
    return command


def candidates_from_response(response, command):
    """Accept only concrete MA URIs; malformed or oversized results are ignored."""
    if not isinstance(response, dict):
        return ()
    rows = response.get(BUCKETS[command.media_type], ())
    if not isinstance(rows, (list, tuple)):
        return ()
    found = {}
    for row in rows[:MAX_RESULTS]:
        if not isinstance(row, dict):
            continue
        uri, name = row.get("uri"), row.get("name")
        if not isinstance(uri, str) or not isinstance(name, str) or not name.strip():
            continue
        try:
            parsed = urlsplit(uri)
        except ValueError:
            continue
        if (
            not 0 < len(uri) <= 2048
            or any(c.isspace() for c in uri)
            or not parsed.scheme
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.scheme in {"http", "https", "file", "javascript", "data", "media-source"}
            or parsed.netloc != command.media_type
            or not parsed.path.strip("/")
        ):
            continue
        if row.get("media_type", command.media_type) != command.media_type:
            continue

        def names(key, row=row):
            values = row.get(key, ())
            if not isinstance(values, (tuple, list)):
                return ()
            return tuple(
                (value.get("name", "") if isinstance(value, dict) else value)[:150]
                for value in values[:5]
                if isinstance(value, str)
                or isinstance(value, dict)
                and isinstance(value.get("name"), str)
            )

        album = row.get("album", {})
        found[uri] = MediaCandidate(
            uri,
            name[:240],
            command.media_type,
            str(row.get("version", ""))[:100],
            names("artists"),
            str(album.get("name", ""))[:150] if isinstance(album, dict) else "",
            names("authors"),
        )
    return tuple(found.values())


def exact_candidates(candidates, command):
    title = normalize(command.query)
    matches = tuple(item for item in candidates if normalize(item.name) == title)
    if command.creator and command.media_type != "audiobook":
        matches = tuple(
            item
            for item in matches
            if normalize(command.creator) in {normalize(x) for x in item.creators}
        )
    if command.creator and command.media_type == "audiobook":
        verified = tuple(
            item
            for item in matches
            if normalize(command.creator) in {normalize(x) for x in item.authors}
        )
        unknown = tuple(item for item in matches if not item.authors)
        return verified or unknown
    return matches


def source_candidates(candidates, source):
    if source is None:
        return candidates
    return tuple(
        item for item in candidates if urlsplit(item.uri).scheme.split("--", 1)[0] == source
    )


def selected_index(text, candidates):
    index, attempted = ordinal_reply(text, suffixes=CHOICE_SUFFIXES)
    if attempted:
        return index if index is not None and index < len(candidates) else None
    if len(candidates) == 1 and is_confirmation_reply(text):
        return 0
    value = reply_words(text)
    if not value:
        return None
    indices = [
        i for i, item in enumerate(candidates)
        if value in {reply_words(item.name), reply_words(item.description)}
    ]
    return indices[0] if len(indices) == 1 else None


def follows(pending, request):
    turn = pending.get(request.session_key)
    if turn is None:
        return False
    if turn.expires_at <= monotonic():
        pending.pop(request.session_key, None)
        return False
    if classify_reply(request.text) is not None:
        return True
    if getattr(turn, "stage", "choice") == "title":
        value = normalize(request.text)
        if not value or len(value) > QUERY_LIMIT or len(value.split()) > 40:
            return False
        if re.match(r"^(?:czy|jaka|jaki|jakie|co|kto|gdzie|kiedy|dlaczego|ile)\b", value):
            return False
        # A pending title must not swallow a fresh HA command, calendar, timer, or Stoic request.
        from .calendar import parse_calendar
        from .nlu import parse as parse_home_command
        from .nlu.capabilities import parse_capability
        from .nlu.timers import is_timer_request
        from .stoic import parse_stoic

        if (
            parse_home_command(request.text)
            or parse_capability(request.text)
            or parse_calendar(request.text)
            or is_timer_request(request.text)
            or parse_stoic(request.text)
        ):
            return False
        music = parse_music(request.text, {})
        return music is None or music.operation == "unknown_shortcut"
    _, attempted = ordinal_reply(request.text, suffixes=CHOICE_SUFFIXES)
    return attempted or selected_index(request.text, turn.candidates) is not None
