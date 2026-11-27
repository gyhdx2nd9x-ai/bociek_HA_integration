"""The one restricted function a fallback model may call."""

import math

from ..types import Action, Proposal

_FALLBACK_ACTIONS = (
    Action.ON, Action.OFF, Action.BRIGHTNESS, Action.TEMPERATURE,
    Action.SET_TEMPERATURE, Action.OPEN, Action.CLOSE,
)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "propose_home_action",
        "description": "Propose ONE immediate home action. Never execute or confirm actions.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": [a.value for a in _FALLBACK_ACTIONS]},
                "kind": {
                    "type": "string",
                    "enum": ["light", "named", "sensor", "safe_all", "cover", "climate"],
                },
                "name": {"type": "string"},
                "location": {"type": "string"},
                "qualifier": {
                    "type": "string",
                    "enum": ["voice_main", "voice_task", "voice_ambient", "voice_accent"],
                },
                "value": {"type": "number"},
            },
            "required": ["action", "kind"],
        },
    },
}


def validate_proposal(data):
    if not isinstance(data, dict) or set(data) - {
        "action",
        "kind",
        "name",
        "location",
        "qualifier",
        "value",
    }:
        raise ValueError("Invalid proposal fields")
    action = Action(data["action"])
    if action not in _FALLBACK_ACTIONS:
        raise ValueError("Unsupported fallback action")
    kind = data["kind"]
    if kind not in SCHEMA["function"]["parameters"]["properties"]["kind"]["enum"]:
        raise ValueError("Invalid kind")
    for field in ("name", "location", "qualifier"):
        if field in data and (not isinstance(data[field], str) or not 0 < len(data[field]) <= 200):
            raise ValueError("Invalid slot")
    if kind == "named" and "name" not in data:
        raise ValueError("Named target required")
    if kind != "named" and "name" in data:
        raise ValueError("Unexpected target name")
    if "qualifier" in data and (
        kind != "light"
        or data["qualifier"] not in {"voice_main", "voice_task", "voice_ambient", "voice_accent"}
    ):
        raise ValueError("Invalid qualifier")
    if kind == "safe_all" and action != Action.OFF:
        raise ValueError("Only safe all off")
    value = data.get("value")
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError("Invalid value")
    if (action in {Action.BRIGHTNESS, Action.SET_TEMPERATURE}) != (value is not None):
        raise ValueError("Unexpected or missing value")
    return Proposal(**{**data, "action": action}, confidence=0.90, origin="llm")
