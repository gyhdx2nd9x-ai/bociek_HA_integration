"""Replay synthetic feedback and keep its expectations independent of the actual result."""

import json
import subprocess
import sys

import pytest

from benchmark.feedback import replay
from custom_components.polish_context_voice.core.feedback import (
    FeedbackError,
    corpus_case,
    document,
)

VERSIONS = {"bociek": "0.10.0b24", "ha_core": "2026.11.2"}


def example(**changes):
    return document("example", {
        "text": "Zapal światła", "source_area": "kitchen", "expected_status": "dry_run",
        "operation": "turn_on", "targets": ["light.kitchen_main"], **changes,
    }, **VERSIONS)


@pytest.mark.parametrize(("changes", "field"), [
    ({"source_area": "private_room"}, "source_area"),
    ({"targets": ["light.some_home_device"]}, "targets"),
    ({"targets": ["light.kitchen_main", "light.kitchen_main"]}, "targets"),
    ({"expected_status": "executed"}, "expected_status"),
    ({"brightness_pct": 60}, "brightness_pct"),
    ({"operation": "brightness", "brightness_pct": 60.5}, "brightness_pct"),
])
def test_feedback_rejects_unmodelled_targets_and_invalid_expectation(changes, field):
    with pytest.raises(FeedbackError, match=field):
        example(**changes)


def test_feedback_does_not_import_live_fields_or_accept_unexpected_payload():
    report = example(session_id="private", addresses=["private"])
    assert "private" not in str(report)
    report["example"]["request"] = {"dry_run": False}
    with pytest.raises(FeedbackError, match="example"):
        corpus_case(report)


async def test_feedback_replay_reports_wrong_target_instead_of_loosening_expectations():
    actual = await replay(example(targets=["light.kitchen_task"]))
    assert not actual["result"]["passed"]
    turn = actual["result"]["turns"][0]
    assert turn["actual"]["executor_calls"] == []
    assert set(turn["mismatches"]) == {"targets", "calls"}
    assert turn["actual"]["targets"] == ("light.kitchen_main",)


async def test_feedback_replays_exact_brightness_and_confirmation_without_writes():
    report = example(
        text="Ustaw światła nad stołem na 35 procent", source_area="living_room",
        expected_status="confirm", operation="brightness", brightness_pct=35,
        targets=["light.living_table"],
    )
    actual = await replay(report)
    assert actual["case"] == corpus_case(report)
    assert actual["result"]["passed"], actual
    assert actual["result"]["turns"][0]["actual"]["executor_calls"] == []


async def test_feedback_handles_ambiguity_between_fictional_rooms():
    actual = await replay(example(
        text="Zapal lampkę", source_area=None, expected_status="clarify", operation=None,
        targets=[],
    ))
    assert actual["result"]["passed"], actual


def conversation(*turns, area="kitchen"):
    return document("dialogue", {"source_area": area, "turns": list(turns)}, **VERSIONS)


def turn(text, *, status="dry_run", operation=None, targets=(), **extra):
    return {
        "text": text, "expected_status": status, "operation": operation,
        "targets": list(targets), **extra,
    }


async def test_dialogue_feedback_replays_references_after_state_and_room_change():
    sample = conversation(
        turn("Zgaś zapalone światła", operation="turn_off", targets=["light.kitchen_main"],
             lights_off=["light.kitchen_task"]),
        turn("A w salonie?", operation="turn_off", targets=["light.living_main"],
             lights_off=["light.living_table"]),
        turn("Włącz je", operation="turn_on", targets=["light.living_main"],
             lights_off=["light.living_main"], lights_on=["light.living_table"]),
    )
    actual = await replay(sample)
    assert actual["result"]["passed"], actual
    assert [row["actual"]["area_ids"] for row in actual["result"]["turns"]] == [
        ("kitchen",), ("living_room",), ("living_room",),
    ]
    for row in actual["result"]["turns"]:
        assert row["actual"]["executor_calls"] == []


async def test_dialogue_feedback_replays_choice_before_and_after_it_expires():
    prompt = turn("Zapal lampkę", status="clarify", followup="yes")
    selected = turn("Nie to, tę drugą lampkę", status="confirm", operation="turn_on",
                    targets=["light.living_table"])
    timely = await replay(conversation(prompt, selected, area="living_room"))
    assert timely["result"]["passed"], timely
    too_late = await replay(conversation(
        prompt, turn(selected["text"], status="clarify", advance_seconds=31), area="living_room",
    ))
    assert too_late["result"]["passed"], too_late
    assert too_late["result"]["turns"][1]["actual"]["reason"] == "no_numbered_options"


@pytest.mark.parametrize(("extra", "field"), [
    ({"lights_on": ["light.private_room"]}, "lights_on"),
    ({"lights_on": ["light.kitchen_main"], "lights_off": ["light.kitchen_main"]}, "lights_off"),
    ({"advance_seconds": 601}, "advance_seconds"),
    ({"advance_seconds": True}, "advance_seconds"),
    ({"followup": "maybe"}, "followup"),
])
def test_dialogue_rejects_invalid_state_timing_and_reply_expectation(extra, field):
    first = turn("Zapal światła", operation="turn_on", targets=["light.kitchen_main"])
    second = turn("Zgaś je", operation="turn_off", targets=["light.kitchen_main"], **extra)
    with pytest.raises(FeedbackError, match=field):
        conversation(first, second)


def test_dialogue_rejects_private_context_and_execution_overrides():
    first = turn("Zapal światła", operation="turn_on", targets=["light.kitchen_main"])
    second = turn("Zgaś je", operation="turn_off", targets=["light.kitchen_main"])
    with pytest.raises(FeedbackError, match="turns"):
        conversation(first)
    with pytest.raises(FeedbackError, match="turns"):
        conversation(*(first for _ in range(7)))
    sample = conversation(first, second)
    sample["dialogue"]["turns"][1]["request"] = {"dry_run": False, "user_id": "private"}
    with pytest.raises(FeedbackError, match="dialogue"):
        corpus_case(sample)


def test_feedback_cli_names_the_later_turn_with_a_mismatch(tmp_path):
    sample = conversation(
        turn("Zapal światła", operation="turn_on", targets=["light.kitchen_main"]),
        turn("A w salonie?", operation="turn_on", targets=["light.living_table"]),
    )
    source, output = tmp_path / "sample.json", tmp_path / "output.json"
    source.write_text(json.dumps(sample))
    result = subprocess.run(
        [sys.executable, "-m", "benchmark.feedback", str(source), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1 and "turn 2: targets, calls" in result.stderr
    assert "turn 1" not in result.stderr
    assert not json.loads(output.read_text())["result"]["passed"]
