"""Small serializable contracts shared by every adapter."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Action(StrEnum):
    ON = "turn_on"
    OFF = "turn_off"
    BRIGHTNESS = "brightness"
    TEMPERATURE = "temperature"
    SET_TEMPERATURE = "set_temperature"
    OPEN = "open_cover"
    CLOSE = "close_cover"
    COVER_POSITION = "set_cover_position"
    FAN_SPEED = "set_percentage"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Request:
    text: str
    source_area: str | None = None
    satellite_id: str | None = None
    device_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    dry_run: bool = False

    @property
    def session_key(self) -> tuple:
        # Shared conversation IDs never cross users, satellites or transport modes.
        return (
            self.user_id,
            self.conversation_id,
            self.satellite_id,
            self.device_id,
            self.source_area,
            self.dry_run,
        )


@dataclass(frozen=True)
class Proposal:
    action: Action
    kind: str
    name: str | None = None
    location: str | None = None
    qualifier: str | None = None
    value: float | None = None
    confidence: float = 0.99
    origin: str = "fast"
    prefer_single_light: bool = False


@dataclass(frozen=True)
class Call:
    domain: str
    service: str
    entity_ids: tuple[str, ...]
    data: dict[str, Any] = field(default_factory=dict)
    guard_entity_ids: tuple[str, ...] = ()
    required_state: str | None = None


@dataclass
class Result:
    status: str
    speech: str
    targets: tuple[str, ...] = ()
    calls: tuple[Call, ...] = ()
    confidence: float = 0.0
    confidence_class: str = "low"
    risk: Risk = Risk.LOW
    route: str = "fast"
    reason: str = ""
    area_ids: tuple[str, ...] = ()
    conversation_id: str | None = None
    request_id: str = ""
    latency_ms: float = 0.0
    completed: tuple[str, ...] = ()
    native_intent: dict[str, Any] | None = None
    response_data: dict[str, Any] | None = None
    continue_conversation: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for call in result["calls"]:
            if not call["guard_entity_ids"]:
                call.pop("guard_entity_ids")
            if call["required_state"] is None:
                call.pop("required_state")
        return result
