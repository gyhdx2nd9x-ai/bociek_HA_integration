import json

from custom_components.polish_context_voice.core.health_attributes import (
    MAX_ATTRIBUTE_BYTES,
    sensor_attributes,
)


def test_health_sensor_attributes_stay_below_recorder_limit_for_many_findings():
    findings = [
        {
            "code": "alias_conflict",
            "severity": "warning" if index % 2 else "info",
            "ids": tuple(f"light.device_{index}_{member}" for member in range(8)),
            "message": f"Alias conflict {index}: " + ("korytarz " * 90),
            "new": False,
        }
        for index in range(500)
    ]

    attributes = sensor_attributes(
        findings,
        llm_enabled=False,
        cache={"rebuilds": 2, "state_updates": 4, "last_rebuild_ms": 1.25},
        active_requests=0,
    )

    assert len(json.dumps(attributes, ensure_ascii=False).encode("utf-8")) < MAX_ATTRIBUTE_BYTES
    assert attributes["finding_count"] == 500
    assert attributes["suggestions"] == 250
    assert attributes["truncated"] is True
    assert attributes["findings"]
    assert len(attributes["findings"]) < 100
