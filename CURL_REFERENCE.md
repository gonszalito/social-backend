# GoBig API – Bearer tokens & curl reference

Use this file as a copy-paste cookbook. Replace `BASE` with your server URL (default `http://127.0.0.1:8000`). Run curls from the **project root** so paths to `keys/iam_private.pem` work.

---

## 1. How to get an Authorization Bearer token

The API verifies **RS256** JWTs with the **public** key loaded at startup (`GOBIG_IAM_PUBLIC_KEY_PATH` or `GOBIG_IAM_PUBLIC_KEY_PEM`). You must sign tokens with the matching **private** key (`keys/iam_private.pem` in local dev).

### Required claims (typical)

| Claim | Meaning |
|-------|---------|
| `sub` | Subject (user id) |
| `jti` | JWT ID (used for revoke blocklist) |
| `exp` | Expiry (Unix time) |

Optional:

| Claim | Meaning |
|-------|---------|
| `admin_role` | `"Developer"` or `"Business"` — both may **GET** `/api/v1/admin/flags`; only **Developer** may **POST** flag updates |

### One-liners (zsh/bash, from project root)

**Plain user** (social, revoke, most protected routes):

```bash
export TOKEN=$(python3 -c 'import jwt,time; p={"sub":"user_1","jti":"jti-1","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
echo "$TOKEN"
```

**Developer** (required for `POST /api/v1/admin/flags/{flag_name}`; may also GET):

```bash
export TOKEN=$(python3 -c 'import jwt,time; p={"sub":"user_1","jti":"jti-dev","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
```

**Business** (can **GET** `/api/v1/admin/flags`; **403** on POST flag updates — useful for demos):

```bash
export TOKEN_BIZ=$(python3 -c 'import jwt,time; p={"sub":"user_1","jti":"jti-biz","exp":int(time.time())+3600,"admin_role":"Business"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
```

### HTTP endpoint (local dev only)

If `GOBIG_DEV_ALLOW_TOKEN_MINT=1` is set and `GOBIG_IAM_PRIVATE_KEY_PATH` points at your signing key (default `./keys/iam_private.pem`), you can mint a token without a Python one-liner:

**Default user (sub="dev")**:
```bash
curl -s "http://127.0.0.1:8000/auth/dev-token?admin_role=Developer&ttl_seconds=3600"
```

**Custom user_id (e.g. "alice")**:
```bash
curl -s "http://127.0.0.1:8000/auth/dev-token?user_id=alice&admin_role=Developer&ttl_seconds=3600"
```

Response: `{"access_token":"...","token_type":"Bearer","expires_in":3600,"user_id":"alice"}`. **Do not enable this in production** (it exposes arbitrary JWT minting to anyone who can reach the API).

Unlimited (no `exp` claim, local dev only):

**Default user**:
```bash
curl -s "http://127.0.0.1:8000/auth/dev-token-unlimited?admin_role=Developer"
```

**Custom user_id**:
```bash
curl -s "http://127.0.0.1:8000/auth/dev-token-unlimited?user_id=bob"
```

Response: `{"access_token":"...","token_type":"Bearer","expires_in":null,"user_id":"bob"}`.

### Use the token in curl

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/social/feed"
```

### Dependencies

```bash
pip install PyJWT cryptography
```

(Already in `requirements.txt`.)

### WebSocket (query param, not `Authorization` header)

```bash
export WS_TOKEN=$(python3 -c 'import jwt,time; p={"sub":"user_1","jti":"jti-ws","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
# websocat "ws://127.0.0.1:8000/ws/room/myroom?token=$WS_TOKEN"
```

---

## 2. Environment snippet

```bash
export BASE=http://127.0.0.1:8000
cd /path/to/gobig-social-backend   # so keys/iam_private.pem resolves
```

---

## 3. Public endpoints (no `Authorization` required)

### `GET /health`

```bash
curl -s "$BASE/health"
```

### `GET /metrics`

```bash
curl -s "$BASE/metrics"
```

### OpenAPI / docs

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/docs"
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/redoc"
curl -s "$BASE/openapi.json" | head -c 200
```

### `POST /api/v1/storage/presign`

`upload_type`: `voice_log` | `attempt_photo` | `avatar` | `taste_profile`

```bash
curl -s -X POST "$BASE/api/v1/storage/presign" \
  -H "Content-Type: application/json" \
  -d '{"upload_type":"avatar","content_type":"image/png"}'
```

### `POST /api/v1/nlp/ingest`

```bash
curl -s -X POST "$BASE/api/v1/nlp/ingest" \
  -H "Content-Type: application/json" \
  -d '{"batch_id":"batch-demo-1","recipes":[{"recipe_id":"r1","text":"hello world","confidence":0.9}]}'
```

---

## 4. Protected endpoints (Bearer token required)

Set `TOKEN` first (see section 1).

### `POST /auth/revoke`

```bash
curl -s -X POST "$BASE/auth/revoke" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"logout demo"}'
```

After revoke, the same `TOKEN` should get **401** on protected routes.

### `GET /api/v1/admin/flags` (Business or Developer)

```bash
export TOKEN=$(python3 -c 'import jwt,time; p={"sub":"user_1","jti":"jti-admin","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')

curl -s "$BASE/api/v1/admin/flags" -H "Authorization: Bearer $TOKEN"
```

### `POST /api/v1/admin/flags/{flag_name}` (Developer only)

Body: `{"enabled": true|false, "reason": "non-empty string"}`. Order: audit file → Redis `SET` → synchronous CF Workers KV sync (or Redis retry enqueue on failure).

```bash
export TOKEN=$(python3 -c 'import jwt,time; p={"sub":"user_1","jti":"jti-admin2","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')

curl -s -X POST "$BASE/api/v1/admin/flags/gobig_nlp_processing" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled":true,"reason":"enable NLP processing"}'
```

### `POST /webhooks/cf-kv-sync` (Developer only)

```bash
curl -s -X POST "$BASE/webhooks/cf-kv-sync" -H "Authorization: Bearer $TOKEN"
```

### Social – follow / unfollow

```bash
export TOKEN=$(python3 -c 'import jwt,time; p={"sub":"alice","jti":"j1","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')

curl -s -X POST "$BASE/social/follow/bob" -H "Authorization: Bearer $TOKEN"

curl -s -X DELETE "$BASE/social/unfollow/bob" -H "Authorization: Bearer $TOKEN"
```

### Social – recipe share

```bash
curl -s -X POST "$BASE/social/recipe-share" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recipe_id":"recipe-123"}'
```

### Social – feed

```bash
curl -s "$BASE/social/feed" -H "Authorization: Bearer $TOKEN"

curl -s "$BASE/social/feed?cursor=PASTE_CURSOR_HERE" -H "Authorization: Bearer $TOKEN"
```

### Social – profile

```bash
curl -sI "$BASE/social/profile/bob" -H "Authorization: Bearer $TOKEN"
```

### Social – potluck

```bash
curl -s -X POST "$BASE/social/potluck/create" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Team dinner"}'

curl -s -X POST "$BASE/social/potluck/invite" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"potluck_id":"PASTE_ID_FROM_CREATE","invitee_user_id":"bob"}'
```

### Testing Multi-User Social Feed (dev-only)

With `GOBIG_DEV_ALLOW_TOKEN_MINT=1`, you can easily test how different users see their feeds:

**Step 1: Alice follows Bob**
```bash
# Get token for Alice
export ALICE_TOKEN=$(curl -s "http://127.0.0.1:8000/auth/dev-token?user_id=alice" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Alice follows Bob
curl -s -X POST "http://127.0.0.1:8000/social/follow/bob" -H "Authorization: Bearer $ALICE_TOKEN"
```

**Step 2: Bob creates recipe shares**
```bash
# Get token for Bob
export BOB_TOKEN=$(curl -s "http://127.0.0.1:8000/auth/dev-token?user_id=bob" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Bob shares recipes
curl -s -X POST "http://127.0.0.1:8000/social/recipe-share" \
  -H "Authorization: Bearer $BOB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recipe_id":"recipe-001"}'

curl -s -X POST "http://127.0.0.1:8000/social/recipe-share" \
  -H "Authorization: Bearer $BOB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recipe_id":"recipe-002"}'
```

**Step 3: Alice sees Bob's shares in her feed**
```bash
# Alice views her feed (should see Bob's recipe shares)
curl -s "http://127.0.0.1:8000/social/feed" -H "Authorization: Bearer $ALICE_TOKEN" | python3 -m json.tool
```

**Step 4: Test pagination with multiple events**
```bash
# Create 25 recipe shares from Bob
for i in {1..25}; do
  curl -s -X POST "http://127.0.0.1:8000/social/recipe-share" \
    -H "Authorization: Bearer $BOB_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"recipe_id\":\"recipe-$(printf %03d $i)\"}"
done

# Alice gets first page (20 items)
curl -s "http://127.0.0.1:8000/social/feed" -H "Authorization: Bearer $ALICE_TOKEN" | python3 -m json.tool > page1.json

# Extract cursor and get second page (5 remaining items)
export CURSOR=$(python3 -c "import json; print(json.load(open('page1.json'))['next_cursor'])")
curl -s "http://127.0.0.1:8000/social/feed?cursor=$CURSOR" -H "Authorization: Bearer $ALICE_TOKEN" | python3 -m json.tool > page2.json
```

---

## 5. WebSocket

Not HTTP curl; use a WebSocket client. Token is the **`token` query parameter**.

```bash
export WS_TOKEN=$(python3 -c 'import jwt,time; p={"sub":"alice","jti":"jws","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
echo "ws://127.0.0.1:8000/ws/room/demo-room?token=$WS_TOKEN"
```

Example with [websocat](https://github.com/vi/websocat):

```bash
websocat "ws://127.0.0.1:8000/ws/room/demo-room?token=$WS_TOKEN"
```

Example with repo script:

```bash
python3 scripts/example_websocket_client.py --room-id demo-room --token "$WS_TOKEN" --client-name listener --mode listen
```

---

## 6. Quick reference table

| Method | Path | Auth |
|--------|------|------|
| GET | `/health` | No |
| GET | `/metrics` | No |
| GET | `/docs`, `/redoc`, `/openapi.json` | No |
| POST | `/api/v1/storage/presign` | No |
| POST | `/api/v1/nlp/ingest` | No |
| POST | `/auth/revoke` | Bearer |
| GET | `/api/v1/admin/flags` | Bearer (Business or Developer) |
| POST | `/api/v1/admin/flags/{flag_name}` | Bearer (Developer) |
| POST | `/webhooks/cf-kv-sync` | Bearer (Developer) |
| POST | `/social/follow/{id}` | Bearer |
| DELETE | `/social/unfollow/{id}` | Bearer |
| POST | `/social/recipe-share` | Bearer |
| GET | `/social/feed` | Bearer |
| GET | `/social/profile/{user_id}` | Bearer |
| POST | `/social/potluck/create` | Bearer |
| POST | `/social/potluck/invite` | Bearer |
| WS | `/ws/room/{room_id}?token=...` | Query token |

---

## 7. Related docs

- **Supervisor demo walkthrough**: `DEMO_GUIDE.md`
- **Pytest commands**: `.cursor/rules/TESTING.mdc`
