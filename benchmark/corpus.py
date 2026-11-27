"""Offline, explicit expectations for Polish commands; never connects to HA.

Parser, planner and dialogue scores are separate. Evaluation mismatches are
reported as gaps and are not silently converted to new expected results.
"""

import argparse
import asyncio
import json
import platform
import statistics
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from pathlib import Path
from time import perf_counter

from custom_components.polish_context_voice.core.calendar import CalendarCommand, parse_calendar
from custom_components.polish_context_voice.core.capabilities import plan_capability
from custom_components.polish_context_voice.core.diagnostics import STATUSES
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.nlu.capabilities import (
    Capability,
    parse_capability,
)
from custom_components.polish_context_voice.core.nlu.timers import (
    is_timer_request,
    timer_prefix_candidates,
)
from custom_components.polish_context_voice.core.types import Request

from .run import percentile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "benchmark/corpus"
CASE_FILES = (
    "engine.json",
    "dialogue.json",
    "parsers.json",
    "capabilities.json",
    "evaluation.json",
    "d01.json",
    "d02.json",
    "context_choices.json",
)
EXPECTATION_UPDATE_FILE = "expectation-update-v3.json"
V2_ENGINE_SHA256 = "23c251d406cf1bcf92c1adb488d807b9b08873c66521205f1aa595c7fd556673"
# An explicit, reviewed contract revision; no general mechanism for weakening
# frozen or observed evaluation cases. The original source file stays intact.
E121_V2_EXPECTED = {"status": "clarify", "targets": [], "calls": [], "reason": "no_targets"}
E121_V3_EXPECTED = {
    "status": "confirm",
    "targets": ["light.main"],
    "calls": [{"domain": "light", "service": "turn_on", "entity_ids": ["light.main"], "data": {}}],
    "reason": "background_confirmation",
    "continue_conversation": False,
    "executor_calls": [],
}
LAYERS = {"engine", "capability_parse", "capability_plan", "calendar_parse", "timer_prefix"}
CATEGORIES = {
    "command",
    "paraphrase",
    "continuation",
    "negation",
    "correction",
    "compound",
    "background",
    "unsupported",
    "configuration",
}
SPLITS = {"regression", "evaluation"}
REQUEST_FIELDS = {field.name for field in fields(Request)}
PLAN_FIELDS = {
    "status",
    "status_in",
    "targets",
    "calls",
    "area_ids",
    "reason",
    "risk",
    "continue_conversation",
    "interpretation",
    "speech_contains",
    "speech_not_contains",
    "executor_calls",
}


class CorpusError(ValueError):
    """Invalid case data; never treat a broken evaluator as a language failure."""


def _strings(value, *, nonempty=False):
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _json_key(value):
    """Typed JSON equality: 40 == 40.0, but true != 1; object order is irrelevant."""
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        if not isfinite(value):
            raise CorpusError("Non-finite JSON number")
        return ("number", Decimal(str(value)))
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_json_key(item) for item in value))
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ("object", tuple(sorted((key, _json_key(item)) for key, item in value.items())))
    raise CorpusError("Expected JSON data")


def _validate_call(call, identifier):
    required = {"domain", "service", "entity_ids", "data"}
    if not isinstance(call, dict) or not required <= call.keys():
        raise CorpusError(f"Incomplete service expectation: {identifier}")
    if set(call) - required - {"guard_entity_ids"} or not all(
        isinstance(call[key], str) and call[key] for key in ("domain", "service")
    ):
        raise CorpusError(f"Invalid service expectation: {identifier}")
    if not _strings(call["entity_ids"], nonempty=True) or not isinstance(call["data"], dict):
        raise CorpusError(f"Invalid service targets/data: {identifier}")
    if "guard_entity_ids" in call and not _strings(call["guard_entity_ids"]):
        raise CorpusError(f"Invalid guard targets: {identifier}")
    _json_key(call["data"])


def load_cases(folder=DEFAULT_CORPUS):
    cases = []
    for name in CASE_FILES:
        path = Path(folder) / name
        if name in {"d02.json", "context_choices.json"} and not path.exists():
            continue  # Saved corpus directories predate these regression cases.
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            raise CorpusError(f"Case file must be an array: {name}")
        cases.extend(data)
    return apply_expectation_update(cases, folder)


def expectation_update(folder):
    """Load only the single authorized v3 regression revision, with a pinned base."""
    folder = Path(folder)
    path = folder / EXPECTATION_UPDATE_FILE
    if not path.exists():
        return None
    document = json.loads(path.read_text())
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema_version",
            "corpus_version",
            "source_file",
            "source_sha256",
            "not_independent_evaluation",
            "authorization",
            "updates",
        }
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or document.get("corpus_version") != 3
        or document.get("source_file") != "engine.json"
        or document.get("source_sha256") != V2_ENGINE_SHA256
        or document.get("not_independent_evaluation") is not True
        or not isinstance(document.get("authorization"), str)
        or not document["authorization"].strip()
        or not isinstance(document.get("updates"), list)
        or len(document["updates"]) != 1
    ):
        raise CorpusError("Invalid or unapproved v3 expectation revision")
    if sha256((folder / "engine.json").read_bytes()).hexdigest() != V2_ENGINE_SHA256:
        raise CorpusError("V3 revision requires the unchanged original v2 engine corpus")
    update = document["updates"][0]
    if (
        not isinstance(update, dict)
        or set(update) != {"id", "split", "turn_index", "reason", "before", "after"}
        or update.get("id") != "E121"
        or update.get("split") != "regression"
        or type(update.get("turn_index")) is not int
        or update["turn_index"] != 0
        or not isinstance(update.get("reason"), str)
        or not update["reason"].strip()
        or _json_key(update.get("before")) != _json_key(E121_V2_EXPECTED)
        or _json_key(update.get("after")) != _json_key(E121_V3_EXPECTED)
    ):
        raise CorpusError("Only the declared E121 regression contract may change in v3")
    return document


def apply_expectation_update(cases, folder):
    """Change one expected plan on a copy; requests and original files are immutable."""
    document = expectation_update(folder)
    if document is None:
        return cases
    updated = deepcopy(cases)
    matches = [case for case in updated if case.get("id") == "E121"]
    if (
        len(matches) != 1
        or matches[0].get("split") != "regression"
        or matches[0].get("layer") != "engine"
        or len(matches[0].get("turns", [])) != 1
        or _json_key(matches[0]["turns"][0].get("expected")) != _json_key(E121_V2_EXPECTED)
    ):
        raise CorpusError("E121 revision precondition does not match the original regression")
    matches[0]["turns"][0]["expected"] = deepcopy(document["updates"][0]["after"])
    return updated


def curated_cases(cases, folder):
    """Keep the frozen evaluation intact; expose any pre-run overlap exclusions."""
    folder = Path(folder)
    freeze_path = folder / "evaluation-freeze.json"
    if freeze_path.exists():
        freeze = json.loads(freeze_path.read_text())
        if sha256((folder / "evaluation.json").read_bytes()).hexdigest() != freeze["sha256"]:
            raise CorpusError("Frozen evaluation has changed")
        for name, item in freeze["homes"].items():
            if (
                sha256((folder / "homes" / (name + ".json")).read_bytes()).hexdigest()
                != item["sha256"]
            ):
                raise CorpusError("Frozen evaluation house has changed")
    excluded_path = folder / "evaluation-exclusions.json"
    exclusions = json.loads(excluded_path.read_text()) if excluded_path.exists() else []
    if not isinstance(exclusions, list):
        raise CorpusError("Exclusions must be an array")
    regression_texts = {
        (case["layer"], " ".join(turn["request"]["text"].casefold().split()).rstrip(".?!"))
        for case in cases
        if case["split"] == "regression"
        for turn in case["turns"]
    }
    identifiers = set()
    for exclusion in exclusions:
        if (
            not isinstance(exclusion, dict)
            or exclusion.get("reason") != "exact_utterance_overlap"
            or exclusion.get("detected_before_first_scored_run") is not True
        ):
            raise CorpusError("Only pre-run exact-overlap exclusions are permitted")
        identifier = exclusion.get("id")
        matches = [case for case in cases if case.get("id") == identifier]
        if len(matches) != 1 or matches[0]["split"] != "evaluation" or identifier in identifiers:
            raise CorpusError("Invalid evaluation exclusion")
        if not any(
            (
                matches[0]["layer"],
                " ".join(turn["request"]["text"].casefold().split()).rstrip(".?!"),
            )
            in regression_texts
            for turn in matches[0]["turns"]
        ):
            raise CorpusError("Excluded case has no exact regression overlap")
        identifiers.add(identifier)
    return [case for case in cases if case["id"] not in identifiers], exclusions


def validate_cases(cases, homes):
    identifiers, families, utterance_splits = set(), defaultdict(set), defaultdict(set)
    for case in cases:
        if not isinstance(case, dict):
            raise CorpusError("Each case must be an object")
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise CorpusError("Every case needs a unique nonempty id")
        identifiers.add(identifier)
        if case.get("split") not in SPLITS or case.get("layer") not in LAYERS:
            raise CorpusError(f"Invalid split/layer: {identifier}")
        if case.get("category") not in CATEGORIES:
            raise CorpusError(f"Invalid category: {identifier}")
        if not isinstance(case.get("family"), str) or not case["family"]:
            raise CorpusError(f"Missing family: {identifier}")
        families[case["family"]].add(case["split"])
        if case.get("home") not in homes:
            raise CorpusError(f"Unknown synthetic house: {identifier}")
        if not isinstance(case.get("turns"), list) or not case["turns"]:
            raise CorpusError(f"Missing turns: {identifier}")
        if case["layer"] != "engine" and len(case["turns"]) != 1:
            raise CorpusError(f"Only engine cases support dialogue: {identifier}")
        for turn in case["turns"]:
            if not isinstance(turn, dict):
                raise CorpusError(f"Each turn must be an object: {identifier}")
            request, expected = turn.get("request"), turn.get("expected")
            if not isinstance(request, dict) or not isinstance(expected, dict):
                raise CorpusError(f"Missing request/expected: {identifier}")
            if set(request) - REQUEST_FIELDS or not isinstance(request.get("text"), str):
                raise CorpusError(f"Invalid Request fields: {identifier}")
            if "dry_run" in request and type(request["dry_run"]) is not bool:
                raise CorpusError(f"Invalid dry_run flag: {identifier}")
            if any(
                value is not None and not isinstance(value, str)
                for name, value in request.items()
                if name not in {"dry_run", "text"}
            ):
                raise CorpusError(f"Invalid Request identifier type: {identifier}")
            if not request["text"].strip() or len(request["text"]) > 2100:
                raise CorpusError(f"Empty or unbounded utterance: {identifier}")
            # Exact duplicates may vary by home/context in one split. They cannot
            # masquerade as fresh evaluation after appearing in regressions.
            key = " ".join(request["text"].casefold().split()).rstrip(".?!")
            utterance_splits[(case["layer"], key)].add(case["split"])
            allowed = (
                PLAN_FIELDS
                if case["layer"] == "engine"
                else PLAN_FIELDS | {"capability"}
                if case["layer"] == "capability_plan"
                else {"capability"}
                if case["layer"] == "capability_parse"
                else {"command"}
                if case["layer"] == "calendar_parse"
                else {"candidates", "is_timer_request"}
            )
            if not expected or set(expected) - allowed:
                raise CorpusError(f"Unknown or empty expectation: {identifier}")
            for field in ("capability", "command", "interpretation"):
                if (
                    field in expected
                    and expected[field] is not None
                    and (not isinstance(expected[field], dict) or not expected[field])
                ):
                    raise CorpusError(
                        f"Empty or invalid parser/interpretation subset: {identifier}"
                    )
                if expected.get(field) is not None and field in {"capability", "command"}:
                    contract = Capability if field == "capability" else CalendarCommand
                    if set(expected[field]) - {item.name for item in fields(contract)}:
                        raise CorpusError(f"Unknown parser slot: {identifier}")
            if case["layer"] in {"engine", "capability_plan"} and not (
                case["layer"] == "capability_plan" and expected.get("capability", 0) is None
            ):
                if ("status" in expected) == ("status_in" in expected):
                    raise CorpusError(f"Specify exactly one status/status_in: {identifier}")
                statuses = expected.get("status_in", [expected.get("status")])
                if not _strings(statuses, nonempty=True) or not set(statuses) <= STATUSES:
                    raise CorpusError(f"Invalid expected statuses: {identifier}")
                if not _strings(expected.get("targets")) or not isinstance(
                    expected.get("calls"), list
                ):
                    raise CorpusError(f"Targets and calls must be explicit: {identifier}")
                if {"executed", "partial"}.intersection(
                    statuses
                ) and "executor_calls" not in expected:
                    raise CorpusError(f"Simulated writes must be explicit: {identifier}")
                if "executor_calls" in expected and not isinstance(
                    expected["executor_calls"], list
                ):
                    raise CorpusError(f"Invalid simulated writes: {identifier}")
                for call in expected["calls"] + expected.get("executor_calls", []):
                    _validate_call(call, identifier)
            advance = turn.get("advance_seconds", 0)
            if type(advance) not in {int, float} or not isfinite(advance) or advance < 0:
                raise CorpusError(f"Invalid clock advance: {identifier}")
            if not isinstance(turn.get("mutations", []), list):
                raise CorpusError(f"Mutations must be an array: {identifier}")
            for mutation in turn.get("mutations", []):
                if not isinstance(mutation, dict) or mutation.get("entity_id") not in {
                    entity["id"] for entity in homes[case["home"]]["entities"]
                }:
                    raise CorpusError(f"Unknown mutation entity: {identifier}")
                if not isinstance(mutation.get("changes"), dict) or set(mutation["changes"]) - {
                    "exposed",
                    "available",
                    "state",
                    "area_id",
                    "labels",
                    "attributes",
                    "aliases",
                }:
                    raise CorpusError(f"Invalid mutation fields: {identifier}")
    if any(len(splits) > 1 for splits in families.values()):
        raise CorpusError("A construction family must not cross splits")
    if any(len(splits) > 1 for splits in utterance_splits.values()):
        raise CorpusError("An identical utterance/layer must not cross splits")


def canonical_calls(calls, *, guards=False):
    """Compare exact entity/service/data semantics, independent of call grouping.

    Repeated identical operations remain repeated; target IDs are not deduplicated.
    Guard targets are explicit testable metadata when the expectation supplies them.
    """
    return sorted(
        (
            call["domain"],
            call["service"],
            entity,
            _json_key(call.get("data", {})),
            tuple(sorted(call.get("guard_entity_ids", []))) if guards else (),
        )
        for call in calls
        for entity in (call["entity_ids"] or [""])
    )


def subset_errors(actual, expected, prefix):
    errors = []
    if expected is None:
        return [] if actual is None else [prefix]
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return [] if actual == expected else [prefix]
    for key, value in expected.items():
        if key not in actual:
            errors.append(f"{prefix}.{key}")
        elif isinstance(value, dict):
            errors.extend(subset_errors(actual[key], value, f"{prefix}.{key}"))
        elif actual[key] != value:
            errors.append(f"{prefix}.{key}")
    return errors


def compare(actual, expected):
    errors = []
    for key, value in expected.items():
        if key == "status_in":
            if actual.get("status") not in value:
                errors.append("status")
        elif key in {"targets", "area_ids"}:
            if set(actual.get(key, [])) != set(value):
                errors.append(key)
        elif key in {"calls", "executor_calls"}:
            guards = any("guard_entity_ids" in call for call in value)
            if canonical_calls(actual.get(key, []), guards=guards) != canonical_calls(
                value, guards=guards
            ):
                errors.append(key)
        elif key in {"interpretation", "capability", "command"}:
            errors.extend(subset_errors(actual.get(key), value, key))
        elif key == "speech_contains":
            fragments = [value] if isinstance(value, str) else value
            if not all(fragment in actual.get("speech", "") for fragment in fragments):
                errors.append("speech")
        elif key == "speech_not_contains":
            fragments = [value] if isinstance(value, str) else value
            if any(fragment in actual.get("speech", "") for fragment in fragments):
                errors.append("speech")
        elif actual.get(key) != value:
            errors.append(key)
    return errors


class RecordingExecutor:
    """Only record synthetic writes; no HA permission, provider or I/O emulation."""

    def __init__(self):
        self.calls = []

    async def preflight(self, calls, request):
        pass

    async def execute(self, call, request):
        self.calls.append(asdict(call))


def result_view(result):
    return {
        key: value
        for key, value in result.to_dict().items()
        if key
        in {
            "status",
            "speech",
            "targets",
            "calls",
            "area_ids",
            "risk",
            "reason",
            "route",
            "continue_conversation",
        }
    } | {"interpretation": (result.response_data or {}).get("interpretation")}


async def run_case(case, home_data):
    data, now = deepcopy(home_data), [0.0]
    current = [HomeModel.from_dict(data)]
    executor = RecordingExecutor()
    engine = Engine(lambda: current[0], executor, clock=lambda: now[0])
    turns = []
    for index, turn in enumerate(case["turns"]):
        now[0] += turn.get("advance_seconds", 0)
        for mutation in turn.get("mutations", []):
            entity = next(e for e in data["entities"] if e["id"] == mutation["entity_id"])
            entity.update(deepcopy(mutation["changes"]))
        if turn.get("mutations"):
            current[0] = HomeModel.from_dict(data)
        request = Request(
            **{
                "dry_run": True,
                "conversation_id": "corpus-" + case["id"],
                "user_id": "synthetic",
                **turn["request"],
            }
        )
        before = len(executor.calls)
        started = perf_counter()
        if case["layer"] == "engine":
            actual = result_view(await engine.process(request))
        elif case["layer"] in {"capability_parse", "capability_plan"}:
            capability = parse_capability(request.text)
            actual = {"capability": asdict(capability) if capability is not None else None}
            if case["layer"] == "capability_plan" and capability is not None:
                actual.update(result_view(plan_capability(capability, request, current[0])))
        elif case["layer"] == "calendar_parse":
            command = parse_calendar(request.text)
            actual = {"command": asdict(command) if command is not None else None}
        else:
            actual = {
                "candidates": timer_prefix_candidates(request.text),
                "is_timer_request": is_timer_request(request.text),
            }
        elapsed = (perf_counter() - started) * 1000
        actual["executor_calls"] = executor.calls[before:]
        expected = turn["expected"]
        errors = compare(actual, expected)
        allowed_statuses = expected.get("status_in", [expected.get("status")])
        unexpected_execution = bool(actual["executor_calls"]) and not {
            "executed",
            "partial",
        }.intersection(allowed_statuses)
        if unexpected_execution and "executor_calls" not in errors:
            errors.append("unexpected_simulated_execution")
        wanted_targets = {
            entity for call in expected.get("calls", []) for entity in call["entity_ids"]
        }
        planned_targets = {
            entity for call in actual.get("calls", []) for entity in call["entity_ids"]
        }
        turns.append(
            {
                "turn": index + 1,
                "passed": not errors,
                "mismatches": errors,
                "elapsed_ms": round(elapsed, 6),
                "expected": expected,
                "actual": actual,
                "unexpected_call_plan": "calls" in expected
                and not expected["calls"]
                and bool(actual.get("calls")),
                "extra_planned_targets": bool(planned_targets - wanted_targets),
                "unexpected_simulated_execution": unexpected_execution,
                "missing_expected_call_plan": bool(expected.get("calls"))
                and not actual.get("calls"),
                "unnecessary_clarification": actual.get("status") == "clarify"
                and not {"clarify"}.intersection(allowed_statuses)
                and bool(expected.get("calls")),
            }
        )
    return {key: case[key] for key in ("id", "split", "family", "category", "layer", "home")} | {
        "passed": all(turn["passed"] for turn in turns),
        "turns": turns,
    }


def summarize(rows):
    turns = [turn for row in rows for turn in row["turns"]]
    times = [turn["elapsed_ms"] for turn in turns]
    mismatches = Counter(field for turn in turns for field in turn["mismatches"])
    reasons = Counter(
        turn["actual"].get("reason", "no_result_reason") for turn in turns if not turn["passed"]
    )
    return {
        "cases": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "failed": sum(not row["passed"] for row in rows),
        "turns": len(turns),
        "passed_turns": sum(turn["passed"] for turn in turns),
        "strict_case_match_rate": sum(row["passed"] for row in rows) / len(rows) if rows else None,
        "mismatches_by_field": dict(sorted(mismatches.items())),
        "mismatched_turns_by_observed_reason": dict(sorted(reasons.items())),
        "unexpected_call_plans": sum(turn["unexpected_call_plan"] for turn in turns),
        "extra_target_plans": sum(turn["extra_planned_targets"] for turn in turns),
        "unexpected_simulated_executions": sum(
            turn["unexpected_simulated_execution"] for turn in turns
        ),
        "missing_expected_call_plans": sum(turn["missing_expected_call_plan"] for turn in turns),
        "unnecessary_clarifications": sum(turn["unnecessary_clarification"] for turn in turns),
        "latency_ms": {
            "count": len(times),
            "p50": statistics.median(times) if times else None,
            "p95": percentile(times, 0.95) if times else None,
        },
    }


async def run_corpus(folder=DEFAULT_CORPUS, *, split="all", case_ids=()):
    folder = Path(folder)
    cases = load_cases(folder)
    revision = expectation_update(folder)
    authored_count = len(cases)
    cases, exclusions = curated_cases(cases, folder)
    homes = {
        file.stem: json.loads(file.read_text())
        for file in sorted((folder / "homes").glob("*.json"))
    }
    validate_cases(cases, homes)
    selected = [case for case in cases if split == "all" or case["split"] == split]
    if case_ids:
        requested = set(case_ids)
        missing = requested - {case["id"] for case in selected}
        if missing:
            raise CorpusError("Unknown cases in this split: " + ", ".join(sorted(missing)))
        selected = [case for case in selected if case["id"] in requested]
    if not selected:
        raise CorpusError("The selection contains no cases")
    rows = [await run_case(case, homes[case["home"]]) for case in selected]
    report = {
        "corpus_version": 3 if revision is not None else 2,
        "expectation_revisions": [revision] if revision is not None else [],
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "component_version": json.loads(
            (ROOT / "custom_components/polish_context_voice/manifest.json").read_text()
        )["version"],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "scope": "Offline synthetic text: core engine, pure capability planning, or parser only; "
        "no HA runtime, providers, network, STT/TTS, physical devices or LLM.",
        "selection": split,
        "case_filter": list(dict.fromkeys(case_ids)),
        "curation": {"authored_cases": authored_count, "excluded_before_execution": exclusions},
        "source_sha256": {
            file.relative_to(ROOT).as_posix(): sha256(file.read_bytes()).hexdigest()
            for file in sorted(
                [
                    *(ROOT / "custom_components/polish_context_voice/core").rglob("*.py"),
                    ROOT / "benchmark/corpus.py",
                    ROOT / "benchmark/run.py",
                ]
            )
        },
        "corpus_sha256": {
            file.relative_to(folder).as_posix(): sha256(file.read_bytes()).hexdigest()
            for file in sorted(folder.rglob("*.json"))
        },
        "inventory": {
            name: {
                "areas": len(home.get("areas", [])),
                "floors": len(home.get("floors", [])),
                "entities": len(home.get("entities", [])),
            }
            for name, home in homes.items()
        },
        "unique_texts": len(
            {
                turn["request"]["text"].casefold().strip()
                for case in selected
                for turn in case["turns"]
            }
        ),
        "construction_families": len({case["family"] for case in selected}),
        "summary": summarize(rows),
        "by_split": {
            name: summarize([row for row in rows if row["split"] == name])
            for name in sorted(SPLITS)
        },
        "by_layer": {
            name: summarize([row for row in rows if row["layer"] == name])
            for name in sorted({row["layer"] for row in rows})
        },
        "by_home": {
            name: summarize([row for row in rows if row["home"] == name]) for name in sorted(homes)
        },
        "by_category": {
            name: summarize([row for row in rows if row["category"] == name])
            for name in sorted({row["category"] for row in rows})
        },
        "by_family": {
            name: summarize([row for row in rows if row["family"] == name])
            for name in sorted({row["family"] for row in rows})
        },
        "results": rows,
        "limits": [
            "Cases, unique utterances and turns have different counts; fixture variants are "
            "not independent language samples.",
            "Strict plan equality tests this project's output contract, not every semantically "
            "equivalent HA representation.",
            "Parser-only success does not establish correct targets, dialogue, authorization "
            "or execution.",
            "RecordingExecutor records local synthetic writes; it does not emulate HA "
            "permissions or provider outcomes.",
            "Independent evaluation is frozen before its first run. After inspection it is "
            "exposed and cannot remain an unseen holdout for future tuning.",
            "Latency is one local sample per turn after fixture creation; mixed paths, "
            "no controlled warm benchmark or N5105 measurement.",
            "This report does not establish whole-home language accuracy, physical-device "
            "behavior or a release acceptance.",
        ],
    }
    report["passed"] = report["summary"]["failed"] == 0
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--split", choices=["all", *sorted(SPLITS)], default="all")
    parser.add_argument(
        "--case", action="append", default=[], help="Replay one case by ID; repeat as needed"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail for evaluation gaps as well as regression failures",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(run_corpus(args.corpus, split=args.split, case_ids=args.case))
    except Exception as error:
        parser.exit(2, f"Invalid corpus/run: {error}\n")
    try:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except OSError as error:
        parser.exit(2, f"Cannot write corpus report: {error}\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "component_version",
                    "selection",
                    "summary",
                    "by_split",
                    "by_layer",
                    "unique_texts",
                    "passed",
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    regression_failed = report["by_split"]["regression"]["failed"]
    raise SystemExit(1 if regression_failed or (args.strict and not report["passed"]) else 0)


if __name__ == "__main__":
    main()
