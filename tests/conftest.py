import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.api.auth import revoke as revoke_module
from app.api.dependencies import auth as auth_module
from app.api.nlp import ingest as nlp_ingest_module
from app.core import rate_limit as rate_limit_module
from app.core import websockets as websockets_module
from app.integration.b2_client import b2_client
from app.integration import redis_client as redis_client_module
from app.main import create_app


class FakePubSub:
    def __init__(self, redis: "FakeRedis") -> None:
        self._redis = redis
        self._channels: list[str] = []
        self._queue: "queue.Queue[dict]" = None

    async def subscribe(self, *channels: str) -> None:
        import queue

        if self._queue is None:
            self._queue = queue.Queue()
        for ch in channels:
            if ch not in self._channels:
                self._channels.append(ch)
                self._redis._subscribers.setdefault(ch, []).append(self._queue)

    async def unsubscribe(self, *channels: str) -> None:
        for ch in channels:
            if ch in self._channels:
                self._channels.remove(ch)
                if ch in self._redis._subscribers and self._queue in self._redis._subscribers[ch]:
                    self._redis._subscribers[ch].remove(self._queue)

    async def listen(self):
        import asyncio

        if self._queue is None:
            return
        while True:
            try:
                # Non-blocking check for messages
                msg = self._queue.get_nowait()
                yield msg
            except Exception:
                # Queue empty, sleep briefly
                await asyncio.sleep(0.01)

    async def close(self) -> None:
        for ch in self._channels:
            if ch in self._redis._subscribers and self._queue in self._redis._subscribers[ch]:
                self._redis._subscribers[ch].remove(self._queue)
        self._channels.clear()

    async def aclose(self) -> None:
        await self.close()


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._sets: dict[str, set[str]] = {}
        self.published: list[tuple[str, str]] = []
        self._subscribers: dict[str, list] = {}

    async def get(self, key: str) -> str | None:
        value = self._store.get(key)
        if value is None:
            return None
        data, expires_at = value
        if expires_at is not None and time.time() >= expires_at:
            self._store.pop(key, None)
            return None
        return data

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        expires_at = None if ex is None else (time.time() + ex)
        self._store[key] = (value, expires_at)

    async def incr(self, key: str) -> int:
        current = await self.get(key)
        next_val = int(current or "0") + 1
        await self.set(key, str(next_val))
        return next_val

    async def delete(self, key: str) -> int:
        removed = 0
        if key in self._store:
            self._store.pop(key, None)
            removed += 1
        if key in self._zsets:
            self._zsets.pop(key, None)
            removed += 1
        return removed

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        if channel in self._subscribers:
            for q in self._subscribers[channel]:
                try:
                    q.put_nowait({"type": "message", "data": message})
                except Exception:
                    pass
        return 1

    async def sadd(self, key: str, *members: str) -> int:
        s = self._sets.setdefault(key, set())
        added = 0
        for m in members:
            if m not in s:
                s.add(m)
                added += 1
        return added

    async def srem(self, key: str, *members: str) -> int:
        s = self._sets.get(key)
        if not s:
            return 0
        removed = 0
        for m in members:
            if m in s:
                s.discard(m)
                removed += 1
        return removed

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    def pubsub(self):
        return FakePubSub(self)

    async def zadd(self, key: str, mapping: dict[str, int | float]) -> int:
        z = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in z:
                added += 1
            z[member] = float(score)
        return added

    async def zrem(self, key: str, *members: str) -> int:
        z = self._zsets.get(key)
        if not z:
            return 0
        removed = 0
        for m in members:
            if m in z:
                z.pop(m, None)
                removed += 1
        return removed

    async def zcard(self, key: str) -> int:
        z = self._zsets.get(key)
        return 0 if not z else len(z)

    async def zrange(self, key: str, start: int, end: int, withscores: bool = False):
        z = self._zsets.get(key) or {}
        items = sorted(z.items(), key=lambda kv: (kv[1], kv[0]))
        members = [m for m, _s in items]
        if end == -1:
            sliced_items = items[start:]
            sliced_members = members[start:]
        else:
            sliced_items = items[start : end + 1]
            sliced_members = members[start : end + 1]
        if withscores:
            return [(m, s) for (m, s) in sliced_items]
        return sliced_members

    async def zremrangebyscore(self, key: str, min_score: int | float, max_score: int | float) -> int:
        z = self._zsets.get(key)
        if not z:
            return 0
        to_remove = [m for m, score in z.items() if float(min_score) <= score <= float(max_score)]
        for member in to_remove:
            z.pop(member, None)
        return len(to_remove)

    async def expire(self, key: str, seconds: int) -> bool:
        value = self._store.get(key)
        if value is not None:
            data, _ = value
            self._store[key] = (data, time.time() + int(seconds))
        return True

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False):
        z = self._zsets.get(key) or {}
        items = sorted(z.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
        if end == -1:
            sliced = items[start:]
        else:
            sliced = items[start : end + 1]
        if withscores:
            return [(m, s) for (m, s) in sliced]
        return [m for (m, _s) in sliced]


@pytest.fixture()
def rsa_keys() -> dict[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return {"private_pem": private_pem, "public_pem": public_pem}


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    r = FakeRedis()

    monkeypatch.setattr(
        auth_module,
        "get_redis",
        lambda: r,
        raising=True,
    )
    monkeypatch.setattr(
        revoke_module,
        "get_redis",
        lambda: r,
        raising=True,
    )
    monkeypatch.setattr(
        websockets_module,
        "get_redis",
        lambda: r,
        raising=True,
    )
    monkeypatch.setattr(
        rate_limit_module,
        "get_redis",
        lambda: r,
        raising=True,
    )
    return r


def _make_app(rsa_keys: dict[str, str], fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch):
    """Create app with test patches (FakeRedis, fake RSA key)."""
    async def _fake_load_key() -> str:
        return rsa_keys["public_pem"]

    monkeypatch.setattr(b2_client, "load_iam_public_key_pem", _fake_load_key, raising=True)
    app = create_app()
    app.dependency_overrides[redis_client_module.get_redis] = lambda: fake_redis
    return app


@pytest.fixture()
def client(rsa_keys: dict[str, str], fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch):
    app = _make_app(rsa_keys, fake_redis, monkeypatch)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def live_app(rsa_keys: dict[str, str], fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch):
    """App instance with test patches, for use with a live server."""
    return _make_app(rsa_keys, fake_redis, monkeypatch)


@pytest.fixture()
def ws_base_url(live_app):
    """Run app with uvicorn in a background thread, yield ws://127.0.0.1:port."""
    import socket
    import threading

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(live_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def run():
        import asyncio
        asyncio.run(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    # Wait for server to be ready
    for _ in range(50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(("127.0.0.1", port))
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    yield f"ws://127.0.0.1:{port}"


@pytest.fixture()
def http_base_url(live_app):
    """Run app with uvicorn in a background thread, yield http://127.0.0.1:port."""
    import socket
    import threading

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(live_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def run():
        import asyncio

        asyncio.run(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(("127.0.0.1", port))
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"

