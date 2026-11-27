"""Observed HA service awaits retain the existing permission and execution boundary."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_native_boundary import load

from custom_components.polish_context_voice.core.diagnostics import RequestDiagnostics
from custom_components.polish_context_voice.core.types import Call, Request, Result


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


@pytest.fixture
def boundary(monkeypatch):
    clock = Clock()
    events = []
    options = {"mode": "allowed", "error": None}

    def exposed(hass, assistant, entity_id):
        events.append(("exposure", assistant, entity_id))
        return options["mode"] != "unexposed"

    def permission(entity_id, policy):
        events.append(("permission", entity_id, policy))
        return options["mode"] != "denied" and not (
            options["mode"] == "guard_denied" and entity_id == "light.PRIVATE_guard"
        )

    async def get_user(user_id):
        events.append(("user", user_id))
        clock.advance(0.002)
        if options["mode"] == "user_missing":
            return None
        return NS(
            is_active=options["mode"] != "inactive",
            permissions=NS(check_entity=permission),
        )

    def state(entity_id):
        events.append(("state", entity_id))
        return NS(state="unavailable" if options["mode"] == "unavailable" else "off")

    async def service(domain, name, data, *, blocking, context):
        events.append(("service", domain, name, data, blocking, context))
        clock.advance(0.010)
        if options["error"] is not None:
            raise options["error"]

    module = load(
        monkeypatch,
        "intents",
        {
            "homeassistant.auth.permissions.const": {"POLICY_CONTROL": "control"},
            "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
            "homeassistant.components.homeassistant.exposed_entities": {
                "async_should_expose": exposed
            },
        },
    )
    hass = NS(
        auth=NS(async_get_user=AsyncMock(side_effect=get_user)),
        states=NS(get=state),
        services=NS(
            has_service=lambda *args: options["mode"] != "no_service",
            async_call=AsyncMock(side_effect=service),
        ),
    )
    context = object()
    return NS(
        executor=module.HAExecutor(hass, context=context),
        recorder=RequestDiagnostics(clock=clock),
        clock=clock,
        hass=hass,
        events=events,
        options=options,
        context=context,
    )


def begin(recorder):
    return recorder.begin(dry_run=False, has_source_area=True, has_satellite=True, has_device=True)


async def test_actual_awaits_preserve_payload_context_order_and_repeated_preflight(boundary):
    b = boundary
    request = Request("PRIVATE command", user_id="PRIVATE_user")
    calls = (
        Call(
            "light",
            "turn_on",
            ("light.PRIVATE_main",),
            {"brightness_pct": 45, "PRIVATE_data": "PRIVATE value"},
            ("light.PRIVATE_guard", "light.PRIVATE_main"),
        ),
        Call("light", "turn_off", ("light.PRIVATE_other",)),
    )
    trace = begin(b.recorder)
    with trace.activate():
        await b.executor.preflight(calls, request)
        for call in calls:
            await b.executor.execute(call, request)
    trace.finish(Result("executed", "PRIVATE speech", calls=calls))

    assert b.hass.auth.async_get_user.await_count == 3
    assert [event[2] for event in b.events if event[0] == "service"] == ["turn_on", "turn_off"]
    assert [event for event in b.events if event[0] == "permission"] == [
        ("permission", "light.PRIVATE_main", "control"),
        ("permission", "light.PRIVATE_guard", "control"),
        ("permission", "light.PRIVATE_other", "control"),
        ("permission", "light.PRIVATE_main", "control"),
        ("permission", "light.PRIVATE_guard", "control"),
        ("permission", "light.PRIVATE_other", "control"),
    ]
    first_service = next(i for i, event in enumerate(b.events) if event[0] == "service")
    assert ("permission", "light.PRIVATE_guard", "control") in b.events[:first_service]
    assert b.hass.services.async_call.call_args_list[0].args == (
        "light",
        "turn_on",
        {
            "brightness_pct": 45,
            "PRIVATE_data": "PRIVATE value",
            "entity_id": ["light.PRIVATE_main"],
        },
    )
    for call in b.hass.services.async_call.call_args_list:
        assert call.kwargs == {"blocking": True, "context": b.context}
    assert "entity_id" not in calls[0].data

    report = b.recorder.snapshot()
    row = report["recent_requests"][0]
    assert row["ha_service_calls"] == {"attempted": 2, "returned": 2, "failed": 0, "interrupted": 0}
    assert row["call_count"] == 2
    assert row["stages_ms"]["ha_preflight"] == 6
    assert row["stages_ms"]["ha_service"] == 20
    assert row["runtime_elapsed_ms"] == 26
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "mode",
    [
        "user_missing",
        "inactive",
        "no_service",
        "unavailable",
        "unexposed",
        "denied",
        "guard_denied",
    ],
)
async def test_denied_preflight_never_counts_a_service_attempt(boundary, mode):
    b = boundary
    b.options["mode"] = mode
    call = Call(
        "light",
        "turn_on",
        ("light.PRIVATE_main",),
        guard_entity_ids=("light.PRIVATE_guard",),
    )
    trace = begin(b.recorder)
    with trace.activate(), pytest.raises(RuntimeError):
        await b.executor.execute(call, Request("PRIVATE command", user_id="PRIVATE_user"))
    trace.finish(Result("blocked", "PRIVATE", calls=(call,)))

    b.hass.services.async_call.assert_not_awaited()
    row = b.recorder.snapshot()["recent_requests"][0]
    assert row["call_count"] == 1
    assert row["ha_service_calls"] == {"attempted": 0, "returned": 0, "failed": 0, "interrupted": 0}
    assert row["stages_ms"]["ha_preflight"] == 2
    assert "ha_service" not in row["stages_ms"]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_service_failure_or_cancellation_preserves_exception_and_timing(boundary, cancelled):
    b = boundary
    exception = (
        asyncio.CancelledError("PRIVATE error") if cancelled else RuntimeError("PRIVATE error")
    )
    b.options["error"] = exception
    trace = begin(b.recorder)
    with trace.activate(), pytest.raises(type(exception)) as caught:
        await b.executor.execute(
            Call("light", "turn_on", ("light.PRIVATE_main",)),
            Request("PRIVATE command", user_id="PRIVATE_user"),
        )
    trace.finish(interrupted=cancelled)

    assert caught.value is exception
    b.hass.services.async_call.assert_awaited_once()
    report = b.recorder.snapshot()
    row = report["recent_requests"][0]
    assert row["ha_service_calls"] == {
        "attempted": 1,
        "returned": 0,
        "failed": int(not cancelled),
        "interrupted": int(cancelled),
    }
    assert row["stages_ms"]["ha_preflight"] == 2
    assert row["stages_ms"]["ha_service"] == 10
    assert "PRIVATE" not in json.dumps(report)


async def test_executor_remains_usable_without_an_active_request_trace(boundary):
    b = boundary
    await b.executor.execute(
        Call("light", "turn_on", ("light.PRIVATE_main",)),
        Request("PRIVATE command", user_id="PRIVATE_user"),
    )
    b.hass.services.async_call.assert_awaited_once()
    assert b.recorder.snapshot()["completed_since_reload"] == 0
