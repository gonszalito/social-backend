from __future__ import annotations

import base64
import json
import os
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.api.dependencies.auth import AuthClaims, require_auth
from app.integration.postgres_client import postgres_client
from app.integration.redis_client import get_redis


router = APIRouter(prefix="/social", tags=["social"])

SOCIAL_FEED_MAX_ITEMS = int(os.environ.get("GOBIG_SOCIAL_FEED_MAX_ITEMS", "500"))
SOCIAL_EVENT_TTL_SECONDS = int(os.environ.get("GOBIG_SOCIAL_EVENT_TTL_SECONDS", str(7 * 24 * 3600)))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _following_key(user_id: str) -> str:
    return f"social:following:{user_id}"


def _followers_key(user_id: str) -> str:
    return f"social:followers:{user_id}"


def _events_key(user_id: str) -> str:
    return f"social:events:{user_id}"


def _feed_key(user_id: str) -> str:
    return f"social:feed:{user_id}"


async def _trim_feed_index(redis: Redis, user_id: str, max_items: int = SOCIAL_FEED_MAX_ITEMS) -> None:
    feed_key = _feed_key(user_id)
    await redis.zremrangebyrank(feed_key, 0, -(max_items + 1))


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode((cursor + padding).encode("utf-8")).decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("cursor must decode to object")
    return parsed


class RecipeShareRequest(BaseModel):
    recipe_id: str = Field(min_length=1)


class FeedItem(BaseModel):
    event_id: str
    actor_user_id: str
    type: str
    ts_ms: int
    data: dict[str, Any]


class FeedResponse(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None = None


class PotluckCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class PotluckInviteRequest(BaseModel):
    potluck_id: str = Field(min_length=1)
    invitee_user_id: str = Field(min_length=1)


@router.post("/follow/{target_user_id}")
async def follow_user(
    target_user_id: str,
    claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
) -> dict:
    if not claims.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")
    if target_user_id == claims.sub:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot follow self")

    ts = _now_ms()
    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                """
                INSERT INTO social_follows (follower_user_id, target_user_id)
                VALUES (%s, %s)
                ON CONFLICT (follower_user_id, target_user_id) DO NOTHING
                """,
                (claims.sub, target_user_id),
            )
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Postgres unavailable: {e!s}")
    else:
        await redis.incr("social_db_follow_writes")

    # Keep Redis writes batched after the DB write succeeds.
    if hasattr(redis, "pipeline"):
        async with redis.pipeline(transaction=False) as pipe:
            pipe.zadd(_following_key(claims.sub), {target_user_id: ts})
            pipe.zadd(_followers_key(target_user_id), {claims.sub: ts})
            await pipe.execute()
    else:
        await redis.zadd(_following_key(claims.sub), {target_user_id: ts})
        await redis.zadd(_followers_key(target_user_id), {claims.sub: ts})

    # Backfill recent events from the newly followed user into the feed index.
    recent_events = await redis.zrevrange(_events_key(target_user_id), 0, 199, withscores=True)
    if recent_events:
        if hasattr(redis, "pipeline"):
            async with redis.pipeline(transaction=False) as pipe:
                for event_id, score in recent_events:
                    pipe.zadd(_feed_key(claims.sub), {str(event_id): int(score)})
                await pipe.execute()
        else:
            for event_id, score in recent_events:
                await redis.zadd(_feed_key(claims.sub), {str(event_id): int(score)})
        await _trim_feed_index(redis, claims.sub)

    return {"ok": True}


@router.delete("/unfollow/{target_user_id}")
async def unfollow_user(
    target_user_id: str,
    claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
) -> dict:
    if not claims.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")
    if target_user_id == claims.sub:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot unfollow self")

    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                "DELETE FROM social_follows WHERE follower_user_id = %s AND target_user_id = %s",
                (claims.sub, target_user_id),
            )
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Postgres unavailable: {e!s}")
    else:
        await redis.incr("social_db_unfollow_writes")

    if hasattr(redis, "pipeline"):
        async with redis.pipeline(transaction=False) as pipe:
            pipe.zrem(_following_key(claims.sub), target_user_id)
            pipe.zrem(_followers_key(target_user_id), claims.sub)
            await pipe.execute()
    else:
        await redis.zrem(_following_key(claims.sub), target_user_id)
        await redis.zrem(_followers_key(target_user_id), claims.sub)

    # Remove recent events from this author to keep the feed index aligned.
    recent_events = await redis.zrevrange(_events_key(target_user_id), 0, 999)
    if recent_events:
        await redis.zrem(_feed_key(claims.sub), *[str(event_id) for event_id in recent_events])

    return {"ok": True}


@router.post("/recipe-share")
async def recipe_share(
    req: RecipeShareRequest,
    claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
) -> dict:
    if not claims.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

    ts = _now_ms()
    event_id = f"rs_{claims.sub}_{ts}"
    item = FeedItem(
        event_id=event_id,
        actor_user_id=claims.sub,
        type="recipe_share",
        ts_ms=ts,
        data={"recipe_id": req.recipe_id},
    )
    await redis.zadd(_events_key(claims.sub), {event_id: ts})
    await redis.set(f"social:event:{event_id}", item.model_dump_json(), ex=SOCIAL_EVENT_TTL_SECONDS)

    # Fan out event IDs into owner + follower feed indexes.
    followers = await redis.zrange(_followers_key(claims.sub), 0, -1)
    recipient_ids = {claims.sub, *[str(follower_id) for follower_id in followers]}
    if hasattr(redis, "pipeline"):
        async with redis.pipeline(transaction=False) as pipe:
            for recipient_id in recipient_ids:
                pipe.zadd(_feed_key(recipient_id), {event_id: ts})
                pipe.zremrangebyrank(_feed_key(recipient_id), 0, -(SOCIAL_FEED_MAX_ITEMS + 1))
            await pipe.execute()
    else:
        for recipient_id in recipient_ids:
            await redis.zadd(_feed_key(recipient_id), {event_id: ts})
            await redis.zremrangebyrank(_feed_key(recipient_id), 0, -(SOCIAL_FEED_MAX_ITEMS + 1))

    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                """
                INSERT INTO social_events (event_id, actor_user_id, event_type, ts_ms, payload_json)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (event_id, claims.sub, "recipe_share", ts, json.dumps({"recipe_id": req.recipe_id})),
            )
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Postgres unavailable: {e!s}")
    else:
        await redis.incr("social_db_event_writes")
    return {"ok": True, "event_id": event_id}


@router.get("/feed", response_model=FeedResponse)
async def get_feed(
    claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
    cursor: str | None = Query(default=None),
) -> FeedResponse:
    if not claims.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

    limit = 20
    cursor_ts: int | None = None
    cursor_event_id: str | None = None
    if cursor:
        try:
            decoded = _decode_cursor(cursor)
            cursor_ts = int(decoded.get("ts_ms"))
            cursor_event_id = str(decoded.get("event_id"))
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor")

    page_size = limit + 1
    feed_events = await redis.zrevrange(_feed_key(claims.sub), 0, SOCIAL_FEED_MAX_ITEMS - 1, withscores=True)
    if not feed_events:
        return FeedResponse(items=[], next_cursor=None)

    candidates: list[tuple[int, str]] = [(int(score), str(event_id)) for event_id, score in feed_events]
    page: list[tuple[int, str]] = []
    for ts, eid in candidates:
        if cursor_ts is not None and cursor_event_id is not None:
            if ts > cursor_ts or (ts == cursor_ts and eid >= cursor_event_id):
                continue
        page.append((ts, eid))
        if len(page) == page_size:
            break

    items: list[FeedItem] = []
    page_slice = page[:limit]
    event_keys = [f"social:event:{eid}" for _ts, eid in page_slice]
    raws: list[Any]
    if event_keys and hasattr(redis, "mget"):
        raws = await redis.mget(*event_keys)
    else:
        raws = []
        for event_key in event_keys:
            raws.append(await redis.get(event_key))
    for (ts, eid), raw in zip(page_slice, raws):
        if raw:
            try:
                items.append(FeedItem.model_validate_json(raw))
                continue
            except Exception:
                pass
        # Fallback: minimal item if cache is missing/corrupt.
        items.append(
            FeedItem(
                event_id=eid,
                actor_user_id="",
                type="unknown",
                ts_ms=ts,
                data={},
            )
        )

    next_cursor = None
    if len(page) > limit:
        last_ts, last_eid = page[limit - 1]
        next_cursor = _encode_cursor({"ts_ms": last_ts, "event_id": last_eid})

    return FeedResponse(items=items, next_cursor=next_cursor)


@router.get("/profile/{user_id}")
async def get_profile(
    user_id: str,
    response: Response,
    _claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
) -> dict:
    response.headers["Cache-Control"] = "public, max-age=300"

    followers = await redis.zcard(_followers_key(user_id))
    following = await redis.zcard(_following_key(user_id))
    return {"user_id": user_id, "followers": int(followers), "following": int(following)}


@router.post("/potluck/create")
async def potluck_create(
    req: PotluckCreateRequest,
    claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
) -> dict:
    if not claims.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

    ts = _now_ms()
    potluck_id = f"p_{claims.sub}_{ts}"
    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                """
                INSERT INTO social_potlucks (potluck_id, title, creator_user_id, ts_ms)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (potluck_id) DO NOTHING
                """,
                (potluck_id, req.title, claims.sub, ts),
            )
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Postgres unavailable: {e!s}")
    else:
        await redis.incr("social_db_potluck_writes")
    await redis.publish("social:potluck", json.dumps({"type": "created", "potluck_id": potluck_id}))
    return {"ok": True, "potluck_id": potluck_id, "title": req.title}


@router.post("/potluck/invite")
async def potluck_invite(
    req: PotluckInviteRequest,
    claims: Annotated[AuthClaims, Depends(require_auth)],
    redis=Depends(get_redis),
) -> dict:
    if not claims.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

    if postgres_client.enabled:
        try:
            await postgres_client.execute(
                """
                INSERT INTO social_potluck_invites (potluck_id, inviter_user_id, invitee_user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (potluck_id, invitee_user_id) DO NOTHING
                """,
                (req.potluck_id, claims.sub, req.invitee_user_id),
            )
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Postgres unavailable: {e!s}")
    else:
        await redis.incr("social_db_potluck_invites")
    await redis.publish(
        "social:potluck",
        json.dumps(
            {
                "type": "invite",
                "potluck_id": req.potluck_id,
                "inviter_user_id": claims.sub,
                "invitee_user_id": req.invitee_user_id,
            }
        ),
    )
    return {"ok": True}

