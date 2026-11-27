from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.presentation import ACKNOWLEDGEMENTS, Presentation
from custom_components.polish_context_voice.core.types import Request, Result


def request(**kwargs):
    return Request(
        "zapal światła", satellite_id="assist_satellite.a", conversation_id="c", **kwargs
    )


def success(**kwargs):
    return replace(
        Result("executed", "Gotowe.", targets=("light.a",), completed=("light.a",)), **kwargs
    )


def test_success_variation_survives_new_wake_session_without_affecting_other_satellite():
    presenter = Presentation()
    replies = [
        presenter.acknowledgement("Gotowe.", success(), replace(request(), conversation_id=str(i)))
        for i in range(6)
    ]
    assert replies[:5] == list(ACKNOWLEDGEMENTS)
    assert all(a != b for a, b in zip(replies, replies[1:], strict=False))
    assert (
        presenter.acknowledgement(
            "Gotowe.", success(), replace(request(), satellite_id="assist_satellite.b")
        )
        == "Gotowe."
    )
    assert presenter.acknowledgement("Gotowe.", success(), request(user_id="other")) == "Gotowe."


@pytest.mark.parametrize(
    "status", ["partial", "error", "confirm", "answered", "dry_run", "clarify"]
)
def test_never_turns_incomplete_or_query_into_generic_success(status):
    presenter = Presentation()
    assert presenter.acknowledgement("Gotowe.", success(status=status), request()) == "Gotowe."
    assert not presenter.acks


def test_silence_meaningful_answers_and_preview_do_not_consume_variation():
    presenter = Presentation()
    for text in ("", "Odliczam dwie minuty.", "Uruchamiam: Jazz.", "W którym pomieszczeniu?"):
        assert presenter.acknowledgement(text, success(), request()) == text
    presenter.acknowledgement("Gotowe.", success(), request(dry_run=True))
    presenter.acknowledgement("Gotowe.", success(completed=()), request())
    assert not presenter.acks


def test_retry_is_once_per_voice_conversation_and_never_a_pending_action():
    presenter = Presentation()
    failure = Result("unsupported", "Powtórz je proszę.", reason="timer_no_match")
    first = replace(failure)
    presenter.retry_timer(first, request())
    assert first.continue_conversation and not first.calls
    second = replace(failure)
    presenter.retry_timer(second, request())
    assert not second.continue_conversation
    assert second.speech == "Nadal nie rozumiem polecenia minutnika."
    other = replace(failure)
    presenter.retry_timer(other, replace(request(), satellite_id="assist_satellite.b"))
    assert other.continue_conversation
    presenter.retry_timer(success(), request())
    assert request().session_key not in presenter.retries


def test_retry_not_for_text_preview_backend_error_or_known_echo():
    presenter = Presentation()
    for req in (replace(request(), satellite_id=None), request(dry_run=True)):
        result = Result("unsupported", "Powtórz.", reason="timer_no_match")
        presenter.retry_timer(result, req)
        assert not result.continue_conversation
    for status, reason in (("error", "timer_handler_error"), ("ignored", "timer_response_echo")):
        result = Result(status, "", reason=reason)
        presenter.retry_timer(result, request())
        assert not result.continue_conversation
    assert not presenter.retries


def test_presentation_memory_is_bounded_and_expires():
    now = [0.0]
    presenter = Presentation(clock=lambda: now[0])
    for i in range(600):
        req = replace(request(), satellite_id=str(i))
        presenter.acknowledgement("Gotowe.", success(), req)
        presenter.retry_timer(Result("unsupported", "Powtórz.", reason="timer_no_match"), req)
    assert len(presenter.acks) == len(presenter.retries) == 512
    now[0] = 601
    presenter.acknowledgement("", success(), request())
    presenter.retry_timer(success(), request())
    assert not presenter.acks and not presenter.retries
