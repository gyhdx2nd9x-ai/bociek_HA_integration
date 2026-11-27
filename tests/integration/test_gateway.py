from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import make_mocked_request

from service.app import CLIENT, create_app, validate
from service.websocket import HAWebSocketClient, HAWebSocketError


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"text": "x", "dry_run": "false"},
        {"text": "x", "user_id": "admin"},
        {"text": "x", "source_area": []},
        {"text": "x" * 2001},
        {"text": "x", "confirmed": True},
    ],
)
def test_rejects_untrusted_request_fields(data):
    with pytest.raises(ValueError):
        validate(data)


def test_gateway_defaults_dry_run():
    assert validate({"text": "zapal światła"})["dry_run"] is True


async def invoke(path, body=None, auth="Bearer test-token", result=None):
    client = SimpleNamespace(request=AsyncMock(return_value=result or {"status": "dry_run"}))
    app = create_app("ws://ha.local/api/websocket", client)
    app[CLIENT] = client
    method = "POST" if body is not None else "GET"
    headers = {"Authorization": auth} if auth else {}
    request = make_mocked_request(method, path, headers=headers, app=app)
    request.json = AsyncMock(return_value=body)
    handler = next(
        r.handler
        for r in app.router.routes()
        if r.method == method and r.resource.canonical == path
    )
    return await handler(request), client


async def test_http_auth_and_identity_forwarding():
    response, client = await invoke("/v1/process", {"text": "zapal światła"})
    assert response.status == 200
    args = client.request.call_args.args
    assert args[0] == "test-token"
    assert args[1]["dry_run"] is True
    assert "user_id" not in args[1]


async def test_missing_token_no_forward():
    response, client = await invoke("/v1/process", {"text": "zapal światła"}, auth="")
    assert response.status == 401
    client.request.assert_not_called()


class WS:
    def __init__(self, auth="auth_ok", success=True, close=False):
        self.handshake = iter([{"type": "auth_required"}, {"type": auth}])
        self.sent, self.success, self.close = [], success, close

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def receive_json(self):
        return next(self.handshake)

    async def send_json(self, data):
        self.sent.append(data)

    async def __aiter__(self):
        if not self.close:
            from aiohttp import WSMsgType

            yield SimpleNamespace(
                type=WSMsgType.TEXT,
                json=lambda: {
                    "id": 1,
                    "type": "result",
                    "success": self.success,
                    "result": {"status": "executed"},
                },
            )


async def test_websocket_auth_and_correlation():
    ws = WS()
    client = HAWebSocketClient(SimpleNamespace(ws_connect=lambda *a, **k: ws), "ws://ha")
    assert (await client.request("token", {"type": "polish_context_voice/process"}))[
        "status"
    ] == "executed"
    assert ws.sent[0] == {"type": "auth", "access_token": "token"}
    assert ws.sent[1]["id"] == 1


@pytest.mark.parametrize(
    ("kwargs", "exception"),
    [
        ({"auth": "auth_invalid"}, PermissionError),
        ({"success": False}, HAWebSocketError),
        ({"close": True}, HAWebSocketError),
    ],
)
async def test_websocket_failures_no_retries(kwargs, exception):
    ws = WS(**kwargs)
    client = HAWebSocketClient(SimpleNamespace(ws_connect=lambda *a, **k: ws), "ws://ha")
    with pytest.raises(exception):
        await client.request("token", {"type": "polish_context_voice/process"})
    assert len([m for m in ws.sent if m["type"] != "auth"]) <= 1
