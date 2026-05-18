# WebSocket Manual Test Guide (`/ws/room/{room_id}`)

This guide shows exactly how to verify live message forwarding for API-M1-05.

## URL template

```text
ws://127.0.0.1:8000/ws/room/{{ws_room_id}}?token={{ws_token}}
```

Use the same `ws_room_id` for listener and sender. Use different valid JWT tokens.

## 1) Start API

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --env-file .env
```

## 2) Start listener terminal

```bash
source .venv/bin/activate
WS_ROOM_ID='room-m1-05-live'
WS_TOKEN_LISTENER=$(python3 -c 'import jwt,time,uuid; p={"sub":"listener-new","jti":f"jti-{uuid.uuid4()}","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
python3 -u scripts/example_websocket_client.py --room-id "$WS_ROOM_ID" --token "$WS_TOKEN_LISTENER" --client-name listener-new --mode listen
```

Expected:
- prints a `connected to ws://...` line
- stays open waiting for messages

## 3) Start sender terminal (confirmed working flow)

```bash
source .venv/bin/activate
WS_ROOM_ID='room-m1-05-live'
WS_TOKEN_SENDER=$(python3 -c 'import jwt,time,uuid; p={"sub":"sender-new","jti":f"jti-{uuid.uuid4()}","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
python3 scripts/example_websocket_client.py --room-id "$WS_ROOM_ID" --token "$WS_TOKEN_SENDER" --client-name sender-new --mode send --type sdp_offer --message "hello-after-fix"
```

Expected sender output:

```text
[sender-new] sent: {"type": "sdp_offer", "data": {"message": "hello-after-fix", "nonce": "..."}}
[sender-new] no response within 3.0s
```

`no response within 3.0s` is expected in sender mode because server does not echo back to the same socket. Success is verified on the listener terminal.

## 4) Success criteria

Listener terminal should show:

```text
[listener-new] received: {"sender_id":"...","user_id":"sender-new","type":"sdp_offer","data":{"message":"hello-after-fix","nonce":"..."}}
```

## 5) Postman usage

Set variables without quotes:
- `ws_room_id = room-m1-05-live`
- `ws_token = <valid jwt>`

Connect to:

```text
ws://127.0.0.1:8000/ws/room/{{ws_room_id}}?token={{ws_token}}
```

Send payload:

```json
{"type":"sdp_offer","data":{"message":"hello-after-fix"}}
```

## 6) Common issues

- **HTTP 403 on connect**: token is empty/invalid/expired or signed by wrong key.
- **Sender shows sent but listener gets nothing**:
  - ensure listener is connected first
  - ensure same room ID on both clients
  - restart API server to pick latest websocket code
  - ensure each terminal generated its own token variable in that same terminal session

Fast Listener
source .venv/bin/activate
WS_ROOM_ID='room-m1-05-live'
WS_TOKEN_LISTENER=$(python3 -c 'import jwt,time,uuid; p={"sub":"listener-new","jti":f"jti-{uuid.uuid4()}","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
python3 -u scripts/example_websocket_client.py --room-id "$WS_ROOM_ID" --token "$WS_TOKEN_LISTENER" --client-name listener-new --mode listen

Fast Sender
source .venv/bin/activate
WS_ROOM_ID='room-m1-05-live'
WS_TOKEN_SENDER=$(python3 -c 'import jwt,time,uuid; p={"sub":"sender-new","jti":f"jti-{uuid.uuid4()}","exp":int(time.time())+3600}; print(jwt.encode(p, open("keys/iam_private.pem").read(), algorithm="RS256"))')
python3 scripts/example_websocket_client.py --room-id "$WS_ROOM_ID" --token "$WS_TOKEN_SENDER" --client-name sender-new --mode send --type sdp_offer --message "hello-after-fix"