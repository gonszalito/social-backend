# GoBig API Examples

This directory contains example scripts and demos for testing the GoBig API.

## multi_user_feed_demo.sh

Demonstrates the social feed pagination functionality with multiple users.

**What it does:**
1. Creates JWT tokens for two users (Alice and Bob) using the dev-token endpoint
2. Alice follows Bob
3. Bob creates 25 recipe shares
4. Alice views her feed with pagination (page 1: 20 items, page 2: 5 items)
5. Verifies that the pages don't overlap
6. Checks Bob's profile to confirm follower count

**Prerequisites:**
- Set `GOBIG_DEV_ALLOW_TOKEN_MINT=1` in your `.env` file
- Server running on `http://127.0.0.1:8000`
- Python 3 with json module

**Usage:**
```bash
# Start the API server in one terminal
source .venv/bin/activate
uvicorn app.main:app --reload

# Run the demo in another terminal
./examples/multi_user_feed_demo.sh
```

**Example output:**
```
=== Multi-User Social Feed Demo ===

Step 1: Creating tokens for Alice and Bob...
✓ Tokens created

Step 2: Alice follows Bob...
✓ Alice is now following Bob

Step 3: Bob creates 25 recipe shares...
  Created share 1/25
  ...
✓ Bob created 25 recipe shares

Step 4: Alice views page 1 of her feed (should see 20 items)...
✓ Page 1: 20 items
✓ Cursor: eyJldmVudF9pZCI6InJzX2JvYl8...

Step 5: Alice views page 2 of her feed (should see 5 remaining items)...
✓ Page 2: 5 items

Step 6: Verifying pages are non-overlapping...
✓ No overlap between pages
  Total unique events: 25

Step 7: Checking Bob's profile...
✓ Bob has 1 follower(s)

=== Demo Complete ===
```

## Related Documentation

- See `CURL_REFERENCE.md` for manual curl commands and token generation
- See `DEMO_GUIDE.md` for step-by-step milestone demonstrations
- See `TESTING.mdc` in `.cursor/rules/` for automated pytest commands
- See `STRIPE_WEBHOOK_GUIDE.md` for Stripe webhook integration details

## test_stripe_webhook.sh

Tests the Stripe webhook handler with various event types and security features.

**What it does:**
1. Tests invalid signature (should return 400)
2. Tests valid `checkout.session.completed` event (tier upgrade)
3. Tests valid `customer.subscription.deleted` event (downgrade)
4. Tests valid `charge.refunded` event (downgrade)
5. Tests idempotency (duplicate event handling)

**Prerequisites:**
- Set `STRIPE_WEBHOOK_SECRET` environment variable
- Server running on `http://localhost:8000`
- `jq` installed for JSON formatting
- `openssl` available (for signature generation)

**Usage:**
```bash
# Start the API server in one terminal
source .venv/bin/activate
uvicorn app.main:app --reload

# Run the demo in another terminal
export STRIPE_WEBHOOK_SECRET="whsec_test123"
./examples/test_stripe_webhook.sh
```

**Example output:**
```
🔧 Testing Stripe webhook handler at http://localhost:8000/webhooks/stripe
📝 Using webhook secret: whsec_test123...

Test 1: Invalid signature
{
  "detail": "Invalid signature"
}
Status: 400

Test 2: Valid checkout.session.completed event
{
  "status": "success",
  "event_id": "evt_checkout_1712345678",
  "event_type": "checkout.session.completed"
}
Status: 200

Test 3: customer.subscription.deleted event
{
  "status": "success",
  "event_id": "evt_sub_deleted_1712345678",
  "event_type": "customer.subscription.deleted"
}
Status: 200

Test 4: charge.refunded event
{
  "status": "success",
  "event_id": "evt_refund_1712345678",
  "event_type": "charge.refunded"
}
Status: 200

Test 5: Idempotency test (send same event twice)
  First request (should return success):
{
  "status": "success",
  "event_id": "evt_idempotent_1712345678",
  "event_type": "checkout.session.completed"
}
Status: 200

  Second request (should return duplicate):
{
  "status": "duplicate",
  "event_id": "evt_idempotent_1712345678",
  "message": "Event already processed"
}
Status: 200

✅ Stripe webhook tests complete!
```
