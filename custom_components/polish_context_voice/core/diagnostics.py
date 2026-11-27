"""Bounded request metadata; never retain utterances or household identifiers.

All times use one process-local monotonic clock. Nested spans are not additive.
Service return means HA's async_call returned, not a physical-device effect.
"""

from asyncio import CancelledError
from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from inspect import isawaitable
from math import ceil
from time import perf_counter
from uuid import uuid4

STAGES = frozenset(
    {
        "source_context",
        "authorization",
        "routing",
        "handler",
        "interpretation",
        "resolution_policy",
        "execution",
        "ha_preflight",
        "ha_service",
    }
)
MILESTONES = frozenset({"plan_ready"})
SERVICE_OUTCOMES = ("attempted", "returned", "failed", "interrupted")
_ACTIVE_TRACE = ContextVar("bociek_request_trace", default=None)


def _active_trace():
    trace = _ACTIVE_TRACE.get()
    # Child tasks can inherit a trace. Finishing the request seals its metadata;
    # late work must neither mutate it nor attach itself to a later request.
    return trace if trace is not None and not trace.finished else None


@contextmanager
def measure_stage(name):
    if name not in STAGES:
        raise ValueError("Unknown diagnostics stage")
    trace = _active_trace()
    if trace is None:
        yield
    else:
        with trace.stage(name):
            yield


def mark_milestone(name):
    if name not in MILESTONES:
        raise ValueError("Unknown diagnostics milestone")
    if (trace := _active_trace()) is not None:
        trace.mark(name)


@contextmanager
def measure_service_call():
    """Only the actual await of HA async_call, after preflight, belongs here."""
    trace = _active_trace()
    if trace is None:
        yield
        return
    trace.service_calls["attempted"] += 1
    outcome = "returned"
    try:
        with trace.stage("ha_service"):
            yield
    except CancelledError:
        outcome = "interrupted"
        raise
    except BaseException:
        outcome = "failed"
        raise
    finally:
        if not trace.finished:
            trace.service_calls[outcome] += 1


STATUSES = frozenset(
    {
        "answered",
        "blocked",
        "busy",
        "cancelled",
        "clarify",
        "confirm",
        "dry_run",
        "error",
        "executed",
        "ignored",
        "partial",
        "ready",
        "unsupported",
    }
)
ROUTES = frozenset(
    {
        "fast",
        "llm",
        "native_timer",
        "ha_calendar",
        "ha_native",
        "music",
        "stoic",
        "media_search",
        "vacuum",
    }
)
CATEGORIES = frozenset(
    {
        "success",
        "authorization",
        "capacity",
        "interpretation",
        "resolution",
        "dialogue",
        "policy",
        "provider_or_execution",
        "unsupported",
        "partial",
        "ignored",
        "cancelled",
        "internal_error",
        "interrupted",
        "other",
    }
)
REASONS = {
    "user_unavailable": "authorization",
    "ha_permission_denied": "authorization",
    "calendar_access": "authorization",
    "request_busy": "capacity",
    "fallback_busy": "capacity",
    "music_player_busy": "capacity",
    "satellite_busy": "capacity",
    "no_intent": "interpretation",
    "timer_no_match": "interpretation",
    "calendar_syntax": "interpretation",
    "confidence": "interpretation",
    "missing_area": "resolution",
    "unknown_location": "resolution",
    "ambiguous_location": "resolution",
    "invalid_source_area": "resolution",
    "empty_floor": "resolution",
    "no_targets": "resolution",
    "calendar_no_targets": "resolution",
    "calendar_not_found": "resolution",
    "calendar_kiosk_missing": "resolution",
    "calendar_kiosk_ambiguous": "resolution",
    "no_maintenance_sources": "resolution",
    "ambiguous_target": "resolution",
    "unknown_group_member": "resolution",
    "group_cycle": "resolution",
    "missing_music_area": "resolution",
    "missing_music_player": "resolution",
    "missing_music_shortcut": "resolution",
    "unknown_shortcut": "resolution",
    "ambiguous_shortcut": "resolution",
    "ambiguous_player": "resolution",
    "ambiguous_music_session": "resolution",
    "music_not_playing": "resolution",
    "invalid_music_config": "resolution",
    "no_reference": "dialogue",
    "no_pending": "dialogue",
    "no_pending_timer": "dialogue",
    "calendar_no_pending": "dialogue",
    "calendar_confirmation_unclear": "dialogue",
    "clarification_unresolved": "dialogue",
    "pending_question": "dialogue",
    "plan_changed": "dialogue",
    "context_changed": "dialogue",
    "calendar_changed": "dialogue",
    "music_plan_changed": "dialogue",
    "selection_changed": "dialogue",
    "music_selection_changed": "dialogue",
    "music_player_moved": "dialogue",
    "confirmation_required": "dialogue",
    "room_correction_confirmation": "dialogue",
    "room_correction_needs_area": "dialogue",
    "room_already_in_scope": "dialogue",
    "light_choice_correction_confirmation": "dialogue",
    "choice_already_in_scope": "dialogue",
    "overlapping_light_choice": "dialogue",
    # These guards prevent unsafe targeting even when the result asks for clarification.
    "ineligible_target": "policy",
    "unsafe_group_member": "policy",
    "cross_area_group": "policy",
    "group_unsupported": "policy",
    "high_risk_disabled": "policy",
    "calendar_write_disabled": "policy",
    "routine_not_approved": "policy",
    "satellite_capability": "policy",
    "same_satellite": "policy",
    "music_player_hidden": "policy",
    "range": "policy",
    "range_or_capability": "policy",
    "capability": "unsupported",
    "unsupported_capability": "unsupported",
    "music_capability": "unsupported",
    "calendar_operation_unsupported": "unsupported",
    "calendar_recurrence": "unsupported",
    # A valid query may fail because the source has no usable state or is unavailable.
    "unavailable": "provider_or_execution",
    "invalid_state": "provider_or_execution",
    "music_player_unavailable": "provider_or_execution",
    "no_executor": "provider_or_execution",
    "execution_failed": "provider_or_execution",
    "vacuum_unavailable": "provider_or_execution",
    "calendar_provider": "provider_or_execution",
    "calendar_kiosk_timeout": "provider_or_execution",
    "native_capability_failed": "provider_or_execution",
    "timer_handler_error": "provider_or_execution",
    "music_failed": "provider_or_execution",
    "music_timeout": "provider_or_execution",
    "fallback_unavailable": "provider_or_execution",
    "calendar_partial": "partial",
    "timer_proposal_cancelled": "cancelled",
    "pipeline_error": "internal_error",
    "calendar_error": "internal_error",
    "stoic_not_exposed": "authorization",
    "stoic_sensor_missing": "resolution",
    "stoic_sensor_ambiguous": "resolution",
    "stoic_no_snapshot": "dialogue",
    "stoic_unavailable": "provider_or_execution",
    "stoic_stale": "provider_or_execution",
    "stoic_invalid_date": "provider_or_execution",
    "stoic_missing_quote": "provider_or_execution",
    "stoic_missing_title": "provider_or_execution",
    "stoic_missing_commentary": "provider_or_execution",
    "stoic_text_too_long": "unsupported",
    "stoic_read_failed": "internal_error",
    "media_search_backend_mismatch": "resolution",
    "media_search_no_results": "resolution",
    "media_search_source_unverified": "unsupported",
    "media_search_choice": "dialogue",
    "media_search_no_pending": "dialogue",
    "media_search_target_changed": "dialogue",
    "media_search_too_many": "dialogue",
    "media_search_disabled": "policy",
    "media_search_unavailable": "provider_or_execution",
    "media_search_timeout": "provider_or_execution",
    "media_search_failed": "provider_or_execution",
}


def _category(status, reason):
    if reason in REASONS:
        return REASONS[reason]
    if status in {"answered", "executed", "dry_run", "ready"}:
        return "success"
    if status == "blocked":
        return "policy"
    if status == "busy":
        return "capacity"
    if status in {"clarify", "confirm"}:
        return "dialogue"
    if status in {"partial", "ignored", "cancelled", "unsupported"}:
        return status
    return "other"


def _percentiles(values):
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None}
    values = sorted(values)
    return {
        "count": len(values),
        "p50_ms": values[ceil(0.5 * len(values)) - 1],
        "p95_ms": values[ceil(0.95 * len(values)) - 1],
    }


class RequestDiagnostics:
    """One fixed-size, in-memory window per integration runtime; no I/O."""

    capacity = 64

    def __init__(self, *, clock=perf_counter):
        self.clock = clock
        self.records = deque(maxlen=self.capacity)
        self.completed = 0
        self.categories = Counter()

    def begin(self, *, dry_run, has_source_area, has_satellite, has_device):
        return RequestTrace(
            self,
            {
                "dry_run": bool(dry_run),
                "has_source_area": bool(has_source_area),
                "has_satellite": bool(has_satellite),
                "has_device": bool(has_device),
            },
        )

    def snapshot(self):
        records = list(self.records)
        return {
            "schema_version": 2,
            "storage": "memory_only",
            "capacity": self.capacity,
            "completed_since_reload": self.completed,
            "window_size": len(records),
            "categories_since_reload": {
                category: self.categories[category] for category in sorted(CATEGORIES)
            },
            "runtime_elapsed": _percentiles([row["runtime_elapsed_ms"] for row in records]),
            "stages": {
                stage: _percentiles(
                    [row["stages_ms"][stage] for row in records if stage in row["stages_ms"]]
                )
                for stage in sorted(STAGES)
            },
            "milestones": {
                name: _percentiles(
                    [row["milestones_ms"][name] for row in records if name in row["milestones_ms"]]
                )
                for name in sorted(MILESTONES)
            },
            "ha_service_calls": {
                outcome: sum(row["ha_service_calls"][outcome] for row in records)
                for outcome in SERVICE_OUTCOMES
            },
            "measurement_scope": {
                "plan_ready": "core_engine_control_paths_only",
                "ha_service": "ha_executor_and_media_search_async_call",
                "timings_are_nested": True,
                "ha_service_calls_window": "recent_requests",
            },
            "not_measured": [
                "stt",
                "ha_provider_acknowledgement",
                "physical_state",
                "tts",
                "audible_response_start",
            ],
            "recent_requests": deepcopy(records),
        }


class RequestTrace:
    def __init__(self, recorder, source):
        self.recorder = recorder
        self.started = recorder.clock()
        self.identifier = str(uuid4())
        self.source = source
        self.durations = {}
        self.milestones = {}
        self.service_calls = Counter()
        self.finished = False

    @contextmanager
    def activate(self):
        token = _ACTIVE_TRACE.set(self)
        try:
            yield
        finally:
            _ACTIVE_TRACE.reset(token)

    def mark(self, name):
        if name not in MILESTONES:
            raise ValueError("Unknown diagnostics milestone")
        if not self.finished and name not in self.milestones:
            self.milestones[name] = max(0.0, self.recorder.clock() - self.started)

    @contextmanager
    def stage(self, name):
        if name not in STAGES:
            raise ValueError("Unknown diagnostics stage")
        started = self.recorder.clock()
        try:
            yield
        finally:
            if not self.finished:
                self.durations[name] = self.durations.get(name, 0.0) + max(
                    0.0, self.recorder.clock() - started
                )

    async def run(self, awaitable):
        with self.stage("handler"):
            # Routes normally pass coroutines, while some async adapters have
            # already awaited the operation and return a Result. Supporting
            # both prevents diagnostics from raising after a successful call.
            return await awaitable if isawaitable(awaitable) else awaitable

    def finish(self, result=None, *, interrupted=False):
        if self.finished:
            return
        self.finished = True
        status = (
            "interrupted"
            if interrupted
            else result.status
            if result is not None and result.status in STATUSES
            else "error"
        )
        route = result.route if result is not None and result.route in ROUTES else "other"
        category = (
            "interrupted"
            if interrupted
            else "internal_error"
            if result is None
            else _category(status, result.reason)
        )
        row = {
            "request_id": self.identifier,
            **self.source,
            "status": status,
            "route": route,
            "category": category,
            "target_count": len(result.targets) if result is not None else None,
            "call_count": len(result.calls) if result is not None else None,
            "completed_target_count": len(result.completed) if result is not None else None,
            "continue_conversation": bool(result and result.continue_conversation),
            "runtime_elapsed_ms": round(max(0.0, self.recorder.clock() - self.started) * 1000, 3),
            "stages_ms": {
                stage: round(duration * 1000, 3) for stage, duration in self.durations.items()
            },
            "milestones_ms": {
                name: round(duration * 1000, 3) for name, duration in self.milestones.items()
            },
            "ha_service_calls": {
                outcome: self.service_calls[outcome] for outcome in SERVICE_OUTCOMES
            },
        }
        self.recorder.records.append(row)
        self.recorder.completed += 1
        self.recorder.categories[category] += 1
