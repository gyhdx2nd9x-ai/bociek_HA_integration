"""Concurrency and cache regressions with HA boundary doubles only."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_native_boundary import load

from custom_components.polish_context_voice.core.context.admission import Admission, RequestBusy
from custom_components.polish_context_voice.core.home_model.cache import ModelCache
from custom_components.polish_context_voice.core.llm.limits import FallbackBusy, LimitedFallback
from custom_components.polish_context_voice.core.types import Action, Proposal, Request

PREFIX = "custom_components.polish_context_voice.ha."


async def test_runtime_retains_dialogue_between_request_local_engines(runtime):
    question = await runtime.process(req("włącz lampę"), None)
    assert question.continue_conversation
    selected = await runtime.process(req("lampa przy stole"), None)
    assert selected.status == "executed"
    off = await runtime.process(req("zgaś ją"), None)
    assert off.status == "executed"
    assert off.targets == selected.targets == ("light.living_room_table",)


async def test_simultaneous_dialogues_use_their_own_targets(runtime):
    await asyncio.gather(
        runtime.process(req("włącz lampkę stołową", user="alice", satellite_id="a"), None),
        runtime.process(req("zapal światła", user="bob", satellite_id="b"), None),
    )
    a, b = await asyncio.gather(
        runtime.process(req("zgaś ją", user="alice", satellite_id="a"), None),
        runtime.process(req("zgaś je", user="bob", satellite_id="b"), None),
    )
    assert a.targets == ("light.living_room_table",)
    assert b.targets == ("light.living_room_main",)
    assert a.status == b.status == "executed"


async def test_native_turn_clears_device_only_core_context(runtime, monkeypatch):
    import sys

    from custom_components.polish_context_voice.core.types import Result

    native = AsyncMock(return_value=Result("answered", "Pogoda"))
    monkeypatch.setitem(sys.modules, PREFIX + "capabilities", NS(process=native))
    request = Request("zapal światła", device_id="device", conversation_id="device-only")
    assert (await runtime.process(request, None)).status == "executed"
    await runtime.process(replace(request, text="jaka jest pogoda"), None)
    assert not runtime.dialogue.memory
    assert (await runtime.process(replace(request, text="zgaś je"), None)).reason == "no_reference"


@pytest.fixture
def runtime(monkeypatch, home):
    calls = []

    class Executor:
        def __init__(self, hass, context):
            self.context = context

        async def preflight(self, calls, request):
            pass

        async def execute(self, call, request):
            calls.append((request.user_id, self.context, call))

    users = {
        name: NS(is_active=True, permissions=NS(check_entity=lambda *a: True))
        for name in ("alice", "bob")
    }
    users["hidden"] = NS(is_active=True, permissions=NS(check_entity=lambda *a: False))
    module = load(
        monkeypatch,
        "runtime",
        {
            "homeassistant.auth.permissions.const": {"POLICY_READ": "read"},
            "homeassistant.components.homeassistant.exposed_entities": {},
            "homeassistant.core": {"callback": lambda f: f},
            "homeassistant.exceptions": {"HomeAssistantError": RuntimeError},
            "homeassistant.helpers.device_registry": {
                "async_get": lambda h: NS(async_get=lambda key: NS(area_id="living_room")),
                "async_get_effective_area_id": lambda h, d: d.area_id,
            },
            "homeassistant.helpers.aiohttp_client": {"async_get_clientsession": lambda h: None},
            PREFIX + "intents": {"HAExecutor": Executor},
            PREFIX + "registry_sync": {"snapshot": lambda h: home, "update_state": lambda *a: None},
        },
    )
    hass = NS(auth=NS(async_get_user=AsyncMock(side_effect=lambda key: users.get(key))))
    result = module.Runtime(hass, NS(data={}, options={}))
    result.recorded_calls = calls
    return result


class WaitingFallback:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def propose(self, *args):
        self.entered.set()
        await self.release.wait()
        return Proposal(Action.ON, "light", confidence=0.90, origin="llm")


def req(text="zapal światła", user="alice", conversation="room-a", **kwargs):
    return Request(
        text, user_id=user, conversation_id=conversation, source_area="living_room", **kwargs
    )


async def test_slow_fallback_does_not_block_fast_path_or_mix_context(runtime):
    waiting = WaitingFallback()
    runtime.fallback = LimitedFallback(waiting)
    context_a, context_b = object(), object()
    slow = asyncio.create_task(runtime.process(req("zrób przyjemny nastrój"), context_a))
    try:
        await asyncio.wait_for(waiting.entered.wait(), 1)
        fast = await asyncio.wait_for(runtime.process(req(user="bob"), context_b), 1)
        assert fast.status == "executed"
        assert not slow.done()
        assert runtime.recorded_calls[0][:2] == ("bob", context_b)
        assert runtime.cache.rebuilds == 1
        waiting.release.set()
        assert (await slow).status == "confirm"
        confirmed = await runtime.process(req("potwierdzam"), context_a)
        assert confirmed.status == "executed"
        assert runtime.recorded_calls[1][:2] == ("alice", context_a)
        assert not runtime.pending
    finally:
        waiting.release.set()
        await slow
    assert not runtime.admission.active
    assert runtime.fallback.active == 0


async def test_parallel_fallback_and_same_session_are_rejected_without_queue(runtime):
    waiting = WaitingFallback()
    runtime.fallback = LimitedFallback(waiting)
    task = asyncio.create_task(runtime.process(req("zrób przyjemny nastrój"), None))
    try:
        await asyncio.wait_for(waiting.entered.wait(), 1)
        assert (await runtime.process(req(), None)).reason == "request_busy"
        second = await runtime.process(req("zrób przyjemny nastrój", user="bob"), None)
        assert second.reason == "fallback_busy"
        assert len(runtime.admission.active) == runtime.fallback.active == 1
    finally:
        waiting.release.set()
        await task
    assert not runtime.recorded_calls


async def test_visibility_views_reuse_indexes_without_mutating_exposure(runtime, home):
    denied = await runtime.process(req(user="hidden"), None)
    assert not denied.calls
    assert home.entities["light.living_room_main"].exposed
    assert (await runtime.process(req(), None)).status == "executed"
    view = runtime.visible_model(NS(permissions=NS(check_entity=lambda *a: False)))
    assert view.entity_index is home.entity_index
    assert view.location_index is home.location_index
    assert not view.entities["light.living_room_main"].exposed
    assert runtime.cache.rebuilds == 1


async def test_shared_confirmation_survives_request_local_engines(runtime):
    first = await runtime.process(req("włącz telewizor"), None)
    assert first.status == "confirm"
    assert (await runtime.process(req("potwierdzam", user="bob"), None)).reason == "no_pending"
    assert (await runtime.process(req("potwierdzam"), None)).status == "executed"
    assert (await runtime.process(req("potwierdzam"), None)).reason == "no_pending"
    assert len(runtime.recorded_calls) == 1


@pytest.mark.parametrize("reply", ["tak да", "potwierdzam Так", "tak ♥", "tak nie"])
async def test_mixed_transcript_cannot_confirm_a_device_or_reach_fallback(runtime, reply):
    assert (await runtime.process(req("włącz telewizor"), None)).status == "confirm"
    runtime.fallback = NS(propose=AsyncMock(side_effect=AssertionError("Unexpected fallback")))

    result = await runtime.process(req(reply), None)

    assert result.status in {"clarify", "unsupported", "cancelled"}
    assert not runtime.recorded_calls
    runtime.fallback.propose.assert_not_called()


async def test_repeated_consent_executes_original_confirmation_once(runtime):
    assert (await runtime.process(req("włącz telewizor"), None)).status == "confirm"
    assert (await runtime.process(req("Tak. Tak"), None)).status == "executed"
    assert (await runtime.process(req("Tak. Tak"), None)).reason == "no_pending"
    assert len(runtime.recorded_calls) == 1


async def test_state_change_while_fallback_waits_is_revalidated(runtime, home):
    waiting = WaitingFallback()
    runtime.fallback = LimitedFallback(waiting)
    task = asyncio.create_task(runtime.process(req("zrób przyjemny nastrój"), None))
    try:
        await asyncio.wait_for(waiting.entered.wait(), 1)
        main = home.entities["light.living_room_main"]
        runtime.cache.update_entity(replace(main, available=False, state="unavailable"))
    finally:
        waiting.release.set()
    result = await task
    assert result.status not in {"confirm", "executed", "ready"}
    assert not result.calls
    assert not runtime.recorded_calls


async def test_missing_and_inactive_user_cannot_execute(runtime):
    result = await runtime.process(req(user="nonexistent"), None)
    assert result.reason == "user_unavailable"
    assert not runtime.recorded_calls
    assert not runtime.admission.active


def test_cache_state_updates_do_not_rebuild_indexes_or_reset_ttl(home):
    now, loads = [0], []

    def loader():
        loads.append(1)
        return home

    cache = ModelCache(loader, max_age=30, clock=lambda: now[0])
    indexes = cache.get().entity_index
    main = home.entities["light.living_room_main"]
    for second in range(1, 31):
        now[0] = second
        cache.update_entity(replace(main, state=str(second)))
        assert cache.get().entity_index is indexes
    assert len(loads) == 1
    assert cache.state_updates == 30
    now[0] = 31
    cache.get()
    assert len(loads) == 2
    cache.invalidate()
    cache.get()
    assert cache.rebuilds == 3


def test_metadata_change_requires_reindexing(home):
    cache = ModelCache(lambda: home)
    cache.get()
    main = home.entities["light.living_room_main"]
    cache.update_entity(replace(main, aliases=("nowy alias",)))
    assert cache.dirty
    assert home.entities[main.id] == main


def test_admission_capacity_cleanup_and_duplicate():
    admission = Admission(limit=2)
    with admission.enter("a"), admission.enter("b"):
        with pytest.raises(RequestBusy), admission.enter("a"):
            pass
        with pytest.raises(RequestBusy), admission.enter("c"):
            pass
    assert not admission.active
    with pytest.raises(ValueError), admission.enter("a"):
        raise ValueError
    assert not admission.active


@pytest.mark.parametrize("finish", ["timeout", "cancel"])
async def test_fallback_slot_released_on_timeout_or_cancellation(finish):
    waiting = WaitingFallback()
    limited = LimitedFallback(waiting, timeout=0.02 if finish == "timeout" else 5)
    task = asyncio.create_task(limited.propose())
    await asyncio.wait_for(waiting.entered.wait(), 1)
    with pytest.raises(FallbackBusy):
        await limited.propose()
    if finish == "cancel":
        task.cancel()
    with pytest.raises(TimeoutError if finish == "timeout" else asyncio.CancelledError):
        await task
    assert limited.active == 0
    waiting.release.set()
    assert (await limited.propose()).origin == "llm"


@pytest.mark.parametrize("reply", ["Tak", "Potwierdzam", "Dobrze", "Okej", "Nie", "Anuluj"])
async def test_pending_timer_short_reply_reaches_timer_handler(runtime, monkeypatch, reply):
    import sys

    from custom_components.polish_context_voice.core.types import Result

    handler = AsyncMock(return_value=Result("answered", "timer"))
    monkeypatch.setitem(sys.modules, PREFIX + "timers", NS(process=handler))
    request = req(reply)
    runtime.pending_timers[request.session_key] = object()
    context = object()
    result = await runtime.process(request, context)
    assert result.speech == "timer"
    handler.assert_awaited_once_with(runtime.hass, request, context, pending=runtime.pending_timers)
    assert not runtime.recorded_calls


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "bob"},
        {"conversation_id": "other"},
        {"satellite_id": "other"},
        {"dry_run": True},
    ],
)
async def test_timer_short_reply_does_not_cross_session_identity(runtime, monkeypatch, change):
    import sys

    handler = AsyncMock()
    monkeypatch.setitem(sys.modules, PREFIX + "timers", NS(process=handler))
    request = req("potwierdzam")
    runtime.pending_timers[request.session_key] = object()
    await runtime.process(replace(request, **change), None)
    handler.assert_not_called()
    assert request.session_key in runtime.pending_timers
    assert not runtime.recorded_calls


async def test_exact_timer_error_echo_is_silent_and_preserves_proposal(runtime):
    request = req("Nie rozumiem polecenia minutnika. Powtórz je proszę.")
    runtime.pending_timers[request.session_key] = object()
    result = await runtime.process(request, None)
    assert result.status == "ignored" and result.speech == ""
    assert result.reason == "timer_response_echo"
    assert request.session_key in runtime.pending_timers
    assert not runtime.recorded_calls


@pytest.mark.parametrize(
    ("sample", "category"),
    [
        (req(), "success"),
        (replace(req(), dry_run=True), "success"),
        (req("fikcyjna kapibara fruwa"), "interpretation"),
        (req(user="missing"), "authorization"),
        (req("Nie rozumiem polecenia minutnika. Powtórz je proszę."), "ignored"),
    ],
)
async def test_runtime_records_one_metadata_trace_per_request(runtime, sample, category):
    result = await runtime.process(sample, None)
    report = runtime.diagnostics.snapshot()
    assert report["window_size"] == report["completed_since_reload"] == 1
    row = report["recent_requests"][0]
    assert result.request_id == row["request_id"]
    assert result.to_dict()["request_id"] == row["request_id"]
    assert row["category"] == category
    assert row["dry_run"] is sample.dry_run
    assert "source_context" in row["stages_ms"]
    assert "authorization" in row["stages_ms"]
    if category == "success":
        assert "handler" in row["stages_ms"]
    assert not runtime.admission.active


async def test_runtime_busy_is_recorded_without_waiting_or_changing_admission(runtime):
    request = req()
    with runtime.admission.enter(request.session_key):
        result = await runtime.process(request, None)
        assert runtime.admission.active == {request.session_key}
    row = runtime.diagnostics.snapshot()["recent_requests"][0]
    assert result.reason == "request_busy"
    assert row["category"] == "capacity"
    assert "handler" not in row["stages_ms"]
    assert not runtime.admission.active


async def test_runtime_concurrent_traces_correlate_independently(runtime):
    a, b = await asyncio.gather(
        runtime.process(req(user="alice", satellite_id="a"), None),
        runtime.process(req(user="bob", satellite_id="b", conversation="other"), None),
    )
    rows = runtime.diagnostics.snapshot()["recent_requests"]
    assert len(rows) == 2
    assert a.request_id != b.request_id
    assert {a.request_id, b.request_id} == {row["request_id"] for row in rows}
    assert all(row["category"] == "success" for row in rows)


async def test_runtime_cancelled_handler_records_interruption_and_reraises(runtime):
    waiting = WaitingFallback()
    runtime.fallback = LimitedFallback(waiting)
    task = asyncio.create_task(runtime.process(req("zrób przyjemny nastrój"), None))
    await asyncio.wait_for(waiting.entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    report = runtime.diagnostics.snapshot()
    assert report["window_size"] == report["completed_since_reload"] == 1
    assert report["recent_requests"][0]["category"] == "interrupted"
    assert "handler" in report["recent_requests"][0]["stages_ms"]
    assert not runtime.admission.active
    assert runtime.fallback.active == 0
    assert not runtime.recorded_calls


async def test_runtime_exception_diagnostics_do_not_store_private_exception(runtime):
    import json

    runtime.hass.auth.async_get_user.side_effect = RuntimeError("PRIVATE-auth-error")
    with pytest.raises(RuntimeError, match="PRIVATE-auth-error"):
        await runtime.process(req("PRIVATE-utterance"), None)
    report = runtime.diagnostics.snapshot()
    assert report["completed_since_reload"] == 1
    assert report["recent_requests"][0]["category"] == "internal_error"
    assert "PRIVATE" not in json.dumps(report)
    assert not runtime.admission.active


async def test_native_calendar_uses_the_same_handler_boundary_and_correlation(runtime, monkeypatch):
    import sys

    from custom_components.polish_context_voice.core.types import Result

    returned = Result("answered", "PRIVATE-calendar-title", route="ha_calendar", latency_ms=123)
    handler = AsyncMock(return_value=returned)
    monkeypatch.setitem(sys.modules, PREFIX + "calendar", NS(process=handler))
    result = await runtime.process(req("co mam jutro w kalendarzu"), None)
    row = runtime.diagnostics.snapshot()["recent_requests"][0]
    assert result is returned
    assert result.latency_ms == 123
    assert result.request_id == row["request_id"]
    assert row["route"] == "ha_calendar"
    assert "handler" in row["stages_ms"]
    assert "PRIVATE" not in str(row)
