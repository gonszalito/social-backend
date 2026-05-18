#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection


def _build_ws_url(base_ws_url: str, room_id: str, token: str) -> str:
    normalized = base_ws_url.rstrip("/")
    return f"{normalized}/ws/room/{room_id}?token={token}"


async def _listen_forever(connection: ClientConnection, client_name: str) -> None:
    try:
        async for raw in connection:
            print(f"[{client_name}] received: {raw}")
    except websockets.ConnectionClosed as exc:
        print(f"[{client_name}] closed: code={exc.code} reason={exc.reason}")


async def _run_listener(url: str, client_name: str) -> None:
    async with websockets.connect(url) as connection:
        print(f"[{client_name}] connected to {url}")
        await _listen_forever(connection, client_name)


async def _run_sender(
    url: str,
    client_name: str,
    message_type: str,
    message_text: str,
    wait_for_response_seconds: float,
) -> None:
    async with websockets.connect(url) as connection:
        payload: dict[str, Any] = {
            "type": message_type,
            "data": {
                "message": message_text,
                "nonce": str(uuid.uuid4()),
            },
        }
        await connection.send(json.dumps(payload))
        print(f"[{client_name}] sent: {json.dumps(payload)}")
        if wait_for_response_seconds <= 0:
            return

        try:
            raw = await asyncio.wait_for(connection.recv(), timeout=wait_for_response_seconds)
            print(f"[{client_name}] received: {raw}")
        except asyncio.TimeoutError:
            print(f"[{client_name}] no response within {wait_for_response_seconds:.1f}s")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Example WebSocket client for /ws/room/{room_id} testing."
    )
    parser.add_argument(
        "--base-ws-url",
        default="ws://127.0.0.1:8000",
        help="WebSocket server origin without trailing slash.",
    )
    parser.add_argument("--room-id", required=True, help="Room identifier.")
    parser.add_argument(
        "--token",
        required=True,
        help="JWT token passed as query parameter `token`.",
    )
    parser.add_argument(
        "--client-name",
        default="client",
        help="Readable label in terminal logs.",
    )
    parser.add_argument(
        "--mode",
        choices=("listen", "send"),
        default="listen",
        help="listen: wait for messages, send: send one message then optionally await response.",
    )
    parser.add_argument(
        "--type",
        default="sdp_offer",
        help="Message type for send mode (example: sdp_offer or ice_candidate).",
    )
    parser.add_argument(
        "--message",
        default="hello-from-example-client",
        help="Message payload text for send mode.",
    )
    parser.add_argument(
        "--wait-for-response-seconds",
        type=float,
        default=3.0,
        help="How long send mode waits for one response before exiting (0 to disable).",
    )
    return parser.parse_args()


async def _async_main() -> None:
    args = _parse_args()
    url = _build_ws_url(args.base_ws_url, args.room_id, args.token)
    if args.mode == "listen":
        await _run_listener(url, args.client_name)
        return
    await _run_sender(
        url=url,
        client_name=args.client_name,
        message_type=args.type,
        message_text=args.message,
        wait_for_response_seconds=args.wait_for_response_seconds,
    )


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
