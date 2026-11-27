"""Two existing operations described independently of interpretation and authorization.

This is the first catalog slice, not a list of everything Bociek can do. Preparing
an operation does not resolve targets, grant exposure, classify risk, authorize a
user or execute a service. The existing resolver, policy and fresh HA preflight
remain responsible for those decisions.
"""

import math
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal

from .home_model import Entity
from .speech import temperature_speech
from .types import Action, Call, Proposal

OperationKind = Literal["command", "query"]
PreparationStatus = Literal["ready", "answered", "blocked", "clarify"]


@dataclass(frozen=True)
class CapabilityDefinition:
    """Static semantics only; descriptors never confer permission to act."""

    id: str
    action: Action
    operation_kind: OperationKind
    domain: str
    device_class: str | None = None
    excluded_color_modes: tuple[str, ...] = ()
    value_min: float | None = None
    value_max: float | None = None
    unit: str | None = None
    finite_value_required: bool = True
    scope_priority: tuple[str, ...] = ("explicit_location", "source_area")
    authorization: str = "external_resolver_policy_and_ha_preflight"


@dataclass(frozen=True)
class CapabilityFacts:
    """Typed operation facts without entity identifiers or claims of completion."""

    capability_id: str
    operation_kind: OperationKind
    value_role: Literal["requested", "observed"]
    source: Literal["validated_parameter", "ha_state_cache"]
    value: float | None = None
    unit: str | None = None
    original_unit: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _PreparedCall:
    domain: str
    service: str
    entity_ids: tuple[str, ...]
    data: tuple[tuple[str, float], ...] = ()

    def to_call(self) -> Call:
        # Call is the legacy transport contract and contains a mutable dict.
        # Return a fresh copy so the prepared operation itself stays immutable.
        return Call(self.domain, self.service, self.entity_ids, dict(self.data))


@dataclass(frozen=True)
class PreparedOperation:
    status: PreparationStatus
    facts: CapabilityFacts
    speech: str = ""
    reason: str = ""
    operation: _PreparedCall | None = None

    @property
    def call(self) -> Call | None:
        return self.operation.to_call() if self.operation is not None else None


_DEFINITIONS = MappingProxyType(
    {
        Action.BRIGHTNESS: CapabilityDefinition(
            id="light.set_brightness",
            action=Action.BRIGHTNESS,
            operation_kind="command",
            domain="light",
            excluded_color_modes=("onoff", "unknown"),
            value_min=0,
            value_max=100,
            unit="%",
        ),
        Action.TEMPERATURE: CapabilityDefinition(
            id="sensor.read_temperature",
            action=Action.TEMPERATURE,
            operation_kind="query",
            domain="sensor",
            device_class="temperature",
        ),
    }
)


def definition_for(action: Action) -> CapabilityDefinition | None:
    """Constant-time lookup; actions outside this slice keep their existing path."""
    return _DEFINITIONS.get(action)


def catalog_snapshot() -> dict:
    """Fresh, JSON-serializable static metadata, safe to share without a home model."""
    return {
        "schema_version": 1,
        "partial": True,
        "capabilities": [asdict(definition) for definition in _DEFINITIONS.values()],
    }


def _unit_fact(unit) -> str | None:
    """Normalize known unit spellings for facts without converting values or speech."""
    if not isinstance(unit, str):
        return None
    normalized = unit.strip().casefold()
    return {
        "°c": "°C",
        "c": "°C",
        "celsius": "°C",
        "°f": "°F",
        "f": "°F",
        "fahrenheit": "°F",
        "k": "K",
        "kelvin": "K",
    }.get(normalized, unit)


def _facts(definition, value=None, unit=None) -> CapabilityFacts:
    query = definition.operation_kind == "query"
    return CapabilityFacts(
        definition.id,
        definition.operation_kind,
        "observed" if query else "requested",
        "ha_state_cache" if query else "validated_parameter",
        value,
        _unit_fact(unit) if query else unit,
        unit if isinstance(unit, str) else None,
    )


def prepare_entity(
    definition: CapabilityDefinition, proposal: Proposal, entity: Entity
) -> PreparedOperation:
    """Prepare one already-resolved entity, preserving the legacy policy behavior.

    In particular, brightness booleans are accepted like Python numeric values by
    the pre-catalog policy. Tightening that contract would be a separate change.
    No authorization or availability check is duplicated here.
    """
    facts = _facts(definition)
    if definition is not definition_for(proposal.action):
        return PreparedOperation(
            "blocked", facts, "Ta operacja nie jest obsługiwana.", "capability"
        )
    if proposal.action == Action.BRIGHTNESS:
        modes = entity.attributes.get("supported_color_modes", [])
        if entity.domain != definition.domain or not (
            set(modes) - set(definition.excluded_color_modes)
        ):
            return PreparedOperation(
                "blocked", facts, "To światło nie obsługuje jasności.", "capability"
            )
        if (
            proposal.value is None
            or (definition.finite_value_required and not math.isfinite(proposal.value))
            or not definition.value_min <= proposal.value <= definition.value_max
        ):
            return PreparedOperation(
                "blocked", facts, "Jasność musi wynosić od 0 do 100 procent.", "range"
            )
        return PreparedOperation(
            "ready",
            _facts(definition, proposal.value, definition.unit),
            operation=_PreparedCall(
                "light", "turn_on", (entity.id,), (("brightness_pct", proposal.value),)
            ),
        )
    if entity.domain != definition.domain or entity.device_class != definition.device_class:
        return PreparedOperation("blocked", facts, "To nie jest czujnik temperatury.", "capability")
    try:
        value = float(entity.state)
        if definition.finite_value_required and not math.isfinite(value):
            raise ValueError
    except (ValueError, TypeError):
        return PreparedOperation(
            "clarify", facts, "Czujnik nie ma poprawnego odczytu.", "invalid_state"
        )
    unit = entity.attributes.get("unit_of_measurement", "")
    return PreparedOperation(
        "answered",
        _facts(definition, value, unit),
        f"Jest {temperature_speech(value, unit)}.",
    )
