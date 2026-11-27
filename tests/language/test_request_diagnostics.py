"""Request diagnostics contain bounded metadata, not private conversation data."""

import asyncio
import json
from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.diagnostics import (
    CATEGORIES,
    RequestDiagnostics,
    mark_milestone,
    measure_service_call,
    measure_stage,
)
from custom_components.polish_context_voice.core.types import Call, Result


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def begin(recorder, **changes):
    source = dict(dry_run=False, has_source_area=True, has_satellite=True, has_device=True)
    return recorder.begin(**(source | changes))


@pytest.mark.parametrize(
    ("route", "status", "reason", "category"),
    [
        ("stoic", "answered", "", "success"),
        ("stoic", "blocked", "stoic_not_exposed", "authorization"),
        ("stoic", "unsupported", "stoic_stale", "provider_or_execution"),
        ("media_search", "clarify", "media_search_choice", "dialogue"),
        ("media_search", "error", "media_search_timeout", "provider_or_execution"),
        ("media_search", "error", "media_search_failed", "provider_or_execution"),
    ],
)
def test_content_diagnostics_classify_routes_without_storing_text(route, status, reason, category):
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    trace.finish(
        Result(
            status,
            "PRIVATE-quote-or-book-title",
            route=route,
            reason=reason,
            response_data={"name": "PRIVATE-search-result"},
        )
    )
    report = recorder.snapshot()
    row = report["recent_requests"][0]
    assert row["route"] == route
    assert row["category"] == category
    assert "PRIVATE" not in json.dumps(report)


def test_own_stages_are_accumulated_without_claiming_audio_or_physical_state():
    clock = Clock()
    recorder = RequestDiagnostics(clock=clock)
    trace = begin(recorder)
    with trace.stage("authorization"):
        clock.advance(0.002)
    for _ in range(2):
        with trace.stage("routing"):
            clock.advance(0.001)
    with trace.stage("handler"):
        clock.advance(0.004)
    clock.advance(0.001)
    trace.finish(Result("answered", "irrelevant", latency_ms=4))
    report = recorder.snapshot()
    row = report["recent_requests"][0]
    assert row["runtime_elapsed_ms"] == 9
    assert row["stages_ms"] == {"authorization": 2, "routing": 2, "handler": 4}
    assert report["runtime_elapsed"] == {"count": 1, "p50_ms": 9, "p95_ms": 9}
    assert report["stages"]["source_context"]["count"] == 0
    assert set(report["not_measured"]) == {
        "stt",
        "ha_provider_acknowledgement",
        "physical_state",
        "tts",
        "audible_response_start",
    }


def test_buffer_and_counter_keys_remain_bounded_and_percentiles_describe_recent_window():
    clock = Clock()
    recorder = RequestDiagnostics(clock=clock)
    for duration in range(1, 101):
        trace = begin(recorder)
        clock.advance(duration / 1000)
        trace.finish(Result("executed", "OK"))
    report = recorder.snapshot()
    assert report["capacity"] == report["window_size"] == 64
    assert report["completed_since_reload"] == 100
    assert report["runtime_elapsed"] == {"count": 64, "p50_ms": 68, "p95_ms": 97}
    assert report["categories_since_reload"]["success"] == 100
    assert set(recorder.categories) <= CATEGORIES
    assert len({row["request_id"] for row in report["recent_requests"]}) == 64
    report["recent_requests"][0]["status"] = "changed"
    assert recorder.snapshot()["recent_requests"][0]["status"] == "executed"


def test_private_payload_and_unknown_vocabulary_never_enter_report():
    private = "PRIVATE-calendar-person-token-command"
    recorder = RequestDiagnostics()
    trace = begin(recorder, has_source_area=private, has_satellite=private)
    result = Result(
        private,
        private,
        targets=(private,),
        calls=(Call(private, private, (private,), {private: private}),),
        completed=(private,),
        route=private,
        reason=private,
        area_ids=(private,),
        conversation_id=private,
        response_data={private: private},
        native_intent={private: private},
    )
    trace.finish(result)
    result.response_data[private] = "changed"
    report = recorder.snapshot()
    row = report["recent_requests"][0]
    assert private not in json.dumps(report)
    assert row["status"] == "error"
    assert row["route"] == row["category"] == "other"
    assert row["target_count"] == row["call_count"] == row["completed_target_count"] == 1
    assert row["has_satellite"] is True
    assert set(row) == {
        "request_id",
        "dry_run",
        "has_source_area",
        "has_satellite",
        "has_device",
        "status",
        "route",
        "category",
        "target_count",
        "call_count",
        "completed_target_count",
        "continue_conversation",
        "runtime_elapsed_ms",
        "stages_ms",
        "milestones_ms",
        "ha_service_calls",
    }


@pytest.mark.parametrize(
    ("status", "reason", "category"),
    [
        ("blocked", "user_unavailable", "authorization"),
        ("busy", "request_busy", "capacity"),
        ("unsupported", "no_intent", "interpretation"),
        ("clarify", "no_targets", "resolution"),
        ("clarify", "no_pending", "dialogue"),
        ("blocked", "high_risk_disabled", "policy"),
        ("error", "calendar_provider", "provider_or_execution"),
        ("error", "pipeline_error", "internal_error"),
        ("cancelled", "", "cancelled"),
        ("ignored", "timer_response_echo", "ignored"),
        ("partial", "", "partial"),
        ("dry_run", "", "success"),
        ("clarify", "unknown_location", "resolution"),
        ("clarify", "invalid_source_area", "resolution"),
        ("clarify", "calendar_not_found", "resolution"),
        ("clarify", "unavailable", "provider_or_execution"),
        ("clarify", "invalid_state", "provider_or_execution"),
        ("clarify", "unsafe_group_member", "policy"),
        ("clarify", "unknown_group_member", "resolution"),
        ("clarify", "satellite_busy", "capacity"),
        ("blocked", "calendar_operation_unsupported", "unsupported"),
        ("cancelled", "timer_proposal_cancelled", "cancelled"),
    ],
)
def test_result_categories_are_diagnostic_groups_not_language_accuracy(status, reason, category):
    recorder = RequestDiagnostics()
    begin(recorder).finish(Result(status, "private", reason=reason))
    assert recorder.snapshot()["recent_requests"][0]["category"] == category


def test_finish_is_single_use_and_reloading_discards_memory():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    trace.finish(Result("answered", "private"))
    trace.finish(Result("error", "private"))
    assert recorder.snapshot()["completed_since_reload"] == 1
    empty = RequestDiagnostics().snapshot()
    assert empty["window_size"] == empty["completed_since_reload"] == 0
    assert empty["runtime_elapsed"] == {"count": 0, "p50_ms": None, "p95_ms": None}


def test_exception_and_interruption_record_no_exception_text():
    recorder = RequestDiagnostics()
    begin(recorder).finish()
    begin(recorder).finish(interrupted=True)
    rows = recorder.snapshot()["recent_requests"]
    assert [row["category"] for row in rows] == ["internal_error", "interrupted"]
    assert rows[1]["status"] == "interrupted"
    assert rows[1]["target_count"] is rows[1]["call_count"] is None


def test_unknown_stage_is_rejected_without_retaining_its_name():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    with pytest.raises(ValueError, match="Unknown diagnostics stage"):
        with trace.stage("PRIVATE-stage"):
            pass
    trace.finish(Result("answered", "private"))
    assert "PRIVATE-stage" not in json.dumps(recorder.snapshot())


async def test_handler_returns_the_same_result_without_changing_existing_latency():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    original = Result("answered", "private", latency_ms=123.456)

    async def handler():
        return original

    returned = await trace.run(handler())
    trace.finish(returned)
    assert returned is original
    assert returned.latency_ms == 123.456
    assert "handler" in recorder.snapshot()["recent_requests"][0]["stages_ms"]
    assert replace(returned).request_id == ""


async def test_handler_accepts_result_returned_by_an_async_router():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    original = Result("executed", "Odkurzacz uruchomiony.", route="vacuum")

    returned = await trace.run(original)
    trace.finish(returned)

    assert returned is original
    assert recorder.snapshot()["recent_requests"][0]["route"] == "vacuum"


def test_milestone_is_elapsed_from_request_start_not_a_summed_stage():
    clock = Clock()
    recorder = RequestDiagnostics(clock=clock)
    trace = begin(recorder)
    with trace.activate():
        with measure_stage("resolution_policy"):
            clock.advance(0.003)
        mark_milestone("plan_ready")
        clock.advance(0.002)
        mark_milestone("plan_ready")
        with measure_service_call():
            clock.advance(0.010)
    trace.finish(Result("executed", "private"))
    snapshot = recorder.snapshot()
    row = snapshot["recent_requests"][0]
    assert snapshot["schema_version"] == 2
    assert row["milestones_ms"] == {"plan_ready": 3}
    assert row["stages_ms"] == {"resolution_policy": 3, "ha_service": 10}
    assert row["runtime_elapsed_ms"] == 15
    assert row["ha_service_calls"] == {"attempted": 1, "returned": 1, "failed": 0, "interrupted": 0}
    assert snapshot["milestones"]["plan_ready"] == {"count": 1, "p50_ms": 3, "p95_ms": 3}
    assert snapshot["stages"]["interpretation"]["count"] == 0
    assert snapshot["ha_service_calls"] == row["ha_service_calls"]


def test_service_outcomes_include_failure_and_interruption_without_exception_text():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    with trace.activate():
        with pytest.raises(RuntimeError):
            with measure_service_call():
                raise RuntimeError("PRIVATE-payload-and-token")
        with pytest.raises(asyncio.CancelledError):
            with measure_service_call():
                raise asyncio.CancelledError("PRIVATE-exception")
    trace.finish(Result("partial", "private"))
    snapshot = recorder.snapshot()
    assert snapshot["ha_service_calls"] == {
        "attempted": 2,
        "returned": 0,
        "failed": 1,
        "interrupted": 1,
    }
    assert "PRIVATE" not in json.dumps(snapshot)


def test_absent_trace_is_noop_and_context_is_restored_after_exception():
    recorder = RequestDiagnostics()
    outer, inner = begin(recorder), begin(recorder)
    with outer.activate():
        with pytest.raises(ValueError):
            with inner.activate():
                with measure_service_call():
                    raise ValueError("private")
        with measure_service_call():
            pass
    with measure_stage("interpretation"), measure_service_call():
        mark_milestone("plan_ready")
    outer.finish(Result("executed", "private"))
    inner.finish(Result("error", "private"))
    rows = recorder.snapshot()["recent_requests"]
    assert rows[0]["ha_service_calls"]["returned"] == 1
    assert rows[1]["ha_service_calls"]["failed"] == 1
    assert all(row["milestones_ms"] == {} for row in rows)
    with pytest.raises(ValueError, match="Unknown diagnostics milestone"):
        mark_milestone("PRIVATE-location")


async def test_concurrent_and_inherited_late_tasks_cannot_contaminate_other_requests():
    recorder = RequestDiagnostics()
    first, second = begin(recorder), begin(recorder)
    started, release = asyncio.Event(), asyncio.Event()

    async def inherited_late_work():
        with measure_stage("interpretation"):
            started.set()
            await release.wait()
        with measure_service_call():
            mark_milestone("plan_ready")

    with first.activate():
        task = asyncio.create_task(inherited_late_work())
        await started.wait()
    first.finish(Result("answered", "private"))
    frozen = recorder.snapshot()
    with second.activate():
        with measure_service_call():
            release.set()
            await task
    second.finish(Result("executed", "private"))
    snapshot = recorder.snapshot()
    assert snapshot["recent_requests"][0] == frozen["recent_requests"][0]
    assert snapshot["recent_requests"][0]["stages_ms"] == {}
    assert snapshot["recent_requests"][1]["ha_service_calls"]["attempted"] == 1
    assert snapshot["recent_requests"][1]["milestones_ms"] == {}


def test_service_and_milestone_aggregates_only_cover_recent_window():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    with trace.activate():
        with measure_service_call():
            mark_milestone("plan_ready")
    trace.finish(Result("executed", "private"))
    for _ in range(recorder.capacity):
        begin(recorder).finish(Result("answered", "private"))
    snapshot = recorder.snapshot()
    assert snapshot["completed_since_reload"] == 65
    assert snapshot["milestones"]["plan_ready"]["count"] == 0
    assert snapshot["ha_service_calls"] == {
        "attempted": 0,
        "returned": 0,
        "failed": 0,
        "interrupted": 0,
    }


async def test_inflight_service_at_finish_stays_unresolved_in_sealed_snapshot():
    recorder = RequestDiagnostics()
    trace = begin(recorder)
    entered, release = asyncio.Event(), asyncio.Event()

    async def outstanding_service():
        with measure_service_call():
            entered.set()
            await release.wait()

    with trace.activate():
        task = asyncio.create_task(outstanding_service())
        await entered.wait()
    trace.finish(Result("answered", "private"))
    sealed = recorder.snapshot()
    assert sealed["ha_service_calls"] == {
        "attempted": 1,
        "returned": 0,
        "failed": 0,
        "interrupted": 0,
    }
    release.set()
    await task
    assert recorder.snapshot() == sealed
