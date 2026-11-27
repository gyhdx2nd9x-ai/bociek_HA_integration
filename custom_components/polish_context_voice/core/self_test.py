"""A bounded local self-test, independent of HA, providers and live conversations.

Correctness is checked against an embedded synthetic household. The real home's
metadata is inspected only for configuration readiness; it cannot tell us which
lamp a resident intended, whether a provider works or whether anything is audible.
Only fixed codes and counts leave this module. No requests enter a live Runtime.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, time
from inspect import isawaitable
from math import ceil
from time import perf_counter
from uuid import uuid4

from . import nlu
from .calendar import moment, parse_calendar, period
from .capabilities import plan_capability
from .capability_catalog import catalog_snapshot
from .capability_contracts import annotate_capability
from .context.dialogue import is_confirmation_reply
from .engine import Engine
from .home_model import Area, Entity, Floor, HomeModel
from .home_model.health import ROLES, check
from .light_feedback import light_state_feedback, snapshot_light_state
from .media_search import candidates_from_response, parse_media_search
from .music import music_health, parse_music
from .nlu.capabilities import Capability, parse_capability
from .nlu.timers import canonical_timer_text, spoken_duration, timer_prefix_candidates
from .presentation import Presentation
from .response_facts import capability_speech
from .spoken_time import spoken_time
from .state_queries import light_state_speech
from .stoic import entry_from_attributes, parse_stoic, reading
from .types import Request
from .weather import current_weather

MAX_CASES = 128
STATUSES = ("pass", "fail", "warn", "skip")
SECTIONS = ("synthetic_correctness", "home_configuration")
DOMAINS = (
    "alarm_control_panel",
    "assist_satellite",
    "automation",
    "binary_sensor",
    "button",
    "calendar",
    "climate",
    "cover",
    "fan",
    "humidifier",
    "lawn_mower",
    "light",
    "lock",
    "media_player",
    "scene",
    "script",
    "sensor",
    "switch",
    "todo",
    "vacuum",
    "valve",
    "water_heater",
    "weather",
)
FINDINGS = (
    "alias_conflict",
    "conflicting_labels",
    "location_alias_conflict",
    "missing_area",
    "missing_semantic_labels",
    "satellite_without_area",
    "music_room_mapping",
    "music_player_unavailable",
    "music_shortcut_missing",
)
SEVERITIES = ("info", "warning", "error")
_NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def _synthetic_home():
    """Small fixed Polish house; intentionally contains hidden/blocked targets."""
    shared = {"voice_all_off", "voice_safe_all"}

    def light(identifier, name, area, labels, aliases=(), exposed=True):
        return Entity(
            "light." + identifier,
            name,
            area,
            tuple(aliases),
            frozenset(labels),
            exposed=exposed,
            state="off",
            attributes={"supported_color_modes": ["brightness"]},
        )

    return HomeModel(
        areas=(
            Area("test_living", "Salon", ("salonie",), "test_ground"),
            Area("test_kitchen", "Kuchnia", ("kuchni",), "test_ground"),
        ),
        floors=(Floor("test_ground", "Parter", ("parterze",)),),
        entities=(
            light("test_main", "Główne światło", "test_living", shared | {"voice_default_on"}),
            light("test_ambient", "Nastrojowe", "test_living", shared | {"voice_ambient"}),
            light("test_table", "Nad stołem", "test_living", shared, ("przy stole",)),
            light(
                "test_kitchen", "Kuchenne światło", "test_kitchen", shared | {"voice_default_on"}
            ),
            light(
                "test_hidden",
                "Ukryta lampa",
                "test_living",
                shared | {"voice_default_on"},
                exposed=False,
            ),
            light(
                "test_blocked",
                "Zablokowana lampa",
                "test_living",
                shared | {"voice_default_on", "voice_block"},
            ),
            light("test_confirm", "Lampa specjalna", "test_living", {"voice_confirm"}),
            Entity(
                "sensor.test_temperature",
                "Temperatura",
                "test_living",
                labels=frozenset({"voice_primary"}),
                device_class="temperature",
                exposed=True,
                state="21.7",
                attributes={"unit_of_measurement": "°C"},
            ),
        ),
        satellites={"assist_satellite.test_living": "test_living"},
        devices={"test_satellite_device": "test_living"},
    )


def _summary(cases):
    durations = sorted(case["duration_ms"] for case in cases)
    return {
        "summary": {status: sum(case["status"] == status for case in cases) for status in STATUSES},
        "case_count": len(cases),
        "case_duration_ms": {
            "p50": durations[ceil(0.50 * len(durations)) - 1] if durations else None,
            "p95": durations[ceil(0.95 * len(durations)) - 1] if durations else None,
        },
    }


class _Suite:
    def __init__(self):
        self.cases = []
        self.sections = {section: [] for section in SECTIONS}

    async def case(self, identifier, expected_code, assertion, *, section="synthetic_correctness"):
        """Only suite-owned fixed identifiers/codes may be supplied by our code."""
        started = perf_counter()
        try:
            result = assertion()
            if isawaitable(result):
                result = await result
            passed = result is True
            status, actual = ("pass", expected_code) if passed else ("fail", "check_failed")
        except Exception:
            # Exceptions can contain private metadata: do not serialize/log them.
            status, actual = "fail", "case_exception"
        self.record(identifier, status, expected_code, actual, started, section)
        if len(self.cases) % 8 == 0:
            await asyncio.sleep(0)

    def record(self, identifier, status, expected, actual, started, section):
        if len(self.cases) >= MAX_CASES:
            raise ValueError("Self-test case limit exceeded")
        row = {
            "id": identifier,
            "status": status,
            "expected_code": expected,
            "actual_code": actual,
            "duration_ms": round(max(0.0, perf_counter() - started) * 1000, 3),
        }
        self.cases.append(row)
        self.sections[section].append(row)


async def _synthetic_cases(suite):
    home = _synthetic_home()

    async def plan(text, *, expected=(), status="dry_run", reason=None, **source):
        engine = Engine(lambda: home, executor=None, fallback=None)
        result = await engine.process(Request(text, dry_run=True, **source))
        return (
            result.status == status
            and not result.completed
            and set(result.targets) == set(expected)
            and (reason is None or result.reason == reason)
            and engine.executor is None
            and engine.fallback is None
        )

    main = ("light.test_main",)
    all_living = ("light.test_main", "light.test_ambient", "light.test_table")
    engine_cases = (
        ("S01", "main_lights_only", "zapal światła", main, {"source_area": "test_living"}),
        ("S02", "all_off_lights", "wyłącz światła", all_living, {"source_area": "test_living"}),
        (
            "S03",
            "explicit_area_priority",
            "włącz światła w kuchni",
            ("light.test_kitchen",),
            {"source_area": "test_living"},
        ),
        (
            "S04",
            "satellite_context",
            "zapal światła",
            main,
            {"satellite_id": "assist_satellite.test_living"},
        ),
        ("S05", "device_context", "zapal światła", main, {"device_id": "test_satellite_device"}),
        (
            "S06",
            "explicit_floor",
            "zapal światła na parterze",
            ("light.test_main", "light.test_kitchen"),
            {"source_area": "test_kitchen"},
        ),
        (
            "S07",
            "entity_alias",
            "włącz światła nad stołem",
            ("light.test_table",),
            {"source_area": "test_living"},
        ),
        (
            "S08",
            "asymmetric_safe_all",
            "wyłącz wszystko",
            all_living,
            {"source_area": "test_living"},
        ),
    )
    for identifier, code, text, expected, source in engine_cases:
        await suite.case(
            identifier,
            code,
            lambda text=text, expected=expected, source=source: plan(
                text, expected=expected, **source
            ),
        )
    for identifier, code, text, reason, source in (
        ("S09", "missing_area_stops", "zapal światła", "missing_area", {}),
        (
            "S10",
            "unknown_area_stops",
            "zapal światła w piwnicy",
            "unknown_location",
            {"source_area": "test_living"},
        ),
        (
            "S11",
            "hidden_target_stops",
            "włącz ukrytą lampę",
            "no_targets",
            {"source_area": "test_living"},
        ),
        (
            "S12",
            "blocked_target_stops",
            "włącz zablokowaną lampę",
            "ineligible_target",
            {"source_area": "test_living"},
        ),
    ):
        await suite.case(
            identifier,
            code,
            lambda text=text, reason=reason, source=source: plan(
                text, status="clarify", reason=reason, **source
            ),
        )

    async def brightness():
        result = await Engine(lambda: home).process(
            Request("ustaw światła na 30%", source_area="test_living", dry_run=True)
        )
        return (
            result.status == "dry_run"
            and result.targets == main
            and not result.completed
            and len(result.calls) == 1
            and result.calls[0].data == {"brightness_pct": 30.0}
        )

    async def temperature():
        result = await Engine(lambda: home).process(
            Request("jaka jest temperatura", source_area="test_living", dry_run=True)
        )
        return (
            result.status == "answered"
            and not result.completed
            and "około 22 stopni" in result.speech
        )

    async def confirmation(reply, *, another_source=False):
        engine = Engine(lambda: home, executor=None, fallback=None)
        request = Request(
            "włącz lampę specjalną",
            source_area="test_living",
            conversation_id="synthetic",
            satellite_id="assist_satellite.test_living",
            dry_run=True,
        )
        first = await engine.process(request)
        followup = Request(
            reply,
            source_area="test_living",
            conversation_id="synthetic",
            satellite_id="assist_satellite.other" if another_source else request.satellite_id,
            dry_run=True,
        )
        second = await engine.process(followup)
        expected = "clarify" if another_source else "cancelled" if reply == "anuluj" else "dry_run"
        return (
            first.status == "confirm"
            and second.status == expected
            and not first.completed
            and not second.completed
            and engine.executor is None
            and (not engine.pending if not another_source else len(engine.pending) == 1)
        )

    await suite.case("S13", "brightness_value", brightness)
    await suite.case("S14", "temperature_device_class", temperature)
    await suite.case("S15", "confirmation_local_dry_run", lambda: confirmation("potwierdzam"))
    await suite.case("S16", "rejection_discards_draft", lambda: confirmation("anuluj"))
    await suite.case(
        "S17",
        "confirmation_source_isolation",
        lambda: confirmation("potwierdzam", another_source=True),
    )
    await suite.case(
        "S18",
        "unknown_without_fallback",
        lambda: plan("wymyśl bajkę", status="unsupported", reason="no_intent"),
    )
    await suite.case(
        "S19", "negation_not_executed", lambda: nlu.parse("nie włączaj świateł") is None
    )
    await suite.case(
        "S20",
        "timer_start_canonical",
        lambda: canonical_timer_text("Odlicz dwie minuty") == "ustaw minutnik na dwie minuty",
    )
    await suite.case(
        "S21",
        "timer_cancel_canonical",
        lambda: canonical_timer_text("Przerwij odliczanie") == "anuluj minutnik",
    )
    await suite.case(
        "S22",
        "timer_natural_duration",
        lambda: spoken_duration({"minutes": 2}, words=True) == "dwie minuty",
    )
    await suite.case(
        "S23",
        "timer_background_proposal",
        lambda: timer_prefix_candidates("Odlicz minutę wow jaka figura") == ["Odlicz minutę"],
    )
    await suite.case(
        "S24",
        "timer_extra_command_rejected",
        lambda: timer_prefix_candidates("Odlicz minutę i wyłącz światła") == [],
    )
    await suite.case(
        "S25",
        "calendar_natural_overview",
        lambda: (
            (command := parse_calendar("Jakie mam jutro plany?")) is not None
            and command.operation == "find"
            and period(command.when, _NOW)[0].date().isoformat() == "2026-10-22"
        ),
    )
    await suite.case(
        "S26", "calendar_invalid_date_rejected", lambda: period("31.02.2026", _NOW) is None
    )
    await suite.case(
        "S27",
        "calendar_fixed_local_time",
        lambda: moment("jutro o piętnastej", _NOW) == (_NOW.replace(day=22, hour=15), True),
    )
    await suite.case("S28", "repeated_confirmation", lambda: is_confirmation_reply("Tak. Tak"))
    await suite.case(
        "S29", "foreign_script_not_confirmation", lambda: not is_confirmation_reply("Так")
    )
    await suite.case("S30", "natural_full_hour", lambda: spoken_time(time(15)) == "o piętnastej")
    await suite.case(
        "S31",
        "natural_morning_quarter",
        lambda: spoken_time(time(5, 15), style="conversational") == "kwadrans po piątej rano",
    )
    await suite.case(
        "S32",
        "weather_rounded_speech",
        lambda: (
            "około 18 stopni"
            in current_weather("cloudy", {"temperature": 17.7, "temperature_unit": "°C"})
        ),
    )
    await suite.case(
        "S33",
        "weather_not_room_temperature",
        lambda: (
            (cap := parse_capability("Jaka jest pogoda?")) is not None
            and cap.domain == "weather"
            and cap.operation == "weather"
        ),
    )
    await suite.case(
        "S34",
        "media_next_synonyms",
        lambda: all(
            (command := parse_music(text, {})) is not None and command.operation == operation
            for text, operation in (
                ("następny", "media_next_track"),
                ("następna", "media_next_track"),
                ("dalej", "continue"),
                ("następna piosenka", "media_next_track"),
                ("następny utwór", "media_next_track"),
            )
        ),
    )

    def reference_request(text):
        return Request(
            text,
            source_area="test_living",
            conversation_id="synthetic_reference",
            satellite_id="assist_satellite.test_living",
            dry_run=True,
        )

    async def missing_reference_answer():
        engine = Engine(lambda: home)
        first = await engine.process(reference_request("wyłącz je"))
        second = await engine.process(reference_request("nad stołem"))
        return (
            first.status == "clarify"
            and first.continue_conversation
            and not first.calls
            and second.status == "dry_run"
            and second.targets == ("light.test_table",)
            and len(second.calls) == 1
            and second.calls[0].service == "turn_off"
            and not second.completed
            and engine.executor is None
        )

    async def recent_reference_exact_targets():
        engine = Engine(lambda: home)
        first = await engine.process(reference_request("włącz światła"))
        second = await engine.process(reference_request("wyłącz je"))
        third = await engine.process(reference_request("wyłącz światła"))
        return (
            first.status == second.status == third.status == "dry_run"
            and first.targets == second.targets == main
            and set(third.targets) == set(all_living)
            and not second.continue_conversation
            and not second.completed
        )

    async def reference_retry_expires():
        now = [0.0]
        engine = Engine(lambda: home, clock=lambda: now[0])
        request = reference_request("wyłącz je")
        await engine.process(request)
        saved = engine.dialogue.questions[request.session_key].expires_at
        now[0] = 29
        retry = await engine.process(replace(request, text="nieznana nazwa"))
        unchanged = engine.dialogue.questions[request.session_key].expires_at == saved
        now[0] = 30
        expired = await engine.process(replace(request, text="nad stołem"))
        return (
            retry.continue_conversation
            and unchanged
            and not retry.calls
            and not expired.calls
            and not expired.continue_conversation
            and request.session_key not in engine.dialogue.questions
        )

    async def reference_source_isolation():
        engine = Engine(lambda: home)
        request = reference_request("wyłącz je")
        await engine.process(request)
        other = await engine.process(
            replace(request, text="nad stołem", satellite_id="assist_satellite.other")
        )
        own = await engine.process(replace(request, text="nad stołem"))
        return (
            not other.calls
            and not other.completed
            and own.status == "dry_run"
            and own.targets == ("light.test_table",)
            and not own.completed
        )

    await suite.case("S35", "missing_reference_answer", missing_reference_answer)
    await suite.case("S36", "recent_reference_exact_targets", recent_reference_exact_targets)
    await suite.case("S37", "reference_retry_does_not_extend_ttl", reference_retry_expires)
    await suite.case("S38", "reference_source_isolation", reference_source_isolation)

    def category_home(both=False):
        entities = [
            replace(e, labels=(e.labels - {"voice_default_on"}) | {"voice_task"})
            if e.id == "light.test_kitchen"
            else e
            for e in home.entities.values()
        ]
        if both:
            entities.append(
                Entity(
                    "light.test_kitchen_decor",
                    "Dekoracja",
                    "test_kitchen",
                    labels=frozenset({"voice_ambient", "voice_all_off"}),
                    exposed=True,
                    state="off",
                )
            )
        return HomeModel(areas=home.areas.values(), floors=home.floors.values(), entities=entities)

    def category_request(text):
        return replace(reference_request(text), source_area="test_kitchen")

    async def category_confirmation():
        model = category_home()
        engine = Engine(lambda: model)
        question = await engine.process(category_request("włącz światła"))
        reply = await engine.process(category_request("tak"))
        return (
            question.continue_conversation
            and not question.calls
            and reply.status == "dry_run"
            and reply.targets == ("light.test_kitchen",)
            and len(reply.calls) == 1
            and reply.calls[0].service == "turn_on"
            and not reply.completed
        )

    async def category_choice():
        model = category_home(both=True)
        engine = Engine(lambda: model)
        question = await engine.process(category_request("włącz światła"))
        ambiguous = await engine.process(category_request("tak"))
        reply = await engine.process(category_request("ozdobne"))
        return (
            question.continue_conversation
            and not question.calls
            and ambiguous.continue_conversation
            and not ambiguous.calls
            and reply.status == "dry_run"
            and reply.targets == ("light.test_kitchen_decor",)
            and not reply.completed
        )

    async def category_expiry():
        model, now = category_home(), [0.0]
        engine = Engine(lambda: model, clock=lambda: now[0])
        request = category_request("włącz światła")
        await engine.process(request)
        deadline = engine.dialogue.questions[request.session_key].expires_at
        now[0] = 29
        retry = await engine.process(replace(request, text="nieznana kategoria"))
        unchanged = engine.dialogue.questions[request.session_key].expires_at == deadline
        now[0] = 30
        expired = await engine.process(replace(request, text="tak"))
        return (
            retry.continue_conversation
            and unchanged
            and not retry.calls
            and not expired.calls
            and not expired.continue_conversation
        )

    def already_off_wording():
        # Pure presentation check; creating a result value does not invoke a
        # service or executor. No synthetic or real device is changed.
        proposal = nlu.parse("wyłącz światła")
        result = Engine(lambda: home).plan(proposal, reference_request("wyłącz światła"))
        observed = snapshot_light_state(proposal, home, result)
        completed = replace(result, status="executed", completed=result.targets)
        return (
            light_state_feedback(observed, completed) == "Te światła były już wyłączone."
            and light_state_feedback(observed, replace(completed, status="error")) is None
        )

    await suite.case(
        "S39",
        "decorative_synonym",
        lambda: plan(
            "wyłącz światła ozdobne", expected=("light.test_ambient",), source_area="test_living"
        ),
    )
    await suite.case("S40", "missing_default_category_confirmation", category_confirmation)
    await suite.case("S41", "missing_default_category_choice", category_choice)
    await suite.case("S42", "category_reply_does_not_extend_ttl", category_expiry)
    await suite.case("S43", "already_off_pure_presentation", already_off_wording)

    def partial_catalog():
        exported = catalog_snapshot()
        identifiers = {item["id"] for item in exported["capabilities"]}
        exported["capabilities"].clear()
        return (
            exported["schema_version"] == 1
            and exported["partial"] is True
            and identifiers == {"light.set_brightness", "sensor.read_temperature"}
            and len(catalog_snapshot()["capabilities"]) == 2
        )

    async def capability_result(text, expected_status, stage, evidence, value_role, value):
        engine = Engine(lambda: home, executor=None, fallback=None)
        result = await engine.process(reference_request(text))
        capability = (result.response_data or {}).get("capability", {})
        facts = capability.get("facts") or {}
        return (
            result.status == expected_status
            and not result.completed
            and capability.get("stage") == stage
            and capability.get("evidence") == evidence
            and capability.get("completed_target_count") == 0
            and facts.get("value_role") == value_role
            and facts.get("value") == value
            and engine.executor is None
            and engine.fallback is None
            and (bool(result.calls) if expected_status == "dry_run" else not result.calls)
        )

    await suite.case("S44", "partial_catalog_snapshot_isolated", partial_catalog)
    await suite.case(
        "S45",
        "brightness_preview_not_observation",
        lambda: capability_result(
            "ustaw światła na 35%", "dry_run", "preview", "validated_plan", "requested", 35
        ),
    )
    await suite.case(
        "S46",
        "temperature_cached_observation",
        lambda: capability_result(
            "jaka jest temperatura", "answered", "answered", "ha_state_cache", "observed", 21.7
        ),
    )
    await suite.case(
        "S47",
        "invalid_brightness_no_execution_claim",
        lambda: capability_result(
            "ustaw światła na 101%", "blocked", "rejected", "none", "requested", None
        ),
    )

    async def background_preview(text, targets, area):
        engine = Engine(lambda: home, executor=None, fallback=None)
        result = await engine.process(reference_request(text))
        return (
            result.status == "confirm"
            and result.reason == "background_confirmation"
            and result.targets == targets
            and result.area_ids == (area,)
            and not result.completed
            and not result.continue_conversation
            and not engine.pending
            and engine.executor is None
            and engine.fallback is None
        )

    async def background_restriction():
        engine = Engine(lambda: home, executor=None, fallback=None)
        result = await engine.process(reference_request("włącz światła nie tylko lampę"))
        return not result.calls and not result.completed and not engine.pending

    async def background_preview_cannot_authorize():
        engine = Engine(lambda: home, executor=None, fallback=None)
        await engine.process(reference_request("włącz światła wow jaka figura"))
        reply = await engine.process(reference_request("tak"))
        return (
            reply.status == "clarify"
            and reply.reason == "no_pending"
            and not reply.calls
            and not reply.completed
            and not engine.pending
        )

    await suite.case(
        "S48",
        "background_light_preview_requires_consent",
        lambda: background_preview("włącz światła wow jaka figura", main, "test_living"),
    )
    await suite.case("S49", "background_restriction_not_noise", background_restriction)
    await suite.case(
        "S50",
        "background_explicit_area_kept",
        lambda: background_preview(
            "wyłącz światła w kuchni wow jaka figura", ("light.test_kitchen",), "test_kitchen"
        ),
    )
    await suite.case(
        "S51", "background_preview_cannot_authorize", background_preview_cannot_authorize
    )

    def factual_brightness_result():
        # Only values are constructed here. No executor, HA service or real
        # Runtime is involved, including for the synthetic completed outcome.
        result = Engine(lambda: home).plan(
            nlu.parse("ustaw światła na 35%"), reference_request("ustaw światła na 35%")
        )
        previous = result._capability_report
        result = replace(result, status="executed", completed=result.targets)
        return annotate_capability(result, previous=previous)

    def brightness_response_facts():
        result = factual_brightness_result()
        return (
            capability_speech(result) == "OK, jasność na 35 procent."
            and result._capability_report.evidence == "executor_returned"
            and result._capability_report.facts.value_role == "requested"
            and home.entities["light.test_main"].state == "off"
        )

    def response_does_not_promote_plan_or_failure():
        result = factual_brightness_result()
        for status in ("dry_run", "confirm", "partial", "error"):
            changed = replace(result, status=status)
            annotate_capability(changed, previous=result._capability_report)
            if capability_speech(changed) is not None:
                return False
        return True

    async def temperature_response_scales():
        original = home.entities["sensor.test_temperature"]
        for value, unit, expected in (
            ("17.7", "Celsius", "Jest około 18 stopni."),
            ("71.6", "Fahrenheit", "Jest około 72 stopni Fahrenheita."),
            ("294.5", "kelvin", "Jest około 295 kelwinów."),
        ):
            sensor = replace(original, state=value, attributes={"unit_of_measurement": unit})
            model = HomeModel(
                home.areas.values(),
                home.floors.values(),
                [sensor if e.id == sensor.id else e for e in home.entities.values()],
                satellites=home.satellites,
                devices=home.devices,
            )
            result = await Engine(lambda model=model: model).process(
                reference_request("jaka jest temperatura")
            )
            if result.speech != expected or result.calls or result.completed:
                return False
        return True

    def factual_variants_respect_silence_and_source():
        result = factual_brightness_result()
        result.speech = capability_speech(result)
        request = replace(reference_request("ustaw światła na 35%"), dry_run=False)
        presentation = Presentation(clock=lambda: 0)
        if presentation.acknowledgement("", result, request) or presentation.fact_replies:
            return False
        replies = [presentation.acknowledgement(result.speech, result, request) for _ in range(3)]
        other = replace(request, satellite_id="assist_satellite.other")
        return (
            len(set(replies)) == 3
            and all("35 procent" in reply for reply in replies)
            and presentation.acknowledgement(result.speech, result, other) == replies[0]
        )

    await suite.case("S52", "brightness_response_uses_requested_facts", brightness_response_facts)
    await suite.case(
        "S53",
        "response_does_not_promote_plan_or_failure",
        response_does_not_promote_plan_or_failure,
    )
    await suite.case("S54", "temperature_response_preserves_scale", temperature_response_scales)
    await suite.case(
        "S55",
        "factual_variants_respect_silence_and_source",
        factual_variants_respect_silence_and_source,
    )

    async def temperature_source_missing():
        original = home.entities["sensor.test_temperature"]
        for sensor, reason in (
            (None, "no_targets"),
            (replace(original, exposed=False), "no_targets"),
            (replace(original, available=False), "ineligible_target"),
        ):
            entities = [e for e in home.entities.values() if e.id != original.id]
            if sensor is not None:
                entities.append(sensor)
            model = home.with_entities(entities)
            engine = Engine(lambda model=model: model, executor=None, fallback=None)
            result = await engine.process(reference_request("jaka jest temperatura"))
            if (
                result.status != "clarify"
                or result.reason != reason
                or result.speech != "Nie mogę teraz sprawdzić temperatury."
                or result.calls
                or result.completed
                or result.continue_conversation
                or engine.pending
            ):
                return False
        return True

    async def temperature_reading_invalid():
        original = home.entities["sensor.test_temperature"]
        for state in ("unknown", "nan", "inf"):
            model = home.with_entities(
                [
                    replace(e, state=state) if e.id == original.id else e
                    for e in home.entities.values()
                ]
            )
            engine = Engine(lambda model=model: model, executor=None, fallback=None)
            result = await engine.process(reference_request("jaka jest temperatura"))
            if (
                result.status != "clarify"
                or result.reason != "invalid_state"
                or result.speech != "Nie mam teraz prawidłowego odczytu temperatury."
                or result.calls
                or result.completed
                or result.continue_conversation
                or engine.pending
            ):
                return False
        return True

    await suite.case("S56", "temperature_missing_source_response", temperature_source_missing)
    await suite.case("S57", "temperature_invalid_reading_response", temperature_reading_invalid)

    def state_query_scope():
        cap = parse_capability("Czy wszystkie światła są wyłączone?")
        if cap is None or cap.scope:
            return False
        result = plan_capability(cap, Request("", source_area="test_kitchen"), home)
        return (
            result.status == "ready"
            and result.targets == ("light.test_kitchen",)
            and result.area_ids == ("test_kitchen",)
            and not result.calls
            and not result.completed
        )

    def state_query_house():
        cap = parse_capability("Czy gdzieś świeci się światło?")
        if cap is None or cap.scope != "house":
            return False
        result = plan_capability(cap, Request("", source_area="test_kitchen"), home)
        return (
            result.status == "ready"
            and set(result.targets)
            == {
                "light.test_main",
                "light.test_ambient",
                "light.test_table",
                "light.test_kitchen",
                "light.test_confirm",
            }
            and not result.area_ids
            and not result.calls
            and not result.completed
        )

    def state_query_missing_is_unknown():
        original = home.entities["light.test_main"]
        entities = (original, replace(original, id="light.test_missing", available=False))
        speech = light_state_speech("all_off", entities)
        cap = parse_capability("Czy wszystkie światła są wyłączone w całym domu?")
        if cap is None:
            return False
        result = plan_capability(cap, Request(""), HomeModel())
        return (
            "Nie mam aktualnego odczytu" in speech
            and not speech.startswith("Tak")
            and result.status == "clarify"
            and result.reason == "no_targets"
            and not result.targets
            and not result.calls
        )

    def state_query_house_cannot_write():
        cap = Capability("routine", "script", name="próba", scope="house")
        result = plan_capability(cap, Request("", source_area="test_living"), home)
        return (
            result.status == "blocked"
            and result.reason == "unsupported_scope"
            and not result.targets
            and not result.calls
            and not result.completed
        )

    await suite.case("S58", "state_read_keeps_room_scope", state_query_scope)
    await suite.case("S59", "state_read_explicit_house_scope", state_query_house)
    await suite.case("S60", "state_read_missing_is_not_off", state_query_missing_is_unknown)
    await suite.case("S61", "state_house_scope_cannot_write", state_query_house_cannot_write)

    def stoic_full_read():
        command = parse_stoic("Przeczytaj dzisiejszą medytację")
        entry = entry_from_attributes(
            {"quote": "Próbny cytat.", "title": "Próbny tytuł", "commentary": "Próbny opis."},
            _NOW.date(),
        )
        speech, reason, missing = reading(entry, command.section)
        return (
            all(text in speech for text in ("Próbny cytat", "Próbny tytuł", "Próbny opis"))
            and not reason
            and not missing
        )

    def stoic_stale_not_current():
        entry = entry_from_attributes({"quote": "Test.", "date": "2026-10-20"}, _NOW.date())
        return entry.date_status == "stale"

    def media_keeps_original_title():
        command = parse_media_search("Puść utwór „Noc w salonie”")
        return command.query == "Noc w salonie" and command.quoted and not command.location

    def media_requires_catalog_uri():
        command = parse_media_search("Puść utwór Próba")
        items = candidates_from_response(
            {
                "tracks": [
                    {"name": "Próba", "uri": "https://example.invalid/signed"},
                    {"name": "Próba", "uri": "library://track/test"},
                ]
            },
            command,
        )
        return len(items) == 1 and items[0].uri == "library://track/test"

    await suite.case("S62", "stoic_full_read_keeps_sections", stoic_full_read)
    await suite.case("S63", "stoic_stale_not_current", stoic_stale_not_current)
    await suite.case("S64", "media_original_title_kept", media_keeps_original_title)
    await suite.case("S65", "media_only_catalog_uri", media_requires_catalog_uri)

    def stoic_natural_questions():
        meditation = parse_stoic("Jaka jest medytacja na dziś?")
        meditation_today = parse_stoic("Jaka jest dzisiejsza medytacja?")
        quote = parse_stoic("Jaki jest cytat na dziś?")
        return (
            meditation.section == meditation_today.section == "full"
            and meditation.today
            and meditation_today.today
            and quote.section == "quote"
            and quote.today
        )

    def media_natural_forms():
        track = parse_media_search("Odpal mi piosenkę Hotel California")
        artist = parse_media_search("Włącz mi muzykę Queen")
        book = parse_media_search("Czy możesz mi włączyć audiobooka Diuna?")
        missing = parse_media_search("Włącz mi audiobooka")
        return (
            track.media_type == "track"
            and track.query == "Hotel California"
            and artist.media_type == "artist"
            and artist.query == "Queen"
            and book.media_type == "audiobook"
            and book.query == "Diuna"
            and missing.media_type == "audiobook"
            and not missing.query
        )

    await suite.case("S66", "stoic_natural_question_forms", stoic_natural_questions)
    await suite.case("S67", "media_natural_request_forms", media_natural_forms)

    async def unlabelled_room_off_fallback():
        bare_home = HomeModel(
            areas=(Area("test_living", "Salon"), Area("test_kitchen", "Kuchnia")),
            entities=(
                Entity("light.living_a", "Lampa A", "test_living", exposed=True),
                Entity("light.living_b", "Lampa B", "test_living", exposed=True),
                Entity(
                    "light.living_excluded",
                    "Lampa wykluczona",
                    "test_living",
                    labels=frozenset({"voice_exclude_all"}),
                    exposed=True,
                ),
                Entity("light.kitchen", "Lampa kuchenna", "test_kitchen", exposed=True),
            ),
        )
        engine = Engine(lambda: bare_home, executor=None, fallback=None)
        local = await engine.process(
            Request("wyłącz światła", source_area="test_living", dry_run=True)
        )
        explicit = await engine.process(
            Request("wyłącz światła w kuchni", source_area="test_living", dry_run=True)
        )
        return (
            local.status == explicit.status == "dry_run"
            and local.targets == ("light.living_a", "light.living_b")
            and explicit.targets == ("light.kitchen",)
            and not local.completed
            and not explicit.completed
            and engine.executor is None
            and engine.fallback is None
        )

    await suite.case(
        "S68", "unlabelled_room_off_fallback_keeps_area_and_guards", unlabelled_room_off_fallback
    )


def _inventory(home):
    entities = tuple(home.entities.values())
    return {
        "areas": len(home.areas),
        "floors": len(home.floors),
        "satellites": len(home.satellites),
        "entities": len(entities),
        "exposed_entities": sum(bool(entity.exposed) for entity in entities),
        "exposed_unavailable_entities": sum(
            entity.exposed and not entity.available for entity in entities
        ),
        "domains": {
            domain: sum(entity.domain == domain for entity in entities) for domain in DOMAINS
        },
        "other_domains": sum(entity.domain not in DOMAINS for entity in entities),
        "role_labels": {
            role: sum(role in entity.labels for entity in entities) for role in sorted(ROLES)
        },
    }


async def _configuration_cases(suite, home, settings):
    started = perf_counter()
    counts = {code: {severity: 0 for severity in SEVERITIES} for code in FINDINGS}
    scan_failed = False
    try:
        findings = check(home, frozenset(home.entities))
        findings.extend(
            {"code": finding.code, "severity": finding.severity}
            for finding in music_health(home, settings)
        )
        for finding in findings:
            if finding["code"] in counts and finding["severity"] in SEVERITIES:
                counts[finding["code"]][finding["severity"]] += 1
        suite.record(
            "H01",
            "pass",
            "configuration_scan_completed",
            "configuration_scan_completed",
            started,
            "home_configuration",
        )
    except Exception:
        scan_failed = True
        suite.record(
            "H01",
            "fail",
            "configuration_scan_completed",
            "case_exception",
            started,
            "home_configuration",
        )
    groups = (
        ("H02", "alias_conflicts_absent", ("alias_conflict", "location_alias_conflict")),
        ("H03", "areas_assigned", ("missing_area",)),
        ("H04", "satellites_have_area", ("satellite_without_area",)),
        ("H05", "labels_compatible", ("conflicting_labels",)),
        (
            "H06",
            "music_mapping_ready",
            ("music_room_mapping", "music_player_unavailable", "music_shortcut_missing"),
        ),
    )
    for identifier, code, codes in groups:
        started = perf_counter()
        count = sum(sum(counts[item].values()) for item in codes)
        absent_music = identifier == "H06" and not settings.get("music_rooms")
        status = "skip" if scan_failed or absent_music else "warn" if count else "pass"
        actual = (
            "configuration_scan_failed"
            if scan_failed
            else "not_configured"
            if absent_music
            else "configuration_findings"
            if count
            else code
        )
        suite.record(identifier, status, code, actual, started, "home_configuration")
    entities = tuple(home.entities.values())
    for identifier, domain in (("H07", "weather"), ("H08", "calendar")):
        started = perf_counter()
        candidates = [entity for entity in entities if entity.domain == domain]
        accessible = [
            entity for entity in candidates if entity.exposed and "voice_block" not in entity.labels
        ]
        status = (
            "skip"
            if not candidates
            else "pass"
            if any(entity.available for entity in accessible)
            else "warn"
        )
        actual = (
            "not_configured"
            if not candidates
            else "local_source_ready"
            if status == "pass"
            else "no_available_exposed_source"
        )
        suite.record(
            identifier, status, "local_source_ready", actual, started, "home_configuration"
        )
    started = perf_counter()
    unavailable = sum(entity.exposed and not entity.available for entity in entities)
    suite.record(
        "H09",
        "warn" if unavailable else "pass",
        "exposed_sources_available",
        "exposed_source_unavailable" if unavailable else "exposed_sources_available",
        started,
        "home_configuration",
    )
    started = perf_counter()
    suite.record(
        "H10",
        "pass" if home.areas else "warn",
        "areas_present",
        "areas_present" if home.areas else "no_areas",
        started,
        "home_configuration",
    )
    await asyncio.sleep(0)
    return {"findings_by_code_and_severity": counts}


async def run_suite(home: HomeModel, music_settings: dict | None = None) -> dict:
    """Return one sanitized report; read metadata, never execute device actions.

    Snapshot acquisition, permissions, storage and download belong to the HA
    adapter. Timing is local suite work, never an audio or device latency claim.
    Unknown household domains, labels and exception details are not serialized.
    """
    started = perf_counter()
    suite = _Suite()
    await _synthetic_cases(suite)
    configuration = await _configuration_cases(suite, home, music_settings or {})
    inventory = _inventory(home)
    return {
        "schema_version": 1,
        "suite_version": 11,
        "run_id": str(uuid4()),
        "suite_name": "bociek_safe_self_test",
        "storage": "memory_only",
        "maximum_cases": MAX_CASES,
        **_summary(suite.cases),
        "duration_ms": round(max(0.0, perf_counter() - started) * 1000, 3),
        "sections": {name: _summary(cases) for name, cases in suite.sections.items()},
        "cases": suite.cases,
        "inventory": inventory,
        "configuration": configuration,
        "not_measured": [
            "stt",
            "tts",
            "audio_capture",
            "audible_response_start",
            "physical_devices",
            "provider_connectivity",
            "forecast_contents",
            "calendar_contents",
            "music_playback",
            "live_user_permissions",
            "live_household_intent_correctness",
        ],
    }
