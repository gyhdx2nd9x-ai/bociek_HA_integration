"""D02 requires fresh, source-bound consent; no trailing speech is executed."""

from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel
from custom_components.polish_context_voice.core.types import Request

NOISY = "Włącz światła wow jaka figura"
MAIN = "light.living_room_main"
TABLE = "light.living_room_table"
AMBIENT = "light.living_room_ambient"


def request(text=NOISY, **changes):
    return replace(
        Request(
            text,
            source_area="living_room",
            satellite_id="assist_satellite.living_room",
            conversation_id="background-test",
            user_id="test-user",
        ),
        **changes,
    )


def change(home, identifier, **changes):
    return HomeModel(
        areas=home.areas.values(),
        floors=home.floors.values(),
        entities=[
            replace(entity, **changes) if entity.id == identifier else entity
            for entity in home.entities.values()
        ],
        satellites=home.satellites,
        devices=home.devices,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", ["Tak", "Potwierdzam", "Dobrze", "Tak. Tak", "Okej", "Tak proszę"]
)
async def test_noisy_command_requires_whole_confirmation_and_is_single_use(engine, executor, reply):
    result = await engine.process(request())
    assert result.status == "confirm" and result.reason == "background_confirmation"
    assert result.route == "fast" and result.continue_conversation
    assert result.targets == (MAIN,)
    assert [call.entity_ids for call in result.calls] == [(MAIN,)]
    assert result.completed == () and executor.calls == [] and executor.checked == []
    assert result.response_data["background"] == {
        "kind": "light_prefix",
        "prefix_words": 2,
        "trailing_words": 3,
    }
    assert "Living Room" in result.speech and "główne światła" in result.speech
    confirmed = await engine.process(request(reply))
    assert confirmed.status == "executed" and confirmed.completed == (MAIN,)
    assert len(executor.calls) == 1
    repeated = await engine.process(request(reply))
    assert repeated.status in {"clarify", "unsupported"}
    assert len(executor.calls) == 1 and not engine.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Nie", "Anuluj", "Nie potwierdzam", "Rezygnuję"])
async def test_rejection_is_an_unambiguous_non_execution(engine, executor, reply):
    await engine.process(request())
    result = await engine.process(request(reply))
    assert result.status == "cancelled"
    assert result.speech == "Polecenie nie zostało wykonane."
    assert executor.calls == [] and not engine.pending
    assert (await engine.process(request("Tak"))).reason == "no_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["Так", "Tak да", "Tak, tak да", "hmm", "co", "Dobrze хорошо"])
async def test_foreign_or_unclear_reply_does_not_authorize_or_extend_ttl(home, executor, reply):
    now = [10.0]
    engine = Engine(lambda: home, executor, clock=lambda: now[0])
    await engine.process(request())
    pending = engine.pending[request().session_key]
    now[0] = 25
    result = await engine.process(request(reply))
    assert result.status == "confirm" and result.reason == "background_confirmation"
    assert result.targets == (MAIN,) and result.calls
    assert engine.pending[request().session_key].expires_at == pending.expires_at == 40
    assert executor.calls == []
    now[0] = 40
    assert (await engine.process(request("Tak"))).reason == "no_pending"
    assert not engine.pending and executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"source_area": "kitchen"},
        {"satellite_id": "assist_satellite.kitchen"},
        {"device_id": "other-device"},
        {"user_id": "other-user"},
        {"conversation_id": "other-conversation"},
        {"dry_run": True},
    ],
)
async def test_confirmation_cannot_cross_source_identity_or_mode(engine, executor, changes):
    await engine.process(request())
    original = engine.pending[request().session_key]
    result = await engine.process(request("Tak", **changes))
    assert result.reason == "no_pending" and executor.calls == []
    assert engine.pending[request().session_key] is original
    assert (await engine.process(request("Tak"))).status == "executed"


@pytest.mark.asyncio
async def test_preview_does_not_arm_or_consume_a_spoken_question(engine, executor):
    preview = await engine.process(request(dry_run=True))
    assert preview.status == "confirm" and not preview.continue_conversation
    assert preview.targets == (MAIN,) and preview.completed == ()
    assert not engine.pending and not engine.dialogue.memory
    assert (await engine.process(request("Tak", dry_run=True))).reason == "no_pending"
    await engine.process(request())
    pending = engine.pending[request().session_key]
    await engine.process(request(dry_run=True))
    assert engine.pending[request().session_key] is pending
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"exposed": False},
        {"available": False},
        {"area_id": "kitchen"},
        {"labels": frozenset({"voice_all_off"})},
        {"labels": frozenset({"voice_default_on", "voice_confirm"})},
        {"labels": frozenset({"voice_default_on", "voice_high_risk"})},
        {"aliases": ("inna nazwa",)},
        {"name": "Nowa nazwa"},
    ],
)
@pytest.mark.parametrize("reply", ["Tak", "hmm"])
async def test_changed_metadata_revokes_question_before_yes_or_retry(
    home, executor, changes, reply
):
    current = [home]
    engine = Engine(lambda: current[0], executor)
    await engine.process(request())
    current[0] = change(home, MAIN, **changes)
    result = await engine.process(request(reply))
    assert result.reason == "plan_changed" and result.status == "clarify"
    assert not engine.pending and executor.calls == []
    assert "Main Lights" not in result.speech and "Living Room" not in result.speech


@pytest.mark.asyncio
async def test_new_matching_role_or_alias_cannot_silently_expand_the_question(home, executor):
    current = [home]
    engine = Engine(lambda: current[0], executor)
    await engine.process(request())
    current[0] = change(home, TABLE, labels=home.entities[TABLE].labels | {"voice_default_on"})
    assert (await engine.process(request("Tak"))).reason == "plan_changed"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_preflight_denial_consumes_consent_without_claiming_success(engine, executor):
    await engine.process(request())
    executor.deny = True
    result = await engine.process(request("Tak"))
    assert result.status == "error" and result.completed == ()
    assert not engine.pending and executor.calls == []
    assert (await engine.process(request("Tak"))).reason == "no_pending"


@pytest.mark.asyncio
async def test_confirmed_actual_target_is_remembered_for_a_pronoun(engine, executor):
    result = await engine.process(request("Włącz lampkę stołową wow jaka figura"))
    assert result.status == "confirm" and result.targets == (TABLE,)
    await engine.process(request("Tak"))
    result = await engine.process(request("Wyłącz je"))
    assert result.status == "executed" and result.targets == (TABLE,)
    assert [call.service for call in executor.calls] == ["turn_on", "turn_off"]


@pytest.mark.asyncio
async def test_new_complete_command_replaces_an_old_background_draft(engine, executor):
    await engine.process(request())
    result = await engine.process(request("Włącz lampkę stołową"))
    assert result.status == "executed" and result.targets == (TABLE,)
    assert not engine.pending and len(executor.calls) == 1
    assert (await engine.process(request("Tak"))).reason == "no_pending"


@pytest.mark.asyncio
async def test_explicit_area_overrides_the_satellite_and_is_named_in_the_question(engine, executor):
    result = await engine.process(request("Włącz światła w kuchni wow jaka figura"))
    assert result.status == "confirm" and result.targets == ("light.kitchen_main",)
    assert result.area_ids == ("kitchen",) and "Kitchen" in result.speech
    confirmed = await engine.process(request("Tak"))
    assert confirmed.completed == ("light.kitchen_main",) and len(executor.calls) == 1


@pytest.mark.asyncio
async def test_on_off_categories_remain_asymmetric(engine, executor):
    result = await engine.process(request("Zgaś światła wow jaka figura"))
    assert result.status == "confirm" and set(result.targets) == {MAIN, TABLE, AMBIENT}
    assert "główne" not in result.speech
    await engine.process(request("Dobrze"))
    assert {identifier for call in executor.calls for identifier in call.entity_ids} == {
        MAIN,
        TABLE,
        AMBIENT,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "labels",
    [
        frozenset({"voice_default_on", "voice_confirm"}),
        frozenset({"voice_default_on", "voice_high_risk"}),
    ],
)
async def test_recovery_never_waives_a_separate_confirmation_or_high_risk(home, executor, labels):
    guarded = change(home, MAIN, labels=labels)
    engine = Engine(lambda: guarded, executor)
    result = await engine.process(request())
    assert result.reason != "background_confirmation" and not engine.pending
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exposed,blocked", [(True, False), (False, False), (True, True)])
async def test_a_complete_exact_alias_wins_even_when_hidden_or_blocked(
    home, executor, exposed, blocked
):
    labels = home.entities[TABLE].labels | ({"voice_block"} if blocked else set())
    changed = change(
        home, TABLE, aliases=("światła wow jaka figura",), exposed=exposed, labels=labels
    )
    engine = Engine(lambda: changed, executor)
    result = await engine.process(request())
    assert result.reason != "background_confirmation" and not engine.pending
    if exposed and not blocked:
        assert result.status == "executed" and result.targets == (TABLE,)
    else:
        assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Włącz światła w piwnicy",
        "Włącz światła w nieznanym pokoju wow jaka figura",
        "Włącz światła na parterze wow jaka figura",
        "Włącz wszystkie światła wow jaka figura",
        "Wyłącz wszystko wow jaka figura",
        "Włącz je wow jaka figura",
        "Przyciemnij światła na 30 procent wow jaka figura",
        "Włącz światła nad stołem obok krzesła",
        "Włącz światła kuchnia wow jaka figura",
        "Włącz światła ale nie w kuchni",
        "Włącz światła i zamknij roletę",
        "Włącz światła odlicz minutę",
        "Włącz światła na czerwono",
        "Włącz światła o 15:00",
        "Włącz światła Tak да",
        '"Włącz światła", powiedział tata',
        "„Włącz światła” wow jaka figura",
        "Włącz światła powiedziała mama",
        "Włącz światła o zmroku",
        "Włącz światła po zachodzie",
        "Włącz światła przed snem",
        "Włącz światła sufitowe wow jaka figura",
    ],
)
async def test_restricted_or_ambiguous_text_never_becomes_a_background_question(
    engine, executor, text
):
    result = await engine.process(request(text))
    assert result.status not in {"executed", "dry_run"}
    assert result.reason != "background_confirmation"
    assert not engine.pending and executor.calls == []
    if text == "Włącz światła w piwnicy":
        assert (result.status, result.reason) == ("clarify", "unknown_location")


@pytest.mark.asyncio
async def test_clean_commands_keep_the_two_existing_model_reads(home, executor):
    reads = []
    engine = Engine(lambda: reads.append(1) or home, executor)
    result = await engine.process(request("Włącz światła"))
    assert result.status == "executed" and len(reads) == 2


@pytest.mark.asyncio
async def test_noisy_generic_off_uses_unlabelled_room_fallback_and_requires_confirmation(
    home, executor
):
    semantic = {
        "voice_default_on",
        "voice_all_off",
        "voice_main",
        "voice_task",
        "voice_ambient",
        "voice_accent",
        "voice_safe_all",
    }
    unlabeled = HomeModel(
        areas=home.areas.values(),
        floors=home.floors.values(),
        entities=[
            replace(entity, labels=entity.labels - semantic)
            if entity.domain == "light" and entity.area_id == "living_room"
            else entity
            for entity in home.entities.values()
        ],
        satellites=home.satellites,
        devices=home.devices,
    )
    engine = Engine(lambda: unlabeled, executor)
    expected = tuple(sorted((MAIN, TABLE, AMBIENT)))

    question = await engine.process(request("Wyłącz światła wow jaka figura"))

    assert question.status == "confirm" and question.reason == "background_confirmation"
    assert question.targets == expected and not executor.calls and not executor.checked
    answer = await engine.process(request("Potwierdzam"))
    assert answer.status == "executed" and answer.completed == expected
    assert tuple(call.entity_ids for call in executor.calls) == tuple((item,) for item in expected)


@pytest.mark.asyncio
async def test_a_rejected_recognized_prefix_is_not_sent_to_fallback(home, executor):
    class ForbiddenFallback:
        async def propose(self, *_):
            pytest.fail("A rejected D02 light prefix must not reach an external model")

    engine = Engine(lambda: home, executor, ForbiddenFallback())
    result = await engine.process(request("Włącz światła ale nie w kuchni"))
    assert (result.status, result.reason) == ("unsupported", "no_intent")
    assert result.speech == "Nie rozumiem."
    assert executor.calls == [] and not engine.pending


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", ["Tak włącz światła", "Tak, zgaś lampkę", "Potwierdzam i zamknij roletę"]
)
async def test_affirmation_with_an_extra_command_never_authorizes_the_draft(
    engine, executor, reply
):
    await engine.process(request())
    result = await engine.process(request(reply))
    assert result.status not in {"executed", "dry_run"}
    assert executor.calls == [] and not engine.pending


@pytest.mark.asyncio
async def test_the_longest_known_light_alias_is_proposed_instead_of_a_default(home, executor):
    aliased = change(home, TABLE, aliases=("światła wow",))
    engine = Engine(lambda: aliased, executor)
    result = await engine.process(request())
    assert result.status == "confirm" and result.targets == (TABLE,)
    assert "Table Light" in result.speech and "główne" not in result.speech
    assert (await engine.process(request("Tak"))).completed == (TABLE,)


@pytest.mark.asyncio
async def test_a_partial_hidden_alias_cannot_be_shortened_into_a_different_light(home, executor):
    aliased = change(home, TABLE, aliases=("światła wow",), exposed=False)
    engine = Engine(lambda: aliased, executor)
    result = await engine.process(request())
    assert result.reason != "background_confirmation" and not engine.pending
    assert executor.calls == []


@pytest.mark.asyncio
async def test_duplicate_full_alias_stays_ambiguous_without_a_default_fallback(home, executor):
    aliased = change(home, TABLE, aliases=("światła wow jaka figura",))
    aliased = change(aliased, AMBIENT, aliases=("światła wow jaka figura",))
    engine = Engine(lambda: aliased, executor)
    result = await engine.process(request())
    assert result.reason == "ambiguous_target" and not engine.pending
    assert executor.calls == []


@pytest.mark.asyncio
async def test_group_membership_and_inherited_restrictions_are_revalidated(home, executor):
    current = [change(home, MAIN, attributes={"entity_id": [TABLE]})]
    engine = Engine(lambda: current[0], executor)
    result = await engine.process(request())
    assert result.status == "confirm" and result.targets == (TABLE,)
    current[0] = change(current[0], MAIN, attributes={"entity_id": [TABLE, AMBIENT]})
    assert (await engine.process(request("Tak"))).reason == "plan_changed"
    assert executor.calls == [] and not engine.pending


@pytest.mark.asyncio
async def test_single_light_in_a_resolved_room_is_eligible(engine, executor):
    result = await engine.process(request("Włącz lampę wow jaka figura", source_area="office"))
    assert result.status == "confirm" and result.targets == ("light.office_main",)
    assert executor.calls == []


@pytest.mark.asyncio
async def test_a_typed_category_is_preserved(engine, executor):
    result = await engine.process(request("Włącz światła dekoracyjne wow jaka figura"))
    assert result.status == "confirm" and result.targets == (AMBIENT,)
    assert "dekoracyjne" in result.speech and executor.calls == []


@pytest.mark.asyncio
async def test_successful_execution_cannot_be_replayed_after_an_executor_error(engine, executor):
    await engine.process(request())
    executor.fail_at = 0
    result = await engine.process(request("Tak"))
    assert result.status == "error" and not result.completed
    assert not engine.pending and executor.calls == []
    assert (await engine.process(request("Tak"))).reason == "no_pending"


@pytest.mark.asyncio
async def test_pending_lifetime_is_capped_at_thirty_seconds(home, executor):
    now = [0]
    engine = Engine(lambda: home, executor, confirmation_ttl=120, clock=lambda: now[0])
    await engine.process(request())
    assert engine.pending[request().session_key].expires_at == 30
    now[0] = 30
    assert (await engine.process(request("Potwierdzam"))).reason == "no_pending"
    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["główne", "robocze", "dekoracyjne", "ozdobne", "nastrojowe"])
async def test_an_unavailable_requested_category_never_falls_back_to_default(
    home, executor, category
):
    current = home
    for identifier in (MAIN, TABLE, AMBIENT):
        current = change(
            current,
            identifier,
            labels=frozenset({"voice_default_on", "voice_all_off"})
            if identifier == MAIN
            else frozenset(),
        )
    engine = Engine(lambda: current, executor)
    result = await engine.process(request(f"Włącz światła {category} wow jaka figura"))
    assert result.reason != "background_confirmation" and not engine.pending
    assert executor.calls == []
