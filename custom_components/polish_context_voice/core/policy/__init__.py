"""Explicit capabilities and risk thresholds, independent of parser and LLM."""

import math

from ..capability_catalog import definition_for, prepare_entity
from ..capability_contracts import annotate_capability
from ..communication import ACTION_FAILED_SPEECH
from ..types import Action, Call, Result, Risk

THRESHOLDS = {Risk.LOW: 0.85, Risk.MEDIUM: 0.97, Risk.HIGH: 1.0}


def evaluate(proposal, resolution):
    entities = resolution.entities
    confidence = proposal.confidence
    base = dict(
        targets=tuple(e.id for e in entities),
        area_ids=resolution.area_ids,
        confidence=confidence,
        route=proposal.origin,
        confidence_class="high"
        if confidence >= 0.97
        else "medium"
        if confidence >= 0.85
        else "low",
    )
    if resolution.target_unavailable:
        return Result("clarify", ACTION_FAILED_SPEECH, reason=resolution.error, **base)
    if resolution.error:
        return Result(
            "clarify",
            "Doprecyzuj pomieszczenie lub nazwę urządzenia. "
            "Sprawdź też role głosowe i dostępność encji.",
            reason=resolution.error,
            **base,
        )
    if resolution.state_filtered:
        unknown = resolution.unknown_state_count
        base["response_data"] = {"light_state": {"unknown": unknown}}
        if not entities:
            return Result(
                "answered",
                "Nie udało się sprawdzić, czy pozostałe światła są zapalone." if unknown else
                "Żadne z tych świateł się nie świeci.",
                reason="unknown_light_state" if unknown else "no_lit_lights",
                **base,
            )
    risk = Risk.LOW
    if any(
        e.domain in {"lock", "alarm_control_panel"}
        or e.device_class in {"garage", "gate", "door", "lock"}
        or "voice_high_risk" in e.labels
        for e in entities
    ):
        risk = Risk.HIGH
    elif any(e.domain in {"cover", "climate", "switch", "media_player"} for e in entities):
        risk = Risk.MEDIUM
    base["risk"] = risk
    if risk == Risk.HIGH:
        return Result(
            "blocked",
            "Sterowanie zabezpieczeniami nie jest obsługiwane w MVP.",
            reason="high_risk_disabled",
            **base,
        )
    if not math.isfinite(confidence) or confidence < THRESHOLDS[risk]:
        return Result("clarify", "Powtórz dokładnie polecenie.", reason="confidence", **base)
    calls = []
    definition = definition_for(proposal.action)
    facts = None
    for entity in entities:
        action = proposal.action
        data = {}
        service = action.value
        if action in {Action.ON, Action.OFF}:
            if entity.domain == "fan":
                feature = 32 if action == Action.ON else 16
                if not entity.attributes.get("supported_features", 0) & feature:
                    return Result(
                        "blocked", "Wentylator nie obsługuje tej operacji.",
                        reason="capability", **base,
                    )
            elif entity.domain not in {"light", "switch", "media_player"}:
                return Result(
                    "blocked", "Ta operacja nie jest obsługiwana.", reason="capability", **base
                )
        elif definition is not None:
            prepared = prepare_entity(definition, proposal, entity)
            facts = prepared.facts
            if prepared.status != "ready":
                return annotate_capability(
                    Result(prepared.status, prepared.speech, reason=prepared.reason, **base),
                    definition,
                    facts,
                )
            calls.append(prepared.call)
            continue
        elif action in {Action.OPEN, Action.CLOSE}:
            required = 1 if action == Action.OPEN else 2
            if (
                entity.domain != "cover"
                or not entity.attributes.get("supported_features", 0) & required
            ):
                return Result(
                    "blocked", "Roleta nie obsługuje tej operacji.", reason="capability", **base
                )
        elif action in {Action.COVER_POSITION, Action.FAN_SPEED}:
            domain, feature, device, parameter = (
                ("cover", 4, "Roleta", "position") if action == Action.COVER_POSITION else
                ("fan", 1, "Wentylator", "percentage")
            )
            if (
                entity.domain != domain
                or not entity.attributes.get("supported_features", 0) & feature
            ):
                return Result(
                    "blocked", f"{device} nie obsługuje takiej regulacji.",
                    reason="capability", **base,
                )
            value = proposal.value
            if value is None or not 0 <= value <= 100 or value != int(value):
                return Result(
                    "blocked", "Podaj pełną wartość od 0 do 100 procent.",
                    reason="range", **base,
                )
            data = {parameter: int(value)}
        elif action == Action.SET_TEMPERATURE:
            lower = entity.attributes.get("min_temp", 7)
            upper = entity.attributes.get("max_temp", 30)
            if (
                entity.domain != "climate"
                or not entity.attributes.get("supported_features", 0) & 1
                or proposal.value is None
                or not math.isfinite(proposal.value)
                or not max(7, lower) <= proposal.value <= min(30, upper)
                or entity.attributes.get("temperature_unit", "°C") != "°C"
            ):
                return Result(
                    "blocked",
                    "Temperatura jest poza zakresem lub urządzenie nie obsługuje tej operacji.",
                    reason="range_or_capability",
                    **base,
                )
            data = {"temperature": proposal.value}
        calls.append(Call(
            entity.domain, service, (entity.id,), data,
            required_state="on" if resolution.state_filtered else None,
        ))
    base["calls"] = tuple(calls)
    confirmation = (
        risk == Risk.MEDIUM
        or proposal.origin == "llm"
        or any("voice_confirm" in e.labels for e in entities)
    )
    if confirmation:
        names = ", ".join(e.name for e in entities)
        if proposal.action in {Action.COVER_POSITION, Action.FAN_SPEED}:
            parameter = "pozycję" if proposal.action == Action.COVER_POSITION else "prędkość"
            device = "urządzenia" if len(entities) == 1 else "urządzeń"
            quoted_names = ", ".join(f"„{entity.name}”" for entity in entities)
            prompt = f"Ustawić {parameter} {device} {quoted_names} na {proposal.value:g} procent?"
        else:
            operation = {
                Action.ON: "włączyć",
                Action.OFF: "wyłączyć",
                Action.OPEN: "otworzyć",
                Action.CLOSE: "zamknąć",
                Action.BRIGHTNESS: f"ustawić jasność {proposal.value}%",
                Action.SET_TEMPERATURE: f"ustawić temperaturę {proposal.value}°C",
            }[proposal.action]
            prompt = f"Czy {operation}: {names}? Powiedz „potwierdzam” lub „anuluj”."
        if resolution.unknown_state_count:
            prompt += " Brakuje odczytu stanu pozostałych świateł."
        return annotate_capability(
            Result(
                "confirm",
                prompt,
                reason="confirmation_required",
                **base,
            ),
            definition,
            facts,
        )
    return annotate_capability(Result("ready", "Gotowe.", **base), definition, facts)
