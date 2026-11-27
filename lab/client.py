"""Command-line client for the local synthetic lab; never uses HA_TOKEN or .env."""

import argparse
import asyncio
import json
from pathlib import Path

from aiohttp import ClientSession

from service.websocket import HAWebSocketClient


async def run(args):
    credentials = Path(__file__).resolve().parents[1] / ".ha-lab/access.json"
    if not credentials.exists():
        raise SystemExit("Start .ha-venv/bin/python -m lab.run --serve in another terminal.")
    token = json.loads(credentials.read_text())["token"]
    command = {
        "type": "polish_context_voice/process",
        "text": args.text,
        "source_area": args.area,
        "dry_run": not args.execute,
    }
    if args.conversation_id:
        command["conversation_id"] = args.conversation_id
    async with ClientSession() as session:
        # Fixed loopback endpoint, independent of any user's HA environment variables.
        result = await HAWebSocketClient(session, "ws://127.0.0.1:18123/api/websocket").request(
            token, command
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--area", default="living_room")
    parser.add_argument("--conversation-id")
    parser.add_argument("--execute", action="store_true", help="Execute on virtual devices")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
