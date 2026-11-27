"""Missing main lighting may offer a category, never silently broaden a command."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request

TABLE = "light.living_room_table"
AMBIENT = "light.living_room_ambient"
MAIN = "light.living_room_main"


def req(text, **changes):
    return Request(
        text,
        **({"source_area": "living_room", "conversation_id": "category", "user_id": "u"} | changes),
    )


def with_entities(home, entities):
    return HomeModel(
        home.areas.values(),
        home.floors.values(),
        entities,
        satellites=home.satellites,
        devices=home.devices,
    )


@pytest.fixture
def task_home(home):
    """One living-room role, with no configured default (including hidden roots)."""
    return with_entities(
        home,
        [
            entity
            for entity in home.entities.values()
            if entity.area_id != "living_room" or entity.domain != "light" or entity.id == TABLE
        ],
    )


@pytest.fixture
def categories_home(task_home, home):
    return with_entities(task_home, [*task_home.entities.values(), home.entities[AMBIENT]])


async def ask(engine, **changes):
    result = await engine.process(req("włącz światła", **changes))
    assert result.status == "clarify" and result.reason == "missing_default_light"
    assert result.continue_conversation
    assert result.targets == result.calls == () and not result.completed
    return result


@pytest.mark.parametrize(
    "reply",
    ["tak", "Potwierdzam", "dobrze", "OK", "okej", "zgoda", "tak proszę", "Tak. Tak"],
)
async def test_single_category_requires_and_accepts_whole_affirmative(task_home, executor, reply):
    engine = Engine(lambda: task_home, executor)
    question = await ask(engine)
    assert question.response_data["light_categories"] == ["voice_task"]
    assert "Włączyć światła robocze?" in question.speech
    assert not executor.calls and not executor.checked
    answer = await engine.process(req(reply))
    assert answer.status == "executed" and answer.targets == (TABLE,)
    assert len(answer.calls) == len(executor.calls) == len(executor.checked) == 1
    assert answer.calls[0].service == "turn_on" and answer.calls[0].data == {}
    assert req("").session_key not in engine.dialogue.questions


@pytest.mark.parametrize("reply", ["ozdobne", "dekoracyjne", "nastrojowe", "światła ozdobne"])
async def test_named_decorative_synonyms_select_only_the_offered_role(
    categories_home, executor, reply
):
    engine = Engine(lambda: categories_home, executor)
    question = await ask(engine)
    assert set(question.response_data["light_categories"]) == {"voice_task", "voice_ambient"}
    assert "Które włączyć: robocze czy dekoracyjne?" in question.speech
    answer = await engine.process(req(reply))
    assert answer.status == "executed" and answer.targets == (AMBIENT,)
    assert [call.entity_ids for call in executor.calls] == [(AMBIENT,)]


async def test_named_work_category_does_not_include_decorative(categories_home, executor):
    engine = Engine(lambda: categories_home, executor)
    await ask(engine)
    answer = await engine.process(req("robocze"))
    assert answer.status == "executed" and answer.targets == (TABLE,)
    assert len(executor.calls) == 1


@pytest.mark.parametrize("reply", ["tak", "Tak. Tak", "potwierdzam", "dobrze"])
async def test_two_categories_cannot_be_chosen_with_generic_assent(
    categories_home, executor, reply
):
    engine = Engine(lambda: categories_home, executor)
    await ask(engine)
    answer = await engine.process(req(reply))
    assert answer.continue_conversation and answer.reason == "clarification_unresolved"
    assert answer.calls == answer.targets == () and not executor.calls
    assert (await engine.process(req("ozdobne"))).targets == (AMBIENT,)


@pytest.mark.parametrize(
    "reply",
    [
        "nieznana nazwa",
        "robocze i dekoracyjne",
        "nie robocze",
        "robocze proszę",
        "tak да",
        "да tak",
        "Так",
        "wyłącz je",
    ],
)
async def test_unresolved_category_reply_keeps_original_deadline(task_home, executor, reply):
    now = [0]
    engine = Engine(lambda: task_home, executor, clock=lambda: now[0])
    await ask(engine)
    key = req("").session_key
    deadline = engine.dialogue.questions[key].expires_at
    now[0] = 29
    answer = await engine.process(req(reply))
    assert answer.continue_conversation and answer.reason == "clarification_unresolved"
    assert engine.dialogue.questions[key].expires_at == deadline == 30
    assert not answer.calls and not executor.calls
    now[0] = deadline
    expired = await engine.process(req("tak"))
    assert not expired.continue_conversation and expired.status != "executed"
    assert not executor.calls and not engine.dialogue.questions


async def test_ambiguous_assent_does_not_extend_category_choice_ttl(categories_home, executor):
    now = [0]
    engine = Engine(lambda: categories_home, executor, clock=lambda: now[0])
    await ask(engine)
    now[0] = 29
    assert (await engine.process(req("tak"))).continue_conversation
    assert engine.dialogue.questions[req("").session_key].expires_at == 30
    now[0] = 30
    assert (await engine.process(req("ozdobne"))).status != "executed"
    assert not executor.calls


@pytest.mark.parametrize("reply", ["nie", "anuluj", "rezygnuję"])
async def test_rejection_discards_category_offer(task_home, executor, reply):
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    assert (await engine.process(req(reply))).status == "cancelled"
    assert not engine.dialogue.questions
    assert (await engine.process(req("tak"))).status != "executed"
    assert not executor.calls


async def test_complete_new_command_replaces_category_question(task_home, executor):
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    answer = await engine.process(req("włącz światła w kuchni"))
    assert answer.status == "executed" and answer.targets == ("light.kitchen_main",)
    assert not engine.dialogue.questions
    assert (await engine.process(req("tak"))).status != "executed"
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "main_changes",
    [
        {"available": False},
        {"exposed": False},
        {"labels": frozenset({"voice_default_on", "voice_block"})},
        {"labels": frozenset({"voice_default_on", "voice_exclude_all"})},
    ],
)
async def test_configured_default_is_not_replaced_when_hidden_unavailable_or_blocked(
    task_home, home, executor, main_changes
):
    task_home = with_entities(
        task_home, [*task_home.entities.values(), replace(home.entities[MAIN], **main_changes)]
    )
    engine = Engine(lambda: task_home, executor)
    answer = await engine.process(req("włącz światła"))
    assert answer.status != "executed" and answer.reason != "missing_default_light"
    assert not answer.continue_conversation and not engine.dialogue.questions
    assert not executor.calls


async def test_valid_default_still_uses_ordinary_fast_path(home, executor):
    engine = Engine(lambda: home, executor)
    result = await engine.process(req("włącz światła"))
    assert result.status == "executed" and result.targets == (MAIN,)
    assert not engine.dialogue.questions


@pytest.mark.parametrize(
    "changes",
    [
        {"exposed": False},
        {"available": False},
        {"labels": frozenset({"voice_task", "voice_block"})},
    ],
)
async def test_unavailable_or_ineligible_category_is_not_offered(task_home, executor, changes):
    task_home.entities[TABLE] = replace(task_home.entities[TABLE], **changes)
    engine = Engine(lambda: task_home, executor)
    answer = await engine.process(req("włącz światła"))
    assert answer.status != "executed" and not answer.continue_conversation
    assert not engine.dialogue.questions and not executor.calls


async def test_generic_off_never_bypasses_all_off_labels(task_home, executor):
    task_home.entities[TABLE] = replace(task_home.entities[TABLE], labels=frozenset({"voice_task"}))
    engine = Engine(lambda: task_home, executor)
    result = await engine.process(req("wyłącz światła"))
    assert result.reason == "no_targets" and not result.continue_conversation
    assert "wspólnego wyłączania" in result.speech
    assert not engine.dialogue.questions and not executor.calls
    # Naming the role remains an intentional, distinct command.
    assert (await engine.process(req("wyłącz światła robocze"))).targets == (TABLE,)


@pytest.mark.parametrize("utterance", ["wyłącz światła", "zgaś światła"])
async def test_generic_off_uses_all_room_lights_when_no_semantic_light_roles_exist(
    task_home, executor, utterance
):
    from custom_components.polish_context_voice.core.home_model import Entity

    task_home.entities[TABLE] = replace(task_home.entities[TABLE], labels=frozenset())
    second = replace(task_home.entities[TABLE], id="light.living_room_second", name="Second light")
    excluded = Entity(
        "light.living_room_excluded",
        "Excluded light",
        "living_room",
        labels=frozenset({"voice_exclude_all"}),
        exposed=True,
    )
    task_home.entities[second.id] = second
    task_home.entities[excluded.id] = excluded
    engine = Engine(lambda: task_home, executor)

    result = await engine.process(req(utterance))

    assert result.status == "executed"
    assert result.targets == (second.id, TABLE)
    assert all(call.service == "turn_off" for call in executor.calls)
    assert excluded.id not in result.targets


async def test_unlabelled_off_fallback_stays_in_explicit_area(task_home, executor):
    from custom_components.polish_context_voice.core.home_model import Entity

    task_home.entities[TABLE] = replace(task_home.entities[TABLE], labels=frozenset())
    kitchen = replace(
        task_home.entities["light.kitchen_main"], labels=frozenset(), exposed=True
    )
    task_home.entities[kitchen.id] = kitchen
    task_home.entities["light.kitchen_second"] = Entity(
        "light.kitchen_second", "Kitchen second light", "kitchen", exposed=True
    )
    engine = Engine(lambda: task_home, executor)

    result = await engine.process(req("wyłącz światła w kuchni", source_area="living_room"))

    assert result.status == "executed"
    assert result.area_ids == ("kitchen",)
    assert result.targets == (kitchen.id, "light.kitchen_second")
    assert all("living_room" not in call.entity_ids for call in executor.calls)


async def test_explicit_kitchen_has_priority_over_living_satellite(task_home, executor):
    kitchen = replace(task_home.entities["light.kitchen_main"], labels=frozenset({"voice_ambient"}))
    task_home.entities[kitchen.id] = kitchen
    engine = Engine(lambda: task_home, executor)
    question = await engine.process(req("włącz światła w kuchni"))
    assert question.continue_conversation and question.reason == "missing_default_light"
    assert question.response_data["light_categories"] == ["voice_ambient"]
    result = await engine.process(req("tak"))
    assert result.status == "executed" and result.targets == (kitchen.id,)
    assert result.area_ids == ("kitchen",)
    assert (await engine.process(req("wyłącz je"))).targets == (kitchen.id,)


async def test_missing_area_asks_for_room_before_category(task_home, executor):
    engine = Engine(lambda: task_home, executor)
    result = await engine.process(req("włącz światła", source_area=None))
    assert result.reason == "missing_area" and result.continue_conversation
    assert result.speech == "W którym pomieszczeniu?"
    assert not engine.dialogue.questions[req("", source_area=None).session_key].missing_category
    assert not executor.calls


async def test_multiple_rooms_cannot_create_one_category_assent(task_home, executor):
    task_home.entities["light.kitchen_main"] = replace(
        task_home.entities["light.kitchen_main"], labels=frozenset({"voice_ambient"})
    )
    engine = Engine(lambda: task_home, executor)
    result = await engine.process(req("włącz światła na parterze"))
    assert result.status == "clarify" and result.reason == "missing_default_light"
    assert not result.continue_conversation and "jedno pomieszczenie" in result.speech
    assert not engine.dialogue.questions and not executor.calls


@pytest.mark.parametrize(
    "changes",
    [
        {"exposed": False},
        {"available": False},
        {"area_id": "kitchen"},
        {"labels": frozenset()},
        {"labels": frozenset({"voice_task", "voice_confirm"})},
        {"labels": frozenset({"voice_task", "voice_block"})},
        {"aliases": ("zmieniona nazwa",)},
    ],
)
async def test_category_selection_revalidates_original_roots(task_home, executor, changes):
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    task_home.entities[TABLE] = replace(task_home.entities[TABLE], **changes)
    result = await engine.process(req("tak"))
    assert result.reason == "plan_changed" and not result.continue_conversation
    assert not result.calls and not executor.calls


@pytest.mark.parametrize("new_role", ["voice_task", "voice_default_on"])
async def test_new_category_member_or_default_invalidates_saved_choice(
    task_home, executor, new_role
):
    models = [task_home]
    engine = Engine(lambda: models[0], executor)
    await ask(engine)
    added = replace(task_home.entities[TABLE], id="light.new", labels=frozenset({new_role}))
    models[0] = with_entities(task_home, [*task_home.entities.values(), added])
    result = await engine.process(req("tak"))
    assert result.reason == "plan_changed" and not result.calls and not executor.calls


async def test_satellite_area_mapping_change_cannot_redirect_saved_category(task_home, executor):
    engine = Engine(lambda: task_home, executor)
    source = {"source_area": None, "satellite_id": "assist_satellite.living_room"}
    await ask(engine, **source)
    task_home.satellites[source["satellite_id"]] = "kitchen"
    answer = await engine.process(req("tak", **source))
    assert answer.reason == "plan_changed" and not executor.calls


def task_group_home(home):
    leaves = [
        replace(home.entities[TABLE], id="light.work_one", labels=frozenset()),
        replace(home.entities[TABLE], id="light.work_two", labels=frozenset()),
    ]
    group = Entity(
        "light.work_group",
        "Roboczy zestaw",
        "living_room",
        labels=frozenset({"voice_task"}),
        exposed=True,
        attributes={"entity_id": [leaf.id for leaf in leaves]},
    )
    rest = [e for e in home.entities.values() if e.area_id != "living_room" or e.domain != "light"]
    return with_entities(home, [*rest, *leaves, group])


async def test_category_group_expands_and_recent_pronoun_retains_exact_set(home, executor):
    grouped = task_group_home(home)
    engine = Engine(lambda: grouped, executor)
    await ask(engine)
    answer = await engine.process(req("tak"))
    assert answer.status == "executed" and answer.targets == ("light.work_one", "light.work_two")
    followup = await engine.process(req("wyłącz je"))
    assert followup.status == "executed" and followup.targets == answer.targets
    assert all(call.service == "turn_off" for call in followup.calls)
    assert len(executor.calls) == 4


@pytest.mark.parametrize("change", ["members", "member_exposure", "member_role"])
async def test_group_closure_changes_invalidate_category_offer(home, executor, change):
    grouped = task_group_home(home)
    engine = Engine(lambda: grouped, executor)
    await ask(engine)
    if change == "members":
        grouped.entities["light.work_group"] = replace(
            grouped.entities["light.work_group"], attributes={"entity_id": ["light.work_one"]}
        )
    else:
        updates = (
            {"exposed": False}
            if change == "member_exposure"
            else {"labels": frozenset({"voice_confirm"})}
        )
        grouped.entities["light.work_one"] = replace(grouped.entities["light.work_one"], **updates)
    answer = await engine.process(req("tak"))
    assert answer.reason == "plan_changed" and not executor.calls


async def test_state_change_alone_does_not_invalidate_category(task_home, executor):
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    task_home.entities[TABLE] = replace(task_home.entities[TABLE], state="off")
    answer = await engine.process(req("tak"))
    assert answer.status == "executed" and answer.targets == (TABLE,)


async def test_fresh_permission_preflight_can_reject_confirmed_category(task_home, executor):
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    executor.deny = True
    answer = await engine.process(req("tak"))
    assert answer.status == "error" and len(executor.checked) == 1 and not executor.calls


@pytest.mark.parametrize(
    "boundary",
    [
        {"user_id": "other"},
        {"conversation_id": "other"},
        {"satellite_id": "other"},
        {"device_id": "other"},
        {"source_area": "kitchen"},
        {"dry_run": True},
    ],
)
async def test_category_consent_cannot_cross_session_boundary(task_home, executor, boundary):
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    other = await engine.process(req("tak", **boundary))
    assert other.status != "executed" and not other.calls and not executor.calls
    assert (await engine.process(req("tak"))).status == "executed"
    assert len(executor.calls) == 1


async def test_preview_and_live_category_sessions_have_separate_consent(task_home, executor):
    engine = Engine(lambda: task_home, executor)
    await ask(engine, dry_run=True)
    assert (await engine.process(req("tak"))).status != "executed"
    preview = await engine.process(req("tak", dry_run=True))
    assert preview.status == "dry_run" and preview.targets == (TABLE,) and not preview.completed
    assert not executor.checked and not executor.calls


async def test_unknown_category_answer_does_not_invoke_llm(task_home, executor):
    class Fallback:
        async def propose(self, *_args):
            pytest.fail("An offered category cannot be replaced by an LLM interpretation")

    engine = Engine(lambda: task_home, executor, fallback=Fallback())
    await ask(engine)
    assert (await engine.process(req("nieznana nazwa"))).continue_conversation
    assert not executor.calls


async def test_category_assent_does_not_replace_separate_voice_confirm(task_home, executor):
    task_home.entities[TABLE] = replace(
        task_home.entities[TABLE], labels=frozenset({"voice_task", "voice_confirm"})
    )
    engine = Engine(lambda: task_home, executor)
    await ask(engine)
    first_yes = await engine.process(req("tak"))
    assert first_yes.status == "confirm" and first_yes.reason == "confirmation_required"
    assert not executor.calls and not executor.checked
    second_yes = await engine.process(req("potwierdzam"))
    assert second_yes.status == "executed" and second_yes.targets == (TABLE,)
    assert len(executor.calls) == 1


async def test_large_category_is_not_silently_truncated(task_home, executor):
    entities = [e for e in task_home.entities.values() if e.id != TABLE]
    entities.extend(replace(task_home.entities[TABLE], id=f"light.work_{n}") for n in range(65))
    task_home = with_entities(task_home, entities)
    engine = Engine(lambda: task_home, executor)
    answer = await engine.process(req("włącz światła"))
    assert answer.status != "executed" and not answer.continue_conversation
    assert not engine.dialogue.questions and not executor.calls

@pytest.mark.parametrize(("text", "expected", "service"), [
    ("Zapal coś do czytania", TABLE, "turn_on"),
    ("W salonie zapal coś do czytania", TABLE, "turn_on"),
    ("Zapal w salonie coś do czytania", TABLE, "turn_on"),
    ("Potrzebuję światła do czytania w tym pokoju", TABLE, "turn_on"),
    ("Poproszę o światło do pracy", TABLE, "turn_on"),
    ("Włącz światła dla nastroju", AMBIENT, "turn_on"),
    ("Zgaś światło dla nastroju", AMBIENT, "turn_off"),
])
async def test_effect_wording_uses_room_lighting_roles(home, executor, text, expected, service):
    result = await Engine(lambda: home, executor).process(req(text))
    assert result.status == "executed" and result.completed == (expected,)
    assert len(executor.calls) == 1 and executor.calls[0].service == service


async def test_purpose_uses_primary_only_within_matching_role(home, executor):
    table = home.entities[TABLE]
    desk = replace(table, id="light.desk", name="Lampka biurkowa", aliases=())
    home.entities[TABLE] = replace(table, labels=table.labels | {"voice_primary"})
    home.entities[desk.id] = desk
    result = await Engine(lambda: home, executor).process(req("Zapal coś do czytania"))
    assert result.completed == (TABLE,)
    assert not result.continue_conversation


async def test_purpose_single_clarifies_but_plural_uses_whole_role(home, executor):
    desk = Entity(
        "light.desk", "Lampka biurkowa", "living_room",
        labels=frozenset({"voice_task"}), exposed=True,
    )
    home = with_entities(home, [*home.entities.values(), desk])
    engine = Engine(lambda: home, executor)

    first = await engine.process(req("Zapal coś do czytania"))
    assert first.continue_conversation and first.reason == "ambiguous_target"
    options = first.response_data["clarification_options"]
    assert {o["entity_id"] for o in options} == {TABLE, desk.id}
    assert not executor.calls
    assert (await engine.process(req("Lampka biurkowa"))).completed == (desk.id,)

    result = await engine.process(req("Włącz światła do pracy"))
    assert result.completed == (desk.id, TABLE)


@pytest.mark.parametrize("exposed", [True, False])
async def test_purpose_alias_preserves_registry_choice_over_generic_role(home, executor, exposed):
    named = replace(home.entities[MAIN], aliases=("światło do czytania",), exposed=exposed)
    home = with_entities(
        home, [named if entity.id == MAIN else entity for entity in home.entities.values()],
    )
    result = await Engine(lambda: home, executor).process(req("Zapal światło do czytania"))
    if exposed:
        assert result.completed == (MAIN,)
    else:
        assert result.continue_conversation and not executor.calls


async def test_missing_role_asks_for_named_light_without_switching_room(home, executor):
    engine = Engine(lambda: home, executor)
    result = await engine.process(req("Zapal coś do czytania w kuchni"))
    assert result.speech == "Którą lampę włączyć? Podaj jej nazwę."
    assert result.continue_conversation and not executor.calls
    assert not (await engine.process(req("lampka stołowa"))).calls
    result = await engine.process(req(home.entities["light.kitchen_main"].name))
    assert result.completed == ("light.kitchen_main",)


async def test_effect_reuses_its_role_in_other_room_not_previous_lamp(home, executor):
    kitchen = home.entities["light.kitchen_main"]
    home.entities[kitchen.id] = replace(kitchen, labels=kitchen.labels | {"voice_task"})
    engine = Engine(lambda: home, executor)
    assert (await engine.process(req("Zapal coś do czytania"))).completed == (TABLE,)
    repeated = await engine.process(req("W kuchni też"))
    assert repeated.completed == (kitchen.id,)


@pytest.mark.parametrize("text", [
    "Zapal coś do czytania, ale nie teraz",
    "Zapal coś do czytania, jeśli wrócę",
    "Zapal coś do czytania i otwórz rolety",
    "Zapal coś do czytania да",
    "Włącz coś do czytania",
    "Potrzebuję światła do pracy, ktoś idzie",
])
async def test_effect_never_silently_discards_extra_meaning(home, executor, text):
    result = await Engine(lambda: home, executor).process(req(text))
    assert not result.calls and not executor.calls
