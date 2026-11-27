"""Resolve only exposed entities. Aggregate semantics are deliberately asymmetric."""

from dataclasses import dataclass, replace

from ..context import resolve_scope
from ..home_model.light_profiles import has_default_light, lights_for_command, matches_light_role
from ..nlu import LIGHTS, LIT_KINDS, SINGULAR
from ..types import Action, Proposal

WINDOW_COVER_CLASSES = {None, "awning", "blind", "curtain", "shade", "shutter"}


@dataclass
class Resolution:
    entities: tuple = ()
    area_ids: tuple[str, ...] = ()
    error: str | None = None
    aggregate: bool = False
    candidates: tuple[str, ...] = ()
    state_filtered: bool = False
    unknown_state_count: int = 0
    target_unavailable: bool = False


@dataclass
class ResolutionTrace:
    """Optional evidence for the local configuration preview only."""

    proposal: Proposal | None = None
    location_source: str = ""
    areas: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()
    not_exposed: int = 0
    outside_scope: int = 0
    blocked: int = 0
    unavailable: int = 0
    excluded: int = 0
    purpose_alias: bool = False
    primary: bool = False
    single_light_fallback: bool = False
    preferred_light: str | None = None
    light_preferences_conflict: bool = False
    member_problem: str = ""


def named_matches(proposal, model):
    name = proposal.name or ""
    if matches := model.match_entity_name(name):
        return matches
    # "Światła nad stołem" may refer to the shorter HA alias "nad stołem".
    parts = name.split(maxsplit=1)
    if len(parts) == 2 and parts[0] in LIGHTS | SINGULAR:
        return {
            identifier
            for identifier in model.match_entity_name(parts[1])
            if identifier.startswith("light.")
        }
    return set()


def resolve(proposal, request, model, selected_ids=(), *, trace=None, selected_leaves=()):
    scope = resolve_scope(proposal, request, model)
    if trace is not None:
        trace.proposal = proposal
        trace.areas = scope.areas
        if proposal.location is not None:
            trace.location_source = "utterance"
        elif scope.areas and request.source_area is not None:
            trace.location_source = "source"
    if scope.error:
        return Resolution(error=scope.error)
    entities = [e for e in model.entities.values() if e.exposed]
    if scope.areas:
        entities = [e for e in entities if e.area_id in scope.areas]
    elif proposal.kind not in {"named", "purpose_light"}:
        return Resolution(error="missing_area")
    aggregate = proposal.kind not in {
        "named", "single_light", "sensor", "climate", "single_cover", "fan",
    }
    visible = entities
    purpose_aliases = ()
    if proposal.kind == "purpose_light":
        # An explicit name or alias in this scope takes precedence over the role.
        # Keep blocked or hidden matches in the ranking to avoid switching to another light.
        if proposal.name:
            purpose_aliases = {
                key for key in named_matches(proposal, model)
                if model.entities[key].domain == "light"
                and (not scope.areas or model.entities[key].area_id in scope.areas)
            }
        if not scope.areas and not purpose_aliases:
            return Resolution(error="missing_area")
        if purpose_aliases or proposal.prefer_single_light:
            aggregate = False
    entities = _select(proposal, model, visible, purpose_aliases)
    if (
        proposal.kind == "light" and proposal.prefer_single_light
        and not entities and len(scope.areas) == 1
    ):
        chosen, primary = _single_light_fallback(model, visible, scope.areas[0])
        if chosen is not None:
            entities = [chosen]
            aggregate = False
            if trace is not None:
                trace.single_light_fallback = True
                trace.primary = primary
    if trace is not None:
        trace.roots = tuple(entity.id for entity in entities)
        trace.purpose_alias = bool(purpose_aliases)
        if not entities:
            hidden = [
                entity for entity in model.entities.values()
                if not entity.exposed and (not scope.areas or entity.area_id in scope.areas)
            ]
            trace.not_exposed = sum(
                not entity.exposed
                for entity in _select(proposal, model, visible + hidden, purpose_aliases)
            )
            if proposal.kind == "named" and scope.areas:
                matches = named_matches(proposal, model)
                trace.outside_scope = sum(
                    entity.exposed and entity.area_id not in scope.areas
                    and (not proposal.qualifier or matches_light_role(
                        entity.labels, proposal.qualifier,
                    ))
                    for entity in (model.entities[identifier] for identifier in matches)
                )
        trace.blocked = sum("voice_block" in entity.labels for entity in entities)
        trace.excluded = sum(
            aggregate and "voice_exclude_all" in entity.labels for entity in entities
        )
        trace.unavailable = sum(not entity.available for entity in entities)
    if aggregate:
        entities = [
            e
            for e in entities
            if "voice_exclude_all" not in e.labels and "voice_block" not in e.labels
        ]
    if selected_ids:
        # Selection may narrow the original query, never introduce another target.
        if not set(selected_ids) <= {e.id for e in entities}:
            return Resolution(area_ids=scope.areas, error="selection_changed")
        entities = [e for e in entities if e.id in selected_ids]
    if len(entities) > 1 and not aggregate:
        primary = [e for e in entities if "voice_primary" in e.labels]
        use_primary = proposal.kind in {"sensor", "climate"} or (
            len(scope.areas) == 1
            and proposal.action in {Action.ON, Action.OFF}
            and (proposal.kind == "single_light" or (
                proposal.kind == "purpose_light" and not purpose_aliases
            ))
        )
        if use_primary and len(primary) == 1:
            entities = primary
            if trace is not None:
                trace.primary = True
        else:
            return Resolution(
                area_ids=scope.areas,
                error="ambiguous_target",
                candidates=tuple(sorted(e.id for e in entities)),
            )
    if not entities:
        return Resolution(area_ids=scope.areas, error="no_targets")
    if trace is not None:
        trace.roots = tuple(entity.id for entity in entities)
    state_filtered = proposal.kind in LIT_KINDS and not selected_leaves
    resolved = _expand_targets(entities, scope.areas, model, aggregate, trace, state_filtered)
    if selected_leaves and not resolved.error:
        leaves = {e.id: e for e in resolved.entities}
        if not set(selected_leaves) <= leaves.keys():
            return Resolution(area_ids=scope.areas, error="selection_changed")
        resolved.entities = tuple(leaves[identifier] for identifier in selected_leaves)
    return resolved


def _single_light_fallback(model, visible, area):
    if has_default_light(model, area):
        return None, False
    lights = [entity for entity in visible if entity.domain == "light"]
    if len(lights) == 1:
        return lights[0], False
    primary = [entity for entity in lights if "voice_primary" in entity.labels]
    return (primary[0], True) if len(primary) == 1 else (None, False)


def _select(proposal, model, entities, purpose_aliases):
    if purpose_aliases:
        entities = [e for e in entities if e.id in purpose_aliases]
    elif proposal.kind == "named":
        matches = named_matches(proposal, model)
        entities = [e for e in entities if e.id in matches]
        if proposal.qualifier:
            entities = [e for e in entities if matches_light_role(e.labels, proposal.qualifier)]
    elif proposal.kind in {"all_lights", "all_lit_lights"}:
        entities = [e for e in entities if e.domain == "light"]
        if proposal.qualifier:
            entities = [e for e in entities if matches_light_role(e.labels, proposal.qualifier)]
    elif proposal.kind in {"light", "lit_lights", "purpose_light"}:
        entities = lights_for_command(entities, proposal.action, proposal.qualifier)
    elif proposal.kind == "safe_all":
        entities = [
            e
            for e in entities
            if "voice_safe_all" in e.labels and e.domain in {"light", "switch", "media_player"}
        ]
    elif proposal.kind == "single_light":
        entities = [e for e in entities if e.domain == "light"]
    elif proposal.kind in {"cover", "single_cover"} and proposal.origin == "fast":
        entities = [
            e for e in entities if e.domain == "cover" and e.device_class in WINDOW_COVER_CLASSES
        ]
    elif proposal.kind == "sensor":
        entities = [e for e in entities if e.domain == "sensor" and e.device_class == "temperature"]
    else:
        entities = [e for e in entities if e.domain == proposal.kind]
    return entities


def _expand_targets(entities, areas, model, aggregate, trace, state_filtered):
    # Expand groups explicitly so HA cannot silently include hidden/cross-area members.
    expanded = {}
    unknown = set() if state_filtered else None

    def expand(entity, seen, guards=frozenset()):
        entity = replace(entity, labels=entity.labels | guards)
        if entity.id in seen:
            return "group_cycle"
        if not entity.exposed or "voice_block" in entity.labels:
            if trace is not None and seen:
                trace.member_problem = "blocked" if entity.exposed else "not_exposed"
            return "ineligible_target"
        if not entity.available and (not state_filtered or entity.members):
            if trace is not None and seen:
                trace.member_problem = "unavailable"
            return "unavailable"
        if aggregate and "voice_exclude_all" in entity.labels:
            return "unsafe_group_member"
        if areas and entity.area_id not in areas:
            return "cross_area_group"
        if entity.members:
            for member in entity.members:
                child = model.entities.get(member)
                if child is None or child.domain != entity.domain:
                    return "unknown_group_member"
                error = expand(
                    child,
                    seen | {entity.id},
                    entity.labels
                    & {"voice_confirm", "voice_high_risk", "voice_block", "voice_exclude_all"},
                )
                if error:
                    return error
        else:
            if state_filtered:
                if not entity.available or entity.state not in {"on", "off"}:
                    unknown.add(entity.id)
                    return None
                if entity.state == "off":
                    return None
            # A leaf can occur through multiple groups; preserve every inherited restriction.
            previous = expanded.get(entity.id)
            expanded[entity.id] = (
                replace(entity, labels=entity.labels | previous.labels) if previous else entity
            )
        return None

    for entity in entities:
        if error := expand(entity, set()):
            unavailable = error == "unavailable"
            return Resolution(
                area_ids=areas,
                error="ineligible_target" if unavailable else error,
                target_unavailable=unavailable,
            )
    return Resolution(
        tuple(expanded[k] for k in sorted(expanded)), areas, aggregate=aggregate,
        state_filtered=state_filtered, unknown_state_count=len(unknown) if state_filtered else 0,
    )
