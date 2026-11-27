"""Run with HA_WS_URL=ws://homeassistant.local:8123/api/websocket python -m service.app."""

import os
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, web

from .websocket import HAWebSocketClient, HAWebSocketError

CLIENT = web.AppKey("ha_client", HAWebSocketClient)
FIELDS = {"text", "source_area", "satellite_id", "device_id", "conversation_id", "dry_run"}


def validate(data):
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("Unexpected request fields")
    if not isinstance(data.get("text"), str) or not 0 < len(data["text"]) <= 2000:
        raise ValueError("text must contain 1–2000 characters")
    for key in FIELDS - {"dry_run", "text"}:
        if key in data and (not isinstance(data[key], str) or not 0 < len(data[key]) <= 200):
            raise ValueError(f"Invalid {key}")
    if "dry_run" in data and type(data["dry_run"]) is not bool:
        raise ValueError("dry_run must be boolean")
    return {"dry_run": True, **data}


def create_app(ha_url, client=None):
    parsed = urlparse(ha_url)
    if (
        parsed.scheme not in {"ws", "wss"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("HA_WS_URL must be a ws/wss URL without credentials or query")
    app = web.Application(client_max_size=8192)

    async def lifecycle(app):
        if client:
            app[CLIENT] = client
            yield
        else:
            async with ClientSession() as session:
                app[CLIENT] = HAWebSocketClient(session, ha_url)
                yield

    app.cleanup_ctx.append(lifecycle)

    async def forward(request, command):
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not authorization[7:].strip():
            return web.json_response({"error": "HA bearer token required"}, status=401)
        try:
            result = await request.app[CLIENT].request(authorization[7:], command)
            return web.json_response(result)
        except PermissionError:
            return web.json_response({"error": "HA authentication failed"}, status=401)
        except (TimeoutError, HAWebSocketError, ClientError, ValueError):
            # Mutation could already have reached HA: don't retry automatically.
            return web.json_response(
                {"error": "HA request failed; outcome may be unknown. Check HA before retrying."},
                status=502,
            )

    async def process(request):
        try:
            data = validate(await request.json())
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid request"}, status=400)
        return await forward(request, {"type": "polish_context_voice/process", **data})

    async def health(request):
        return await forward(request, {"type": "polish_context_voice/health"})

    app.router.add_post("/v1/process", process)
    app.router.add_get("/v1/health", health)
    return app


def main():
    web.run_app(
        create_app(os.environ["HA_WS_URL"]),
        host=os.getenv("PCV_HOST", "127.0.0.1"),
        port=int(os.getenv("PCV_PORT", "8099")),
        access_log=None,
    )


if __name__ == "__main__":
    main()
