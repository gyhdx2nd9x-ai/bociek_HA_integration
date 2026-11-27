"""Trust the offline evaluator only after testing its failure and isolation paths."""

import json
import sys
from copy import deepcopy
from hashlib import sha256

import pytest

from benchmark import corpus


def house(identifier="light.main"):
    return {
        "areas": [{"id": "room", "name": "Salon", "aliases": ["salon"]}],
        "entities": [
            {
                "id": identifier,
                "name": "Lampa",
                "area_id": "room",
                "exposed": True,
                "available": True,
                "labels": ["voice_default_on", "voice_all_off"],
            }
        ],
    }


def call(*entities, data=None, guards=None):
    value = {
        "domain": "light",
        "service": "turn_on",
        "entity_ids": list(entities),
        "data": {} if data is None else data,
    }
    if guards is not None:
        value["guard_entity_ids"] = guards
    return value


def turn(text="zapal światła", **request):
    return {
        "request": {"text": text, "source_area": "room", **request},
        "expected": {"status": "dry_run", "targets": ["light.main"], "calls": [call("light.main")]},
    }


def case(*turns, **metadata):
    return {
        "id": "test",
        "split": "regression",
        "family": "test-family",
        "category": "command",
        "layer": "engine",
        "home": "test-house",
        "turns": list(turns) or [turn()],
        **metadata,
    }


def test_comparison_keeps_exact_targets_service_and_data():
    wanted = {"targets": ["light.main"], "calls": [call("light.main", data={"brightness_pct": 40})]}
    wrong = {
        "targets": ["light.other"],
        "calls": [call("light.other", data={"brightness_pct": 41})],
    }
    assert set(corpus.compare(wrong, wanted)) == {"targets", "calls"}
    wrong = deepcopy(wanted)
    wrong["calls"][0]["service"] = "turn_off"
    assert corpus.compare(wrong, wanted) == ["calls"]


def test_grouping_is_irrelevant_but_duplicate_operations_are_not():
    grouped = {"calls": [call("light.a", "light.b")]}
    separate = {"calls": [call("light.b"), call("light.a")]}
    assert not corpus.compare(grouped, separate)
    separate["calls"].append(call("light.a"))
    assert "calls" in corpus.compare(grouped, separate)


def test_numeric_service_data_equivalence_does_not_merge_booleans_with_numbers():
    integer = {"calls": [call("light.a", data={"brightness_pct": 40})]}
    floating = {"calls": [call("light.a", data={"brightness_pct": 40.0})]}
    assert not corpus.compare(integer, floating)
    boolean = {"calls": [call("light.a", data={"brightness_pct": True})]}
    numeric = {"calls": [call("light.a", data={"brightness_pct": 1})]}
    assert "calls" in corpus.compare(boolean, numeric)


def test_empty_target_call_cannot_disappear_from_safety_comparison():
    # An emitted service call with no target must never look like no call at all.
    assert "calls" in corpus.compare({"calls": [call()]}, {"calls": []})


def test_guards_stay_attached_to_the_operation_they_protect():
    wanted = {
        "calls": [
            call("light.a", guards=["light.member_a"]),
            call("light.b", guards=["light.member_b"]),
        ]
    }
    wrong = {
        "calls": [
            call("light.a", guards=["light.member_b"]),
            call("light.b", guards=["light.member_a"]),
        ]
    }
    assert corpus.compare(wrong, wanted)


@pytest.mark.parametrize("actual,expected", [(None, {}), ({}, None), ({"operation": "time"}, None)])
def test_parser_none_does_not_equal_empty_or_nonempty_result(actual, expected):
    assert corpus.compare({"capability": actual}, {"capability": expected})


def test_subset_expectation_still_requires_asserted_slot():
    assert corpus.compare(
        {"capability": {"operation": "time"}}, {"capability": {"operation": "time", "domain": ""}}
    )


@pytest.mark.parametrize("broken", [None, [], "case"])
def test_invalid_top_level_case_is_a_corpus_error(broken):
    with pytest.raises(corpus.CorpusError):
        corpus.validate_cases([broken], {"test-house": house()})


@pytest.mark.parametrize(
    "changes",
    [
        {"turns": [None]},
        {"turns": [{"request": {"text": "test"}, "expected": []}]},
        {
            "turns": [
                {
                    "request": {"text": "test", "dry_run": "false"},
                    "expected": {"status": "unsupported", "targets": [], "calls": []},
                }
            ]
        },
    ],
)
def test_invalid_turn_or_request_is_rejected_before_engine(changes):
    with pytest.raises(corpus.CorpusError):
        corpus.validate_cases([case(**changes)], {"test-house": house()})


@pytest.mark.parametrize(
    "expected",
    [
        {"status_in": "dry_run", "targets": [], "calls": []},
        {"status_in": [], "targets": [], "calls": []},
        {"status": "dry_run", "targets": [None], "calls": []},
        {"status": "dry_run", "targets": [], "calls": [call()]},
        {"status": "dry_run", "targets": [], "calls": [None]},
    ],
)
def test_invalid_plan_expectations_cannot_create_vacuous_passes(expected):
    value = turn()
    value["expected"] = expected
    with pytest.raises(corpus.CorpusError):
        corpus.validate_cases([case(value)], {"test-house": house()})


def test_executed_case_requires_an_explicit_recorded_write_expectation():
    value = turn(dry_run=False)
    value["expected"]["status"] = "executed"
    with pytest.raises(corpus.CorpusError):
        corpus.validate_cases([case(value)], {"test-house": house()})


@pytest.mark.parametrize("advance", [True, float("nan"), float("inf"), -1])
def test_virtual_clock_must_advance_by_finite_nonnegative_number(advance):
    value = turn()
    value["advance_seconds"] = advance
    with pytest.raises(corpus.CorpusError):
        corpus.validate_cases([case(value)], {"test-house": house()})


def test_family_cannot_cross_regression_and_evaluation():
    second = case(turn("wyłącz światła"), id="other", split="evaluation")
    with pytest.raises(corpus.CorpusError, match="family"):
        corpus.validate_cases([case(), second], {"test-house": house()})


def test_duplicate_normalized_text_cannot_be_fresh_evaluation():
    second = case(turn("  ZAPAL   ŚWIATŁA!"), id="other", split="evaluation", family="new")
    with pytest.raises(corpus.CorpusError, match="utterance"):
        corpus.validate_cases([case(), second], {"test-house": house()})


async def test_default_plan_does_not_execute_even_with_recording_adapter():
    result = await corpus.run_case(case(), house())
    assert result["passed"], result
    assert result["turns"][0]["actual"]["executor_calls"] == []


async def test_simulated_execution_is_checked_independently_of_plan(monkeypatch):
    class ExtraWrite(corpus.RecordingExecutor):
        async def execute(self, operation, request):
            await super().execute(operation, request)
            self.calls.append(call("light.unexpected"))

    monkeypatch.setattr(corpus, "RecordingExecutor", ExtraWrite)
    value = turn(dry_run=False)
    value["expected"].update(status="executed", executor_calls=[call("light.main")])
    result = await corpus.run_case(case(value), house())
    assert not result["passed"]
    assert "executor_calls" in result["turns"][0]["mismatches"]


async def test_each_case_gets_fresh_house_and_conversation():
    first = case(turn(conversation_id="same"), id="first")
    assert (await corpus.run_case(first, house()))["passed"]
    followup = turn("zgaś je", conversation_id="same")
    followup["expected"] = {
        "status": "clarify",
        "reason": "no_reference",
        "targets": [],
        "calls": [],
    }
    assert (await corpus.run_case(case(followup, id="second"), house()))["passed"]
    other = turn()
    other["expected"].update(targets=["light.other"], calls=[call("light.other")])
    assert (await corpus.run_case(case(other), house("light.other")))["passed"]


async def test_dialogue_clock_expiry_uses_virtual_clock_without_sleep():
    followup = turn("zgaś je")
    followup["advance_seconds"] = 60
    followup["expected"] = {
        "status": "clarify",
        "reason": "no_reference",
        "targets": [],
        "calls": [],
    }
    result = await corpus.run_case(case(turn(), followup), house())
    assert result["passed"], result


async def test_mutation_rebuilds_model_but_does_not_mutate_input_fixture():
    data = house()
    before = deepcopy(data)
    followup = turn("zgaś je")
    followup["mutations"] = [{"entity_id": "light.main", "changes": {"exposed": False}}]
    followup["expected"] = {
        "status": "clarify",
        "reason": "context_changed",
        "targets": [],
        "calls": [],
    }
    result = await corpus.run_case(case(turn(), followup), data)
    assert result["passed"], result
    assert data == before


async def test_parser_exception_is_not_reported_as_language_mismatch(monkeypatch):
    def broken_parser(text):
        raise RuntimeError("evaluator dependency failed")

    monkeypatch.setattr(corpus, "parse_capability", broken_parser)
    value = turn("która jest godzina")
    value["expected"] = {"capability": None}
    with pytest.raises((RuntimeError, corpus.CorpusError)):
        await corpus.run_case(case(value, layer="capability_parse"), house())


def test_cli_invalid_corpus_exits_two_without_publishing_a_report(monkeypatch, tmp_path):
    async def invalid_run(*args, **kwargs):
        raise corpus.CorpusError("broken expectation")

    output = tmp_path / "report.json"
    monkeypatch.setattr(corpus, "run_corpus", invalid_run)
    monkeypatch.setattr(sys, "argv", ["corpus", "--output", str(output)])
    with pytest.raises(SystemExit) as exited:
        corpus.main()
    assert exited.value.code == 2
    assert not output.exists()


async def test_replay_filters_exact_cases_and_records_only_selected_scope():
    report = await corpus.run_corpus(split="regression", case_ids=["CTX-001", "CTX-003"])
    assert report["case_filter"] == ["CTX-001", "CTX-003"]
    assert {row["id"] for row in report["results"]} == {"CTX-001", "CTX-003"}
    assert report["summary"]["cases"] == 2
    assert report["by_split"]["evaluation"]["cases"] == 0
    assert report["passed"]


async def test_replay_rejects_unknown_or_differently_split_cases():
    with pytest.raises(corpus.CorpusError, match="Unknown cases in this split: CTX-001, typo"):
        await corpus.run_corpus(split="evaluation", case_ids=["CTX-001", "typo"])


@pytest.mark.parametrize(
    "regression_failed,evaluation_failed,strict,exit_code",
    [
        (0, 0, False, 0),
        (0, 1, False, 0),
        (0, 1, True, 1),
        (1, 0, False, 1),
    ],
)
def test_cli_distinguishes_regression_gate_and_strict_evaluation_gate(
    monkeypatch, tmp_path, regression_failed, evaluation_failed, strict, exit_code
):
    report = {
        "component_version": "test",
        "selection": "all",
        "summary": {},
        "by_layer": {},
        "unique_texts": 1,
        "passed": not (regression_failed or evaluation_failed),
        "by_split": {
            "regression": {"failed": regression_failed},
            "evaluation": {"failed": evaluation_failed},
        },
    }

    async def fake_run(*args, **kwargs):
        return report

    output = tmp_path / "report.json"
    monkeypatch.setattr(corpus, "run_corpus", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["corpus", "--output", str(output)] + (["--strict"] if strict else [])
    )
    with pytest.raises(SystemExit) as exited:
        corpus.main()
    assert exited.value.code == exit_code
    assert json.loads(output.read_text()) == report


def write_curated_fixture(folder):
    """A tiny frozen evaluation, independent of the real project's evaluation data."""
    (folder / "homes").mkdir()
    home_path = folder / "homes/test-house.json"
    home_path.write_text(json.dumps(house()))
    regression = case(id="regression")
    evaluation = case(
        turn("  ZAPAL   ŚWIATŁA!"),
        turn("zgaś je"),
        id="evaluation-overlap",
        family="evaluation-family",
        split="evaluation",
    )
    for name in corpus.CASE_FILES:
        values = (
            [regression]
            if name == "engine.json"
            else ([evaluation] if name == "evaluation.json" else [])
        )
        (folder / name).write_text(json.dumps(values))
    evaluation_path = folder / "evaluation.json"
    freeze = {
        "sha256": sha256(evaluation_path.read_bytes()).hexdigest(),
        "homes": {"test-house": {"sha256": sha256(home_path.read_bytes()).hexdigest()}},
    }
    (folder / "evaluation-freeze.json").write_text(json.dumps(freeze))
    exclusions = [
        {
            "id": "evaluation-overlap",
            "reason": "exact_utterance_overlap",
            "detected_before_first_scored_run": True,
        }
    ]
    (folder / "evaluation-exclusions.json").write_text(json.dumps(exclusions))
    return regression, evaluation


async def test_curation_excludes_whole_overlapping_dialogue_before_any_execution(
    monkeypatch, tmp_path
):
    write_curated_fixture(tmp_path)
    original_run = corpus.run_case
    executed = []

    async def observed_run(value, home):
        executed.append(value["id"])
        return await original_run(value, home)

    monkeypatch.setattr(corpus, "run_case", observed_run)
    report = await corpus.run_corpus(tmp_path)
    assert executed == ["regression"]
    assert report["curation"]["authored_cases"] == 2
    assert report["curation"]["excluded_before_execution"][0]["id"] == "evaluation-overlap"
    assert report["by_split"]["evaluation"]["cases"] == 0
    assert report["summary"]["turns"] == 1


@pytest.mark.parametrize("name", ["evaluation.json", "homes/test-house.json"])
def test_freeze_rejects_even_valid_json_changes_to_evaluation_or_its_home(tmp_path, name):
    write_curated_fixture(tmp_path)
    path = tmp_path / name
    path.write_text(path.read_text() + "\n")
    with pytest.raises(corpus.CorpusError, match="Frozen evaluation"):
        corpus.curated_cases(corpus.load_cases(tmp_path), tmp_path)


def test_curation_never_rewrites_frozen_bytes_or_authored_cases(tmp_path):
    write_curated_fixture(tmp_path)
    values = corpus.load_cases(tmp_path)
    before = deepcopy(values)
    frozen = (tmp_path / "evaluation.json").read_bytes()
    selected, _ = corpus.curated_cases(values, tmp_path)
    assert [item["id"] for item in selected] == ["regression"]
    assert values == before
    assert (tmp_path / "evaluation.json").read_bytes() == frozen


@pytest.mark.parametrize("identifier", ["regression", "missing"])
def test_exclusion_cannot_remove_regression_or_an_unknown_case(tmp_path, identifier):
    write_curated_fixture(tmp_path)
    (tmp_path / "evaluation-exclusions.json").write_text(
        json.dumps(
            [
                {
                    "id": identifier,
                    "reason": "exact_utterance_overlap",
                    "detected_before_first_scored_run": True,
                },
            ]
        )
    )
    with pytest.raises(corpus.CorpusError):
        corpus.curated_cases(corpus.load_cases(tmp_path), tmp_path)


def test_exclusion_cannot_remove_a_unique_failing_candidate(tmp_path):
    write_curated_fixture(tmp_path)
    evaluation = case(
        turn("odpowiedz na nieznane polecenie"),
        id="evaluation-overlap",
        split="evaluation",
        family="evaluation-family",
    )
    values = [case(id="regression"), evaluation]
    with pytest.raises(corpus.CorpusError, match="no exact regression overlap"):
        corpus.curated_cases(values, tmp_path)


@pytest.mark.parametrize(
    "layer,field",
    [
        ("capability_parse", "capability"),
        ("calendar_parse", "command"),
        ("engine", "interpretation"),
    ],
)
def test_empty_parser_subset_cannot_be_used_as_a_vacuous_expectation(layer, field):
    value = turn()
    value["expected"] = {**value["expected"], field: {}} if layer == "engine" else {field: {}}
    with pytest.raises(corpus.CorpusError, match="Empty or invalid"):
        corpus.validate_cases([case(value, layer=layer)], {"test-house": house()})


async def test_success_status_without_recorded_execution_cannot_pass(monkeypatch):
    class MissingWrite(corpus.RecordingExecutor):
        async def execute(self, operation, request):
            pass

    monkeypatch.setattr(corpus, "RecordingExecutor", MissingWrite)
    value = turn(dry_run=False)
    value["expected"].update(status="executed", executor_calls=[call("light.main")])
    result = await corpus.run_case(case(value), house())
    assert result["turns"][0]["actual"]["status"] == "executed"
    assert not result["passed"]
    assert "executor_calls" in result["turns"][0]["mismatches"]
