import hashlib
import hmac
import json
import os
import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.integration.redis_client import get_redis

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class StripeEvent(BaseModel):
    id: str
    type: str
    data: dict
    created: int | None = None


def verify_stripe_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """Verify Stripe webhook signature using HMAC-SHA256.
    
    Args:
        payload: Raw request body as bytes
        signature: Stripe-Signature header value
        secret: Stripe webhook secret
        
    Returns:
        True if signature is valid, False otherwise
    """
    if not signature or not secret:
        return False
    
    # Parse the signature header
    # Format: "t=timestamp,v1=signature"
    sig_parts = {}
    for part in signature.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            sig_parts[key] = value
    
    timestamp = sig_parts.get("t")
    expected_sig = sig_parts.get("v1")
    
    if not timestamp or not expected_sig:
        return False
    
    # Check timestamp is within 5 minutes (300 seconds) to prevent replay attacks
    try:
        sig_timestamp = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - sig_timestamp) > 300:
            return False
    except (ValueError, TypeError):
        return False
    
    # Construct the signed payload
    signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
    
    # Compute the expected signature
    computed_sig = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    # Compare signatures using constant-time comparison
    return hmac.compare_digest(computed_sig, expected_sig)


async def handle_checkout_completed(event: StripeEvent) -> None:
    """Handle checkout.session.completed event.
    
    Updates user tier and publishes event to Redis pub/sub.
    """
    redis = get_redis()
    
    # Extract session data
    session = event.data.get("object", {})
    customer_id = session.get("customer")
    metadata = session.get("metadata", {})
    user_id = metadata.get("user_id")
    tier = metadata.get("tier", "premium")
    
    if not user_id:
        # Log warning but don't fail the webhook
        return
    
    # Update user tier in Redis
    await redis.set(f"user:{user_id}:tier", tier)
    
    # Publish event for downstream consumers
    event_data = json.dumps({
        "event_type": "tier_updated",
        "user_id": user_id,
        "tier": tier,
        "customer_id": customer_id,
        "timestamp": int(time.time()),
    })
    await redis.publish("subscription:events", event_data)


async def handle_subscription_deleted(event: StripeEvent) -> None:
    """Handle customer.subscription.deleted event.
    
    Downgrades user to basic tier.
    """
    redis = get_redis()
    
    # Extract subscription data
    subscription = event.data.get("object", {})
    metadata = subscription.get("metadata", {})
    user_id = metadata.get("user_id")
    
    if not user_id:
        return
    
    # Downgrade to basic tier
    await redis.set(f"user:{user_id}:tier", "basic")
    
    # Publish downgrade event
    event_data = json.dumps({
        "event_type": "tier_downgraded",
        "user_id": user_id,
        "tier": "basic",
        "reason": "subscription_deleted",
        "timestamp": int(time.time()),
    })
    await redis.publish("subscription:events", event_data)


async def handle_charge_refunded(event: StripeEvent) -> None:
    """Handle charge.refunded event.
    
    Downgrades user to basic tier.
    """
    redis = get_redis()
    
    # Extract charge data
    charge = event.data.get("object", {})
    metadata = charge.get("metadata", {})
    user_id = metadata.get("user_id")
    
    if not user_id:
        return
    
    # Downgrade to basic tier
    await redis.set(f"user:{user_id}:tier", "basic")
    
    # Publish downgrade event
    event_data = json.dumps({
        "event_type": "tier_downgraded",
        "user_id": user_id,
        "tier": "basic",
        "reason": "charge_refunded",
        "timestamp": int(time.time()),
    })
    await redis.publish("subscription:events", event_data)


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict:
    """Handle Stripe webhook events.
    
    - Verifies signature using HMAC-SHA256
    - Implements idempotency using Redis
    - Handles: checkout.session.completed, customer.subscription.deleted, charge.refunded
    - Returns 400 on invalid signature (no DB/Redis writes)
    - Returns 200 on duplicate events (no reprocessing)
    """
    # Get webhook secret from environment
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook secret not configured",
        )
    
    # Read raw body for signature verification
    raw_body = await request.body()
    
    # Verify signature FIRST (before any processing)
    if not verify_stripe_signature(raw_body, stripe_signature, webhook_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )
    
    # Parse event
    try:
        event_dict = json.loads(raw_body)
        event = StripeEvent.model_validate(event_dict)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid event payload: {e!s}",
        )
    
    # Idempotency check using Redis
    redis = get_redis()
    idempotency_key = f"stripe_event:{event.id}"
    
    try:
        # Try to set the key with NX flag (only if not exists)
        # Using 24 hours expiry
        existing = await redis.get(idempotency_key)
        if existing:
            # Event already processed, return success without reprocessing
            return {
                "status": "duplicate",
                "event_id": event.id,
                "message": "Event already processed",
            }
        
        # Mark event as being processed
        await redis.set(idempotency_key, "processed", ex=86400)  # 24 hours
        
    except (RedisConnectionError, RedisTimeoutError) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis unavailable: {e!s}",
        )
    
    # Handle the event based on type
    try:
        if event.type == "checkout.session.completed":
            await handle_checkout_completed(event)
        elif event.type == "customer.subscription.deleted":
            await handle_subscription_deleted(event)
        elif event.type == "charge.refunded":
            await handle_charge_refunded(event)
        # Other event types are acknowledged but not processed
        
    except (RedisConnectionError, RedisTimeoutError) as e:
        # If processing fails, delete the idempotency key so retry can happen
        try:
            await redis.delete(idempotency_key)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Event processing failed: {e!s}",
        )
    
    return {
        "status": "success",
        "event_id": event.id,
        "event_type": event.type,
    }
