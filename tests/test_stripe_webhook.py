import hashlib
import hmac
import json
import time

import pytest

from app.api.webhooks import stripe as stripe_module


def create_stripe_signature(payload: str, secret: str, timestamp: int | None = None) -> str:
    """Create a valid Stripe signature for testing."""
    if timestamp is None:
        timestamp = int(time.time())
    
    signed_payload = f"{timestamp}.{payload}"
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    return f"t={timestamp},v1={signature}"


def test_stripe_webhook_missing_signature(client, monkeypatch):
    """Test that requests without Stripe-Signature header return 400."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test123")
    
    payload = {
        "id": "evt_test_001",
        "type": "checkout.session.completed",
        "data": {"object": {}},
    }
    
    # Send request without Stripe-Signature header
    response = client.post(
        "/webhooks/stripe",
        json=payload,
    )
    
    assert response.status_code == 400
    assert "Invalid signature" in response.json()["detail"]


def test_stripe_webhook_invalid_signature(client, monkeypatch):
    """Test that requests with invalid signature return 400."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test123")
    
    payload = {
        "id": "evt_test_002",
        "type": "checkout.session.completed",
        "data": {"object": {}},
    }
    
    # Send request with invalid signature
    response = client.post(
        "/webhooks/stripe",
        json=payload,
        headers={"Stripe-Signature": "t=123456789,v1=invalid_signature"},
    )
    
    assert response.status_code == 400
    assert "Invalid signature" in response.json()["detail"]


def test_stripe_webhook_expired_timestamp(client, monkeypatch, fake_redis):
    """Test that requests with expired timestamp return 400."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test123")
    
    payload = {
        "id": "evt_test_003",
        "type": "checkout.session.completed",
        "data": {"object": {}},
    }
    
    # Create signature with old timestamp (more than 5 minutes ago)
    old_timestamp = int(time.time()) - 400
    payload_str = json.dumps(payload)
    signature = create_stripe_signature(payload_str, "whsec_test123", old_timestamp)
    
    # Patch the stripe module to use our fake_redis
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    response = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response.status_code == 400
    assert "Invalid signature" in response.json()["detail"]


def test_stripe_webhook_valid_signature_checkout_completed(client, monkeypatch, fake_redis):
    """Test checkout.session.completed with valid signature."""
    secret = "whsec_test123"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    
    payload = {
        "id": "evt_test_checkout_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_test123",
                "metadata": {
                    "user_id": "user_001",
                    "tier": "premium",
                },
            }
        },
    }
    
    payload_str = json.dumps(payload)
    signature = create_stripe_signature(payload_str, secret)
    
    # Patch the stripe module to use our fake_redis
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    response = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["event_id"] == "evt_test_checkout_001"
    assert result["event_type"] == "checkout.session.completed"


@pytest.mark.asyncio
async def test_stripe_webhook_checkout_completed_updates_redis(monkeypatch, fake_redis):
    """Test that checkout.session.completed updates user tier in Redis."""
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    event = stripe_module.StripeEvent(
        id="evt_test_tier",
        type="checkout.session.completed",
        data={
            "object": {
                "customer": "cus_test456",
                "metadata": {
                    "user_id": "user_premium",
                    "tier": "premium",
                },
            }
        },
    )
    
    await stripe_module.handle_checkout_completed(event)
    
    # Verify tier was set in Redis
    tier = await fake_redis.get("user:user_premium:tier")
    assert tier == "premium"
    
    # Verify pub/sub event was published
    assert len(fake_redis.published) == 1
    channel, message = fake_redis.published[0]
    assert channel == "subscription:events"
    
    event_data = json.loads(message)
    assert event_data["event_type"] == "tier_updated"
    assert event_data["user_id"] == "user_premium"
    assert event_data["tier"] == "premium"
    assert event_data["customer_id"] == "cus_test456"


def test_stripe_webhook_subscription_deleted(client, monkeypatch, fake_redis):
    """Test customer.subscription.deleted downgrades to basic."""
    secret = "whsec_test123"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    
    # First set user to premium
    fake_redis._store["user:user_002:tier"] = ("premium", None)
    
    payload = {
        "id": "evt_test_sub_deleted",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "metadata": {
                    "user_id": "user_002",
                },
            }
        },
    }
    
    payload_str = json.dumps(payload)
    signature = create_stripe_signature(payload_str, secret)
    
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    response = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_stripe_webhook_subscription_deleted_downgrades(monkeypatch, fake_redis):
    """Test that subscription deletion downgrades user to basic."""
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    # Set user to premium first
    await fake_redis.set("user:user_downgrade:tier", "premium")
    
    event = stripe_module.StripeEvent(
        id="evt_test_downgrade",
        type="customer.subscription.deleted",
        data={
            "object": {
                "metadata": {
                    "user_id": "user_downgrade",
                },
            }
        },
    )
    
    await stripe_module.handle_subscription_deleted(event)
    
    # Verify tier was downgraded to basic
    tier = await fake_redis.get("user:user_downgrade:tier")
    assert tier == "basic"
    
    # Verify pub/sub event was published
    assert len(fake_redis.published) == 1
    channel, message = fake_redis.published[0]
    assert channel == "subscription:events"
    
    event_data = json.loads(message)
    assert event_data["event_type"] == "tier_downgraded"
    assert event_data["user_id"] == "user_downgrade"
    assert event_data["tier"] == "basic"
    assert event_data["reason"] == "subscription_deleted"


def test_stripe_webhook_charge_refunded(client, monkeypatch, fake_redis):
    """Test charge.refunded downgrades to basic."""
    secret = "whsec_test123"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    
    # First set user to premium
    fake_redis._store["user:user_003:tier"] = ("premium", None)
    
    payload = {
        "id": "evt_test_refund",
        "type": "charge.refunded",
        "data": {
            "object": {
                "metadata": {
                    "user_id": "user_003",
                },
            }
        },
    }
    
    payload_str = json.dumps(payload)
    signature = create_stripe_signature(payload_str, secret)
    
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    response = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["event_type"] == "charge.refunded"


@pytest.mark.asyncio
async def test_stripe_webhook_charge_refunded_downgrades(monkeypatch, fake_redis):
    """Test that charge refund downgrades user to basic."""
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    # Set user to premium first
    await fake_redis.set("user:user_refund:tier", "premium")
    
    event = stripe_module.StripeEvent(
        id="evt_test_refund_handler",
        type="charge.refunded",
        data={
            "object": {
                "metadata": {
                    "user_id": "user_refund",
                },
            }
        },
    )
    
    await stripe_module.handle_charge_refunded(event)
    
    # Verify tier was downgraded to basic
    tier = await fake_redis.get("user:user_refund:tier")
    assert tier == "basic"
    
    # Verify pub/sub event was published
    assert len(fake_redis.published) == 1
    channel, message = fake_redis.published[0]
    assert channel == "subscription:events"
    
    event_data = json.loads(message)
    assert event_data["event_type"] == "tier_downgraded"
    assert event_data["user_id"] == "user_refund"
    assert event_data["tier"] == "basic"
    assert event_data["reason"] == "charge_refunded"


def test_stripe_webhook_idempotency(client, monkeypatch, fake_redis):
    """Test that duplicate events return 200 but are not reprocessed."""
    secret = "whsec_test123"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    
    payload = {
        "id": "evt_test_idempotent",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_test789",
                "metadata": {
                    "user_id": "user_idempotent",
                    "tier": "premium",
                },
            }
        },
    }
    
    payload_str = json.dumps(payload)
    signature = create_stripe_signature(payload_str, secret)
    
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    # First request - should process normally
    response1 = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response1.status_code == 200
    result1 = response1.json()
    assert result1["status"] == "success"
    
    # Verify tier was set
    assert fake_redis._store.get("user:user_idempotent:tier") is not None
    
    # Clear published events to track the second request
    fake_redis.published.clear()
    
    # Second request with same event ID - should return duplicate
    response2 = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response2.status_code == 200
    result2 = response2.json()
    assert result2["status"] == "duplicate"
    assert result2["event_id"] == "evt_test_idempotent"
    
    # Verify no new pub/sub events were published on duplicate
    assert len(fake_redis.published) == 0


def test_stripe_webhook_invalid_signature_no_redis_writes(client, monkeypatch, fake_redis):
    """Test that invalid signature returns 400 with zero Redis writes."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test123")
    
    payload = {
        "id": "evt_test_no_writes",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {
                    "user_id": "user_should_not_write",
                    "tier": "premium",
                },
            }
        },
    }
    
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    # Track initial state
    initial_store_size = len(fake_redis._store)
    initial_published_count = len(fake_redis.published)
    
    # Send request with invalid signature
    response = client.post(
        "/webhooks/stripe",
        json=payload,
        headers={"Stripe-Signature": "t=123,v1=bad_sig"},
    )
    
    assert response.status_code == 400
    
    # Verify no Redis writes occurred
    assert len(fake_redis._store) == initial_store_size
    assert len(fake_redis.published) == initial_published_count


def test_stripe_webhook_unknown_event_type(client, monkeypatch, fake_redis):
    """Test that unknown event types are acknowledged but not processed."""
    secret = "whsec_test123"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    
    payload = {
        "id": "evt_test_unknown",
        "type": "invoice.payment_failed",  # Not handled
        "data": {"object": {}},
    }
    
    payload_str = json.dumps(payload)
    signature = create_stripe_signature(payload_str, secret)
    
    monkeypatch.setattr(stripe_module, "get_redis", lambda: fake_redis, raising=True)
    
    response = client.post(
        "/webhooks/stripe",
        content=payload_str,
        headers={
            "Stripe-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["event_type"] == "invoice.payment_failed"
