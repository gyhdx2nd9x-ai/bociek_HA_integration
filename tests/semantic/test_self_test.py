"""The installed self-test is isolated and reports readiness separately from correctness."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest

from custom_components.polish_context_voice.core import self_test
from custom_components.polish_context_voice.core.home_model import Area, Entity, HomeModel


async def test_fixed_suite_has_consistent_results_without_optional_sources():
    report = await self_test.run_suite(HomeModel())
    assert UUID(report["run_id"]).version == 4
    assert report["sections"]["synthetic_correctness"]["summary"] == {
        "pass": 68,
        "fail": 0,
        "warn": 0,
        "skip": 0,
    }
    assert report["summary"]["fail"] == 0
    assert report["suite_version"] == 11
    assert {"S35", "S36", "S37", "S38"} <= {row["id"] for row in report["cases"]}
    assert {"S39", "S40", "S41", "S42", "S43"} <= {row["id"] for row in report["cases"]}
    assert {"S44", "S45", "S46", "S47"} <= {row["id"] for row in report["cases"]}
    assert {"S48", "S49", "S50", "S51"} <= {row["id"] for row in report["cases"]}
    assert {"S52", "S53", "S54", "S55"} <= {row["id"] for row in report["cases"]}
    assert {"S56", "S57"} <= {row["id"] for row in report["cases"]}
    assert {"S58", "S59", "S60", "S61"} <= {row["id"] for row in report["cases"]}
    assert {"S62", "S63", "S64", "S65"} <= {row["id"] for row in report["cases"]}
    assert {"S66", "S67", "S68"} <= {row["id"] for row in report["cases"]}
    assert report["summary"]["skip"] == 3
    assert report["summary"]["warn"] == 1  # A house without areas needs configuration.
    assert sum(report["summary"].values()) == report["case_count"] == len(report["cases"])
    assert len({row["id"] for row in report["cases"]}) == len(report["cases"]) <= 128
    assert all(row["duration_ms"] >= 0 for row in report["cases"])
    assert {"stt", "tts", "physical_devices", "live_household_intent_correctness"} <= set(
        report["not_measured"]
    )


async def test_every_engine_request_is_synthetic_dry_run_without_executors(home, monkeypatch):
    before = deepcopy(home.export())
    original = self_test.Engine.process
    seen = []

    async def guarded(engine, request):
        assert request.dry_run and engine.executor is None and engine.fallback is None
        assert engine.model_provider() is not home
        assert request.user_id is None
        seen.append(request)
        return await original(engine, request)

    monkeypatch.setattr(self_test.Engine, "process", guarded)
    result = await self_test.run_suite(home)
    assert result["summary"]["fail"] == 0
    assert len(seen) >= 20
    assert home.export() == before


async def test_private_configuration_values_never_enter_the_report():
    secret = "PRYWATNE_QZ482"
    home = HomeModel(
        areas=[Area(secret + "_area", secret + "_name", (secret + "_alias",))],
        entities=[
            Entity(
                "light." + secret,
                secret + "_lamp",
                secret + "_area",
                aliases=(secret + "_alias",),
                labels=frozenset({secret, "voice_default_on"}),
                exposed=True,
                available=False,
                state=secret,
                attributes={"private": secret, "entity_id": [secret]},
                device_id=secret,
                device_name=secret,
                unique_id=secret,
            ),
            Entity("private_domain." + secret, secret, None, exposed=True),
        ],
        satellites={secret + "_satellite": None},
    )
    settings = {"music_rooms": {secret: {"player": secret, "default_shortcut": secret}}}
    report = await self_test.run_suite(home, settings)
    assert secret not in json.dumps(report, ensure_ascii=False)
    assert "private_domain" not in json.dumps(report)
    assert report["inventory"]["entities"] == 2
    assert report["inventory"]["other_domains"] == 1
    assert report["summary"]["warn"] >= 2 and report["summary"]["fail"] == 0


async def test_exposure_and_unavailability_are_configuration_warnings_not_parser_failures(home):
    unavailable = replace(home.entities["light.living_room_main"], available=False)
    model = home.with_entities(
        [
            unavailable if entity.id == unavailable.id else entity
            for entity in home.entities.values()
        ]
    )
    report = await self_test.run_suite(model)
    assert report["sections"]["synthetic_correctness"]["summary"]["fail"] == 0
    assert next(row for row in report["cases"] if row["id"] == "H09")["status"] == "warn"
    assert report["inventory"]["exposed_unavailable_entities"] >= 1


async def test_source_readiness_never_claims_provider_connectivity():
    home = HomeModel(
        areas=[Area("a", "Pokój")],
        entities=[
            Entity("weather.a", "Pogoda", None, exposed=True),
            Entity("calendar.a", "Plan", None, exposed=False),
        ],
    )
    report = await self_test.run_suite(home)
    rows = {row["id"]: row for row in report["cases"]}
    assert rows["H07"]["status"] == "pass"
    assert rows["H08"]["status"] == "warn"
    assert "provider_connectivity" in report["not_measured"]
    assert "calendar_contents" in report["not_measured"]


async def test_scan_exception_is_redacted_and_does_not_produce_false_success(monkeypatch, home):
    def broken(*args):
        raise RuntimeError("PRIVATE_device_token_and_alias")

    monkeypatch.setattr(self_test, "check", broken)
    report = await self_test.run_suite(home)
    rows = {row["id"]: row for row in report["cases"]}
    assert rows["H01"]["status"] == "fail"
    assert all(rows[key]["status"] == "skip" for key in ("H02", "H03", "H04", "H05", "H06"))
    assert "PRIVATE" not in json.dumps(report)
    assert report["sections"]["synthetic_correctness"]["summary"]["fail"] == 0


async def test_report_size_does_not_scale_with_home_inventory():
    small = await self_test.run_suite(HomeModel())
    home = HomeModel(
        areas=[Area("a", "Pokój")],
        entities=[Entity(f"sensor.private_{i}", f"Prywatny czujnik {i}", "a") for i in range(2000)],
    )
    large = await self_test.run_suite(home)
    assert large["case_count"] == small["case_count"]
    assert large["inventory"]["entities"] == 2000
    assert len(json.dumps(large)) < 30_000
    assert "Prywatny" not in json.dumps(large) and "private_" not in json.dumps(large)


async def test_suite_cancellation_propagates(monkeypatch, home):
    entered = asyncio.Event()

    async def wait(suite):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(self_test, "_synthetic_cases", wait)
    task = asyncio.create_task(self_test.run_suite(home))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
