"""Small, recorder-safe summary attributes for the Voice Health sensor."""

import json

MAX_ATTRIBUTE_BYTES = 12 * 1024
MAX_FINDINGS = 100


def sensor_attributes(findings, *, llm_enabled, cache, active_requests):
    """Keep useful findings on the sensor while bounding its serialized size.

    The full finding list remains available through the integration websocket
    API. Sensor attributes are a compact preview for HA state/history/Recorder.
    """
    suggestions = sum(item.get("severity") == "info" for item in findings)
    attributes = {
        "finding_count": len(findings),
        "suggestions": suggestions,
        "findings": [],
        "truncated": bool(findings),
        "llm_enabled": bool(llm_enabled),
        "cache": cache,
        "active_requests": active_requests,
    }

    def encoded_size(value):
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    for finding in findings[:MAX_FINDINGS]:
        compact = {
            "code": str(finding.get("code", ""))[:64],
            "severity": str(finding.get("severity", ""))[:16],
            "ids": [str(entity_id)[:128] for entity_id in finding.get("ids", ())[:5]],
            "message": str(finding.get("message", ""))[:300],
        }
        candidate = [*attributes["findings"], compact]
        if encoded_size({**attributes, "findings": candidate}) > MAX_ATTRIBUTE_BYTES:
            break
        attributes["findings"] = candidate

    attributes["truncated"] = len(attributes["findings"]) < len(findings)
    return attributes
