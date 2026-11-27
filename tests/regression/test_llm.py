from dataclasses import replace

import pytest

from custom_components.polish_context_voice.core.llm.tools import validate_proposal
from custom_components.polish_context_voice.core.types import Request


class StubFallback:
    def __init__(self, data=None, fail=False):
        self.calls = 0
        self.data, self.fail = data, fail

    async def propose(self, text, request, home):
        self.calls += 1
        if self.fail:
            raise TimeoutError
        return validate_proposal(self.data)


async def test_fast_path_never_calls_llm(engine):
    fallback = engine.fallback = StubFallback(fail=True)
    result = await engine.process(Request("zapal światła", source_area="living_room"))
    assert result.status == "executed"
    assert fallback.calls == 0


async def test_fallback_same_semantics_requires_confirmation(engine, executor):
    engine.fallback = StubFallback({"action": "turn_on", "kind": "light"})
    request = Request("jest za ciemno", source_area="living_room", conversation_id="c")
    result = await engine.process(request)
    assert result.status == "confirm"
    assert result.targets == ("light.living_room_main",)
    assert not executor.calls
    assert (await engine.process(replace(request, text="potwierdzam"))).status == "executed"
    assert engine.fallback.calls == 1


async def test_llm_timeout_is_closed(engine, executor):
    engine.fallback = StubFallback(fail=True)
    assert (await engine.process(Request("jest za ciemno"))).reason == "fallback_unavailable"
    assert not executor.calls


@pytest.mark.parametrize(
    "data",
    [
        {"action": "turn_on", "kind": "light", "entity_id": "light.hidden"},
        {"action": "unlock", "kind": "named", "name": "door"},
        {"action": "turn_on", "kind": "light", "confidence": 1},
        {"action": "turn_on", "kind": "safe_all"},
        {"action": "brightness", "kind": "light", "value": float("nan")},
        {"action": "brightness", "kind": "light", "value": True},
        {"action": "turn_on", "kind": "light", "qualifier": "voice_block"},
        {"action": "turn_on", "kind": "light", "location": None},
        {"action": "turn_on", "kind": "named"},
    ],
)
def test_untrusted_llm_schema(data):
    with pytest.raises((ValueError, KeyError)):
        validate_proposal(data)
