# GoBig API – Milestone JON-A Demo Guide

This guide helps you demonstrate each completed milestone to a supervisor. Each section shows **how to test** and **what to expect**.

**Bearer tokens and all curl commands**: see `CURL_REFERENCE.md` in the project root.

---

## Prerequisites

1. **Environment**
   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```

2. **Redis**
   - This repo uses `REDIS_URL` from `.env`.
   - If you have a shared/remote Redis, set `REDIS_URL` accordingly.
   - If you want local Redis via Docker:
     ```bash
     docker run -d --name gobig-redis -p 6379:6379 redis:7-alpine
     ```

3. **IAM keys** (dev)
   ```bash
   mkdir -p keys
   openssl genrsa -out keys/iam_private.pem 2048
   openssl rsa -in keys/iam_private.pem -pubout -out keys/iam_public_key.pem
   ```

4. **.env**
   ```bash
   GOBIG_IAM_PUBLIC_KEY_PATH=./keys/iam_public_key.pem
   # Example (local Docker Redis)
   REDIS_URL=redis://127.0.0.1:6379/0
   ```

5. **Start the app**
   ```bash
   uvicorn app.main:app --reload --env-file .env
   ```
   Base URL: `http://127.0.0.1:8000`

6. **JWT helper** – get a token for demos:
   ```bash
   # Developer token (for /api/v1/admin/flags)
   TOKEN=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"j1","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')

   # Business token (can GET /api/v1/admin/flags; POST flag update returns 403)
   TOKEN_B=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"j2","exp":int(time.time())+3600,"admin_role":"Business"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')

   # Plain token (for /social/*, no admin_role)
   TOKEN_P=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"j3","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
   ```

---

## API-M1-01: JWT RS256 Auth Middleware

### Pass criteria

1. Missing token → **401**
2. Business role → **200** on `GET /api/v1/admin/flags`; **403** on `POST /api/v1/admin/flags/...`
3. Developer role → **200** on `GET` and `POST` admin flags (with valid body)
4. Revoke → then protected call → **401** immediately

### Demo steps

| Step | Command | Expected |
|------|---------|----------|
| 1. No token | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/v1/admin/flags` | `401` |
| 2. Business role GET | `TOKEN_B=...` (as in Prerequisites) then `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN_B" http://127.0.0.1:8000/api/v1/admin/flags` | `200` |
| 2b. Business POST flag | same `TOKEN_B` then `curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/api/v1/admin/flags/gobig_nlp_processing -H "Authorization: Bearer $TOKEN_B" -H "Content-Type: application/json" -d '{"enabled":true,"reason":"x"}'` | `403` |
| 3. Developer GET | `TOKEN=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"j1","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')` then `curl -s http://127.0.0.1:8000/api/v1/admin/flags -H "Authorization: Bearer $TOKEN"` | `200` + JSON with `flags.gobig_nlp_processing` |
| 4. Revoke flow | See below | After revoke, same token → `401` |

**Revoke flow (step 4):**
```bash
# Get a Developer token (use unique jti for revoke demo)
TOKEN=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"revoke_demo","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')

# Before revoke: works
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/admin/flags
# Expect: 200

# Revoke
curl -s -X POST http://127.0.0.1:8000/auth/revoke -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"reason":"demo"}'
# Expect: {"revoked":true}

# After revoke: blocked
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/admin/flags
# Expect: 401, {"detail":"Token revoked"}
```

### Automated test

```bash
pytest -q tests/test_auth_middleware.py -v
pytest -q tests/test_admin_flags.py -v
```

---

## API-M3-01: Feature flag admin

### Pass criteria

1. Empty or missing `reason` on `POST /api/v1/admin/flags/{flag_name}` → **400**
2. Valid Developer POST → audit file under `GOBIG_B2_FLAG_AUDIT_DIR`, Redis updated, **200** with `audit_path`

### Example (Developer)

```bash
TOKEN=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"ff1","exp":int(time.time())+3600,"admin_role":"Developer"}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
curl -s -X POST "http://127.0.0.1:8000/api/v1/admin/flags/gobig_nlp_processing" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled":true,"reason":"demo enable nlp"}'
```

---

## API-M1-02: B2 Pre-signed URL Endpoint

### Pass criteria

- Valid `upload_type` returns `{alias, max_size_mb, format, presigned_url}`
- Invalid `upload_type` → **422**
- Empty `content_type` → **422**
- Response **never** contains "b2" or "backblaze"

### Demo steps

| Step | Command | Expected |
|------|---------|----------|
| 1. Valid request | `curl -s -X POST http://127.0.0.1:8000/api/v1/storage/presign -H "Content-Type: application/json" -d '{"upload_type":"avatar","content_type":"image/png"}'` | `200`, JSON with `alias`, `max_size_mb`, `format`, `presigned_url`; `max_size_mb`=2 for avatar |
| 2. Invalid upload_type | `curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/api/v1/storage/presign -H "Content-Type: application/json" -d '{"upload_type":"invalid","content_type":"image/png"}'` | `422` |
| 3. Empty content_type | `curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/api/v1/storage/presign -H "Content-Type: application/json" -d '{"upload_type":"avatar","content_type":""}'` | `422` |
| 4. No B2 leak | Run step 1 and pipe to `grep -iE 'b2|backblaze'` | No matches |

### Automated test

```bash
pytest -q tests/test_storage_presign.py -v
```

---

## API-M1-03: NLP Ingest Staged Endpoint

### Pass criteria

- Flag **OFF** → **202** + "staged", no DB upsert
- Flag **ON** → **200** + "processed", DB upsert
- Missing `confidence` → **422**
- Idempotency: same `batch_id` twice → second is "duplicate"

### Demo steps

| Step | Command | Expected |
|------|---------|----------|
| 1. Flag OFF (default) | `curl -s -X POST http://127.0.0.1:8000/api/v1/nlp/ingest -H "Content-Type: application/json" -d '{"batch_id":"b1","recipes":[{"recipe_id":"r1","text":"hello","confidence":0.9}]}'` | `202`, `{"status":"staged"}` |
| 2. Missing confidence | `curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/api/v1/nlp/ingest -H "Content-Type: application/json" -d '{"batch_id":"b2","recipes":[{"recipe_id":"r1","text":"hello"}]}'` | `422` |
| 3. Flag ON + idempotency | Requires Redis flag `gobig_nlp_processing=1` and AI wrapper stub; easiest via test | See automated test |

### Automated test (includes flag ON + idempotency)

```bash
pytest -q tests/test_nlp_ingest.py -v
```

---

## API-M1-04: Social Graph Endpoints

### Pass criteria

- Follow/unfollow updates profile counts
- Feed: cursor pagination, 20 items/page, non-overlapping pages
- Profile: `Cache-Control: public, max-age=300`
- Recipe-share invalidates follower feed cache
- Potluck create/invite publish pub/sub events

### Demo steps (need JWT)

```bash
TOKEN=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"j1","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
```

| Step | Command | Expected |
|------|---------|----------|
| 1. Profile + Cache-Control | `curl -sI "http://127.0.0.1:8000/social/profile/u2" -H "Authorization: Bearer $TOKEN" \| grep -i cache-control` | `Cache-Control: public, max-age=300` |
| 2. Follow | `curl -s -X POST "http://127.0.0.1:8000/social/follow/u_target" -H "Authorization: Bearer $TOKEN"` | `200` |
| 3. Profile count | `curl -s "http://127.0.0.1:8000/social/profile/u_target" -H "Authorization: Bearer $TOKEN"` | `{"followers":1,...}` |
| 4. Unfollow | `curl -s -X DELETE "http://127.0.0.1:8000/social/unfollow/u_target" -H "Authorization: Bearer $TOKEN"` | `200` |
| 5. Feed pagination | Create 50 recipe-share events, then GET feed with cursor; verify 20+20 non-overlapping | See automated test |

### Automated test (full coverage)

```bash
pytest -q tests/test_social_graph.py -v
```

---

## API-M1-05: WebRTC WebSocket Signaling

Detailed manual setup for listener/sender terminals: `WEBSOCKET_TEST_GUIDE.md`.

### Pass criteria

- No token → connection rejected
- SDP offer from client 1 received by client 2 (cross-pod)
- Disconnect removes client from Redis room set

### Demo steps (manual WebSocket)

Use the example client at `scripts/example_websocket_client.py` (recommended), or `websocat`.

1. **Reject without token**
   ```bash
   websocat ws://127.0.0.1:8000/ws/room/room1
   # Expect: connection closed with policy violation
   ```

2. **Accept with token**
   ```bash
   TOKEN=$(python3 -c 'import jwt,time; p={"sub":"u1","jti":"j1","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
   python3 scripts/example_websocket_client.py --room-id room1 --token "$TOKEN" --client-name listener --mode listen
   # Expect: connection stays open and prints incoming messages
   ```

3. **Cross-pod SDP forwarding** (two terminals):
   ```bash
   # Terminal A (listener)
   TOKEN_A=$(python3 -c 'import jwt,time; p={"sub":"u-listener","jti":"j-listener","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
   python3 scripts/example_websocket_client.py --room-id room1 --token "$TOKEN_A" --client-name listener --mode listen

   # Terminal B (sender)
   TOKEN_B=$(python3 -c 'import jwt,time; p={"sub":"u-sender","jti":"j-sender","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
   python3 scripts/example_websocket_client.py --room-id room1 --token "$TOKEN_B" --client-name sender --mode send --type sdp_offer --message "offer-sdp-demo"
   ```
   Expected: Terminal A receives a JSON message envelope with `type: "sdp_offer"` and your payload under `data`.

### Automated test

```bash
pytest -q tests/test_websocket_signaling.py -v
```

---

## API-M1-06: Prometheus Histograms

### Pass criteria

- `curl /metrics` shows `_bucket`, `_count`, `_sum` for `request_latency_seconds`

### Demo steps

| Step | Command | Expected |
|------|---------|----------|
| 1. Generate traffic | `curl -s http://127.0.0.1:8000/docs > /dev/null` | (any 200) |
| 2. Metrics | `curl -s http://127.0.0.1:8000/metrics` | Contains `request_latency_seconds_bucket`, `_count`, `_sum` |

```bash
curl -s http://127.0.0.1:8000/docs > /dev/null
curl -s http://127.0.0.1:8000/metrics | grep -E 'request_latency_seconds_(bucket|count|sum)'
# Expect: multiple lines with _bucket, _count, _sum
```

### Automated test

```bash
pytest -q tests/test_prometheus_metrics.py -v
```

---

## Run all JON-A tests

```bash
pytest -q tests/test_auth_middleware.py tests/test_storage_presign.py tests/test_nlp_ingest.py tests/test_social_graph.py tests/test_websocket_signaling.py tests/test_prometheus_metrics.py -v
```

Or simply:

```bash
pytest -q
```
