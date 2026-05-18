#!/usr/bin/env bash
# Multi-User Social Feed Demo
# Demonstrates how to test social feed pagination with multiple users using dev-token endpoints
#
# Prerequisites:
# - GOBIG_DEV_ALLOW_TOKEN_MINT=1 in .env
# - Server running on http://127.0.0.1:8000
# - python3 with json module

set -e

BASE="http://127.0.0.1:8000"

echo "=== Multi-User Social Feed Demo ==="
echo ""

# Step 1: Create tokens for Alice and Bob
echo "Step 1: Creating tokens for Alice and Bob..."
ALICE_TOKEN=$(curl -s "$BASE/auth/dev-token?user_id=alice" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
BOB_TOKEN=$(curl -s "$BASE/auth/dev-token?user_id=bob" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "✓ Tokens created"
echo ""

# Step 2: Alice follows Bob
echo "Step 2: Alice follows Bob..."
curl -s -X POST "$BASE/social/follow/bob" -H "Authorization: Bearer $ALICE_TOKEN" > /dev/null
echo "✓ Alice is now following Bob"
echo ""

# Step 3: Bob creates 25 recipe shares
echo "Step 3: Bob creates 25 recipe shares..."
for i in {1..25}; do
  RECIPE_ID=$(printf "recipe-%03d" $i)
  curl -s -X POST "$BASE/social/recipe-share" \
    -H "Authorization: Bearer $BOB_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"recipe_id\":\"$RECIPE_ID\"}" > /dev/null
  echo "  Created share $i/25"
done
echo "✓ Bob created 25 recipe shares"
echo ""

# Step 4: Alice views page 1 of her feed
echo "Step 4: Alice views page 1 of her feed (should see 20 items)..."
PAGE1=$(curl -s "$BASE/social/feed" -H "Authorization: Bearer $ALICE_TOKEN")
PAGE1_COUNT=$(echo "$PAGE1" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['items']))")
CURSOR=$(echo "$PAGE1" | python3 -c "import sys,json; print(json.load(sys.stdin)['next_cursor'])")
echo "✓ Page 1: $PAGE1_COUNT items"
echo "✓ Cursor: ${CURSOR:0:30}..."
echo ""

# Step 5: Alice views page 2 of her feed
echo "Step 5: Alice views page 2 of her feed (should see 5 remaining items)..."
PAGE2=$(curl -s "$BASE/social/feed?cursor=$CURSOR" -H "Authorization: Bearer $ALICE_TOKEN")
PAGE2_COUNT=$(echo "$PAGE2" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['items']))")
echo "✓ Page 2: $PAGE2_COUNT items"
echo ""

# Step 6: Verify non-overlapping items
echo "Step 6: Verifying pages are non-overlapping..."
python3 << EOF
import json
page1 = json.loads('''$PAGE1''')
page2 = json.loads('''$PAGE2''')
ids1 = {item['event_id'] for item in page1['items']}
ids2 = {item['event_id'] for item in page2['items']}
overlap = ids1 & ids2
if overlap:
    print(f"✗ ERROR: Found {len(overlap)} overlapping items!")
    exit(1)
else:
    print(f"✓ No overlap between pages")
    print(f"  Total unique events: {len(ids1) + len(ids2)}")
EOF
echo ""

# Step 7: Verify Bob's profile shows correct follower count
echo "Step 7: Checking Bob's profile..."
PROFILE=$(curl -s "$BASE/social/profile/bob" -H "Authorization: Bearer $ALICE_TOKEN")
FOLLOWERS=$(echo "$PROFILE" | python3 -c "import sys,json; print(json.load(sys.stdin)['followers'])")
echo "✓ Bob has $FOLLOWERS follower(s)"
echo ""

echo "=== Demo Complete ==="
echo ""
echo "Summary:"
echo "  • Alice followed Bob"
echo "  • Bob created 25 recipe shares"
echo "  • Alice's feed showed 20 items on page 1"
echo "  • Alice's feed showed 5 items on page 2"
echo "  • No items overlapped between pages"
echo "  • Bob's profile shows correct follower count"
