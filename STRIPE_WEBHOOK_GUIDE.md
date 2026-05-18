# Stripe Webhook Integration Guide

## Overview

The Stripe webhook handler (`POST /webhooks/stripe`) processes subscription and payment events from Stripe, implementing security best practices and reliable event processing.

## Security Features

### 1. HMAC-SHA256 Signature Verification
- Verifies `Stripe-Signature` header before any processing
- Uses webhook secret from `STRIPE_WEBHOOK_SECRET` env var
- Returns **400** immediately on invalid signatures (zero DB/Redis writes)

### 2. Timestamp Validation
- Rejects requests with timestamps older than 5 minutes
- Prevents replay attacks

### 3. Idempotency
- Uses Redis key `stripe_event:{event_id}` with 24-hour TTL
- Duplicate events return **200** with `status: "duplicate"`
- No reprocessing of duplicate events

## Supported Events

### `checkout.session.completed`
- **Action**: Updates user tier to premium
- **Redis**: Sets `user:{user_id}:tier`
- **Pub/Sub**: Publishes to `subscription:events` channel
- **Required metadata**: `user_id`, `tier`

### `customer.subscription.deleted`
- **Action**: Downgrades user to basic tier
- **Redis**: Sets `user:{user_id}:tier` to "basic"
- **Pub/Sub**: Publishes downgrade event to `subscription:events`
- **Required metadata**: `user_id`

### `charge.refunded`
- **Action**: Downgrades user to basic tier
- **Redis**: Sets `user:{user_id}:tier` to "basic"
- **Pub/Sub**: Publishes downgrade event to `subscription:events`
- **Required metadata**: `user_id`

## Configuration

### Environment Variables

```bash
# Required: Stripe webhook signing secret (from Stripe Dashboard)
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret_here
```

### Stripe Dashboard Setup

1. Go to Stripe Dashboard → Developers → Webhooks
2. Click "Add endpoint"
3. Set endpoint URL: `https://your-domain.com/webhooks/stripe`
4. Select events to send:
   - `checkout.session.completed`
   - `customer.subscription.deleted`
   - `charge.refunded`
5. Copy the webhook signing secret (starts with `whsec_`)
6. Set `STRIPE_WEBHOOK_SECRET` in your environment

## Testing

### Automated Tests

Run the comprehensive test suite:

```bash
source .venv/bin/activate
pytest tests/test_stripe_webhook.py -v
```

The test suite covers:
- Invalid/missing signature → 400
- Expired timestamp → 400
- All three event types with tier updates
- Idempotency (duplicate events)
- Zero writes on signature failure
- Redis pub/sub event publishing

### Manual Testing

Use the provided test script:

```bash
export STRIPE_WEBHOOK_SECRET="whsec_test123"
./examples/test_stripe_webhook.sh
```

Or with Stripe CLI:

```bash
# Install Stripe CLI
# https://stripe.com/docs/stripe-cli

# Forward webhooks to local server
stripe listen --forward-to localhost:8000/webhooks/stripe

# Trigger test events
stripe trigger checkout.session.completed
stripe trigger customer.subscription.deleted
stripe trigger charge.refunded
```

## Response Codes

| Code | Meaning | Scenario |
|------|---------|----------|
| 200  | Success | Event processed or duplicate |
| 400  | Bad Request | Invalid signature, expired timestamp, or malformed payload |
| 503  | Service Unavailable | Redis connection failure |

## Response Format

### Success
```json
{
  "status": "success",
  "event_id": "evt_1234567890",
  "event_type": "checkout.session.completed"
}
```

### Duplicate
```json
{
  "status": "duplicate",
  "event_id": "evt_1234567890",
  "message": "Event already processed"
}
```

### Error
```json
{
  "detail": "Invalid signature"
}
```

## Redis Keys

### Idempotency
- **Key**: `stripe_event:{event_id}`
- **Type**: String
- **Value**: "processed"
- **TTL**: 86400 seconds (24 hours)

### User Tier
- **Key**: `user:{user_id}:tier`
- **Type**: String
- **Value**: "basic" | "premium"
- **TTL**: None (persistent)

## Pub/Sub Events

Events are published to the `subscription:events` channel in JSON format:

### Tier Updated Event
```json
{
  "event_type": "tier_updated",
  "user_id": "user_123",
  "tier": "premium",
  "customer_id": "cus_abc123",
  "timestamp": 1712345678
}
```

### Tier Downgraded Event
```json
{
  "event_type": "tier_downgraded",
  "user_id": "user_123",
  "tier": "basic",
  "reason": "subscription_deleted",
  "timestamp": 1712345678
}
```

## Monitoring

### Logs
- All processed events log at INFO level
- Signature failures log at WARNING level
- Redis failures log at ERROR level

### Metrics
Prometheus metrics are automatically recorded via the global request latency histogram:
- `request_latency_seconds{method="POST",endpoint="/webhooks/stripe",status_code="200"}`
- Track error rates via `status_code="400"` or `status_code="503"`

## Production Considerations

1. **Always use HTTPS** - Never expose webhook endpoints over HTTP in production
2. **Monitor idempotency keys** - Watch Redis memory usage for the `stripe_event:*` keys
3. **Alert on 400 errors** - High rate of signature failures may indicate an attack
4. **Backup metadata** - Always include `user_id` in Stripe metadata when creating sessions
5. **Test webhook secret rotation** - Update `STRIPE_WEBHOOK_SECRET` and redeploy atomically

## Troubleshooting

### "Invalid signature" errors in production
- Verify `STRIPE_WEBHOOK_SECRET` matches the secret in Stripe Dashboard
- Check that the endpoint URL in Stripe matches your deployment
- Ensure no reverse proxy is modifying the request body

### Events not being processed
- Check Redis connectivity: `redis-cli PING`
- Verify webhook secret is set: `echo $STRIPE_WEBHOOK_SECRET`
- Check application logs for detailed error messages

### Duplicate events
- This is normal behavior - Stripe may retry webhooks
- The idempotency system will handle this automatically
- Duplicates return 200 but don't reprocess

## Related Files

- Implementation: `app/api/webhooks/stripe.py`
- Tests: `tests/test_stripe_webhook.py`
- Main app: `app/main.py` (routes registered here)
- Task tracking: `.cursor/rules/TASKS.mdc` (API-M2-01)
- Test guide: `.cursor/rules/TESTING.mdc`
