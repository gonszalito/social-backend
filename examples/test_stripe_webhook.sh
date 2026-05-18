#!/bin/bash
# Manual Stripe webhook testing script
# 
# Usage:
#   1. Set your webhook secret: export STRIPE_WEBHOOK_SECRET="whsec_your_secret"
#   2. Make script executable: chmod +x examples/test_stripe_webhook.sh
#   3. Run: ./examples/test_stripe_webhook.sh
#
# This script demonstrates:
# - Creating a valid Stripe signature
# - Testing checkout.session.completed event
# - Testing customer.subscription.deleted event
# - Testing charge.refunded event
# - Testing idempotency

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-whsec_test123}"

echo "🔧 Testing Stripe webhook handler at ${BASE_URL}/webhooks/stripe"
echo "📝 Using webhook secret: ${WEBHOOK_SECRET:0:20}..."
echo ""

# Function to create a Stripe signature
create_stripe_signature() {
    local payload="$1"
    local secret="$2"
    local timestamp=$(date +%s)
    
    local signed_payload="${timestamp}.${payload}"
    local signature=$(echo -n "${signed_payload}" | openssl dgst -sha256 -hmac "${secret}" | cut -d' ' -f2)
    
    echo "t=${timestamp},v1=${signature}"
}

# Test 1: Invalid signature (should return 400)
echo "Test 1: Invalid signature"
PAYLOAD='{"id":"evt_test_001","type":"checkout.session.completed","data":{"object":{}}}'
curl -s -w "\nStatus: %{http_code}\n" \
    -X POST "${BASE_URL}/webhooks/stripe" \
    -H "Content-Type: application/json" \
    -H "Stripe-Signature: t=123456789,v1=invalid_signature" \
    -d "${PAYLOAD}" | jq .
echo ""

# Test 2: Valid checkout.session.completed
echo "Test 2: Valid checkout.session.completed event"
PAYLOAD=$(cat <<EOF
{
  "id": "evt_checkout_$(date +%s)",
  "type": "checkout.session.completed",
  "data": {
    "object": {
      "customer": "cus_test123",
      "metadata": {
        "user_id": "user_demo_001",
        "tier": "premium"
      }
    }
  }
}
EOF
)
SIGNATURE=$(create_stripe_signature "$PAYLOAD" "$WEBHOOK_SECRET")
curl -s -w "\nStatus: %{http_code}\n" \
    -X POST "${BASE_URL}/webhooks/stripe" \
    -H "Content-Type: application/json" \
    -H "Stripe-Signature: ${SIGNATURE}" \
    -d "${PAYLOAD}" | jq .
echo ""

# Test 3: customer.subscription.deleted
echo "Test 3: customer.subscription.deleted event"
PAYLOAD=$(cat <<EOF
{
  "id": "evt_sub_deleted_$(date +%s)",
  "type": "customer.subscription.deleted",
  "data": {
    "object": {
      "metadata": {
        "user_id": "user_demo_002"
      }
    }
  }
}
EOF
)
SIGNATURE=$(create_stripe_signature "$PAYLOAD" "$WEBHOOK_SECRET")
curl -s -w "\nStatus: %{http_code}\n" \
    -X POST "${BASE_URL}/webhooks/stripe" \
    -H "Content-Type: application/json" \
    -H "Stripe-Signature: ${SIGNATURE}" \
    -d "${PAYLOAD}" | jq .
echo ""

# Test 4: charge.refunded
echo "Test 4: charge.refunded event"
PAYLOAD=$(cat <<EOF
{
  "id": "evt_refund_$(date +%s)",
  "type": "charge.refunded",
  "data": {
    "object": {
      "metadata": {
        "user_id": "user_demo_003"
      }
    }
  }
}
EOF
)
SIGNATURE=$(create_stripe_signature "$PAYLOAD" "$WEBHOOK_SECRET")
curl -s -w "\nStatus: %{http_code}\n" \
    -X POST "${BASE_URL}/webhooks/stripe" \
    -H "Content-Type: application/json" \
    -H "Stripe-Signature: ${SIGNATURE}" \
    -d "${PAYLOAD}" | jq .
echo ""

# Test 5: Idempotency (send same event twice)
echo "Test 5: Idempotency test (send same event twice)"
EVENT_ID="evt_idempotent_$(date +%s)"
PAYLOAD=$(cat <<EOF
{
  "id": "${EVENT_ID}",
  "type": "checkout.session.completed",
  "data": {
    "object": {
      "customer": "cus_test456",
      "metadata": {
        "user_id": "user_demo_004",
        "tier": "premium"
      }
    }
  }
}
EOF
)
SIGNATURE=$(create_stripe_signature "$PAYLOAD" "$WEBHOOK_SECRET")

echo "  First request (should return success):"
curl -s -w "\nStatus: %{http_code}\n" \
    -X POST "${BASE_URL}/webhooks/stripe" \
    -H "Content-Type: application/json" \
    -H "Stripe-Signature: ${SIGNATURE}" \
    -d "${PAYLOAD}" | jq .
echo ""

sleep 1

echo "  Second request (should return duplicate):"
curl -s -w "\nStatus: %{http_code}\n" \
    -X POST "${BASE_URL}/webhooks/stripe" \
    -H "Content-Type: application/json" \
    -H "Stripe-Signature: ${SIGNATURE}" \
    -d "${PAYLOAD}" | jq .
echo ""

echo "✅ Stripe webhook tests complete!"
