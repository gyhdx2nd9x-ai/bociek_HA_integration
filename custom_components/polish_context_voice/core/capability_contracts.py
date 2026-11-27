"""Additive reports for the migrated operations, without a second execution path.

Requested values stay requested after execution. Executor return is the highest
evidence this core can report for a write; it is not physical state verification.
"""

from dataclasses import asdict, dataclass
from typing import Literal

from .capability_catalog import CapabilityDefinition, CapabilityFacts, OperationKind
from .types import Result

Stage = Literal[
    "planned",
    "confirmation_required",
    "answered",
    "preview",
    "executed",
    "partial",
    "failed",
    "rejected",
    "clarification_required",
]
STAGES = {
    "ready": "planned",
    "confirm": "confirmation_required",
    "answered": "answered",
    "dry_run": "preview",
    "executed": "executed",
    "partial": "partial",
    "error": "failed",
    "blocked": "rejected",
    "clarify": "clarification_required",
}


@dataclass(frozen=True)
class CapabilityReport:
    id: str
    operation_kind: OperationKind
    stage: Stage
    evidence: str
    facts: CapabilityFacts | None
    completed_target_count: int = 0
    schema_version: int = 1

    def to_dict(self):
        return asdict(self)


def annotate_capability(
    result: Result,
    definition: CapabilityDefinition | None = None,
    facts: CapabilityFacts | None = None,
    *,
    previous: CapabilityReport | None = None,
) -> Result:
    """Describe the existing outcome only. Never resolve, authorize or call HA."""
    prior = previous or getattr(result, "_capability_report", None)
    if definition is None and prior is None:
        return result
    identifier = definition.id if definition is not None else prior.id
    kind = definition.operation_kind if definition is not None else prior.operation_kind
    if facts is None and prior is not None and prior.id == identifier:
        facts = prior.facts
    stage = STAGES.get(result.status)
    if stage is None:
        return result
    evidence = {
        "planned": "validated_plan",
        "confirmation_required": "validated_plan",
        "preview": "validated_plan",
        "answered": "ha_state_cache",
        "executed": "executor_returned",
        "partial": "executor_partial_return",
    }.get(stage, "none")
    report = CapabilityReport(identifier, kind, stage, evidence, facts, len(result.completed))
    # The typed report is private to this result; only its bounded public fields
    # are serialized. Replanning creates a new report, including fresh query data.
    result._capability_report = report
    result.response_data = {**(result.response_data or {}), "capability": report.to_dict()}
    return result
