"""A declared regression contract revision cannot rewrite an evaluation oracle."""

import json
import shutil
from copy import deepcopy
from hashlib import sha256

import pytest

from benchmark import corpus


@pytest.fixture
def revision_folder(tmp_path):
    folder = tmp_path / "corpus"
    shutil.copytree(corpus.DEFAULT_CORPUS, folder)
    return folder


def raw_cases(folder):
    return [case for name in corpus.CASE_FILES for case in json.loads((folder / name).read_text())]


def write_revision(folder, mutate):
    path = folder / corpus.EXPECTATION_UPDATE_FILE
    document = json.loads(path.read_text())
    mutate(document)
    path.write_text(json.dumps(document))


def test_v3_changes_exactly_one_expected_plan_and_never_mutates_inputs_or_files():
    folder = corpus.DEFAULT_CORPUS
    original = raw_cases(folder)
    saved = deepcopy(original)
    file_hashes = {
        name: sha256((folder / name).read_bytes()).hexdigest() for name in corpus.CASE_FILES
    }
    revised = corpus.apply_expectation_update(original, folder)
    assert original == saved and revised is not original
    changes = [
        (before, after) for before, after in zip(original, revised, strict=True) if before != after
    ]
    assert len(changes) == 1
    before, after = changes[0]
    assert before["id"] == after["id"] == "E121"
    assert before["split"] == after["split"] == "regression"
    assert before["turns"][0]["expected"] == corpus.E121_V2_EXPECTED
    assert after["turns"][0]["expected"] == corpus.E121_V3_EXPECTED
    after["turns"][0]["expected"] = before["turns"][0]["expected"]
    assert after == before, "No request, identity, home or metadata may change"
    assert file_hashes == {
        name: sha256((folder / name).read_bytes()).hexdigest() for name in corpus.CASE_FILES
    }
    freeze = json.loads((folder / "evaluation-freeze.json").read_text())
    assert file_hashes["evaluation.json"] == freeze["sha256"]
    assert file_hashes["engine.json"] == corpus.V2_ENGINE_SHA256


def test_v3_requires_unchanged_bytes_of_original_engine_file(revision_folder):
    path = revision_folder / "engine.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(corpus.CorpusError, match="unchanged original"):
        corpus.load_cases(revision_folder)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.update(source_file="evaluation.json"),
        lambda doc: doc.update(source_sha256="0" * 64),
        lambda doc: doc.update(corpus_version=4),
        lambda doc: doc.update(not_independent_evaluation=False),
        lambda doc: doc.update(authorization=""),
        lambda doc: doc.update(schema_version=True),
        lambda doc: doc.update(rewrite_evaluation=True),
        lambda doc: doc["updates"].append(deepcopy(doc["updates"][0])),
        lambda doc: doc["updates"][0].update(id="V001"),
        lambda doc: doc["updates"][0].update(split="evaluation"),
        lambda doc: doc["updates"][0].update(turn_index=True),
        lambda doc: doc["updates"][0].update(request={"text": "different"}),
        lambda doc: doc["updates"][0]["before"].update(reason="different"),
        lambda doc: doc["updates"][0]["after"].update(calls=[]),
        lambda doc: doc["updates"][0]["after"].update(continue_conversation=True),
        lambda doc: doc["updates"][0]["after"].update(executor_calls=[{"domain": "light"}]),
    ],
)
def test_v3_rejects_unapproved_targets_or_weakened_expectations(revision_folder, mutate):
    write_revision(revision_folder, mutate)
    with pytest.raises(corpus.CorpusError):
        corpus.load_cases(revision_folder)


@pytest.mark.parametrize("change", ["evaluation", "duplicate", "changed_before"])
def test_v3_checks_selected_regression_precondition_again(revision_folder, change):
    original = raw_cases(revision_folder)
    target = next(case for case in original if case["id"] == "E121")
    if change == "evaluation":
        target["split"] = "evaluation"
    elif change == "duplicate":
        original.append(deepcopy(target))
    else:
        target["turns"][0]["expected"]["status"] = "unsupported"
    with pytest.raises(corpus.CorpusError, match="precondition"):
        corpus.apply_expectation_update(original, revision_folder)


def test_saved_v2_folder_without_overlay_keeps_original_expectation(revision_folder):
    (revision_folder / corpus.EXPECTATION_UPDATE_FILE).unlink()
    (revision_folder / "d02.json").unlink()
    original = corpus.load_cases(revision_folder)
    target = next(case for case in original if case["id"] == "E121")
    assert target["turns"][0]["expected"] == corpus.E121_V2_EXPECTED
    assert corpus.expectation_update(revision_folder) is None


def test_new_d02_cases_are_separate_authored_regressions_with_explicit_execution_oracles():
    cases = json.loads((corpus.DEFAULT_CORPUS / "d02.json").read_text())
    assert len(cases) == 12 and sum(len(case["turns"]) for case in cases) == 33
    assert all(case["id"].startswith("D02-") and case["split"] == "regression" for case in cases)
    assert all("before executing" in case["origin"] for case in cases)
    assert all("executor_calls" in turn["expected"] for case in cases for turn in case["turns"])


@pytest.mark.asyncio
async def test_scored_report_explicitly_discloses_the_contract_revision(monkeypatch):
    original_curate = corpus.curated_cases

    def select_revised_case(cases, folder):
        selected, exclusions = original_curate(cases, folder)
        return [case for case in selected if case["id"] == "E121"], exclusions

    monkeypatch.setattr(corpus, "curated_cases", select_revised_case)
    report = await corpus.run_corpus(split="regression")
    assert report["passed"] and report["corpus_version"] == 3
    revision = report["expectation_revisions"][0]
    assert revision["not_independent_evaluation"] is True
    assert revision["source_sha256"] == corpus.V2_ENGINE_SHA256
    assert revision["updates"][0]["before"] == corpus.E121_V2_EXPECTED
    assert revision["updates"][0]["after"] == corpus.E121_V3_EXPECTED
    assert report["results"][0]["id"] == "E121"
