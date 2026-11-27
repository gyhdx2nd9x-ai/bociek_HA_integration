"""Read-only household maintenance from exposed HA state and device classes."""

from .context import Scope, resolve_scope
from .speech import rounded
from .types import Result

DEFAULT_BATTERY_THRESHOLD = 20
MONITORED_DOMAINS = {
    "sensor",
    "binary_sensor",
    "light",
    "switch",
    "cover",
    "climate",
    "fan",
    "lock",
    "media_player",
    "vacuum",
    "humidifier",
    "water_heater",
    "assist_satellite",
    "weather",
}


def valid_threshold(value):
    return type(value) in (int, float) and 1 <= value <= 50 and int(value) == value


def is_battery(entity):
    return entity.device_class == "battery" and entity.domain in {"sensor", "binary_sensor"}


def plan_maintenance(cap, request, model):
    # A household review is global by user preference, even when a satellite is supplied.
    scope = (
        resolve_scope(cap, request, model)
        if cap.location and cap.location not in {"domu", "calym domu"}
        else Scope()
    )
    base = dict(
        route="ha_native",
        confidence=0.99,
        confidence_class="high",
        conversation_id=request.conversation_id,
        area_ids=scope.areas,
    )
    if scope.error:
        return Result(
            "clarify",
            "Nie rozpoznaję tego miejsca. Podaj nazwę pomieszczenia lub piętra.",
            reason=scope.error,
            **base,
        )
    candidates = tuple(
        sorted(
            e.id
            for e in model.entities.values()
            if e.exposed
            and not e.labels & {"voice_block", "voice_exclude_all"}
            and (not scope.areas or e.area_id in scope.areas)
            and (is_battery(e) if cap.kind == "battery" else e.domain in MONITORED_DOMAINS)
        )
    )
    if not candidates:
        what = "czujników baterii" if cap.kind == "battery" else "encji do sprawdzenia"
        return Result(
            "clarify",
            f"Nie mam udostępnionych {what} w tym zakresie. "
            "Udostępnij je asystentowi w Home Assistant.",
            reason="no_maintenance_sources",
            **base,
        )
    return Result("ready", "", targets=candidates, **base)


def _finding(entity, threshold):
    if entity.state == "unavailable":
        return {"code": "unavailable"}
    if not entity.available or entity.state == "unknown":
        return {"code": "unknown"}
    if entity.domain == "sensor" and entity.device_class == "battery":
        try:
            value = float(entity.state)
            if entity.attributes.get("unit_of_measurement") != "%" or not 0 <= value <= 100:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return {"code": "invalid_battery_reading"}
        return {"code": "low_battery", "percent": value} if value <= threshold else None
    if entity.domain == "binary_sensor":
        code = {
            "battery": ("on", "low_battery"),
            "problem": ("on", "problem"),
            "connectivity": ("off", "disconnected"),
        }.get(entity.device_class)
        if code:
            if entity.state not in {"on", "off"}:
                return {"code": "unknown"}
            if entity.state == code[0]:
                return {"code": code[1]}
    return None


def maintenance_report(
    kind, entities, *, threshold=DEFAULT_BATTERY_THRESHOLD, area_ids=(), area_names=None
):
    """Group findings by registry device, never by similar names or unexposed siblings."""
    threshold = int(threshold) if valid_threshold(threshold) else DEFAULT_BATTERY_THRESHOLD
    groups = {}
    for entity in entities:
        finding = _finding(entity, threshold)
        if not finding:
            continue
        key = ("device", entity.device_id) if entity.device_id else ("entity", entity.id)
        group = groups.setdefault(
            key,
            {
                "device_id": entity.device_id,
                "name": entity.device_name or entity.name,
                "area_ids": [],
                "area_names": [],
                "findings": [],
            },
        )
        if entity.area_id and entity.area_id not in group["area_ids"]:
            group["area_ids"].append(entity.area_id)
            if name := (area_names or {}).get(entity.area_id):
                group["area_names"].append(name)
        group["findings"].append({"entity_id": entity.id, **finding})
    # Report known problems first but always mention omitted/missing data.
    priority = {"problem": 0, "disconnected": 1, "low_battery": 2, "unavailable": 3}
    ordered = sorted(
        groups.values(),
        key=lambda g: (
            min(priority.get(f["code"], 4) for f in g["findings"]),
            g["name"],
            g["findings"][0]["entity_id"],
        ),
    )
    missing = sum(
        f["code"] in {"unknown", "unavailable", "invalid_battery_reading"}
        for group in ordered
        for f in group["findings"]
    )
    if not entities:
        speech = "Nie mam danych do takiego przeglądu."
    elif not ordered:
        speech = (
            "W dostępnych odczytach nie widzę słabych baterii."
            if kind == "battery"
            else "W dostępnych danych nie widzę zgłoszonych problemów."
        )
    else:
        speech = "Do sprawdzenia: " + " ".join(_describe(g) for g in ordered[:5])
        if len(ordered) > 5:
            speech += f" Lista zawiera jeszcze {len(ordered) - 5} pozycji."
            if missing:
                speech += f" W całym przeglądzie brakuje wiarygodnych odczytów: {missing}."
    return speech, {
        "kind": kind,
        "scope": "areas" if area_ids else "home",
        "area_ids": list(area_ids),
        "battery_threshold_percent": threshold,
        "checked_entities": len(entities),
        "battery_entities": sum(is_battery(e) for e in entities),
        "problem_entities": sum(
            e.domain == "binary_sensor" and e.device_class == "problem" for e in entities
        ),
        "connectivity_entities": sum(
            e.domain == "binary_sensor" and e.device_class == "connectivity" for e in entities
        ),
        "missing_readings": missing,
        "groups_with_findings": len(ordered),
        "groups": ordered,
    }


def _describe(group):
    findings = group["findings"]
    codes = {f["code"] for f in findings}
    descriptions = []
    for code, text in (
        ("problem", "zgłoszony problem"),
        ("disconnected", "brak połączenia"),
        ("low_battery", "słaba bateria"),
        ("unavailable", "stan niedostępny w Home Assistant"),
        ("unknown", "brak aktualnego odczytu"),
        ("invalid_battery_reading", "brak prawidłowego odczytu baterii"),
    ):
        if code in codes:
            if code == "low_battery":
                levels = [f["percent"] for f in findings if "percent" in f]
                if levels:
                    text += f", {rounded(min(levels))} procent"
            descriptions.append(text)
    place = ", ".join(name[:100] for name in group["area_names"])
    location = f", pomieszczenie: {place}" if place else ", bez przypisanego pomieszczenia"
    # Legacy callers without a registry map retain their prior wording.
    if group["area_ids"] and not group["area_names"]:
        location = ""
    return f"{group['name'][:100]}{location} — {', '.join(descriptions)}."
