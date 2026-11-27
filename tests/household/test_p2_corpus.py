"""Dataset integrity is separate from strict language scores and the CI gate."""

import json
from hashlib import sha256

from benchmark.corpus import DEFAULT_CORPUS, curated_cases, load_cases, validate_cases


def test_corpus_has_multiple_houses_and_distinct_coverage_layers():
    cases, exclusions = curated_cases(load_cases(), DEFAULT_CORPUS)
    homes = {
        file.stem: json.loads(file.read_text())
        for file in (DEFAULT_CORPUS / "homes").glob("*.json")
    }
    validate_cases(cases, homes)
    assert len(homes) >= 3
    assert 300 <= len(cases) <= 500
    assert {case["split"] for case in cases} == {"regression", "evaluation"}
    assert {case["category"] for case in cases} >= {
        "negation",
        "correction",
        "compound",
        "background",
        "continuation",
    }
    assert {case["layer"] for case in cases} >= {
        "engine",
        "calendar_parse",
        "timer_prefix",
        "capability_parse",
    }
    assert all(
        case["split"] == "evaluation"
        for case in load_cases()
        if case["id"] in {item["id"] for item in exclusions}
    )


def test_frozen_evaluation_keeps_authored_expectations_before_observation():
    freeze = json.loads((DEFAULT_CORPUS / "evaluation-freeze.json").read_text())
    data = (DEFAULT_CORPUS / "evaluation.json").read_bytes()
    assert sha256(data).hexdigest() == freeze["sha256"]
    cases = json.loads(data)
    assert len(cases) == freeze["case_count"]
    assert sum(len(case["turns"]) for case in cases) == freeze["turn_count"]
    assert freeze["independent_creation"]["cases_executed_before_freeze"] is False
