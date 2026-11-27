import json
from pathlib import Path

import pytest

from benchmark.run import correct, request_for

CASES = json.loads(Path("benchmark/cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_household_corpus(engine, case):
    result = await engine.process(request_for(case))
    assert correct(case, result), result.to_dict()
