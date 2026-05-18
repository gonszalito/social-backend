from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.dependencies.auth import auth_service
from app.integration.redis_client import get_redis


router = APIRouter(tags=["websockets"])
logger = logging.getLogger(__name__)


def _room_clients_key(room_id: str) -> str:
    return f"ws:room:{room_id}:clients"


def _room_channel(room_id: str) -> str:
    return f"ws:room:{room_id}:channel"


async def _authenticate_websocket(token: str | None) -> str:
    if not token:
        return ""
    try:
        claims = await auth_service.verify_bearer_token(f"Bearer {token}")
        return claims.sub or ""
    except Exception:
        return ""


@router.websocket("/ws/room/{room_id}")
async def websocket_room(websocket: WebSocket, room_id: str, token: str | None = None):
    user_id = await _authenticate_websocket(token)
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    redis = get_redis()

    client_id = f"{user_id}_{id(websocket)}"
    await redis.sadd(_room_clients_key(room_id), client_id)

    pubsub = redis.pubsub()
    await pubsub.subscribe(_room_channel(room_id))
    logger.debug("WebSocket client subscribed: room=%s client_id=%s", room_id, client_id)

    async def _listen_pubsub():
        try:
            while True:
                try:
                    # Avoid task death on Redis socket timeouts by polling with a short timeout.
                    if hasattr(pubsub, "get_message"):
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=False,
                            timeout=1.0,
                        )
                    else:
                        message = None
                        async for candidate in pubsub.listen():
                            message = candidate
                            break
                    if not message or message.get("type") != "message":
                        await asyncio.sleep(0.01)
                        continue

                    data = message.get("data")
                    parsed = json.loads(data)
                    logger.debug(
                        "WebSocket pubsub message: room=%s sender=%s receiver=%s type=%s",
                        room_id,
                        parsed.get("sender_id"),
                        client_id,
                        parsed.get("type"),
                    )
                    if parsed.get("sender_id") != client_id:
                        await websocket.send_json(parsed)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.debug("WebSocket pubsub listen retry after error: %s", exc)
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass

    listener_task = asyncio.create_task(_listen_pubsub())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                await websocket.send_json({"error": "Invalid JSON"})
                continue

            envelope = {
                "sender_id": client_id,
                "user_id": user_id,
                "type": payload.get("type", "unknown"),
                "data": payload.get("data", {}),
            }
            listeners = await redis.publish(_room_channel(room_id), json.dumps(envelope))
            logger.debug(
                "WebSocket published: room=%s client_id=%s listeners=%s type=%s",
                room_id,
                client_id,
                listeners,
                envelope.get("type"),
            )

    except WebSocketDisconnect:
        pass
    finally:
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        await pubsub.unsubscribe(_room_channel(room_id))
        close_fn = getattr(pubsub, "aclose", pubsub.close)
        await close_fn()
        await redis.srem(_room_clients_key(room_id), client_id)
