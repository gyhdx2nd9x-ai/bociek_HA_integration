"""Resolve additional HA capabilities against the same exposure and area model."""

from .context import resolve_scope
from .maintenance import plan_maintenance
from .types import Call, Result, Risk
from .weather import unavailable_weather_speech

MEDIA_FEATURES = {
    "media_pause": 1,
    "media_play": 16384,
    "media_next_track": 32,
    "media_previous_track": 16,
    "volume_set": 4,
    "volume_up": 1024,
    "volume_down": 1024,
    "mute": 8,
    "unmute": 8,
}
READ_OPERATIONS = {
    "maintenance",
    "time",
    "date",
    "state",
    "sensor",
    "weather",
    "forecast",
    "calendar",
    "todo_read",
}
GLOBAL_DOMAINS = {"todo", "weather", "calendar", "script", "scene"}
READ_DOMAINS = {"sensor", "binary_sensor", "light", "switch", "cover", "climate", "media_player"}


def plan_capability(cap, request, model):
    base = dict(
        route="ha_native",
        confidence=0.99,
        confidence_class="high",
        conversation_id=request.conversation_id,
    )

    def fail(reason, speech=None, status="clarify"):
        if speech is None and cap.domain == "weather":
            if reason in {"no_targets", "unavailable"}:
                speech = unavailable_weather_speech(cap.operation, cap.kind)
            elif reason == "ambiguous_target":
                speech = "Mam kilka źródeł pogody i nie wiem, którego użyć."
        if speech is None:
            speech = "Doprecyzuj nazwę lub pomieszczenie. Sprawdź udostępnienie encji i etykiety."
        return Result(status, speech, reason=reason, **base)

    if cap.scope not in ("", "house"):
        return fail("invalid_scope", "Ten zakres pytania nie jest obsługiwany.", "blocked")
    house = cap.scope == "house"
    if house:
        allowed_house = (
            cap.operation == "state"
            and cap.name is None
            and (
                (
                    cap.domain == "light"
                    and cap.kind == "on"
                    and cap.query in {"", "any_on", "which_on", "all_off"}
                )
                or (
                    cap.domain == "binary_sensor"
                    and cap.kind in {"window", "door"}
                    and cap.query in {"open", "closed", "all_open", "which_open", "which_closed"}
                )
            )
        )
        if not allowed_house:
            return fail("unsupported_scope", "Ten zakres pytania nie jest obsługiwany.", "blocked")
        if cap.location is not None:
            return fail(
                "conflicting_scope", "Wskaż jedno miejsce: pomieszczenie, piętro lub cały dom."
            )
    if cap.operation == "maintenance":
        return plan_maintenance(cap, request, model)
    if cap.operation in {"time", "date"}:
        return Result("ready", "", **base)
    if house:
        # An explicit whole-house read includes permitted entities without an Area.
        # It never changes the source Area or the scope of any write operation.
        areas = ()
    else:
        scope = resolve_scope(cap, request, model)
        if scope.error:
            return fail(scope.error)
        # Lists/weather/calendars belong to the household unless explicitly named/scoped.
        areas = scope.areas if cap.domain not in GLOBAL_DOMAINS or scope.explicit else ()
    candidates = [e for e in model.entities.values() if e.exposed and "voice_block" not in e.labels]
    if areas:
        candidates = [e for e in candidates if e.area_id in areas]
    elif cap.domain not in GLOBAL_DOMAINS and not cap.name and not house:
        if cap.kind in {"window", "door"}:
            return fail("missing_area", "W którym pomieszczeniu mam sprawdzić?")
        return fail("missing_area")
    if cap.domain:
        candidates = [e for e in candidates if e.domain == cap.domain]
    elif cap.operation == "state":
        candidates = [e for e in candidates if e.domain in READ_DOMAINS]
    if cap.name:
        matches = model.match_entity_name(cap.name)
        candidates = [e for e in candidates if e.id in matches]
    if cap.kind in {"window", "door", "humidity"}:
        candidates = [e for e in candidates if e.device_class == cap.kind]
    if cap.kind == "shopping" and not cap.name:
        candidates = [e for e in candidates if "voice_shopping" in e.labels]
    aggregate = cap.operation == "state" and not cap.name
    if aggregate:
        candidates = [e for e in candidates if "voice_exclude_all" not in e.labels]
    if len(candidates) > 1 and not aggregate:
        primary = [e for e in candidates if "voice_primary" in e.labels]
        if cap.name or len(primary) != 1:
            return fail("ambiguous_target")
        candidates = primary
    if not candidates:
        if cap.domain == "calendar":
            return fail(
                "no_targets",
                "Nie mam dostępnego kalendarza dla tego pytania. "
                "Sprawdź jego udostępnienie asystentowi.",
            )
        if cap.domain == "todo":
            return fail(
                "no_targets",
                "Nie mam dostępnej listy zakupów dla tego polecenia. "
                "Sprawdź jej udostępnienie i przypisanie roli listy zakupów.",
            )
        if cap.kind in {"window", "door"}:
            noun = "okien" if cap.kind == "window" else "drzwi"
            return fail(
                "no_targets",
                f"Nie mam dostępnych czujników {noun} w wybranym miejscu. "
                "Sprawdź, czy są udostępnione asystentowi.",
            )
        if cap.operation == "state" and cap.domain == "light" and cap.kind == "on":
            return fail(
                "no_targets",
                "Nie mam dostępnych świateł do sprawdzenia w wybranym miejscu. "
                "Sprawdź, czy są udostępnione asystentowi.",
            )
        return fail("no_targets")
    entities = tuple(sorted(candidates, key=lambda e: e.id))
    base.update(targets=tuple(e.id for e in entities), area_ids=areas)
    if cap.operation not in {"state", "sensor"} and any(not e.available for e in entities):
        return fail("unavailable")
    if cap.operation in READ_OPERATIONS:
        if (
            cap.operation == "forecast"
            and not entities[0].attributes.get("supported_features", 0) & 1
        ):
            return fail("capability", "To źródło pogody nie udostępnia prognozy dziennej.")
        return Result("ready", "", **base)
    if any("voice_high_risk" in e.labels for e in entities):
        return fail(
            "high_risk_disabled", "Ta encja jest oznaczona jako wysokiego ryzyka.", "blocked"
        )
    entity = entities[0]
    # These services may act on a group internally. Do not infer unseen members.
    if entity.members or len(entity.attributes.get("group_members", ())) > 1:
        return fail("group_unsupported", "Wybierz pojedynczą encję zamiast grupy.", "blocked")
    features = entity.attributes.get("supported_features", 0)
    service, data = cap.operation, {}
    if cap.domain == "media_player":
        required = MEDIA_FEATURES[cap.operation]
        if not features & required:
            return fail("capability", "Odtwarzacz nie obsługuje tej operacji.", "blocked")
        if cap.operation == "volume_set":
            if cap.value is None or not 0 <= cap.value <= 100:
                return fail("range", "Głośność musi wynosić od 0 do 100 procent.", "blocked")
            data = {"volume_level": cap.value / 100}
        elif cap.operation in {"mute", "unmute"}:
            service, data = "volume_mute", {"is_volume_muted": cap.operation == "mute"}
    elif cap.operation == "todo_add":
        if not features & 1:
            return fail("capability", "Ta lista nie obsługuje dodawania pozycji.", "blocked")
        service, data = "add_item", {"item": cap.payload}
    elif cap.operation == "routine":
        if "voice_routine" not in entity.labels:
            return fail(
                "routine_not_approved",
                "Dodaj etykietę voice_routine do zatwierdzonej rutyny.",
                "blocked",
            )
        service = "turn_on"
    elif cap.operation in {"announce", "ask"}:
        required = 1 if cap.operation == "announce" else 2
        if "voice_announce" not in entity.labels or not features & required:
            return fail(
                "satellite_capability",
                "Satelita wymaga etykiety voice_announce i obsługi tej akcji.",
                "blocked",
            )
        if entity.id == request.satellite_id or (
            request.device_id and entity.device_id == request.device_id
        ):
            return fail(
                "same_satellite",
                "Wskaż innego satelitę. Pytanie do tego urządzenia uruchom z automatyzacji HA.",
                "blocked",
            )
        if entity.state != "idle":
            return fail("satellite_busy", "Ten satelita obsługuje teraz rozmowę.")
        if cap.operation == "announce":
            data = {"message": cap.payload}
        else:
            service = "ask_question"
            data = {
                "question": cap.payload,
                "answers": [
                    {"id": "yes", "sentences": ["tak", "tak proszę", "potwierdzam"]},
                    {"id": "no", "sentences": ["nie", "nie dziękuję", "anuluj"]},
                ],
            }
    else:
        return fail("unsupported_capability", status="blocked")
    base["calls"] = (Call(entity.domain, service, (entity.id,), data),)
    base["risk"] = Risk.MEDIUM
    description = {
        "media_pause": "wstrzymać odtwarzanie",
        "media_play": "wznowić odtwarzanie",
        "media_next_track": "przejść do następnego utworu",
        "media_previous_track": "przejść do poprzedniego utworu",
        "volume_up": "zwiększyć głośność",
        "volume_down": "zmniejszyć głośność",
        "volume_set": f"ustawić głośność na {cap.value}%",
        "mute": "wyciszyć",
        "unmute": "przywrócić dźwięk",
        "todo_add": f"dodać: {cap.payload}",
        "routine": "uruchomić rutynę",
        "announce": f"ogłosić: {cap.payload}",
        "ask": f"zadać pytanie: {cap.payload}",
    }[cap.operation]
    return Result(
        "confirm",
        f"Czy {description} — {entity.name}? Powiedz: potwierdzam lub anuluj.",
        reason="confirmation_required",
        **base,
    )


def selected_entities(plan, model):
    return tuple(model.entities[identifier] for identifier in plan.targets)
