import json
import os
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from starlette.responses import JSONResponse

from app.integration import redis_client
from app.integration.b2_client import b2_client
from app.integration.postgres_client import postgres_client
from app.services.ai_wrapper import enrich_nlp_batch


router = APIRouter(prefix="/api/v1/nlp", tags=["nlp"])


class RecipeIngest(BaseModel):
    recipe_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class IngestRequest(BaseModel):
    batch_id: str = Field(min_length=1)
    recipes: list[RecipeIngest] = Field(min_length=1)


class IngestResponse(BaseModel):
    batch_id: str
    status: Literal["staged", "processed", "duplicate"]
    ai_result: dict[str, Any] | None = None
    enriched_recipe_count: int | None = Field(
        default=None,
        description="Recipes included in enrichment (flag ON → 200); omitted/null for 202 staged.",
    )


async def _is_flag_on(flag_value: str | None) -> bool:
    if flag_value is None:
        return False
    return flag_value.strip().lower() in {"1", "true", "on", "yes", "enabled"}


def _nlp_processing_force_on_from_env() -> bool:
    """Local/testing: treat ``gobig_nlp_processing`` as ON without reading Redis. Do not use in prod."""
    v = os.getenv("GOBIG_NLP_PROCESSING_FORCE_ON", "").strip().lower()
    return v in {"1", "true", "yes", "on", "enabled"}


async def _store_idempotency(redis, key: str, status_code: int, body: dict[str, Any]) -> None:
    await redis.set(key, json.dumps({"status_code": status_code, "body": body}), ex=24 * 3600)


async def _read_idempotency(redis, key: str) -> tuple[int, dict[str, Any]] | None:
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return int(parsed["status_code"]), dict(parsed["body"])
    except Exception:
        return None


@router.post("/ingest", response_model=IngestResponse)
async def ingest_nlp(
    req: IngestRequest,
    redis=Depends(redis_client.get_redis),
) -> IngestResponse:
    idem_key = f"nlp_batch:{req.batch_id}"
    try:
        existing = await _read_idempotency(redis, idem_key)
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")

    processing_on = _nlp_processing_force_on_from_env()
    if not processing_on:
        try:
            flag = await redis.get("gobig_nlp_processing")
        except (RedisConnectionError, RedisTimeoutError) as e:
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")
        processing_on = await _is_flag_on(flag)

    if existing is not None:
        prev_status, body = existing
        # Previously 202 staged; now flag-ON (or FORCE_ON): run full process path (Postgres + wrapper).
        if not (prev_status == status.HTTP_202_ACCEPTED and processing_on):
            body = dict(body)
            body["status"] = "duplicate"
            return IngestResponse.model_validate(body)

    if not processing_on:
        # Flag OFF: stage to B2 and return 202 (and ensure no DB upserts happen).
        await b2_client.stage_nlp_batch(req.batch_id, req.model_dump())
        body = {
            "batch_id": req.batch_id,
            "status": "staged",
            "ai_result": None,
            "enriched_recipe_count": None,
        }
        try:
            await _store_idempotency(redis, idem_key, status.HTTP_202_ACCEPTED, body)
        except (RedisConnectionError, RedisTimeoutError) as e:
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=body)

    # Flag ON: upsert to Postgres when enabled; keep Redis counter fallback when disabled.
    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                """
                INSERT INTO nlp_ingest_batches (batch_id, status, ai_result_json)
                VALUES (%s, %s, NULL)
                ON CONFLICT (batch_id)
                DO UPDATE SET
                  status = EXCLUDED.status,
                  updated_at = NOW()
                """,
                (req.batch_id, "processing"),
            )
            for recipe in req.recipes:
                await postgres_client.execute(
                    """
                    INSERT INTO nlp_ingest_recipes (batch_id, recipe_id, text, confidence)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (batch_id, recipe_id)
                    DO UPDATE SET
                      text = EXCLUDED.text,
                      confidence = EXCLUDED.confidence,
                      updated_at = NOW()
                    """,
                    (req.batch_id, recipe.recipe_id, recipe.text, recipe.confidence),
                )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Postgres unavailable: {e!s}")
    else:
        try:
            await redis.incr("nlp_db_upsert_count")
        except (RedisConnectionError, RedisTimeoutError) as e:
            raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")

    batch_context: dict[str, Any] = {
        "batch_id": req.batch_id,
        "recipes": [r.model_dump() for r in req.recipes],
    }
    joined = "\n".join(r.text for r in req.recipes)
    if len(joined) > 8000:
        batch_context["truncated"] = True

    try:
        ai_result = await enrich_nlp_batch(batch_context, max_tokens=2000)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"AI wrapper call failed: {e!s}")

    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                """
                UPDATE nlp_ingest_batches
                SET status = %s, ai_result_json = %s::jsonb, updated_at = NOW()
                WHERE batch_id = %s
                """,
                ("processed", json.dumps(ai_result), req.batch_id),
            )
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Postgres unavailable: {e!s}")

    enriched_count = len(req.recipes)
    body = {
        "batch_id": req.batch_id,
        "status": "processed",
        "ai_result": ai_result,
        "enriched_recipe_count": enriched_count,
    }
    try:
        await _store_idempotency(redis, idem_key, status.HTTP_200_OK, body)
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e!s}")
    return IngestResponse.model_validate(body)

