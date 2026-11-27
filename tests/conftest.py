import json
from pathlib import Path

import pytest

from custom_components.polish_context_voice.core.engine import Engine
from custom_components.polish_context_voice.core.home_model import HomeModel


class RecordingExecutor:
    def __init__(self):
        self.calls = []
        self.checked = []
        self.fail_at = None
        self.deny = False

    async def preflight(self, calls, request):
        self.checked.extend(calls)
        if self.deny:
            raise PermissionError("Denied")

    async def execute(self, call, request):
        if self.fail_at == len(self.calls):
            raise ConnectionError("HA disconnected")
        self.calls.append(call)


@pytest.fixture
def house_data():
    return json.loads(Path("examples/household.json").read_text())


@pytest.fixture
def home(house_data):
    return HomeModel.from_dict(house_data)


@pytest.fixture
def executor():
    return RecordingExecutor()


@pytest.fixture
def engine(home, executor):
    return Engine(lambda: home, executor)
