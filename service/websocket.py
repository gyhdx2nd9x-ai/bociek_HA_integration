"""Small request/response WebSocket client; no replay of uncertain writes."""

import asyncio

import aiohttp


class HAWebSocketError(Exception):
    pass


class HAWebSocketClient:
    def __init__(self, session, url, timeout=20):
        self.session, self.url, self.timeout = session, url, timeout

    async def request(self, token, command):
        async with asyncio.timeout(self.timeout):
            async with self.session.ws_connect(self.url, max_msg_size=1_048_576) as ws:
                message = await ws.receive_json()
                if message.get("type") != "auth_required":
                    raise HAWebSocketError("Unexpected authentication handshake")
                await ws.send_json({"type": "auth", "access_token": token})
                if (await ws.receive_json()).get("type") != "auth_ok":
                    raise PermissionError("HA rejected credentials")
                await ws.send_json({**command, "id": 1})
                async for frame in ws:
                    if frame.type != aiohttp.WSMsgType.TEXT:
                        raise HAWebSocketError("Connection closed; outcome unknown")
                    message = frame.json()
                    if message.get("id") != 1 or message.get("type") != "result":
                        continue
                    if not message.get("success"):
                        raise HAWebSocketError("HA rejected the request")
                    return message["result"]
                raise HAWebSocketError("Connection closed; outcome unknown")
