from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.types import Request


async def test_medium_risk_confirmation_and_single_use(engine, executor):
    request = Request(
        "włącz telewizor", source_area="living_room", conversation_id="c", user_id="u"
    )
    assert (await engine.process(request)).status == "confirm"
    assert not executor.calls
    assert (await engine.process(replace(request, text="potwierdzam"))).status == "executed"
    assert (await engine.process(replace(request, text="potwierdzam"))).reason == "no_pending"
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "other"},
        {"conversation_id": "other"},
        {"satellite_id": "other"},
        {"source_area": "kitchen"},
        {"dry_run": True},
    ],
)
async def test_confirmation_cannot_cross_context(engine, executor, change):
    request = Request(
        "włącz telewizor", source_area="living_room", conversation_id="c", user_id="u"
    )
    await engine.process(request)
    result = await engine.process(replace(request, text="potwierdzam", **change))
    assert result.reason == "no_pending"
    assert not executor.calls


async def test_expired_confirmation(home, executor):
    now = [0]
    engine = Engine(lambda: home, executor, clock=lambda: now[0])
    request = Request("włącz telewizor", source_area="living_room", conversation_id="c")
    await engine.process(request)
    now[0] = 31
    assert (await engine.process(replace(request, text="tak"))).reason == "no_pending"
    assert not executor.calls


async def test_confirmation_revalidates_exposure(home, engine, executor):
    request = Request("włącz telewizor", source_area="living_room", conversation_id="c")
    await engine.process(request)
    home.entities["media_player.tv"] = replace(home.entities["media_player.tv"], exposed=False)
    assert (await engine.process(replace(request, text="tak"))).reason == "plan_changed"
    assert not executor.calls


async def test_intervening_utterance_cancels_confirmation(engine, executor):
    request = Request("włącz telewizor", source_area="living_room", conversation_id="c")
    await engine.process(request)
    await engine.process(replace(request, text="co to znaczy"))
    assert (await engine.process(replace(request, text="tak"))).reason == "no_pending"
    assert not executor.calls


async def test_cancel(engine, executor):
    request = Request("włącz telewizor", source_area="living_room", conversation_id="c")
    await engine.process(request)
    assert (await engine.process(replace(request, text="anuluj"))).status == "cancelled"
    assert (await engine.process(replace(request, text="tak"))).reason == "no_pending"


async def test_high_risk_never_executes(engine, executor):
    result = await engine.process(Request("otwórz bramę", source_area="kitchen"))
    # Proper noun morphology should be an HA alias, not guessed.
    assert result.status in {"blocked", "clarify"}
    result = await engine.process(Request("otwórz brama", source_area="kitchen"))
    assert result.reason == "high_risk_disabled"
    assert not executor.calls


@pytest.mark.parametrize("text", ["ustaw światła na 101%", "ustaw temperaturę na 99 stopni"])
async def test_out_of_range(engine, executor, text):
    result = await engine.process(Request(text, source_area="living_room"))
    assert result.status == "blocked"
    assert not executor.calls


async def test_preflight_all_before_any_write(engine, executor):
    executor.deny = True
    result = await engine.process(Request("wyłącz światła", source_area="living_room"))
    assert result.status == "error"
    assert len(executor.checked) == 3
    assert not executor.calls


async def test_partial_failure_never_reports_success(engine, executor):
    executor.fail_at = 1
    result = await engine.process(Request("wyłącz światła", source_area="living_room"))
    assert result.status == "partial"
    assert result.completed == ("light.living_room_ambient",)
    assert len(executor.calls) == 1


async def test_dry_run_never_calls_executor(engine, executor):
    result = await engine.process(Request("zapal światła", source_area="living_room", dry_run=True))
    assert result.status == "dry_run"
    assert not executor.calls and not executor.checked


async def test_unavailable_does_not_silently_drop_target(home, engine, executor):
    home.entities["light.living_room_main"] = replace(
        home.entities["light.living_room_main"], available=False
    )
    result = await engine.process(Request("wyłącz światła", source_area="living_room"))
    assert result.reason == "ineligible_target"
    assert result.speech == "Nie mogę ci z tym pomóc."
    assert not executor.calls
