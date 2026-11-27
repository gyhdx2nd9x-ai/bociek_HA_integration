"""Core plan/execution timings describe boundaries without changing command behavior."""

import asyncio
from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core import engine as engine_module
from custom_components.polish_context_voice.core.diagnostics import RequestDiagnostics
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.types import Action, Proposal, Request


class Clock:
    def __init__(self):
        self.milliseconds = 0

    def __call__(self):
        return self.milliseconds / 1000

    def advance(self, milliseconds):
        self.milliseconds += milliseconds


class Executor:
    def __init__(self, clock):
        self.clock = clock
        self.events = []
        self.completed = []
        self.fail_at = None
        self.deny = False
        self.cancel_at = None

    async def preflight(self, calls, request):
        self.events.append(("preflight", tuple(call.entity_ids for call in calls)))
        self.clock.advance(4)
        if self.cancel_at == "preflight":
            raise asyncio.CancelledError
        if self.deny:
            raise PermissionError("denied")

    async def execute(self, call, request):
        self.events.append(("execute", call.entity_ids))
        self.clock.advance(5)
        if self.cancel_at == "execute":
            raise asyncio.CancelledError
        if self.fail_at == len(self.completed):
            raise ConnectionError("disconnected")
        self.completed.append(call)


@pytest.fixture
def timed_engine(home, monkeypatch):
    clock = Clock()
    recorder = RequestDiagnostics(clock=clock)
    original_normalize = engine_module.normalize
    original_parse = engine_module.nlu.parse

    def normalize(text):
        clock.advance(1)
        return original_normalize(text)

    def parse(text):
        clock.advance(2)
        return original_parse(text)

    def model():
        clock.advance(3)
        return home

    monkeypatch.setattr(engine_module, "normalize", normalize)
    monkeypatch.setattr(engine_module.nlu, "parse", parse)
    executor = Executor(clock)
    return Engine(model, executor), executor, recorder, clock


def begin(recorder, request):
    return recorder.begin(
        dry_run=request.dry_run,
        has_source_area=bool(request.source_area),
        has_satellite=bool(request.satellite_id),
        has_device=bool(request.device_id),
    )


async def run(engine, recorder, request):
    trace = begin(recorder, request)
    with trace.activate():
        result = await trace.run(engine.process(request))
    trace.finish(result)
    return result, recorder.snapshot()["recent_requests"][-1]


async def test_preview_records_stable_plan_but_never_an_execution_span(timed_engine):
    engine, executor, recorder, _ = timed_engine
    result, row = await run(
        engine, recorder, Request("zapal światła", source_area="living_room", dry_run=True)
    )
    assert result.status == "dry_run"
    assert row["milestones_ms"] == {"plan_ready": 9}
    assert row["stages_ms"] == {
        "interpretation": 3,
        "resolution_policy": 6,
        "handler": 9,
    }
    assert not executor.events


async def test_execution_starts_after_plan_and_preflights_all_calls_first(timed_engine):
    engine, executor, recorder, _ = timed_engine
    result, row = await run(engine, recorder, Request("wyłącz światła", source_area="living_room"))
    assert result.status == "executed"
    assert row["milestones_ms"] == {"plan_ready": 9}
    assert row["stages_ms"]["execution"] == 19
    assert row["stages_ms"]["handler"] == row["runtime_elapsed_ms"] == 28
    assert executor.events[0] == ("preflight", tuple(call.entity_ids for call in result.calls))
    assert executor.events[1:] == [("execute", call.entity_ids) for call in result.calls]
    assert result.completed == tuple(
        identifier for call in result.calls for identifier in call.entity_ids
    )
    # A fake executor has no HA-service instrumentation; planned calls are separate.
    assert row["call_count"] == row["completed_target_count"] == 3
    assert not any(row["ha_service_calls"].values())


async def test_confirmation_has_no_ready_marker_until_consent_is_revalidated(timed_engine):
    engine, executor, recorder, _ = timed_engine
    request = Request("włącz telewizor", source_area="living_room", conversation_id="test")
    proposal, proposed = await run(engine, recorder, request)
    assert proposal.status == "confirm"
    assert proposed["milestones_ms"] == {}
    assert "execution" not in proposed["stages_ms"]
    assert not executor.events
    result, confirmed = await run(engine, recorder, replace(request, text="potwierdzam"))
    assert result.status == "executed"
    assert confirmed["milestones_ms"] == {"plan_ready": 4}
    assert confirmed["stages_ms"]["resolution_policy"] == 3
    assert confirmed["stages_ms"]["execution"] == 9
    assert len(executor.completed) == 1
    repeated, row = await run(engine, recorder, replace(request, text="potwierdzam"))
    assert repeated.reason == "no_pending"
    assert row["milestones_ms"] == {}
    assert len(executor.completed) == 1


@pytest.mark.parametrize("text", ["nieznane polecenie", "zapal światła w piwnicy"])
async def test_failed_interpretation_or_resolution_never_claims_a_ready_plan(timed_engine, text):
    engine, executor, recorder, _ = timed_engine
    result, row = await run(engine, recorder, Request(text, source_area="living_room"))
    assert result.status in {"unsupported", "clarify"}
    assert row["milestones_ms"] == {}
    assert "execution" not in row["stages_ms"]
    assert not executor.events


async def test_changed_plan_before_execution_has_no_ready_marker(timed_engine, home):
    engine, executor, recorder, clock = timed_engine
    calls = 0

    def model():
        nonlocal calls
        calls += 1
        clock.advance(3)
        if calls == 2:
            home.entities["light.living_room_main"] = replace(
                home.entities["light.living_room_main"], exposed=False
            )
        return home

    engine.model_provider = model
    result, row = await run(engine, recorder, Request("zapal światła", source_area="living_room"))
    assert result.reason == "plan_changed"
    assert row["milestones_ms"] == {}
    assert row["stages_ms"]["resolution_policy"] == 6
    assert not executor.events


@pytest.mark.parametrize("failure", ["preflight", "first_call", "second_call"])
async def test_failure_preserves_order_partial_results_and_timed_boundary(timed_engine, failure):
    engine, executor, recorder, _ = timed_engine
    executor.deny = failure == "preflight"
    executor.fail_at = {"first_call": 0, "second_call": 1}.get(failure)
    result, row = await run(engine, recorder, Request("wyłącz światła", source_area="living_room"))
    expected_completed = 1 if failure == "second_call" else 0
    expected_execution_ms = {"preflight": 4, "first_call": 9, "second_call": 14}[failure]
    assert result.status == ("partial" if expected_completed else "error")
    assert result.reason == "execution_failed"
    assert result.completed == tuple(
        identifier for call in result.calls[:expected_completed] for identifier in call.entity_ids
    )
    assert row["completed_target_count"] == expected_completed
    assert row["call_count"] == 3
    assert row["milestones_ms"] == {"plan_ready": 9}
    assert row["stages_ms"]["execution"] == expected_execution_ms
    assert executor.events[0][0] == "preflight"
    assert executor.completed == list(result.calls[:expected_completed])


@pytest.mark.parametrize("cancel_at", ["preflight", "execute"])
async def test_cancellation_is_propagated_and_closes_execution_timing(timed_engine, cancel_at):
    engine, executor, recorder, _ = timed_engine
    executor.cancel_at = cancel_at
    request = Request("zapal światła", source_area="living_room")
    trace = begin(recorder, request)
    with trace.activate(), pytest.raises(asyncio.CancelledError):
        await trace.run(engine.process(request))
    trace.finish(interrupted=True)
    row = recorder.snapshot()["recent_requests"][-1]
    assert row["status"] == "interrupted"
    assert row["milestones_ms"] == {"plan_ready": 9}
    assert row["stages_ms"]["execution"] == (4 if cancel_at == "preflight" else 9)
    assert not executor.completed


async def test_fallback_interpretation_includes_provider_snapshot_and_proposal_wait(timed_engine):
    engine, _, recorder, clock = timed_engine

    class Fallback:
        async def propose(self, text, request, home):
            clock.advance(10)
            return Proposal(Action.ON, "light", origin="llm")

    engine.fallback = Fallback()
    result, row = await run(
        engine, recorder, Request("nietypowa wypowiedź", source_area="living_room", dry_run=True)
    )
    assert result.status == "confirm"
    # Includes the fallback snapshot, proposal wait and explicit-location normalization.
    assert row["stages_ms"]["interpretation"] == 17
    assert row["stages_ms"]["resolution_policy"] == 3
    assert row["milestones_ms"] == {}


async def test_no_active_trace_preserves_execution_and_legacy_result_latency(
    timed_engine, monkeypatch
):
    engine, executor, recorder, _ = timed_engine
    counter = iter((100.0, 100.125))
    monkeypatch.setattr(engine_module, "perf_counter", lambda: next(counter))
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.status == "executed"
    assert result.completed == ("light.living_room_main",)
    assert result.latency_ms == 125
    assert [event[0] for event in executor.events] == ["preflight", "execute"]
    assert recorder.snapshot()["recent_requests"] == []


async def test_valid_plan_without_executor_still_has_ready_marker(timed_engine):
    engine, _, recorder, _ = timed_engine
    engine.executor = None
    result, row = await run(engine, recorder, Request("zapal światła", source_area="living_room"))
    assert result.reason == "no_executor"
    assert row["milestones_ms"] == {"plan_ready": 9}
    assert "execution" not in row["stages_ms"]
