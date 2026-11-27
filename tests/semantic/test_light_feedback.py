from dataclasses import replace
from datetime import time

import pytest

from custom_components.polish_context_voice.core.communication import response_speech
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel
from custom_components.polish_context_voice.core.light_feedback import (
    light_state_feedback,
    snapshot_light_state,
)
from custom_components.polish_context_voice.core.types import (
    Action,
    Call,
    Proposal,
    Request,
    Result,
)


def light(identifier="light.first", state="off", **options):
    return Entity(identifier, identifier, "room", exposed=True, state=state, **options)


def plan(*identifiers, action=Action.OFF, **options):
    return Result(
        "ready",
        "Gotowe.",
        targets=tuple(identifiers),
        calls=tuple(Call("light", action.value, (identifier,)) for identifier in identifiers),
        **options,
    )


def executed(result, **changes):
    return replace(result, status="executed", completed=result.targets, **changes)


@pytest.mark.parametrize(
    ("action", "state", "speech"),
    [
        (Action.ON, "on", "To światło było już włączone."),
        (Action.OFF, "off", "To światło było już wyłączone."),
    ],
)
def test_single_light_already_matches_requested_state(action, state, speech):
    home = HomeModel(entities=[light(state=state)])
    result = plan("light.first", action=action)
    snapshot = snapshot_light_state(Proposal(action, "single_light"), home, result)
    assert snapshot is not None
    assert light_state_feedback(snapshot, executed(result)) == speech


def test_expanded_group_uses_each_leaf_not_the_parent_state():
    group = light(
        "light.group", state="on", attributes={"entity_id": ["light.first", "light.second"]}
    )
    home = HomeModel(entities=[group, light(), light("light.second")])
    result = plan("light.first", "light.second")
    snapshot = snapshot_light_state(Proposal(Action.OFF, "named", name="group"), home, result)
    assert light_state_feedback(snapshot, executed(result)) == "Te światła były już wyłączone."
    # A group's aggregate state alone cannot establish every bulb's state.
    assert (
        snapshot_light_state(
            Proposal(Action.ON, "named", name="group"), home, plan("light.group", action=Action.ON)
        )
        is None
    )


@pytest.mark.parametrize("second_state", ["on", "unknown", "unavailable", "", None])
def test_mixed_or_missing_leaf_state_does_not_claim_already_done(second_state):
    home = HomeModel(entities=[light(), light("light.second", state=second_state)])
    assert (
        snapshot_light_state(
            Proposal(Action.OFF, "lights"), home, plan("light.first", "light.second")
        )
        is None
    )


@pytest.mark.parametrize(
    "entity",
    [
        replace(light(), available=False),
        replace(light(), exposed=False),
        replace(light(), platform="group"),
        replace(light(), id="switch.first"),
    ],
)
def test_only_current_available_exposed_light_leaves_are_eligible(entity):
    assert (
        snapshot_light_state(
            Proposal(Action.OFF, "named"), HomeModel(entities=[entity]), plan(entity.id)
        )
        is None
    )


def test_missing_entity_and_empty_target_plan_are_not_feedback_candidates():
    proposal = Proposal(Action.OFF, "lights")
    assert snapshot_light_state(proposal, HomeModel(), plan("light.first")) is None
    assert snapshot_light_state(proposal, HomeModel(), plan()) is None


@pytest.mark.parametrize(
    "proposal",
    [
        Proposal(Action.BRIGHTNESS, "lights", value=0),
        Proposal(Action.ON, "lights", value=50),
        Proposal(Action.OFF, "lights", value=0),
        Proposal(Action.TEMPERATURE, "sensor"),
        Proposal(Action.CLOSE, "cover"),
    ],
)
def test_brightness_or_other_actions_never_receive_on_off_feedback(proposal):
    assert (
        snapshot_light_state(proposal, HomeModel(entities=[light()]), plan("light.first")) is None
    )


@pytest.mark.parametrize(
    "calls",
    [
        (),
        (Call("light", "turn_on", ("light.first",)),),
        (Call("light", "turn_off", ("light.first",), {"transition": 0}),),
        (Call("light", "turn_off", ("light.first",), {"brightness_pct": 0}),),
        (Call("switch", "turn_off", ("light.first",)),),
        (Call("light", "turn_off", ("light.other",)),),
        (Call("light", "turn_off", ()),),
        (Call("light", "turn_off", ("switch.first",)),),
        (
            Call("light", "turn_off", ("light.first",)),
            Call("switch", "turn_off", ("switch.first",)),
        ),
        (
            Call("light", "turn_off", ("light.first",)),
            Call("light", "turn_off", ("light.first",)),
        ),
    ],
)
def test_only_pure_light_calls_exactly_covering_targets_are_eligible(calls):
    result = replace(plan("light.first"), calls=calls)
    assert (
        snapshot_light_state(Proposal(Action.OFF, "lights"), HomeModel(entities=[light()]), result)
        is None
    )


def test_batched_calls_and_different_completion_order_are_supported():
    home = HomeModel(entities=[light(), light("light.second")])
    result = replace(
        plan("light.first", "light.second"),
        calls=(Call("light", "turn_off", ("light.first", "light.second")),),
    )
    snapshot = snapshot_light_state(Proposal(Action.OFF, "lights"), home, result)
    completed = replace(result, status="executed", completed=("light.second", "light.first"))
    assert light_state_feedback(snapshot, completed) == "Te światła były już wyłączone."


@pytest.mark.parametrize("status", ["partial", "error", "dry_run", "ready", "confirm", "blocked"])
def test_successful_complete_execution_is_required(status):
    result = plan("light.first")
    snapshot = snapshot_light_state(
        Proposal(Action.OFF, "lights"), HomeModel(entities=[light()]), result
    )
    assert (
        light_state_feedback(snapshot, replace(result, status=status, completed=result.targets))
        is None
    )


@pytest.mark.parametrize(
    "completed",
    [(), ("light.other",), ("light.first", "light.other"), ("light.first", "light.first")],
)
def test_incomplete_or_extra_completions_do_not_receive_feedback(completed):
    result = plan("light.first")
    snapshot = snapshot_light_state(
        Proposal(Action.OFF, "lights"), HomeModel(entities=[light()]), result
    )
    assert (
        light_state_feedback(snapshot, replace(result, status="executed", completed=completed))
        is None
    )


def test_snapshot_cannot_be_reused_for_different_targets_or_changed_call_data():
    result = plan("light.first")
    snapshot = snapshot_light_state(
        Proposal(Action.OFF, "lights"), HomeModel(entities=[light()]), result
    )
    assert light_state_feedback(snapshot, executed(plan("light.second"))) is None
    result.calls[0].data["transition"] = 1
    assert light_state_feedback(snapshot, executed(result)) is None
    assert light_state_feedback(None, executed(result)) is None


def test_feedback_is_based_on_pre_execution_state_not_the_eventual_state():
    home = HomeModel(entities=[light(state="on")])
    result = plan("light.first")
    snapshot = snapshot_light_state(Proposal(Action.OFF, "lights"), home, result)
    home.entities["light.first"] = light(state="off")
    assert light_state_feedback(snapshot, executed(result)) is None
    assert snapshot_light_state(Proposal(Action.OFF, "lights"), home, result) is not None


def test_confirmed_pure_light_write_can_receive_feedback_after_execution():
    result = replace(plan("light.first"), status="confirm")
    snapshot = snapshot_light_state(
        Proposal(Action.OFF, "lights"), HomeModel(entities=[light()]), result
    )
    assert light_state_feedback(snapshot, executed(result)) == "To światło było już wyłączone."


def test_quiet_hours_continue_to_silence_local_success_feedback():
    home = HomeModel(
        areas=[Area("room", "Pokój")],
        entities=[light()],
        satellites={"assist_satellite.room": "room"},
    )
    result = plan("light.first", route="fast", area_ids=("room",))
    snapshot = snapshot_light_state(Proposal(Action.OFF, "lights"), home, result)
    result = executed(result)
    result.speech = light_state_feedback(snapshot, result)
    request = Request("wyłącz światła", satellite_id="assist_satellite.room")
    assert response_speech(result, request, home, {"quiet_ack_enabled": True}, time(21)) == ""
    assert response_speech(result, request, home, {"quiet_ack_enabled": True}, time(12)) == (
        "To światło było już wyłączone."
    )


async def test_engine_feedback_reuses_final_plan_model_and_never_serializes_observation(executor):
    home = HomeModel(
        areas=[Area("room", "Pokój")],
        entities=[light(labels=frozenset({"voice_all_off"}))],
    )
    reads = []

    def provider():
        reads.append(True)
        return home

    result = await Engine(provider, executor).process(Request("wyłącz światła", source_area="room"))
    assert result.status == "executed" and result.completed == ("light.first",)
    assert result.speech == "To światło było już wyłączone."
    assert len(executor.calls) == 1  # The usual idempotent HA call is still performed.
    assert len(reads) == 2  # Existing initial plan and fresh revalidation, no third read.
    assert "_light_state_snapshot" not in result.to_dict()


@pytest.mark.parametrize("dry_run,denied", [(True, False), (False, True)])
async def test_engine_preview_or_failed_preflight_never_gets_state_feedback(
    executor, dry_run, denied
):
    home = HomeModel(
        areas=[Area("room", "Pokój")],
        entities=[light(labels=frozenset({"voice_all_off"}))],
    )
    executor.deny = denied
    result = await Engine(lambda: home, executor).process(
        Request("wyłącz światła", source_area="room", dry_run=dry_run)
    )
    assert result.status == ("dry_run" if dry_run else "error")
    assert "już" not in result.speech
    assert not result.completed and not executor.calls


@pytest.mark.parametrize(("name", "alias", "spoken", "state", "speech"), [
    ("Kinkiet nad łóżkiem", (), "Zgaś kinkiet nad łóżkiem", "off",
     "Kinkiet był już wyłączony."),
    ("Bedside light", ("lewa lampka",), "Włącz lewą lampkę", "on",
     "Lampka była już włączona."),
    ("Prawe światło", (), "Włącz prawe światło", "on",
     "Światło było już włączone."),
    ("Girlanda", (), "Zgaś girlandę", "off",
     "Girlanda była już wyłączona."),
    ("Lumos", (), "Wyłącz Lumos", "off", "To światło było już wyłączone."),
])
async def test_named_light_state_feedback_agrees_with_the_spoken_noun(
    executor, name, alias, spoken, state, speech,
):
    item = replace(light(state=state), name=name, aliases=alias)
    home = HomeModel(areas=[Area("room", "Pokój")], entities=[item])
    result = await Engine(lambda: home, executor).process(Request(spoken, source_area="room"))
    assert result.completed == (item.id,)
    assert result.speech == speech
