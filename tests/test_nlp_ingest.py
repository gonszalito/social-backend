import asyncio

from app.api.nlp import ingest as nlp_ingest_module


class _FakePostgresClient:
    """Captures execute() calls when ingest uses the Postgres path (flag ON)."""

    def __init__(self) -> None:
        self.enabled = True
        self.executes: list[tuple[str, tuple]] = []

    async def execute(self, query: str, params: tuple | None = None) -> None:
        self.executes.append((query, params or ()))


def test_nlp_ingest_staged_then_force_on_reprocesses_not_duplicate(client, fake_redis, monkeypatch):
    """202 idempotency must not block flag-ON when user enables FORCE_ON or Redis flag after staging."""
    r1 = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b-reprocess-1",
            "recipes": [{"recipe_id": "r1", "text": "hello", "confidence": 0.9}],
        },
    )
    assert r1.status_code == 202
    assert r1.json()["status"] == "staged"

    monkeypatch.setenv("GOBIG_NLP_PROCESSING_FORCE_ON", "1")
    import httpx

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    async def _fake_post(self, url, json=None):  # noqa: A002
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    r2 = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b-reprocess-1",
            "recipes": [{"recipe_id": "r1", "text": "hello", "confidence": 0.9}],
        },
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "processed"
    assert r2.json()["enriched_recipe_count"] == 1


def test_nlp_ingest_flag_off_returns_202_and_no_db_upsert(client, fake_redis):
    # flag off by default
    r = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b1",
            "recipes": [{"recipe_id": "r1", "text": "hello", "confidence": 0.9}],
        },
    )
    assert r.status_code == 202
    assert r.json()["status"] == "staged"
    assert r.json().get("enriched_recipe_count") is None
    assert asyncio.run(fake_redis.get("nlp_db_upsert_count")) is None


def test_nlp_ingest_missing_confidence_is_422(client):
    r = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b2",
            "recipes": [{"recipe_id": "r1", "text": "hello"}],
        },
    )
    assert r.status_code == 422


def test_nlp_ingest_force_on_env_skips_redis_flag(client, fake_redis, monkeypatch):
    """GOBIG_NLP_PROCESSING_FORCE_ON=1 uses flag-ON path without Redis gobig_nlp_processing key."""
    monkeypatch.setenv("GOBIG_NLP_PROCESSING_FORCE_ON", "1")

    import httpx

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "via": "force_on"}

    async def _fake_post(self, url, json=None):  # noqa: A002
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    r = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b-force-env",
            "recipes": [{"recipe_id": "r1", "text": "hello", "confidence": 0.9}],
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processed"
    assert r.json()["enriched_recipe_count"] == 1
    assert r.json()["ai_result"]["via"] == "force_on"


def test_nlp_ingest_flag_on_calls_ai_and_sets_idempotency(client, fake_redis, monkeypatch):
    original_get = fake_redis.get

    async def _fake_get(key: str):
        if key == "gobig_nlp_processing":
            return "1"
        return await original_get(key)

    monkeypatch.setattr(fake_redis, "get", _fake_get, raising=True)

    # stub httpx call by monkeypatching AsyncClient.post
    import httpx

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    async def _fake_post(self, url, json=None):  # noqa: A002
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    r1 = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b3",
            "recipes": [{"recipe_id": "r1", "text": "hello", "confidence": 0.9}],
        },
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "processed"
    assert r1.json()["ai_result"]["ok"] is True
    assert r1.json()["enriched_recipe_count"] == 1

    # idempotent duplicate should return duplicate status without reprocessing
    r2 = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "b3",
            "recipes": [{"recipe_id": "r1", "text": "hello", "confidence": 0.9}],
        },
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["enriched_recipe_count"] == 1


def test_nlp_ingest_flag_on_with_postgres_writes_batch_recipes_and_completion(
    client, fake_redis, monkeypatch
):
    """Flag ON + postgres enabled: upsert batch + recipes, then update batch after AI."""
    original_get = fake_redis.get

    async def _fake_get(key: str):
        if key == "gobig_nlp_processing":
            return "1"
        return await original_get(key)

    monkeypatch.setattr(fake_redis, "get", _fake_get, raising=True)

    import httpx

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"enriched": True}

    async def _fake_post(self, url, json=None):  # noqa: A002
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post, raising=True)

    fake_pg = _FakePostgresClient()
    monkeypatch.setattr(nlp_ingest_module, "postgres_client", fake_pg, raising=True)

    r = client.post(
        "/api/v1/nlp/ingest",
        json={
            "batch_id": "pg-batch-1",
            "recipes": [
                {"recipe_id": "r1", "text": "hello", "confidence": 0.9},
                {"recipe_id": "r2", "text": "world", "confidence": 0.8},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processed"
    assert r.json()["enriched_recipe_count"] == 2

    assert len(fake_pg.executes) == 4  # 1 batch + 2 recipes + 1 completion update
    batch_sql, batch_params = fake_pg.executes[0]
    assert "INSERT INTO nlp_ingest_batches" in batch_sql
    assert batch_params[0] == "pg-batch-1"
    assert batch_params[1] == "processing"

    for i, recipe_id in enumerate(("r1", "r2"), start=1):
        sql, params = fake_pg.executes[i]
        assert "INSERT INTO nlp_ingest_recipes" in sql
        assert params[0] == "pg-batch-1"
        assert params[1] == recipe_id

    upd_sql, upd_params = fake_pg.executes[3]
    assert "UPDATE nlp_ingest_batches" in upd_sql
    assert upd_params[0] == "processed"
    assert upd_params[2] == "pg-batch-1"

