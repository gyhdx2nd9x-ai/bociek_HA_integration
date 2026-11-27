"""A missing light reference is a question, never permission to use defaults."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.context.dialogue import Dialogue
from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import Entity, HomeModel
from custom_components.polish_context_voice.core.types import Request

TABLE = "light.living_room_table"
MAIN = "light.living_room_main"


def req(text, **kwargs):
    return Request(
        text, **({"source_area": "living_room", "conversation_id": "c", "user_id": "u"} | kwargs)
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
def reference_home(home):
    return with_entities(
        home,
        [
            replace(entity, aliases=(*entity.aliases, "nad stołem"))
            if entity.id == TABLE
            else entity
            for entity in home.entities.values()
        ],
    )


@pytest.mark.parametrize(
    "command,service,data",
    [
        ("wyłącz je", "turn_off", {}),
        ("włącz je", "turn_on", {}),
        ("ustaw je na 40%", "turn_on", {"brightness_pct": 40}),
    ],
)
async def test_orphan_asks_then_exact_alias_completes_same_operation(
    reference_home, executor, command, service, data
):
    engine = Engine(lambda: reference_home, executor)
    question = await engine.process(req(command))
    assert question.reason == "no_reference" and question.continue_conversation
    assert question.targets == question.calls == ()
    assert not executor.calls
    answer = await engine.process(req("nad stołem"))
    assert answer.status == "executed" and answer.targets == (TABLE,)
    assert answer.calls[0].service == service and answer.calls[0].data == data
    assert len(executor.calls) == len(executor.checked) == 1
    assert req("").session_key not in engine.dialogue.questions
    # The newly resolved named set becomes ordinary short-lived light memory.
    assert (await engine.process(req("wyłącz je"))).targets == (TABLE,)


async def test_even_one_eligible_light_requires_a_name(home, executor):
    home = with_entities(home, [home.entities[TABLE]])
    engine = Engine(lambda: home, executor)
    question = await engine.process(req("wyłącz je"))
    assert question.continue_conversation and question.targets == ()
    assert not executor.calls
    assert (await engine.process(req("lampka stołowa"))).targets == (TABLE,)


@pytest.mark.parametrize(
    "first,expected",
    [
        ("zapal światła", (MAIN,)),
        ("włącz lampkę stołową", (TABLE,)),
        ("włącz światła w kuchni", ("light.kitchen_main",)),
    ],
)
async def test_valid_memory_has_priority_and_never_expands_to_generic_off(
    engine, executor, first, expected
):
    assert (await engine.process(req(first))).targets == expected
    followup = await engine.process(req("wyłącz je"))
    assert followup.status == "executed" and followup.targets == expected
    assert not followup.continue_conversation
    assert (await engine.process(req("wyłącz światła"))).targets == (
        "light.living_room_ambient",
        MAIN,
        TABLE,
    )


async def test_explicit_room_overrides_satellite_for_missing_reference(reference_home, executor):
    engine = Engine(lambda: reference_home, executor)
    assert (await engine.process(req("wyłącz je w kuchni"))).continue_conversation
    wrong = await engine.process(req("nad stołem"))
    assert wrong.continue_conversation and not executor.calls
    answer = await engine.process(req("lampa sufitowa"))
    assert answer.status == "executed" and answer.targets == ("light.kitchen_main",)


async def test_no_source_area_accepts_exact_global_alias(reference_home, executor):
    engine = Engine(lambda: reference_home, executor)
    assert (await engine.process(req("wyłącz je", source_area=None))).continue_conversation
    answer = await engine.process(req("nad stołem", source_area=None))
    assert answer.status == "executed" and answer.targets == (TABLE,)


@pytest.mark.parametrize("text", ["wyłącz je w piwnicy", "otwórz je", "zamknij je"])
async def test_unknown_scope_or_unsupported_reference_action_never_creates_target_draft(
    engine, executor, text
):
    answer = await engine.process(req(text))
    assert answer.status == "clarify" and answer.reason == "no_reference"
    assert not answer.continue_conversation
    assert not engine.dialogue.questions and not executor.calls


@pytest.mark.parametrize(
    "reply",
    [
        "tak",
        "potwierdzam",
        "to nad stołem",
        "nad stołem proszę",
        "nad stołem i taśma led",
        "nie nad stołem",
        "nad stołem да",
        "да nad stołem",
        "wyłącz je",
        "wyłącz je да",
        "włącz lampkę stołową да",
        "coś niezrozumiałego",
    ],
)
async def test_unresolved_reply_never_executes_or_extends_ttl(reference_home, executor, reply):
    now = [0]
    engine = Engine(lambda: reference_home, executor, clock=lambda: now[0])
    await engine.process(req("wyłącz je"))
    key = req("").session_key
    deadline = engine.dialogue.questions[key].expires_at
    now[0] = 20
    answer = await engine.process(req(reply))
    assert answer.reason == "clarification_unresolved" and answer.continue_conversation
    assert engine.dialogue.questions[key].expires_at == deadline == 30
    assert answer.calls == answer.targets == () and not executor.calls
    now[0] = deadline
    assert (await engine.process(req("nad stołem"))).status != "executed"
    assert not executor.calls


@pytest.mark.parametrize("text", ["wyłącz je да", "да wyłącz je"])
async def test_foreign_words_cannot_be_deleted_to_authorize_existing_reference(
    engine, executor, text
):
    await engine.process(req("zapal światła"))
    assert (await engine.process(req(text))).status == "clarify"
    assert len(executor.calls) == 1


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
async def test_missing_reference_does_not_cross_any_session_boundary(
    reference_home, executor, change
):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je"))
    assert (await engine.process(req("nad stołem", **change))).status != "executed"
    assert not executor.calls
    assert (await engine.process(req("nad stołem"))).status == "executed"


@pytest.mark.parametrize("rejection", ["nie", "anuluj", "rezygnuję"])
async def test_rejection_clears_missing_reference(reference_home, executor, rejection):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je"))
    assert (await engine.process(req(rejection))).status == "cancelled"
    assert (await engine.process(req("nad stołem"))).status != "executed"
    assert not executor.calls


async def test_new_complete_command_discards_missing_reference(reference_home, executor):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je"))
    assert (await engine.process(req("włącz światła w kuchni"))).status == "executed"
    assert not engine.dialogue.questions
    assert (await engine.process(req("nad stołem"))).status != "executed"
    assert len(executor.calls) == 1


async def test_uncertain_clarification_never_uses_fallback(reference_home, executor):
    class Fallback:
        async def propose(self, *_args):
            pytest.fail("A missing target answer must not become an LLM action")

    engine = Engine(lambda: reference_home, executor, fallback=Fallback())
    await engine.process(req("wyłącz je"))
    assert (await engine.process(req("coś zupełnie innego"))).continue_conversation
    assert not executor.calls


@pytest.mark.parametrize(
    "updates",
    [
        {"exposed": False},
        {"available": False},
        {"area_id": "kitchen"},
        {"labels": frozenset({"voice_block"})},
        {"labels": frozenset({"voice_confirm"})},
        {"labels": frozenset({"voice_default_on"})},
        {"aliases": ("nowa nazwa",)},
        {"attributes": {"supported_color_modes": ["onoff"]}},
    ],
)
async def test_selected_target_metadata_is_rechecked_before_write(
    reference_home, executor, updates
):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je"))
    reference_home.entities[TABLE] = replace(reference_home.entities[TABLE], **updates)
    answer = await engine.process(req("nad stołem"))
    assert answer.reason == "plan_changed" and not answer.continue_conversation
    assert not executor.calls


async def test_state_and_unrelated_entity_changes_do_not_invalidate_target(
    reference_home, executor
):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je"))
    reference_home.entities[TABLE] = replace(reference_home.entities[TABLE], state="off")
    reference_home.entities[MAIN] = replace(reference_home.entities[MAIN], available=False)
    assert (await engine.process(req("nad stołem"))).status == "executed"


async def test_satellite_area_change_cannot_redirect_question(reference_home, executor):
    engine = Engine(lambda: reference_home, executor)
    kwargs = {"source_area": None, "satellite_id": "assist_satellite.living_room"}
    await engine.process(req("wyłącz je", **kwargs))
    reference_home.satellites[kwargs["satellite_id"]] = "kitchen"
    answer = await engine.process(req("nad stołem", **kwargs))
    assert answer.reason == "plan_changed" and not executor.calls


async def test_initial_candidate_set_cannot_admit_new_alias(reference_home, executor):
    homes = [reference_home]
    engine = Engine(lambda: homes[0], executor)
    await engine.process(req("wyłącz je"))
    extra = replace(reference_home.entities[TABLE], id="light.extra", name="Nowe", aliases=())
    homes[0] = with_entities(reference_home, [*reference_home.entities.values(), extra])
    assert (await engine.process(req("Nowe"))).continue_conversation
    assert not executor.calls
    assert (await engine.process(req("nad stołem"))).targets == (TABLE,)


async def test_new_alias_collision_cannot_silently_pick_old_candidate(reference_home, executor):
    homes = [reference_home]
    engine = Engine(lambda: homes[0], executor)
    await engine.process(req("wyłącz je"))
    extra = replace(reference_home.entities[TABLE], id="light.extra", name="Nowe")
    homes[0] = with_entities(reference_home, [*reference_home.entities.values(), extra])
    answer = await engine.process(req("nad stołem"))
    assert answer.reason == "plan_changed" and not executor.calls


async def test_shared_alias_is_a_choice_never_aggregate(reference_home, executor):
    home = with_entities(
        reference_home,
        [
            replace(entity, aliases=(*entity.aliases, "nad stołem"))
            if entity.id == MAIN
            else entity
            for entity in reference_home.entities.values()
        ],
    )
    engine = Engine(lambda: home, executor)
    await engine.process(req("wyłącz je"))
    answer = await engine.process(req("nad stołem"))
    assert answer.continue_conversation and answer.targets == answer.calls == ()
    assert {option["entity_id"] for option in answer.response_data["clarification_options"]} == {
        MAIN,
        TABLE,
    }
    assert not executor.calls
    assert (await engine.process(req("Main Lights"))).targets == (MAIN,)


async def test_changed_shared_alias_choice_is_not_advertised(reference_home, executor):
    home = with_entities(
        reference_home,
        [
            replace(entity, aliases=(*entity.aliases, "nad stołem"))
            if entity.id == MAIN
            else entity
            for entity in reference_home.entities.values()
        ],
    )
    engine = Engine(lambda: home, executor)
    await engine.process(req("wyłącz je"))
    home.entities[MAIN] = replace(home.entities[MAIN], exposed=False)
    answer = await engine.process(req("nad stołem"))
    assert answer.reason == "plan_changed" and not answer.continue_conversation
    assert not answer.response_data and not executor.calls


def group_home(home, **kwargs):
    group = Entity(
        "light.group",
        "Nad stołem",
        "living_room",
        exposed=True,
        attributes={"entity_id": [TABLE, MAIN]},
        **kwargs,
    )
    return with_entities(home, [*home.entities.values(), group])


async def test_group_selection_expands_only_original_members_and_keeps_memory(home, executor):
    home = group_home(home)
    engine = Engine(lambda: home, executor)
    await engine.process(req("wyłącz je"))
    answer = await engine.process(req("nad stołem"))
    assert answer.status == "executed" and answer.targets == (MAIN, TABLE)
    assert (await engine.process(req("włącz je"))).targets == (MAIN, TABLE)


@pytest.mark.parametrize("change", ["members", "member_exposure", "member_role", "root_role"])
async def test_group_closure_cannot_change_between_question_and_answer(home, executor, change):
    home = group_home(home)
    engine = Engine(lambda: home, executor)
    await engine.process(req("wyłącz je"))
    if change == "members":
        group = home.entities["light.group"]
        home.entities[group.id] = replace(
            group, attributes={"entity_id": [*group.members, "light.living_room_ambient"]}
        )
    else:
        identifier = "light.group" if change == "root_role" else TABLE
        updates = {"exposed": False} if change == "member_exposure" else {"labels": frozenset()}
        home.entities[identifier] = replace(home.entities[identifier], **updates)
        if change == "root_role":
            home.entities[identifier] = replace(
                home.entities[identifier], labels=frozenset({"voice_task"})
            )
    assert (await engine.process(req("nad stołem"))).reason == "plan_changed"
    assert not executor.calls


async def test_group_confirmation_and_preflight_remain_required(home, executor):
    home = group_home(home, labels=frozenset({"voice_confirm"}))
    engine = Engine(lambda: home, executor)
    await engine.process(req("wyłącz je"))
    assert (await engine.process(req("nad stołem"))).status == "confirm"
    assert not executor.calls
    assert (await engine.process(req("tak"))).status == "executed"
    assert len(executor.calls) == len(executor.checked) == 2


async def test_permission_preflight_can_reject_after_answer(reference_home, executor):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je"))
    executor.deny = True
    assert (await engine.process(req("nad stołem"))).status == "error"
    assert not executor.calls and not engine.dialogue.memory


async def test_preview_reference_is_isolated_and_never_writes(reference_home, executor):
    engine = Engine(lambda: reference_home, executor)
    await engine.process(req("wyłącz je", dry_run=True))
    answer = await engine.process(req("nad stołem", dry_run=True))
    assert answer.status == "dry_run" and answer.targets == (TABLE,)
    assert not executor.calls
    assert (await engine.process(req("wyłącz je"))).reason == "no_reference"


async def test_missing_reference_drafts_are_bounded(reference_home, executor):
    engine = Engine(lambda: reference_home, executor, dialogue=Dialogue(limit=2))
    for index in range(3):
        await engine.process(req("wyłącz je", conversation_id=str(index)))
    assert len(engine.dialogue.questions) == 2
    assert req("", conversation_id="0").session_key not in engine.dialogue.questions
    assert not executor.calls


async def test_large_candidate_set_requires_full_command_instead_of_partial_snapshot(
    home, executor
):
    home = with_entities(
        home,
        [replace(home.entities[TABLE], id=f"light.test_{index}") for index in range(65)],
    )
    engine = Engine(lambda: home, executor)
    answer = await engine.process(req("wyłącz je"))
    assert answer.reason == "no_reference" and not answer.continue_conversation
    assert not engine.dialogue.questions and not executor.calls
