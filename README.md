# CrewLink

CrewLink is a callout and dispatch platform for union locals. It ensures union members receive callouts reliably and gives leaders real-time visibility into receipts (delivered, read, acknowledged) and RSVPs (coming, can't make it), while enforcing strict local-level data isolation (Rule 1) and exactly-once delivery guarantees (Rule 2).

---

## Running the Application

### 1. Backend & Infrastructure (Docker Compose)
```bash
docker compose up
```
Starts PostgreSQL, Redis, Celery worker, Celery beat, and the Django API server on `http://localhost:8000`. Database migrations and `seed_demo` run automatically on startup. Wait for `seed_demo complete.` in the console before making requests.

### 2. Frontend (Flutter Web)
```bash
cd flutter_member_callout
flutter run -d chrome
```
Runs the web app in Chrome (defaults to backend at `http://localhost:8000`).

---

## TEST ACCOUNTS

```json
{
  "base_url": "http://localhost:8000",
  "auth": {
    "login_method": "POST",
    "login_path": "/api/auth/login/",
    "login_body": {
      "username": "<username>",
      "password": "<password>"
    },
    "token_usage": "Authorization: Token <token>"
  },
  "locals": [
    {
      "name": "Local 27",
      "id": "2a004577-dd0d-56f6-b5a1-59dad9c37f66",
      "leader": {
        "username": "leader_local27",
        "password": "Demo1234!",
        "member_id": "add59109-72d9-5ad6-b96a-14ebb1e434a5"
      },
      "member": {
        "username": "member_local27",
        "password": "Demo1234!",
        "member_id": "20ba580d-34cb-5a1c-9f9e-d771f6c10222"
      }
    },
    {
      "name": "Local 104",
      "id": "aaf64bfb-be56-54c6-9066-2061c7ecbbbd",
      "leader": {
        "username": "leader_local104",
        "password": "Demo1234!",
        "member_id": "f059cc9d-2b36-5647-b7fd-8896eb8d01eb"
      },
      "member": {
        "username": "member_local104",
        "password": "Demo1234!",
        "member_id": "e4c9f755-5e08-58e4-b214-a1dacb7118ef"
      }
    }
  ],
  "announcement_local27_id": "81c7d323-2c24-5fea-93d1-faffc3a9e9b8",
  "member_local104_id": "e4c9f755-5e08-58e4-b214-a1dacb7118ef",
  "endpoints": [
    {
      "method": "POST",
      "path": "/api/auth/login/",
      "auth": "none",
      "roles": "anyone",
      "notes": "Returns {token, member_id}"
    },
    {
      "method": "GET",
      "path": "/api/me/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member",
      "notes": "Returns caller's member id, local {id, name}, and leadership roles"
    },
    {
      "method": "GET",
      "path": "/api/announcements/",
      "auth": "Authorization: Token <token>",
      "roles": "leader only",
      "notes": "Lists announcements belonging to caller's local"
    },
    {
      "method": "POST",
      "path": "/api/announcements/",
      "auth": "Authorization: Token <token>",
      "headers": "Idempotency-Key: <unique-key> (optional)",
      "roles": "leader only",
      "notes": "Creates a draft announcement scoped to caller's local. Retried POST with same key returns existing announcement."
    },
    {
      "method": "POST",
      "path": "/api/announcements/ai-draft/",
      "auth": "Authorization: Token <token>",
      "headers": "Idempotency-Key: <unique-key> (optional)",
      "roles": "leader of that local only",
      "notes": "Generates AI draft with title, body, push_preview (<=120), and fact-check warnings. Requires human approval before send."
    },
    {
      "method": "GET",
      "path": "/api/announcements/{id}/",
      "auth": "Authorization: Token <token>",
      "roles": "leader only — 404 for other locals"
    },
    {
      "method": "PATCH",
      "path": "/api/announcements/{id}/",
      "auth": "Authorization: Token <token>",
      "roles": "leader only — only while status=draft; any text edit clears approved_at"
    },
    {
      "method": "POST",
      "path": "/api/announcements/{id}/approve/",
      "auth": "Authorization: Token <token>",
      "roles": "leader of that local only",
      "notes": "Approves draft announcement. Required before an AI-sourced announcement can be sent."
    },
    {
      "method": "POST",
      "path": "/api/announcements/{id}/send/",
      "auth": "Authorization: Token <token>",
      "headers": "Idempotency-Key: <unique-key> (optional)",
      "roles": "leader of that local only",
      "notes": "Atomic CAS draft -> sending. Enqueues one Celery fan-out task. 202 on first send, 200 on retry."
    },
    {
      "method": "GET",
      "path": "/api/announcements/{id}/stats/",
      "auth": "Authorization: Token <token>",
      "roles": "leader of that local only",
      "notes": "Grouped count cached in Redis for 10s. If status=closed, returns saved final_counts."
    },
    {
      "method": "GET",
      "path": "/api/me/inbox/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member",
      "notes": "Returns caller's own recipient rows, newest-first. Cursor paginated; supports ?since=<ISO-8601>"
    },
    {
      "method": "POST",
      "path": "/api/me/receipts/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member",
      "notes": "Body: {announcement_ids: [...], kind: 'delivered' | 'read'}. Idempotent."
    },
    {
      "method": "POST",
      "path": "/api/me/announcements/{id}/acknowledge/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member",
      "notes": "Sets acknowledged_at and backfills read_at/delivered_at if empty. 409 if closed."
    },
    {
      "method": "POST",
      "path": "/api/me/announcements/{id}/respond/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member",
      "notes": "Body: {response: 'coming' | 'cant_make_it'}. Idempotent. 409 if closed."
    },
    {
      "method": "GET",
      "path": "/api/classifications/?local_id=...",
      "auth": "Authorization: Token <token>",
      "roles": "leader of that local only",
      "notes": "Returns distinct classifications of active members of that local (404 for other locals, 403 for members)."
    }
  ]
}
```

---

## Member Read / Acknowledge Flow (cURL)

```bash
# 1. Login as member (Local 27)
MEMBER_TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"member_local27","password":"Demo1234!"}' | jq -r .token)

# 2. View member inbox
curl -s -H "Authorization: Token $MEMBER_TOKEN" http://localhost:8000/api/me/inbox/

# 3. Report delivered and read receipts
curl -s -X POST http://localhost:8000/api/me/receipts/ \
  -H "Authorization: Token $MEMBER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"announcement_ids":["81c7d323-2c24-5fea-93d1-faffc3a9e9b8"],"kind":"read"}'

# 4. Acknowledge the announcement ("Got it")
curl -s -X POST http://localhost:8000/api/me/announcements/81c7d323-2c24-5fea-93d1-faffc3a9e9b8/acknowledge/ \
  -H "Authorization: Token $MEMBER_TOKEN"

# 5. RSVP response ("Coming" / "Can't make it")
curl -s -X POST http://localhost:8000/api/me/announcements/81c7d323-2c24-5fea-93d1-faffc3a9e9b8/respond/ \
  -H "Authorization: Token $MEMBER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"response":"coming"}'
```

---

## Rule 1: Scoped Isolation (cURL Examples)

Nobody sees or touches data outside their own local access. Queries are filtered to the authenticated caller's local at the database and permission layers. Requesting another local's data strictly returns `404 Not Found` (never `403`), preventing existence leaks.

```bash
# Example A: Local 27 login attempts to read Local 104's announcement:
curl -s -i -H "Authorization: Token $MEMBER_TOKEN" \
  http://localhost:8000/api/announcements/fc7f133c-02d1-5a72-8f4d-940f9f714190/

# Response:
# HTTP/1.1 404 Not Found
# {"detail":"Not found."}

# Example B: Local 104 leader attempts to access Local 27's announcement:
LEADER_104_TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"leader_local104","password":"Demo1234!"}' | jq -r .token)

curl -s -i -H "Authorization: Token $LEADER_104_TOKEN" \
  http://localhost:8000/api/announcements/81c7d323-2c24-5fea-93d1-faffc3a9e9b8/

# Response:
# HTTP/1.1 404 Not Found
# {"detail":"Not found."}
```

---

## Rule 2 Verification: No Double-Delivery

A retry or restart must never deliver twice to the same member.
1. **Atomic CAS:** Sending executes `UPDATE announcements SET status='sending', sent_at=now() WHERE id=%s AND status='draft'`. The first request updates 1 row and returns `202 Accepted`. Any retry updates 0 rows and returns `200 OK` without creating duplicate recipient rows or re-enqueuing fan-out.
2. **Recipient Uniqueness:** Enforced by unique constraint `(announcement_id, member_id)` with `ON CONFLICT DO NOTHING`.
3. **Push Claim:** Workers claim batches with `UPDATE announcement_recipients SET push_sent_at=now() WHERE push_sent_at IS NULL RETURNING member_id`.

### cURL Verification Sequence
```bash
LEADER_TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"leader_local27","password":"Demo1234!"}' | jq -r .token)

# 1. Create a draft with Idempotency-Key
ANN_ID=$(curl -s -X POST http://localhost:8000/api/announcements/ \
  -H "Authorization: Token $LEADER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: send-retry-test-1" \
  -d '{"title":"Test Dispatch","body":"Verify no double delivery.","needs_ack":true}' | jq -r .id)

# 2. First send -> returns 202 Accepted
curl -s -i -X POST http://localhost:8000/api/announcements/$ANN_ID/send/ \
  -H "Authorization: Token $LEADER_TOKEN"
# HTTP/1.1 202 Accepted (status: "sending")

# 3. Retried send -> returns 200 OK (no-op, does not duplicate recipient rows)
curl -s -i -X POST http://localhost:8000/api/announcements/$ANN_ID/send/ \
  -H "Authorization: Token $LEADER_TOKEN"
# HTTP/1.1 200 OK (status: "sending" or "sent")
```

### Automated Concurrency Verification & Test Suites
```bash
# 1. Concurrency Hammer Script: fires 20 parallel sends against one announcement
# Verifies exactly 1 send returns 202, 19 return 200, exactly 1 row per active member, 0 duplicate pushes
python scripts/hammer_send.py

# 2. Backend Automated Test Suite (Sessions 1, 2, 3a, 3b)
docker compose exec api pytest

# 3. Flutter Widget Test Suite (12 passing tests)
cd flutter_member_callout && flutter test
```
