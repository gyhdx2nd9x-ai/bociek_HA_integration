"""One read-only control preview for both Home Assistant interfaces."""

from ..core.engine import Engine
from ..core.home_model.light_profiles import matches_light_role
from ..core.home_model.role_catalog import CATALOG
from ..core.nlu import LIT_KINDS
from ..core.types import Action, Request


def explain_trial(model, result):
    trace = getattr(result, "_resolution_trace", None)
    if trace is None:
        return ["Wpisz pełne polecenie sterujące, np. „zapal światło w kuchni”."]
    lines = []
    if trace.location_source == "utterance":
        lines.append("Zakres pochodzi z nazwy pokoju lub piętra podanej w poleceniu.")
    elif trace.location_source == "source":
        lines.append("Zakres pochodzi z pokoju wybranego w formularzu.")

    proposal = trace.proposal
    roots = [model.entities[identifier] for identifier in trace.roots]
    role = proposal.qualifier
    if trace.purpose_alias:
        role = None
        if result.targets:
            lines.append("Światło pasuje do nazwy lub aliasu skonfigurowanego w Home Assistant.")
    elif proposal.kind == "named" and roots and result.targets:
        name = proposal.name or ""
        root = roots[0]  # Named resolution can select only one root.
        if root.id not in model.match_entity_name(name):
            name = name.partition(" ")[2]
        if model.key(name) == model.key(root.name):
            lines.append("Dopasowana nazwa encji to „" + root.name + "”.")
        elif alias := next((a for a in root.aliases if model.key(a) == model.key(name)), None):
            lines.append("Dopasowany alias w Home Assistant to „" + alias + "”.")
        else:
            lines.append("Dopasowano nazwę lub alias z uwzględnieniem polskiej odmiany.")
    elif trace.single_light_fallback:
        if not trace.primary:
            lines.append("W tym pokoju jest tylko jedna lampa udostępniona Assist.")
    elif proposal.kind in {"light", "lit_lights"} and role is None:
        role = "voice_all_off" if proposal.action == Action.OFF else "voice_default_on"
        if (
            proposal.action == Action.OFF and roots
            and not any(matches_light_role(root.labels, role) for root in roots)
        ):
            role = None
            lines.append(
                "Bez ról oświetlenia polecenie wyłączenia obejmuje wszystkie światła w zakresie."
            )
    if proposal.kind == "safe_all":
        role = "voice_safe_all"
    if trace.primary:
        role = "voice_primary"
    if role in CATALOG:
        lines.append("Rola użyta do wyszukania encji to „" + CATALOG[role].title + "”.")
    if trace.preferred_light:
        preferred = model.entities[trace.preferred_light].name
        lines.append(f"Domyślna opcja to „{preferred}”. W rozmowie możesz wybrać „tę domyślną”.")
    elif trace.light_preferences_conflict:
        lines.append("Kilka pasujących lamp ma etykietę urządzenia preferowanego.")
    if proposal.kind in LIT_KINDS:
        lines.append("Plan obejmuje tylko światła, które według Home Assistant są włączone.")
        if (unknown := (result.response_data or {}).get("light_state", {}).get("unknown")):
            lines.append(f"Światła bez odczytu stanu ({unknown}).")

    if result.targets and any(root.members for root in roots):
        names = ", ".join(root.name for root in roots[:5] if root.members)
        lines.append("Wybrane grupy rozwinięto do ich poszczególnych urządzeń. " + names)
    counts = (
        (trace.not_exposed, "Pasujące encje nieudostępnione w Assist"),
        (trace.outside_scope, "Dopasowania nazwy poza wybranym pomieszczeniem lub piętrem"),
        (trace.blocked, "Pasujące encje z blokadą Boćka"),
        (trace.excluded, "Pasujące encje pomijane w akcjach zbiorczych"),
        (trace.unavailable, "Pasujące encje bez dostępnego stanu w Home Assistant"),
    )
    lines.extend(f"{label} ({count})." for count, label in counts if count)
    hint = {
        "unknown_location": "Sprawdź nazwę lub alias pomieszczenia albo piętra w Home Assistant.",
        "ambiguous_location": "Podaj nazwę lub alias, który wskazuje jeden obszar HA.",
        "missing_area": "Wybierz pokój w formularzu albo wskaż go w poleceniu.",
        "ambiguous_target": "Użyj nazwy lub aliasu wskazującego jedno urządzenie.",
        "cross_area_group": "Grupa zawiera urządzenie spoza zakresu polecenia.",
        "group_cycle": "Grupa oświetlenia zawiera odwołanie do samej siebie.",
        "unknown_group_member": "Grupa zawiera nierozpoznane lub niezgodne urządzenie.",
        "unsafe_group_member": "Urządzenie z tej grupy jest pomijane w akcjach zbiorczych.",
    }.get(result.reason)
    member_hint = {
        "not_exposed": "Co najmniej jedno urządzenie z grupy nie jest udostępnione w Assist.",
        "unavailable": "Co najmniej jedno urządzenie z grupy jest niedostępne.",
        "blocked": "Co najmniej jedno urządzenie z grupy ma blokadę Boćka.",
    }.get(trace.member_problem)
    lines.extend(message for message in (hint, member_hint) if message)
    if result.reason == "no_targets" and not any(count for count, _ in counts):
        lines.append("W tym zakresie żadna encja nie pasuje do polecenia.")
    return lines


async def preview(model, text, area, *, names=None):
    result = await Engine(lambda: model, explain_resolution=True).process(
        Request(text, source_area=area, dry_run=True)
    )
    trace = getattr(result, "_resolution_trace", None)
    area_ids = result.area_ids or (trace.areas if trace else ())
    names = names or {}
    return {
        "speech": result.speech,
        "status": result.status,
        "targets": [
            names.get(identifier, model.entities[identifier].name) for identifier in result.targets
            if identifier in model.entities
        ],
        "areas": [model.areas[key].name for key in area_ids if key in model.areas],
        "explanation": explain_trial(model, result),
        "actions": list(dict.fromkeys(f"{c.domain}.{c.service}" for c in result.calls)),
        "preference_conflict": bool(trace and trace.light_preferences_conflict),
    }
