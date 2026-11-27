"""Catalog facts describe a plan, cached read or executor outcome precisely."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from custom_components.polish_context_voice.core.capability_contracts import annotate_capability
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.types import Action, Proposal, Request


def model_with(home, *entities):
    updated = dict(home.entities)
    updated.update({entity.id: entity for entity in entities})
    return HomeModel(
        home.areas.values(),
        home.floors.values(),
        updated.values(),
        satellites=home.satellites,
        devices=home.devices,
    )


def request(text, **extras):
    return Request(text, **({"source_area": "living_room", "conversation_id": "contract"} | extras))


def report(result):
    return result.response_data["capability"]


async def test_brightness_preview_is_requested_value_and_not_completion(home, executor):
    result = await Engine(lambda: home, executor).process(
        request("ustaw światła na 35%", dry_run=True)
    )
    data = report(result)
    assert result.status == "dry_run" and not result.completed and not executor.calls
    assert data["id"] == "light.set_brightness" and data["stage"] == "preview"
    assert data["evidence"] == "validated_plan" and data["completed_target_count"] == 0
    assert data["facts"]["value"] == 35 and data["facts"]["unit"] == "%"
    assert data["facts"]["value_role"] == "requested"
    assert data["facts"]["source"] == "validated_parameter"
    assert "_capability_report" not in result.to_dict()
    json.dumps(data, allow_nan=False)


async def test_successful_write_reports_executor_return_without_converting_request_to_observation(
    home, executor
):
    result = await Engine(lambda: home, executor).process(request("ustaw światła na 35%"))
    data = report(result)
    assert result.status == "executed" and len(executor.calls) == 1
    assert data["stage"] == "executed" and data["evidence"] == "executor_returned"
    assert data["completed_target_count"] == len(result.completed) == 1
    assert data["facts"]["value_role"] == "requested"
    assert executor.calls[0].data == {"brightness_pct": 35}


async def test_confirmation_and_repeated_confirmation_keep_existing_single_use_rules(
    home, executor
):
    lamp = replace(
        home.entities["light.living_room_main"],
        labels=frozenset({"voice_default_on", "voice_confirm"}),
    )
    model = model_with(home, lamp)
    engine = Engine(lambda: model, executor)
    question = await engine.process(request("ustaw światła na 35%"))
    assert report(question)["stage"] == "confirmation_required" and not executor.calls
    done = await engine.process(request("potwierdzam"))
    assert report(done)["evidence"] == "executor_returned" and len(executor.calls) == 1
    repeated = await engine.process(request("potwierdzam"))
    assert repeated.reason == "no_pending" and len(executor.calls) == 1


@pytest.mark.parametrize("dry_run", [False, True])
async def test_temperature_is_cached_observation_with_unchanged_speech_and_no_service(
    home, executor, dry_run
):
    result = await Engine(lambda: home, executor).process(
        request("jaka jest temperatura", dry_run=dry_run)
    )
    data = report(result)
    assert result.status == "answered" and not result.completed and not executor.calls
    assert data["id"] == "sensor.read_temperature" and data["stage"] == "answered"
    assert data["evidence"] == "ha_state_cache" and data["facts"]["value_role"] == "observed"
    assert data["facts"]["source"] == "ha_state_cache"
    assert data["facts"]["value"] == 21.5 and data["facts"]["unit"] == "°C"
    assert result.speech == "Jest około 22 stopni."


@pytest.mark.parametrize("failure", ["preflight", "execute", "no_executor"])
async def test_failed_execution_never_claims_executor_return_or_completion(home, executor, failure):
    if failure == "preflight":
        executor.deny = True
    if failure == "execute":
        executor.fail_at = 0
    result = await Engine(lambda: home, None if failure == "no_executor" else executor).process(
        request("ustaw światła na 35%")
    )
    data = report(result)
    assert result.status == "error" and not result.completed
    assert data["stage"] == "failed" and data["evidence"] == "none"
    assert data["completed_target_count"] == 0 and data["facts"]["value_role"] == "requested"


async def test_partial_write_reports_only_completed_target_count(home, executor):
    extra = replace(
        home.entities["light.living_room_main"], id="light.extra", name="Drugie światło"
    )
    model = model_with(home, extra)
    executor.fail_at = 1
    result = await Engine(lambda: model, executor).process(request("ustaw światła na 35%"))
    data = report(result)
    assert result.status == "partial" and len(result.completed) == 1
    assert data["stage"] == "partial" and data["evidence"] == "executor_partial_return"
    assert data["completed_target_count"] == 1 and len(result.targets) == 2
    assert data["facts"]["value_role"] == "requested"


@pytest.mark.parametrize("value", [101, -1])
async def test_rejected_range_is_not_reported_as_a_plan(home, executor, value):
    result = Engine(lambda: home, executor).plan(
        Proposal(Action.BRIGHTNESS, "light", value=value), request("synthetic proposal")
    )
    data = report(result)
    assert result.status == "blocked" and not executor.calls
    assert data["stage"] == "rejected" and data["evidence"] == "none"
    assert data["facts"]["value"] is None


async def test_unresolved_scope_has_no_observed_state_or_execution_evidence(home, executor):
    result = await Engine(lambda: home, executor).process(
        request("jaka jest temperatura w piwnicy")
    )
    data = report(result)
    assert result.status == "clarify" and not executor.calls
    assert data["stage"] == "clarification_required" and data["evidence"] == "none"
    assert data["facts"] is None


async def test_typed_report_is_immutable_and_json_mutation_cannot_rewrite_it(home, executor):
    result = await Engine(lambda: home, executor).process(
        request("ustaw światła na 35%", dry_run=True)
    )
    typed = result._capability_report
    with pytest.raises(FrozenInstanceError):
        typed.evidence = "physical_verification"
    report(result)["facts"]["value"] = 99
    annotate_capability(result)
    assert report(result)["facts"]["value"] == 35


async def test_non_migrated_on_off_has_no_catalog_report_and_same_model_reads(home, executor):
    reads = []

    def provider():
        reads.append(True)
        return home

    result = await Engine(provider, executor).process(request("zapal światła"))
    assert result.status == "executed" and "capability" not in result.response_data
    assert len(reads) == 2 and len(executor.calls) == 1
