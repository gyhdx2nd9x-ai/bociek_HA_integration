"""Real HA 2026.10 timer intents and event lifecycle, with simulated receivers."""

import asyncio
from dataclasses import replace

from homeassistant.components.intent import TimerEventType, async_register_timer_handler
from homeassistant.components.intent.const import TIMER_DATA
from homeassistant.helpers import entity_registry as er

NOISY_TIMER = "Odlicz dziesięć sekund Usiądź normalnie, żebyś się nie ochlapał. Okej, masz więcej."


async def exercise_timers(hass, fixture, client, agent, report, case):
    device_id = er.async_get(hass).async_get(fixture["satellite"]).device_id
    other_device = fixture["devices"]["office"][0]
    manager = hass.data[TIMER_DATA]
    runtime = next(iter(hass.data["polish_context_voice"].values()))
    events = []
    finished = asyncio.Event()

    def receiver(event, timer):
        events.append((event, timer.id, timer.device_id))
        if event == TimerEventType.FINISHED:
            finished.set()

    unsubscribe = async_register_timer_handler(hass, device_id, receiver)

    async def voice(text, **kwargs):
        return await client.call(
            "conversation/process",
            agent_id=agent,
            language="pl",
            text=text,
            **({"satellite_id": fixture["satellite"]} | kwargs),
        )

    async def preview(text, **kwargs):
        result = await client.call(
            "call_service",
            domain="polish_context_voice",
            service="preview",
            service_data={"text": text, "satellite_id": fixture["satellite"]} | kwargs,
            return_response=True,
        )
        return result["response"]

    def success(result):
        assert result["response"]["response_type"] != "error", result
        assert result["response"]["speech"]["plain"]["speech"], result

    def rejected(result):
        # An unparsed voice request can ask once for repetition, never claim execution.
        assert result["response"]["response_type"] in {"error", "query_answer"}, result
        if result["response"]["response_type"] == "query_answer":
            assert result["continue_conversation"], result
            assert result["response"]["speech"]["plain"]["speech"] == (
                "Nie rozumiem polecenia minutnika. Powtórz je proszę."
            ), result

    before = {s.entity_id: s.state for s in hass.states.async_all("light")}
    try:
        with case(report, "timer_preview_words_digits_and_composite_no_side_effects"):
            for text, slots in (
                ("Odlicz dziesięć sekund", {"seconds": 10}),
                ("odlicz 10 sekund", {"seconds": 10}),
                ("Ustaw minutnik na dwie minuty", {"minutes": 2}),
                ("odlicz minutę i trzydzieści sekund", {"minutes": 1, "seconds": 30}),
                ("ustaw minutnik na 1 godzinę i 5 minut", {"hours": 1, "minutes": 5}),
            ):
                result = await preview(text)
                assert result["status"] == "dry_run", (text, result)
                assert result["native_intent"] == {
                    "name": "HassStartTimer",
                    "slots": slots,
                    "device_id": device_id,
                }, result
                assert not result["calls"] and not result["completed"], result
            assert not manager.timers and not events

        with case(report, "timer_reject_negation_unknown_zero_extra_commands"):
            for text in (
                "Nie odlicz dziesięć sekund",
                "odlicz zero sekund",
                "odlicz -10 sekund",
                "odlicz 10 sekund i włącz światła",
                "Wąć się od warstwowania.",
                "wyłącz światła za 10 sekund",
            ):
                result = await voice(text)
                rejected(result)
            assert not manager.timers and not events

        with case(report, "timer_unparsed_voice_reopens_once_and_accepts_full_retry"):
            result = await voice("odlicz banany")
            rejected(result)
            assert result["continue_conversation"] and not manager.timers
            cid = result["conversation_id"]
            second = await voice("odlicz banany", conversation_id=cid)
            assert not second["continue_conversation"]
            assert second["response"]["response_type"] == "error"
            assert not runtime.pending_timers and not manager.timers
            completed = await voice("odlicz dwie minuty", conversation_id=cid)
            success(completed)
            assert not completed["continue_conversation"]
            assert next(iter(manager.timers.values())).seconds == 120
            manager.cancel_timer(next(iter(manager.timers)))
            await hass.async_block_till_done()
            assert not manager.timers
            assert [event[0] for event in events] == [
                TimerEventType.STARTED,
                TimerEventType.CANCELLED,
            ]
            events.clear()

        with case(report, "timer_missing_device_unsupported_device_and_mismatch"):
            result = await client.call(
                "polish_context_voice/process",
                text="odlicz 10 sekund",
                dry_run=False,
            )
            assert result["reason"] == "timer_device_required", result
            result = await client.call(
                "polish_context_voice/process",
                text="odlicz 10 sekund",
                dry_run=False,
                device_id=other_device,
            )
            assert result["reason"] == "no_timer_support", result
            result = await preview("odlicz 10 sekund", device_id=other_device)
            assert result["reason"] == "timer_device_mismatch", result
            assert not manager.timers and not events

        with case(report, "timer_voice_ten_seconds_native_started_and_finished"):
            result = await voice("Odlicz dziesięć sekund")
            success(result)
            assert result["response"]["speech"]["plain"]["speech"] == "Odliczam dziesięć sekund."
            assert len(manager.timers) == 1
            timer = next(iter(manager.timers.values()))
            assert timer.seconds == 10 and timer.device_id == device_id
            assert timer.conversation_command is None
            assert events == [(TimerEventType.STARTED, timer.id, device_id)], events
            await asyncio.wait_for(finished.wait(), timeout=12)
            assert events[-1] == (TimerEventType.FINISHED, timer.id, device_id)
            assert not manager.timers

        with case(report, "timer_user_two_minutes_natural_duration_and_single_start"):
            start_events = len(events)
            result = await voice("Odlicz dwie minuty")
            assert result["response"]["response_type"] == "action_done", result
            assert result["response"]["speech"]["plain"]["speech"] == "Odliczam dwie minuty."
            assert len(manager.timers) == 1
            timer = next(iter(manager.timers.values()))
            assert timer.seconds == 120 and timer.device_id == device_id
            assert events[start_events:] == [(TimerEventType.STARTED, timer.id, device_id)]
            manager.cancel_timer(timer.id)

        with case(report, "timer_natural_composite_named_and_no_claim_on_preview_error"):
            result = await voice("odlicz minutę i trzydzieści sekund")
            assert result["response"]["speech"]["plain"]["speech"] == (
                "Odliczam minutę i trzydzieści sekund."
            ), result
            timer = next(iter(manager.timers.values()))
            assert timer.seconds == 90
            manager.cancel_timer(timer.id)
            result = await voice("ustaw minutnik na 2 minuty o nazwie herbata")
            assert result["response"]["speech"]["plain"]["speech"] == "Odliczam dwie minuty."
            timer = next(iter(manager.timers.values()))
            assert timer.name == "herbata" and timer.seconds == 120
            manager.cancel_timer(timer.id)
            result = await preview("Odlicz dwie minuty")
            assert "Odliczam" not in result["speech"] and not manager.timers
            result = await voice("odlicz zero sekund")
            rejected(result)
            assert "Odliczam" not in result["response"]["speech"]["plain"]["speech"]
            assert not manager.timers

        with case(report, "timer_native_pause_resume_adjust_status_and_cancel"):
            success(await voice("ustaw minutnik na dwie minuty"))
            timer = next(iter(manager.timers.values()))
            success(await voice("wstrzymaj minutnik"))
            assert not timer.is_active
            success(await voice("dodaj 10 sekund do minutnika"))
            success(await voice("odejmij 5 sekund od minutnika"))
            assert 124 <= timer.seconds <= 125, timer
            success(await voice("wznów minutnik"))
            assert timer.is_active
            result = await voice("status minutnika")
            success(result)
            assert result["response"]["response_type"] == "query_answer", result
            assert "Pozosta" in result["response"]["speech"]["plain"]["speech"], result
            success(await voice("anuluj minutnik"))
            assert not manager.timers
            result = await voice("anuluj minutnik")
            assert result["response"]["response_type"] == "error", result
            assert result["response"]["speech"]["plain"]["speech"] == (
                "Nie ma takiego minutnika."
            ), result

        with case(report, "timer_background_preview_does_not_arm_confirmation"):
            result = await preview(NOISY_TIMER)
            assert result["status"] == "confirm", result
            assert result["reason"] == "additional_speech", result
            assert result["native_intent"]["slots"] == {"seconds": 10}, result
            assert result["confidence_class"] == "medium" and result["risk"] == "medium"
            assert not runtime.pending_timers and not manager.timers
            response = await voice(
                "potwierdzam minutnik",
                conversation_id=result["conversation_id"],
            )
            assert response["response"]["response_type"] == "error", response
            assert not manager.timers

        with case(report, "timer_background_voice_confirm_exact_duration_once"):
            response = await voice(NOISY_TIMER)
            conversation_id = response["conversation_id"]
            success(response)
            assert response["continue_conversation"], response
            assert "Odliczać dziesięć sekund?" in response["response"]["speech"]["plain"]["speech"]
            assert not manager.timers
            wrong = await voice("potwierdzam minutnik", conversation_id="different-conversation")
            assert wrong["response"]["response_type"] == "error", wrong
            confirmed = await voice("potwierdzam minutnik", conversation_id=conversation_id)
            success(confirmed)
            assert not confirmed["continue_conversation"]
            assert confirmed["response"]["speech"]["plain"]["speech"] == "Odliczam dziesięć sekund."
            assert len(manager.timers) == 1
            timer = next(iter(manager.timers.values()))
            assert timer.seconds == 10 and timer.device_id == device_id
            repeated = await voice("potwierdzam minutnik", conversation_id=conversation_id)
            assert repeated["response"]["response_type"] == "error", repeated
            assert len(manager.timers) == 1
            manager.cancel_timer(timer.id)

        with case(report, "timer_background_cancel_expiry_interruption_and_source_isolation"):
            for mode in ("cancel", "expire", "unrelated", "source", "plan"):
                response = await voice(NOISY_TIMER)
                conversation_id = response["conversation_id"]
                key = next(key for key in runtime.pending_timers if key[1] == conversation_id)
                if mode == "cancel":
                    success(await voice("nie", conversation_id=conversation_id))
                elif mode == "expire":
                    runtime.pending_timers[key] = replace(
                        runtime.pending_timers[key],
                        expires_at=0,
                    )
                elif mode == "unrelated":
                    await voice("jaka jest temperatura", conversation_id=conversation_id)
                elif mode == "plan":
                    runtime.pending_timers[key] = replace(
                        runtime.pending_timers[key],
                        native_intent={
                            "name": "HassStartTimer",
                            "slots": {"seconds": 99},
                            "device_id": device_id,
                        },
                    )
                kwargs = (
                    {"device_id": other_device, "satellite_id": None} if mode == "source" else {}
                )
                result = await voice(
                    "potwierdzam minutnik",
                    conversation_id=conversation_id,
                    **kwargs,
                )
                assert result["response"]["response_type"] == "error", (mode, result)
                assert not manager.timers
                runtime.pending_timers.clear()

        with case(report, "timer_background_never_discards_correction_or_device_command"):
            for text in (
                "odlicz 10 sekund nie jednak 20 sekund",
                "odlicz 10 sekund albo 20",
                "odlicz 10 sekund. Anuluj minutnik",
                "odlicz 10 sekund i włącz światła",
                "Nie odlicz dziesięć sekund Usiądź normalnie",
                "odlicz 10 sekund za 20 sekund",
            ):
                result = await voice(text)
                rejected(result)
                assert not runtime.pending_timers and not manager.timers
            # The non-timer utterance "wyłącz światła Usiądź normalnie" now
            # belongs to D02's question-only light recovery contract. Its
            # explicit no-action/no-timer checks live in light_background_scenarios.

        with case(report, "timer_user_background_short_confirmations_start_once"):
            for reply in (
                "Tak",
                "Tak. Tak",
                "Potwierdzam",
                "Dobrze",
                "OK",
                "Okej",
                "Zgoda",
                "Tak proszę",
                "Tak potwierdzam",
            ):
                response = await voice("Odlicz minutę Wow jaka figura")
                cid = response["conversation_id"]
                assert response["continue_conversation"]
                assert response["response"]["speech"]["plain"]["speech"] == ("Odliczać minutę?"), (
                    response
                )
                assert not manager.timers
                start_events = len(events)
                confirmed = await voice(reply, conversation_id=cid)
                assert confirmed["response"]["response_type"] == "action_done", (reply, confirmed)
                assert confirmed["response"]["speech"]["plain"]["speech"] == "Odliczam minutę."
                timer = next(iter(manager.timers.values()))
                assert timer.seconds == 60 and timer.device_id == device_id
                assert events[start_events:] == [(TimerEventType.STARTED, timer.id, device_id)]
                await voice(reply, conversation_id=cid)
                assert len(manager.timers) == 1 and len(events) == start_events + 1
                manager.cancel_timer(timer.id)

        with case(report, "timer_short_rejection_discards_proposal_not_running_timer"):
            success(await voice("odlicz dziesięć minut"))
            running = set(manager.timers)
            for reply in (
                "Nie",
                "Nie dziękuję",
                "Anuluj",
                "Anuluj to",
                "Nie uruchamiaj",
                "Rezygnuję",
            ):
                response = await voice("Odlicz minutę Wow jaka figura")
                cid = response["conversation_id"]
                result = await voice(reply, conversation_id=cid)
                assert (
                    result["response"]["speech"]["plain"]["speech"] == "Nie uruchamiam minutnika."
                )
                assert not result["continue_conversation"] and not runtime.pending_timers
                await voice("Tak", conversation_id=cid)
                assert set(manager.timers) == running
            success(await voice("przerwij odliczanie"))
            assert not manager.timers

        with case(report, "timer_short_confirmation_expiry_context_and_whole_reply"):
            for mode in ("expire", "conversation", "source", "correction", "mixed_script", "plan"):
                response = await voice("Odlicz minutę Wow jaka figura")
                cid = response["conversation_id"]
                key = next(key for key in runtime.pending_timers if key[1] == cid)
                kwargs = {"conversation_id": cid}
                text = "Potwierdzam"
                if mode == "expire":
                    runtime.pending_timers[key] = replace(runtime.pending_timers[key], expires_at=0)
                elif mode == "conversation":
                    kwargs["conversation_id"] = "wrong-timer-conversation"
                elif mode == "source":
                    kwargs.update(device_id=other_device, satellite_id=None)
                elif mode == "correction":
                    text = "Tak ale na dwie minuty"
                elif mode == "mixed_script":
                    text = "tak да"
                elif mode == "plan":
                    runtime.pending_timers[key] = replace(
                        runtime.pending_timers[key],
                        native_intent={
                            "name": "HassStartTimer",
                            "slots": {"seconds": 99},
                            "device_id": device_id,
                        },
                    )
                result = await voice(text, **kwargs)
                assert result["response"]["response_type"] == "error", (mode, result)
                assert not manager.timers
                runtime.pending_timers.clear()

        with case(report, "timer_user_interrupt_cancels_native_countdown_and_emits_event"):
            success(await voice("Odlicz dziesięć minut"))
            timer = next(iter(manager.timers.values()))
            result = await preview("Przerwij odliczanie")
            assert result["native_intent"]["name"] == "HassCancelTimer", result
            assert timer.id in manager.timers
            start_events = len(events)
            success(await voice("Przerwij odliczanie"))
            assert not manager.timers
            assert events[start_events:] == [(TimerEventType.CANCELLED, timer.id, device_id)]
            response = await voice("Przerwij odliczanie")
            assert response["response"]["speech"]["plain"]["speech"] == "Nie ma takiego minutnika."

        with case(report, "timer_countdown_pause_resume_and_cancel_aliases"):
            for cancel in ("Anuluj odliczanie", "Zakończ odliczanie", "Przerwij minutnik"):
                success(await voice("Odlicz dziesięć minut"))
                timer = next(iter(manager.timers.values()))
                success(await voice("Wstrzymaj odliczanie"))
                assert not timer.is_active
                success(await voice("Wznów odliczanie"))
                assert timer.is_active
                for text in ("Nie przerwij odliczanie", "Przerwij odliczanie i włącz światła"):
                    result = await voice(text)
                    rejected(result)
                    assert timer.id in manager.timers
                success(await voice(cancel))
                assert not manager.timers

        with case(report, "timer_interrupt_ambiguous_named_and_duration_selectors"):
            success(await voice("ustaw minutnik na 2 minuty o nazwie herbata"))
            success(await voice("ustaw minutnik na 3 minuty o nazwie jajka"))
            result = await voice("Przerwij odliczanie")
            assert result["response"]["response_type"] == "error" and len(manager.timers) == 2
            success(await voice("Przerwij odliczanie herbata"))
            assert len(manager.timers) == 1
            assert next(iter(manager.timers.values())).name == "jajka"
            success(await voice("Przerwij odliczanie na 3 minuty"))
            assert not manager.timers

        with case(report, "timer_known_error_echo_silent_no_start_no_lost_proposal"):
            response = await voice("Odlicz minutę Wow jaka figura")
            cid = response["conversation_id"]
            key = next(key for key in runtime.pending_timers if key[1] == cid)
            proposal = runtime.pending_timers[key]
            start_events = len(events)
            for echo in (
                "Nie rozumiem polecenia minutnika. Powiedz na przykład: odlicz dziesięć sekund",
                "Nie rozumiem polecenia minutnika. Powtórz je proszę.",
            ):
                for kwargs in (
                    {"conversation_id": cid},
                    {"device_id": other_device, "satellite_id": None},
                ):
                    result = await voice(echo, **kwargs)
                    assert result["response"]["speech"]["plain"]["speech"] == "", result
                    assert result["response"]["response_type"] == "query_answer"
                    assert not result["continue_conversation"]
                    assert not manager.timers and len(events) == start_events
                    assert runtime.pending_timers[key] is proposal
            success(await voice("Tak", conversation_id=cid))
            assert len(manager.timers) == 1
            manager.cancel_timer(next(iter(manager.timers)))

        with case(report, "timer_named_multiple_cancel_all_and_preserve_lights"):
            success(await voice("ustaw minutnik na 2 minuty o nazwie herbata"))
            success(await voice("ustaw minutnik na 3 minuty o nazwie jajka"))
            assert len(manager.timers) == 2
            result = await voice("anuluj minutnik")
            assert result["response"]["response_type"] == "error", result
            success(await voice("anuluj minutnik herbata"))
            assert len(manager.timers) == 1
            success(await voice("anuluj wszystkie minutniki"))
            assert not manager.timers
            assert before == {s.entity_id: s.state for s in hass.states.async_all("light")}
    finally:
        for timer in list(manager.timers.values()):
            manager.cancel_timer(timer.id)
        unsubscribe()
