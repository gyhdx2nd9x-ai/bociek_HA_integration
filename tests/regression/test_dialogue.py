"""Multi-turn behavior, exact targets, changing metadata and source isolation."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.context.dialogue import Dialogue
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request


def req(text, **kwargs):
    return Request(
        text, **({"source_area": "living_room", "conversation_id": "c", "user_id": "u"} | kwargs)
    )


async def test_named_target_then_brightness_then_off(engine, executor):
    first = await engine.process(req("włącz lampkę stołową"))
    assert first.status == "executed"
    second = await engine.process(req("ustaw ją na czterdzieści procent"))
    assert second.status == "executed"
    assert second.calls[0].data == {"brightness_pct": 40}
    third = await engine.process(req("zgaś ją"))
    assert third.targets == first.targets == second.targets == ("light.living_room_table",)
    assert third.status == "executed"
    assert len(executor.calls) == 3


async def test_pronoun_keeps_exact_set_and_generic_off_keeps_asymmetry(engine):
    await engine.process(req("zapal światła"))
    assert (await engine.process(req("zgaś je"))).targets == ("light.living_room_main",)
    assert len((await engine.process(req("wyłącz światła"))).targets) == 3


async def test_explicit_area_ellipsis_overrides_satellite(engine):
    await engine.process(req("zapal światła"))
    result = await engine.process(req("a w kuchni?"))
    assert result.status == "executed"
    assert result.targets == ("light.kitchen_main",)
    assert result.area_ids == ("kitchen",)
    assert (await engine.process(req("zgaś je"))).targets == ("light.kitchen_main",)


async def test_explicit_unknown_area_ellipsis_never_uses_previous_room(engine, executor):
    await engine.process(req("zapal światła"))
    result = await engine.process(req("a w piwnicy?"))
    assert result.reason == "unknown_location"
    assert len(executor.calls) == 1


@pytest.mark.parametrize("reply", ["W kuchni też.", "W kuchni tak samo.", "A w kuchni również."])
async def test_short_room_repeat_resolves_targets_in_named_room(engine, executor, reply):
    result = await engine.process(req("Włącz światła", satellite_id="salon"))
    assert result.completed == ("light.living_room_main",)
    result = await engine.process(req(reply, satellite_id="salon"))
    assert result.completed == ("light.kitchen_main",) and not result.continue_conversation
    assert [call.entity_ids for call in executor.calls] == [
        ("light.living_room_main",), ("light.kitchen_main",),
    ]


@pytest.mark.parametrize("first, reply, source", [
    ("Włącz lampkę stołową", "W kuchni też", "salon"),
    ("Włącz światła", "W kuchni też", "kuchnia"),
    ("Włącz światła", "W kuchni też да", "salon"),
    ("Włącz światła", "W piwnicy tak samo", "salon"),
    ("Włącz światła", "W kuchni też, ale nie teraz", "salon"),
])
async def test_short_room_repeat_requires_trusted_previous_action(
    engine, executor, first, reply, source,
):
    await engine.process(req(first, satellite_id="salon"))
    result = await engine.process(req(reply, satellite_id=source))
    assert not result.calls and len(executor.calls) == 1


async def test_short_brightness_and_pronoun_keep_exact_light_in_original_scope(engine, executor):
    first = await engine.process(req("Włącz lampę w kuchni"))
    exact = ("light.kitchen_main",)
    assert first.completed == exact
    dimmed = await engine.process(req("I jeszcze na pięćdziesiąt pięć procent."))
    assert dimmed.completed == exact
    assert dimmed.calls[0].data == {"brightness_pct": 55}
    assert dimmed.response_data["interpretation"]["target_source"] == "dialogue"
    assert (await engine.process(req("A teraz ją zgaś."))).completed == exact
    assert (await engine.process(req("Teraz ją zapal."))).completed == exact
    assert len(executor.calls) == 4


@pytest.mark.parametrize("reply", [
    "I jeszcze na 55 procent да", "I jeszcze na 55 procent, ale jutro",
    "A teraz ją zgaś i otwórz rolety", "A teraz ją zgaś да",
])
async def test_short_update_never_drops_extra_words(engine, executor, reply):
    await engine.process(req("Zapal lampkę stołową"))
    result = await engine.process(req(reply))
    assert not result.calls and len(executor.calls) == 1


async def test_here_is_current_room_after_remote_command(engine, executor):
    await engine.process(req("Włącz światła w salonie", source_area="kitchen"))
    turned_on = await engine.process(req("Włącz tutejsze światło", source_area="kitchen"))
    assert turned_on.completed == ("light.kitchen_main",)
    turned_off = await engine.process(req("Teraz zgaś tutaj światło", source_area="kitchen"))
    assert turned_off.completed == turned_on.completed
    assert executor.calls[-1].service == "turn_off"


@pytest.mark.parametrize("text", [
    "Ale tu ciemno!",
    "Ale tutaj jest ciemno.",
    "Zrób tu jasno.",
    "Tu zrób jasno.",
    "Daj mi światło, proszę.",
    "Poproszę o światło.",
    "Potrzebuję zapalonego światła.",
])
async def test_request_for_bright_room_uses_default_lighting(engine, executor, text):
    result = await engine.process(req(text))
    assert result.completed == ("light.living_room_main",)
    assert len(executor.calls) == 1
    assert executor.calls[0].service == "turn_on" and executor.calls[0].data == {}


@pytest.mark.parametrize("text", [
    "Ale ciemno w kuchni", "W kuchni zrób jasno", "Zrób w kuchni jasno",
])
async def test_request_for_bright_room_respects_explicit_place(engine, executor, text):
    result = await engine.process(req(text))
    assert result.completed == ("light.kitchen_main",)
    assert result.area_ids == ("kitchen",) and len(executor.calls) == 1


@pytest.mark.parametrize("text", [
    "Zrób ciemno w kuchni.", "Czy dasz radę zgasić światło w kuchni?",
])
async def test_request_for_dark_room_uses_only_lights_in_scope(engine, executor, text):
    result = await engine.process(req(text))
    assert result.completed == ("light.kitchen_main",)
    assert result.area_ids == ("kitchen",)
    assert len(executor.calls) == 1 and executor.calls[0].service == "turn_off"


@pytest.mark.parametrize("text", [
    "Czy tu jest jasno?", "Zrób tu jasno, gdy wrócę.", "Zrób tu jasno po 22.",
    "Daj światło, ale nie teraz.", "Zrób jasno i otwórz rolety.",
    "Zrób tu jasno да.", "W kuchni zrób jasno w salonie.",
    "Powiedział: zrób tu jasno.", "Daj mi światło do muzyki.",
    "Ale tu ciemno?", "Ale tu ciemno, kiedy pada.", "Ale tu ciemno да.",
    "Ale ciemno.", "Ale tu ciemno i zasłoń rolety.",
])
async def test_room_outcomes_do_not_execute_questions_conditions_or_extra_actions(
    engine, executor, text,
):
    result = await engine.process(req(text))
    assert not result.calls and not executor.calls


async def test_room_correction_answers_current_question(engine, executor):
    first = await engine.process(req("zapal światła", source_area=None))
    assert first.reason == "missing_area"
    mixed = await engine.process(req("Nie, w kuchni да", source_area=None))
    assert mixed.continue_conversation and not executor.calls
    result = await engine.process(req("Nie, w kuchni", source_area=None))
    assert result.targets == ("light.kitchen_main",)
    unused = await engine.process(req("Nie, w salonie", source_area=None))
    assert unused.status == "confirm" and len(executor.calls) == 1


@pytest.mark.parametrize("correction", ["Nie, w kuchni.", "Nie tutaj tylko w kuchni"])
async def test_completed_room_correction_requires_same_source_to_confirm(
    engine, executor, correction,
):
    await engine.process(req("Zapal światła", satellite_id="salon"))
    question = await engine.process(req(correction, satellite_id="salon"))
    assert question.reason == "room_correction_confirmation" and question.continue_conversation
    assert question.speech == "Gotowe. Włączyć też światła w kuchni?"
    assert question.targets == ("light.kitchen_main",)
    assert len(executor.calls) == 1
    assert (await engine.process(req("Tak", satellite_id="inne"))).reason == "no_pending"
    confirmed = await engine.process(req("Tak", satellite_id="salon"))
    assert confirmed.completed == question.targets and len(executor.calls) == 2
    assert (await engine.process(req("Tak", satellite_id="salon"))).reason == "no_pending"
    assert [call.entity_ids for call in executor.calls] == [
        ("light.living_room_main",), ("light.kitchen_main",),
    ]


@pytest.mark.parametrize(
    ("first", "correction", "reason"),
    [
        ("Zapal światła", "Nie, w salonie", "room_already_in_scope"),
        ("Zapal światła", "Nie, w piwnicy", "unknown_location"),
        ("Zapal światła", "Nie, na parterze", "room_correction_needs_area"),
        ("Zapal światła", "Nie, w kuchni да", "unclear_reply"),
        ("Zapal światła", "Nie tylko w kuchni", "no_intent"),
        ("Zapal lampkę stołową", "Nie, w kuchni", "no_reference"),
    ],
)
async def test_room_correction_does_not_infer_or_expand_scope(
    engine, executor, first, correction, reason,
):
    await engine.process(req(first))
    result = await engine.process(req(correction))
    assert result.reason == reason and not result.calls
    assert len(executor.calls) == 1


@pytest.mark.parametrize("changed", ["state", "exposure"])
async def test_room_correction_rechecks_live_filter_and_exposure(home, engine, executor, changed):
    kitchen = home.entities["light.kitchen_main"]
    home.entities[kitchen.id] = replace(kitchen, state="on")
    first = await engine.process(req("Zgaś zapalone"))
    assert first.completed == ("light.living_room_ambient", "light.living_room_table")
    question = await engine.process(req("Nie, w kuchni"))
    assert question.reason == "room_correction_confirmation"
    assert "zapalone światła w kuchni" in question.speech
    assert question.calls[0].required_state == "on"
    change = {"state": "off"} if changed == "state" else {"exposed": False}
    home.entities[kitchen.id] = replace(home.entities[kitchen.id], **change)
    assert (await engine.process(req("Tak"))).reason == "plan_changed"
    assert len(executor.calls) == 2


@pytest.mark.parametrize("interruption", ["Nie", "Włącz światło w biurze", None])
async def test_room_correction_cancel_interruption_and_expiry(home, executor, interruption):
    now = [0]
    engine = Engine(lambda: home, executor, clock=lambda: now[0])
    await engine.process(req("Zapal światła"))
    assert (await engine.process(req("Nie, w kuchni"))).status == "confirm"
    if interruption is None:
        now[0] = engine.confirmation_ttl
    else:
        await engine.process(req(interruption))
    assert (await engine.process(req("Tak"))).reason == "no_pending"
    assert all(call.entity_ids != ("light.kitchen_main",) for call in executor.calls)


async def test_room_correction_in_preview_is_preview_only(engine, executor):
    await engine.process(req("Zapal światła", dry_run=True))
    correction = await engine.process(req("Nie, w kuchni", dry_run=True))
    assert correction.status == "confirm" and correction.speech.startswith("Poprzednia próba")
    assert (await engine.process(req("Tak"))).reason == "no_pending"
    preview = await engine.process(req("Tak", dry_run=True))
    assert preview.status == "dry_run" and preview.targets == ("light.kitchen_main",)
    assert not executor.calls


async def test_pending_room_correction_replaces_only_the_room(home, engine, executor):
    salon = home.entities["light.living_room_main"]
    home.entities[salon.id] = replace(salon, labels=salon.labels | {"voice_confirm"})
    request = req("Zapal światła", satellite_id="salon")
    original = await engine.process(request)
    assert original.status == "confirm" and original.targets == (salon.id,)
    foreign = await engine.process(replace(request, text="Nie, w kuchni", satellite_id="obca"))
    assert foreign.reason == "no_reference"
    assert engine.pending[request.session_key].proposal.proposal.location is None
    corrected = await engine.process(replace(request, text="Nie, w kuchni"))
    assert corrected.status == "confirm" and corrected.continue_conversation
    assert corrected.speech == "Włączyć światła w kuchni?"
    assert corrected.targets == ("light.kitchen_main",)
    assert not executor.calls
    result = await engine.process(replace(request, text="Tak"))
    assert result.completed == corrected.targets
    assert [call.entity_ids for call in executor.calls] == [corrected.targets]
    assert (await engine.process(replace(request, text="Tak"))).reason == "no_pending"


@pytest.mark.parametrize("correction", ["Nie, w piwnicy", "Nie, w kuchni да", "Nie tylko w kuchni"])
async def test_unusable_correction_discards_old_plan(home, engine, executor, correction):
    salon = home.entities["light.living_room_main"]
    home.entities[salon.id] = replace(salon, labels=salon.labels | {"voice_confirm"})
    assert (await engine.process(req("Zapal światła"))).status == "confirm"
    assert (await engine.process(req(correction))).status != "confirm"
    assert (await engine.process(req("Tak"))).reason == "no_pending"
    assert not executor.calls


async def test_repeated_room_corrections_keep_original_confirmation_deadline(home, executor):
    salon = home.entities["light.living_room_main"]
    home.entities[salon.id] = replace(salon, labels=salon.labels | {"voice_confirm"})
    now = [0]
    engine = Engine(lambda: home, executor, clock=lambda: now[0])
    assert (await engine.process(req("Zapal światła"))).status == "confirm"
    now[0] = 20
    assert (await engine.process(req("Nie, w kuchni"))).status == "confirm"
    now[0] = 29
    assert (await engine.process(req("Nie, w biurze"))).targets == ("light.office_main",)
    now[0] = 30
    assert (await engine.process(req("Tak"))).reason == "no_pending"
    assert not executor.calls


async def test_repeated_correction_after_execution_remembers_original_room(engine, executor):
    await engine.process(req("Zapal światła"))
    assert (await engine.process(req("Nie, w kuchni"))).status == "confirm"
    other = await engine.process(req("Nie, w biurze"))
    assert other.speech == "Gotowe. Włączyć też światła w biurze?"
    assert (await engine.process(req("Nie, w salonie"))).reason == "room_already_in_scope"
    assert (await engine.process(req("Tak"))).reason == "no_pending"
    assert [call.entity_ids for call in executor.calls] == [("light.living_room_main",)]


async def test_lit_light_selection_uses_live_state_for_the_new_room(home, engine, executor):
    for identifier, state in {
        "light.living_room_main": "off", "light.living_room_table": "on",
        "light.living_room_ambient": "unavailable", "light.kitchen_main": "on",
    }.items():
        home.entities[identifier] = replace(home.entities[identifier], state=state)
    first = await engine.process(req("Zgaś zapalone w tym pokoju"))
    assert first.completed == ("light.living_room_table",)
    assert "Brakuje odczytu" in first.speech
    assert first.calls[0].required_state == "on"
    other = await engine.process(req("A w kuchni?"))
    assert other.completed == ("light.kitchen_main",)
    assert len(executor.calls) == 2


async def test_lit_light_pronoun_does_not_expand_when_state_changes(home, engine):
    table = home.entities["light.living_room_table"]
    ambient = home.entities["light.living_room_ambient"]
    for entity, state in ((table, "on"), (ambient, "off")):
        home.entities[entity.id] = replace(entity, state=state)
    home.entities["light.living_room_main"] = replace(
        home.entities["light.living_room_main"], state="off",
    )
    first = await engine.process(req("Wyłącz światła, które się świecą"))
    assert first.completed == (table.id,)
    home.entities[table.id] = replace(table, state="off")
    home.entities[ambient.id] = replace(ambient, state="on")
    next_result = await engine.process(req("Zapal ją"))
    assert next_result.completed == (table.id,)
    assert next_result.calls[0].required_state is None


async def test_lit_light_confirmation_is_invalidated_if_another_light_turns_on(home, engine):
    for identifier, changes in {
        "light.living_room_main": {"state": "off"},
        "light.living_room_table": {"state": "on", "labels": {"voice_all_off", "voice_confirm"}},
        "light.living_room_ambient": {"state": "off"},
    }.items():
        home.entities[identifier] = replace(home.entities[identifier], **changes)
    planned = await engine.process(req("Zgaś włączone światła"))
    assert planned.status == "confirm"
    home.entities["light.living_room_ambient"] = replace(
        home.entities["light.living_room_ambient"], state="on",
    )
    assert (await engine.process(req("Tak"))).reason == "plan_changed"


async def test_ambiguous_lamp_select_exact_alias_then_reference(engine, executor):
    question = await engine.process(req("włącz lampę"))
    assert question.reason == "ambiguous_target" and question.continue_conversation
    options = question.response_data["clarification_options"]
    assert {o["entity_id"] for o in options} == {
        "light.living_room_main",
        "light.living_room_table",
        "light.living_room_ambient",
    }
    assert not executor.calls
    result = await engine.process(req("lampa przy stole"))
    assert result.status == "executed" and result.targets == ("light.living_room_table",)
    assert (await engine.process(req("wyłącz ją"))).targets == result.targets



@pytest.mark.parametrize(
    ("text", "domain", "features", "data", "status"),
    [
        ("Otwórz zasłony do połowy", "cover", 4, {"position": 50}, "confirm"),
        ("Ustaw rolety na 35 procent", "cover", 3, None, "blocked"),
        ("Ustaw rolety na 105 procent", "cover", 4, None, "blocked"),
        ("Ustaw wentylator na 30 proc.", "fan", 1, {"percentage": 30}, "dry_run"),
        ("Ustaw wiatrak na 30 procent", "fan", 32, None, "blocked"),
        ("Wyłącz wiatrak", "fan", 16, {}, "dry_run"),
    ],
)
async def test_room_device_commands_check_features_and_ranges(
    home, executor, text, domain, features, data, status,
):
    identifier = "cover.blind" if domain == "cover" else "fan.test"
    entity = Entity(
        identifier, "Urządzenie testowe", "living_room",
        exposed=True, attributes={"supported_features": features},
    )
    home.entities[entity.id] = entity
    result = await Engine(lambda: home, executor).process(req(text, dry_run=True))
    assert result.status == status and not executor.calls
    if data is None:
        assert not result.calls
    else:
        assert len(result.calls) == 1
        assert result.calls[0].entity_ids == (entity.id,) and result.calls[0].data == data


async def test_motion_command_never_operates_a_garage_door(home, executor):
    entity = Entity(
        "cover.garage", "Brama", "living_room", device_class="garage",
        exposed=True, attributes={"supported_features": 4},
    )
    home.entities[entity.id] = entity
    result = await Engine(lambda: home, executor).process(req("Otwórz rolety do połowy"))
    assert not result.calls and not executor.calls
    assert entity.id not in result.targets


async def test_two_fans_or_singular_covers_prompt_for_one(home, executor):
    home.entities.pop("cover.blind")
    for domain, features in (("fan", 32), ("cover", 4)):
        for identifier in ("one", "two"):
            entity = Entity(
                f"{domain}.{identifier}", f"Urządzenie {identifier}", "living_room",
                exposed=True, attributes={"supported_features": features},
            )
            home.entities[entity.id] = entity
    engine = Engine(lambda: home, executor)
    for text in ("Włącz wiatrak", "Otwórz roletę do połowy"):
        result = await engine.process(req(text))
        assert result.reason == "ambiguous_target" and result.continue_conversation
        assert not executor.calls


def named_lights(home):
    return HomeModel(
        home.areas.values(), home.floors.values(),
        (
            replace(entity, aliases=(*entity.aliases, "panel próbny"))
            if entity.domain == "light" and entity.area_id == "living_room" else entity
            for entity in home.entities.values()
        ),
        satellites=home.satellites, devices=home.devices,
    )


@pytest.mark.parametrize("text", ["Włącz lampę", "Zapal lampkę"])
async def test_generic_lamp_selects_preferred_without_question(home, executor, text):
    table = home.entities["light.living_room_table"]
    home.entities[table.id] = replace(table, labels=table.labels | {"voice_primary"})
    engine = Engine(lambda: home, executor, explain_resolution=True)
    preview = await engine.process(req(text, dry_run=True))
    assert preview.status == "dry_run" and preview._resolution_trace.primary
    assert preview.targets == (table.id,) and not executor.calls
    result = await engine.process(req(text))
    assert result.completed == (table.id,) and not result.continue_conversation
    assert (await engine.process(req("zgaś ją"))).targets == (table.id,)


@pytest.mark.parametrize("text", ["Włącz lampę w kuchni", "Włącz światło w kuchni"])
async def test_generic_singular_light_uses_room_with_one_unlabelled_light(home, executor, text):
    kitchen = home.entities["light.kitchen_main"]
    home.entities[kitchen.id] = replace(kitchen, labels=frozenset())
    engine = Engine(lambda: home, executor)
    result = await engine.process(req(text))
    assert result.completed == (kitchen.id,) and not result.continue_conversation


@pytest.mark.parametrize("change", [{"available": False}, {"labels": {"voice_block"}}])
async def test_generic_lamp_does_not_replace_ineligible_preferred_light(home, executor, change):
    table = home.entities["light.living_room_table"]
    updated = replace(table, **change)
    home.entities[table.id] = replace(updated, labels=updated.labels | {"voice_primary"})
    engine = Engine(lambda: home, executor)
    result = await engine.process(req("Włącz lampę"))
    assert result.reason == "ineligible_target" and not executor.calls


@pytest.mark.parametrize("answer", ["tę domyślną", "ta preferowana", "pierwszą"])
async def test_light_preference_reorders_choices_without_resolving_alias(home, executor, answer):
    home = named_lights(home)
    table = home.entities["light.living_room_table"]
    home.entities[table.id] = replace(table, labels=table.labels | {"voice_primary"})
    engine = Engine(lambda: home, executor, explain_resolution=True)
    preview = await engine.process(req("Włącz panel próbny", dry_run=True))
    assert preview._resolution_trace.preferred_light == table.id
    question = await engine.process(req("Włącz panel próbny"))
    options = question.response_data["clarification_options"]
    assert options[0]["entity_id"] == table.id and "(opcja domyślna)" in question.speech
    assert len(options) == 3 and not executor.calls
    result = await engine.process(req(answer))
    assert result.completed == (table.id,)


async def test_two_labeled_lights_do_not_offer_a_default(home, executor):
    for key in ("light.living_room_main", "light.living_room_table"):
        entity = home.entities[key]
        home.entities[key] = replace(entity, labels=entity.labels | {"voice_primary"})
    engine = Engine(lambda: home, executor, explain_resolution=True)
    preview = await engine.process(req("Włącz lampę", dry_run=True))
    assert preview._resolution_trace.light_preferences_conflict
    await engine.process(req("Włącz lampę"))
    assert (await engine.process(req("tę domyślną"))).continue_conversation
    assert not executor.calls
    assert (await engine.process(req("lampę przy stole"))).completed == ("light.living_room_table",)


@pytest.mark.parametrize("reply", ["tę domyślną", "ta pierwsza"])
async def test_changed_preference_preserves_announced_order(home, executor, reply):
    home = named_lights(home)
    table = home.entities["light.living_room_table"]
    engine = Engine(lambda: home, executor)
    main = home.entities["light.living_room_main"]
    home.entities[table.id] = replace(table, labels=table.labels | {"voice_primary"})
    await engine.process(req("Włącz panel próbny"))
    home.entities[table.id] = table
    home.entities[main.id] = replace(main, labels=main.labels | {"voice_primary"})
    result = await engine.process(req(reply))
    if reply == "tę domyślną":
        assert result.reason == "plan_changed" and not executor.calls
    else:
        assert result.completed == (table.id,)


async def test_default_reply_preserves_source_and_guard(home, executor):
    home = named_lights(home)
    table = home.entities["light.living_room_table"]
    home.entities[table.id] = replace(
        table, labels=table.labels | {"voice_primary", "voice_confirm"},
    )
    engine = Engine(lambda: home, executor)
    await engine.process(req("Włącz panel próbny", satellite_id="salon"))
    other = await engine.process(req("domyślną", satellite_id="inna"))
    assert other.status != "executed" and not other.calls
    result = await engine.process(req("tę domyślną", satellite_id="salon"))
    assert result.status == "confirm" and result.targets == (table.id,)
    assert not executor.calls
    assert (await engine.process(req("tak", satellite_id="salon"))).completed == (table.id,)


async def test_default_reply_without_question_skips_fallback(home, executor):
    class Fallback:
        async def propose(self, *args):
            pytest.fail("A contextual reply reached the fallback without an active question")

    engine = Engine(lambda: home, executor, fallback=Fallback())
    assert (await engine.process(req("tę domyślną"))).reason == "no_reference"
    assert not executor.calls


async def test_numbered_reply_uses_announced_order_and_original_command(engine, executor):
    first = await engine.process(req("włącz lampę"))
    expected = first.response_data["clarification_options"][1]["entity_id"]
    for text in ("druga да", "druga albo trzecia", "numer 5"):
        reply = await engine.process(req(text))
        assert reply.continue_conversation and not executor.calls
    result = await engine.process(req("Nie to, tę drugą lampkę"))
    assert result.status == "executed" and result.targets == (expected,)
    assert len(executor.calls) == 1 and executor.calls[0].service == "turn_on"
    assert (await engine.process(req("ta druga"))).status == "clarify"
    assert len(executor.calls) == 1


async def test_correcting_completed_light_choice_uses_the_announced_order(engine, executor):
    question = await engine.process(req("włącz lampę"))
    options = question.response_data["clarification_options"]
    first, second = options[:2]
    # Name and ordinal responses must both carry the original list forward.
    assert (await engine.process(req(first["name"]))).completed == (first["entity_id"],)
    foreign = await engine.process(req("Nie, tę drugą", satellite_id="obce"))
    assert foreign.reason == "no_numbered_options"
    correction = await engine.process(req("Nie, tę drugą"))
    assert correction.reason == "light_choice_correction_confirmation"
    assert correction.targets == (second["entity_id"],)
    assert correction.speech.startswith("Gotowe. Włączyć też ")
    assert second["name"] in correction.speech
    assert len(executor.calls) == 1
    assert (await engine.process(req("Tak"))).completed == correction.targets
    back = await engine.process(req("Nie, tę pierwszą"))
    assert back.reason == "choice_already_in_scope"
    assert [call.entity_ids for call in executor.calls] == [
        (first["entity_id"],), (second["entity_id"],),
    ]


async def test_correcting_pending_light_choice_replaces_only_the_lamp(home, engine, executor):
    for entity in list(home.entities.values()):
        if entity.domain == "light":
            home.entities[entity.id] = replace(entity, labels=entity.labels | {"voice_confirm"})
    question = await engine.process(req("wyłącz lampę"))
    first, second = question.response_data["clarification_options"][:2]
    assert (await engine.process(req("pierwsza"))).targets == (first["entity_id"],)
    correction = await engine.process(req("Nie, tą drugą"))
    assert correction.status == "confirm" and correction.speech.startswith("Zgasić „")
    assert correction.targets == (second["entity_id"],) and not executor.calls
    assert (await engine.process(req("Tak"))).completed == correction.targets
    assert len(executor.calls) == 1 and executor.calls[0].service == "turn_off"


@pytest.mark.parametrize("change", ["before_correction", "after_correction", "expired", "noise"])
async def test_corrected_choice_is_revalidated(home, executor, change):
    now = [0]
    engine = Engine(lambda: home, executor, clock=lambda: now[0])
    question = await engine.process(req("włącz lampę"))
    second = question.response_data["clarification_options"][1]["entity_id"]
    assert (await engine.process(req("pierwsza"))).status == "executed"
    if change == "expired":
        now[0] = engine.dialogue.clarification_ttl
    if change == "before_correction":
        home.entities[second] = replace(home.entities[second], exposed=False)
    text = "Nie, tę drugą да" if change == "noise" else "Nie, tę drugą"
    corrected = await engine.process(req(text))
    if change == "after_correction":
        assert corrected.status == "confirm"
        home.entities[second] = replace(home.entities[second], exposed=False)
        assert (await engine.process(req("Tak"))).reason == "plan_changed"
    else:
        assert corrected.status == "clarify" and not corrected.calls
        assert (await engine.process(req("Tak"))).reason == "no_pending"
    assert len(executor.calls) == 1


async def test_option_correction_does_not_reapply_shared_group_members(home, executor):
    leaf = home.entities["light.living_room_table"]
    groups = [
        Entity(
            id=f"light.group_{index}", name=f"Grupa {index}", area_id=leaf.area_id, exposed=True,
            aliases=("panel wspólny",), attributes={"entity_id": [leaf.id]},
        )
        for index in (1, 2)
    ]
    model = type(home)(
        home.areas.values(), home.floors.values(), [*home.entities.values(), *groups],
    )
    engine = Engine(lambda: model, executor)
    question = await engine.process(req("włącz panel wspólny"))
    assert question.continue_conversation and question.reason == "ambiguous_target"
    assert (await engine.process(req("pierwsza"))).completed == (leaf.id,)
    correction = await engine.process(req("Nie, tę drugą"))
    assert correction.reason == "overlapping_light_choice"
    assert len(executor.calls) == 1


async def test_resolution_evidence_is_optional_and_does_not_change_targets(home, executor):
    engine = Engine(lambda: home, executor, explain_resolution=True)
    request = req("zapal światła", dry_run=True)
    preview = await engine.process(request)
    baseline = await Engine(lambda: home).process(request)
    assert preview.targets == baseline.targets
    assert preview.calls == baseline.calls
    assert preview.response_data == baseline.response_data
    assert preview._resolution_trace.roots == ("light.living_room_main",)
    assert preview._resolution_trace.location_source == "source"
    assert "_resolution_trace" not in preview.to_dict()
    assert not hasattr(baseline, "_resolution_trace")
    assert not executor.calls
    result = await engine.process(replace(request, dry_run=False))
    assert result.status == "executed" and not hasattr(result, "_resolution_trace")


async def test_missing_area_is_answerable(engine, executor):
    question = await engine.process(req("zapal światła", source_area=None))
    assert question.speech == "W którym pomieszczeniu?" and question.continue_conversation
    assert not executor.calls
    result = await engine.process(req("w kuchni", source_area=None))
    assert result.targets == ("light.kitchen_main",) and result.status == "executed"


@pytest.mark.parametrize(
    "first,reply", [("włącz lampę", "lampa przy stole"), ("zapal światła", "zgaś je")]
)
@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "other"},
        {"conversation_id": "other"},
        {"satellite_id": "other"},
        {"device_id": "other"},
        {"source_area": "kitchen"},
        {"dry_run": True},
    ],
)
async def test_dialogue_never_crosses_session_parts(engine, executor, first, reply, change):
    await engine.process(req(first))
    before = len(executor.calls)
    result = await engine.process(req(reply, **change))
    assert result.status not in {"executed", "dry_run", "confirm"}
    assert len(executor.calls) == before
    assert (await engine.process(req(reply))).status == "executed"


@pytest.mark.parametrize(
    "first,reply,ttl", [("włącz lampę", "lampa przy stole", 30), ("zapal światła", "zgaś je", 60)]
)
async def test_dialogue_expires_even_when_followup_requested(home, executor, first, reply, ttl):
    now = [0]
    engine = Engine(lambda: home, executor, clock=lambda: now[0])
    await engine.process(req(first))
    before = len(executor.calls)
    now[0] = ttl
    assert (await engine.process(req(reply))).status not in {"executed", "confirm"}
    assert len(executor.calls) == before


@pytest.mark.parametrize("change", ["exposure", "unavailable", "area", "label"])
@pytest.mark.parametrize(
    "first,reply,identifier",
    [
        ("włącz lampę", "lampa przy stole", "light.living_room_table"),
        ("zapal światła", "zgaś je", "light.living_room_main"),
    ],
)
async def test_followup_rechecks_configuration(
    home, engine, executor, first, reply, identifier, change
):
    await engine.process(req(first))
    before = len(executor.calls)
    entity = home.entities[identifier]
    updates = {
        "exposure": {"exposed": False},
        "unavailable": {"available": False},
        "area": {"area_id": "kitchen"},
        "label": {"labels": entity.labels | {"voice_block"}},
    }
    home.entities[identifier] = replace(entity, **updates[change])
    result = await engine.process(req(reply))
    assert result.status == "clarify"
    assert len(executor.calls) == before


async def test_new_aggregate_member_never_broadens_pronoun(home, engine, executor):
    await engine.process(req("zapal światła"))
    home.entities["light.extra"] = replace(
        home.entities["light.living_room_main"], id="light.extra"
    )
    assert (await engine.process(req("zgaś je"))).reason == "context_changed"
    assert len(executor.calls) == 1


async def test_group_guards_survive_pronoun(home, executor):
    group = Entity(
        "light.group",
        "Grupa",
        "living_room",
        exposed=True,
        labels=frozenset({"voice_confirm"}),
        attributes={"entity_id": ["light.living_room_table"]},
    )
    home = type(home)(home.areas.values(), home.floors.values(), [*home.entities.values(), group])
    engine = Engine(lambda: home, executor)
    assert (await engine.process(req("włącz grupa"))).status == "confirm"
    assert (await engine.process(req("dobrze"))).status == "executed"
    assert (await engine.process(req("zgaś ją"))).status == "confirm"
    assert len(executor.calls) == 1
    assert (await engine.process(req("tak"))).status == "executed"


@pytest.mark.parametrize("interruption", ["anuluj", "coś niezrozumiałego", "nie zgaś światła"])
async def test_interruption_clears_reference(engine, executor, interruption):
    await engine.process(req("zapal światła"))
    await engine.process(req(interruption))
    assert (await engine.process(req("zgaś je"))).reason == "no_reference"
    assert len(executor.calls) == 1


async def test_failed_execution_does_not_create_memory(engine, executor):
    executor.deny = True
    assert (await engine.process(req("zapal światła"))).status == "error"
    assert (await engine.process(req("zgaś je"))).reason == "no_reference"


async def test_questions_and_memory_are_bounded(home, executor):
    dialogue = Dialogue(limit=2)
    engine = Engine(lambda: home, executor, dialogue=dialogue)
    for i in range(3):
        await engine.process(req("włącz lampę", conversation_id=f"q{i}"))
        await engine.process(req("zapal światła", conversation_id=f"m{i}"))
    assert len(dialogue.questions) == len(dialogue.memory) == 2
    assert req("", conversation_id="q0").session_key not in dialogue.questions
    assert req("", conversation_id="m0").session_key not in dialogue.memory


async def test_clarification_group_cannot_gain_members(home, executor):
    group = Entity(
        "light.group",
        "Grupa",
        "living_room",
        exposed=True,
        attributes={"entity_id": ["light.living_room_table"]},
    )
    home = type(home)(home.areas.values(), home.floors.values(), [*home.entities.values(), group])
    engine = Engine(lambda: home, executor)
    assert (await engine.process(req("włącz lampę"))).continue_conversation
    home.entities[group.id] = replace(
        group,
        attributes={
            "entity_id": ["light.living_room_table", "light.living_room_ambient"],
        },
    )
    assert (await engine.process(req("grupa"))).reason == "plan_changed"
    assert not executor.calls


async def test_global_alias_conflict_can_be_clarified_by_area(home, executor):
    entities = [
        replace(e, aliases=e.aliases + ("lampka",))
        if e.id
        in {
            "light.living_room_table",
            "light.kitchen_main",
        }
        else e
        for e in home.entities.values()
    ]
    home = type(home)(home.areas.values(), home.floors.values(), entities)
    engine = Engine(lambda: home, executor)
    question = await engine.process(req("włącz światło lampka", source_area=None))
    assert question.continue_conversation
    result = await engine.process(req("w kuchni", source_area=None))
    assert result.status == "executed" and result.targets == ("light.kitchen_main",)


@pytest.fixture
def side_lights(home):
    room = "living_room"
    return (
        Entity("light.left", "Lewy kinkiet", room, exposed=True),
        Entity("light.right", "Kinkiet prawy", room, exposed=True),
        Entity("light.other_room", "Kinkiet prawy", "kitchen", exposed=True),
        Entity("light.extra", "Prawy kinkiet dla gości", room, exposed=True),
    )


def home_with_lights(home, *lights):
    return HomeModel(
        home.areas.values(), home.floors.values(), [*home.entities.values(), *lights],
        satellites=home.satellites, devices=home.devices,
    )


@pytest.mark.parametrize(
    "reply", ["Prawy też, poproszę.", "A ten prawy również.", "Prawy tak samo."],
)
async def test_light_side_repeats_only_the_opposite_matching_lamp(
    home, side_lights, executor, reply,
):
    home = home_with_lights(home, *side_lights)
    engine = Engine(lambda: home, executor)
    assert (await engine.process(req("Zapal lewy kinkiet"))).completed == ("light.left",)
    result = await engine.process(req(reply))
    assert result.completed == ("light.right",) and not result.continue_conversation
    assert result.response_data["interpretation"]["target_source"] == "dialogue"
    assert (await engine.process(req("Zgaś go"))).completed == ("light.right",)
    assert [call.entity_ids for call in executor.calls] == [
        ("light.left",), ("light.right",), ("light.right",),
    ]


async def test_light_side_repeats_brightness_with_explicit_remote_room(home, side_lights, executor):
    modes = {"supported_color_modes": ["brightness"]}
    lights = [replace(light, attributes=modes) for light in side_lights]
    engine = Engine(lambda: home_with_lights(home, *lights), executor)
    first = await engine.process(req(
        "Ustaw lewy kinkiet na 40 procent w salonie", source_area="kitchen",
    ))
    assert first.completed == ("light.left",)
    second = await engine.process(req("Prawy też", source_area="kitchen"))
    assert second.completed == ("light.right",)
    assert second.area_ids == ("living_room",) and second.calls[0].data == {"brightness_pct": 40}


@pytest.mark.parametrize("group_accessible", [True, False])
async def test_light_side_uses_only_an_eligible_group_to_break_collision(
    home, side_lights, executor, group_accessible,
):
    duplicate = replace(side_lights[-1], aliases=("Prawy kinkiet",))
    group = Entity(
        "light.pair", "Para kinkietów", "living_room", exposed=group_accessible,
        attributes={"entity_id": ("light.left", "light.right")},
    )
    home = home_with_lights(home, *side_lights[:-1], duplicate, group)
    engine = Engine(lambda: home, executor)
    await engine.process(req("Zapal lewy kinkiet"))
    result = await engine.process(req("Prawy też"))
    if group_accessible:
        assert result.completed == ("light.right",)
    else:
        assert result.reason == "ambiguous_target" and result.continue_conversation
        options = result.response_data["clarification_options"]
        assert {option["entity_id"] for option in options} == {"light.right", "light.extra"}
        assert len(executor.calls) == 1
        assert (await engine.process(req("Prawy kinkiet dla gości"))).completed == ("light.extra",)
    assert len(executor.calls) == 2


async def test_light_side_revalidates_new_peer_collision_after_asking_for_consent(
    home, side_lights, executor,
):
    original = home
    right = replace(side_lights[1], labels=frozenset({"voice_confirm"}))
    lights = (side_lights[0], right, *side_lights[2:])
    home = home_with_lights(original, *lights)
    engine = Engine(lambda: home, executor)
    await engine.process(req("Zapal lewy kinkiet"))
    question = await engine.process(req("Prawy też"))
    assert question.status == "confirm" and question.targets == (right.id,)
    duplicate = replace(lights[-1], aliases=("prawy kinkiet",))
    home = home_with_lights(original, *lights[:-1], duplicate)
    result = await engine.process(req("Tak"))
    assert result.reason == "plan_changed" and len(executor.calls) == 1


@pytest.mark.parametrize(("reply", "source", "elapsed"), [
    ("Prawy też", "inny", 0),
    ("Prawy też", "salon", 61),
    ("Prawy też да", "salon", 0),
    ("Prawy też, ale jutro", "salon", 0),
    ("Prawy też i zamknij rolety", "salon", 0),
])
async def test_light_side_requires_fresh_whole_source_bound_context(
    home, side_lights, executor, reply, source, elapsed,
):
    now = 0
    home = home_with_lights(home, *side_lights)
    engine = Engine(lambda: home, executor, clock=lambda: now)
    await engine.process(req("Zapal lewy kinkiet", satellite_id="salon"))
    now = elapsed
    result = await engine.process(req(reply, satellite_id=source))
    assert not result.calls and len(executor.calls) == 1


@pytest.mark.parametrize("changes", [{"exposed": False}, {"available": False}])
async def test_ineligible_light_side_never_falls_back_to_unrelated_light(
    home, side_lights, executor, changes,
):
    right = replace(side_lights[1], **changes)
    home = home_with_lights(home, side_lights[0], right, *side_lights[2:])
    engine = Engine(lambda: home, executor)
    assert (await engine.process(req("Zapal lewy kinkiet"))).completed == ("light.left",)
    result = await engine.process(req("Prawy też"))
    assert result.status == "clarify"
    assert not result.calls and len(executor.calls) == 1


async def test_side_reply_preserves_ambiguous_names_even_when_one_is_hidden(
    home, side_lights, executor,
):
    hidden = replace(side_lights[-1], aliases=("prawy kinkiet",), exposed=False)
    home = home_with_lights(home, *side_lights[:-1], hidden)
    engine = Engine(lambda: home, executor)
    await engine.process(req("Zapal lewy kinkiet"))
    result = await engine.process(req("Prawy też"))
    assert result.status == "clarify" and result.continue_conversation
    assert result.reason == "ambiguous_target" and len(executor.calls) == 1
    assert "light.extra" not in str(result)


@pytest.mark.parametrize(("left", "right", "first", "reply", "wrong"), [
    ("Lewy kinkiet", "Prawy kinkiet", "Zapal lewy kinkiet", "Prawy też", "Prawa też"),
    ("Lewa lampka", "Prawa lampka", "Zapal lewą lampkę", "Prawa też", "Prawy też"),
    ("Lewa lampka", "Prawa lampka", "Zapal lewą lampkę", "Prawą też", "Prawe też"),
    ("Lewe światło", "Prawe światło", "Zapal lewe światło", "Prawe też", "Prawa też"),
])
async def test_paired_light_followup_agrees_with_the_light_name(
    home, executor, left, right, first, reply, wrong,
):
    home = home_with_lights(
        home,
        Entity("light.left", left, "living_room", exposed=True),
        Entity("light.right", right, "living_room", exposed=True),
    )
    engine = Engine(lambda: home, executor)
    assert (await engine.process(req(first))).completed == ("light.left",)
    assert (await engine.process(req(wrong))).reason == "no_reference"
    assert len(executor.calls) == 1
    await engine.process(req(first))
    assert (await engine.process(req(reply))).completed == ("light.right",)
    assert len(executor.calls) == 3
