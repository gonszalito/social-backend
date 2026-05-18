import asyncio
import json
import time

import jwt
import pytest


def _make_token(private_pem: str, sub: str) -> str:
    now = int(time.time())
    payload = {"sub": sub, "jti": f"jti_{sub}_{now}", "exp": now + 3600}
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_websocket_room_rejects_missing_token(client):
    """WebSocket without token should be rejected with policy violation."""
    try:
        with client.websocket_connect("/ws/room/room1"):
            pytest.fail("Should have rejected connection without token")
    except Exception as e:
        # Expected: WebSocketDisconnect with code 1008 (policy violation)
        assert "WebSocketDisconnect" in str(type(e).__name__) or "1008" in str(e)


def test_websocket_room_accepts_valid_token(client, rsa_keys):
    token = _make_token(rsa_keys["private_pem"], sub="u1")
    with client.websocket_connect(f"/ws/room/room1?token={token}") as ws:
        data = {"type": "ping"}
        ws.send_json(data)
        # Close cleanly
        ws.close()


@pytest.mark.asyncio
async def test_websocket_cross_pod_sdp_message_forwarding(
    rsa_keys, fake_redis, monkeypatch, ws_base_url
):
    """
    Pass criteria: SDP offer from WebSocket on pod-1 is successfully received by WebSocket on pod-2.
    This test simulates two WebSocket clients in the same room (representing different pods)
    and verifies that messages sent by one are forwarded via Redis pub/sub to the other.
    Uses FakeRedis for in-process pub/sub and a live uvicorn server for true concurrent connections.
    """
    import websockets

    token1 = _make_token(rsa_keys["private_pem"], sub="user_pod1")
    token2 = _make_token(rsa_keys["private_pem"], sub="user_pod2")
    url_base = ws_base_url
    uri1 = f"{url_base}/ws/room/room_sdp?token={token1}"
    uri2 = f"{url_base}/ws/room/room_sdp?token={token2}"

    received: list[dict] = []

    async def receiver():
        async with websockets.connect(uri2) as ws2:
            try:
                msg = await asyncio.wait_for(ws2.recv(), timeout=2.0)
                received.append(json.loads(msg))
            except asyncio.TimeoutError:
                pass

    async def sender():
        await asyncio.sleep(0.3)  # Let receiver connect and subscribe first
        async with websockets.connect(uri1) as ws1:
            await asyncio.sleep(0.1)  # Let ws1 subscribe
            sdp_offer = {
                "type": "offer",
                "data": {
                    "sdp": "v=0\r\no=- 123 456 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n",
                    "ice_candidates": [
                        "candidate:1 1 UDP 2130706431 192.168.1.1 54321 typ host"
                    ],
                },
            }
            await ws1.send(json.dumps(sdp_offer))

    await asyncio.gather(
        asyncio.create_task(receiver()),
        asyncio.create_task(sender()),
    )

    assert len(received) > 0, "Expected SDP offer to be received by pod-2"
    offer_msg = next((m for m in received if m.get("type") == "offer"), None)
    assert offer_msg is not None, f"No offer message found in: {received}"
    assert "sdp" in offer_msg.get("data", {}), f"No SDP in offer: {offer_msg}"
    assert offer_msg["user_id"] == "user_pod1"


def test_websocket_disconnect_removes_client_from_room(client, rsa_keys, fake_redis):
    """Disconnect removes client from Redis room set (uses FakeRedis)."""
    import asyncio

    token = _make_token(rsa_keys["private_pem"], sub="u_disconnect")

    with client.websocket_connect(f"/ws/room/room_cleanup?token={token}") as ws:
        ws.send_json({"type": "ping"})
        time.sleep(0.05)

    time.sleep(0.1)  # Give cleanup time to finish

    async def check_cleanup():
        clients = await fake_redis.smembers("ws:room:room_cleanup:clients")
        return len(clients)

    client_count = asyncio.run(check_cleanup())
    assert client_count == 0, f"Expected 0 clients in room, found {client_count}"
