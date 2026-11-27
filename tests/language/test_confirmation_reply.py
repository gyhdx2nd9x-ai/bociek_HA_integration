"""Whole affirmative replies tolerate repetition without dropping semantic words."""

import pytest

from custom_components.polish_context_voice.core.context.dialogue import is_confirmation_reply


@pytest.mark.parametrize(
    "reply",
    [
        "Tak",
        "Tak. Tak",
        "tak tak",
        "TAK! Tak! tak!",
        "Potwierdzam. Potwierdzam",
        "dobrze, dobrze",
        "Okej; okej",
        "tak, proszę",
        "Tak, proszę. Tak, proszę.",
        "tak potwierdzam",
        "tak potwierdzam tak potwierdzam",
    ],
)
def test_complete_unambiguous_affirmative_or_bounded_repetition(reply):
    assert is_confirmation_reply(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "...",
        "Tak tak tak tak",
        "potwierdzam " * 40,
        "Так",
        "tаk",  # Cyrillic 'a' embedded in Latin letters.
        "tak да",
        "tak Так",
        "tak nie",
        "Nie, tak",
        "nie potwierdzam",
        "tak, ale nie dodawaj",
        "Tak, zmień godzinę na szesnastą",
        "tak i wyłącz światło",
        "Tak? Nie wiem",
        "tak dobrze",
        "t.a.k",
        "tak 2",
    ],
)
def test_foreign_script_negation_correction_or_extra_instruction_is_not_consent(reply):
    assert not is_confirmation_reply(reply)
