# member-callout-exercise
CrewLink is a fictional platform for labour unions, invented for this exercise. Union members are working people: electricians, transit operators, sheet-metal workers. Union leadership uses the platform to reach them.

---

## About CrewLink

CrewLink solves the "we missed people on the callout" problem. When a leader sends a callout, every active member of their local gets it in the app and it waits there until their phone has signal. Leaders see who received it, read it, and replied *Coming* or *Can't make it*. Nobody outside a local can see that local's data.

Session 1 delivers: the data model, PostgreSQL, DRF auth, all Rule 1 permission enforcement, and the full set of member/leader endpoints.

---

## Running

### Option 1 — Docker Compose

```bash
git clone <repo>
cd member-callout-exercise
docker compose up
```

That's it. The compose file runs `migrate` then `seed_demo` then starts `api` on port 8000. Wait for the log line `seed_demo complete.` before hitting the API.

### Option 2 — Local venv

```bash
# 1. Start only db and redis in Docker
docker compose up db redis

# 2. Copy the env file and adjust DB_HOST to localhost
cp django-member-callout/.env.example django-member-callout/.env
# Edit .env: set DB_HOST=localhost

# 3. Activate the venv and install deps
cd django-member-callout
source .venv/bin/activate
pip install -r requirements.txt

# 4. Migrate and seed
python manage.py migrate
python manage.py seed_demo

# 5. Run the dev server
python manage.py runserver
```

### Running tests

**In Docker (CI path):**
```bash
docker compose run --rm api pytest
```

**Local venv:**
```bash
cd django-member-callout
source .venv/bin/activate
pytest
```

Tests require the database to be seeded (`seed_demo` must have been run first). The test suite does **not** re-run seed_demo — it reads data the seed already created.

---

## TEST ACCOUNTS

```json
{
  "base_url": "http://localhost:8000",
  "auth": {
    "login_method": "POST",
    "login_path": "/api/auth/login/",
    "login_body": {"username": "<username>", "password": "<password>"},
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
  "announcement_local27_draft_id": "ada55d9c-8630-5bdd-a449-99ff3f838a47",
  "announcement_local104_draft_id": "fc7f133c-02d1-5a72-8f4d-940f9f714190",
  "member_local104_id": "e4c9f755-5e08-58e4-b214-a1dacb7118ef",
  "create_example_with_idempotency": {
    "method": "POST",
    "path": "/api/announcements/",
    "headers": {
      "Authorization": "Token <token>",
      "Idempotency-Key": "d8b37e0e-94d3-4f93-bc42-6e2eb27ef871",
      "Content-Type": "application/json"
    },
    "body": {
      "title": "Safety Meeting Announcement",
      "body": "Mandatory safety meeting this Friday.",
      "needs_ack": true,
      "source": "manual"
    }
  },
  "ai_draft_example": {
    "method": "POST",
    "path": "/api/announcements/ai-draft/",
    "headers": {
      "Authorization": "Token <token>",
      "Idempotency-Key": "sample-ai-draft-key-1",
      "Content-Type": "application/json"
    },
    "body": {
      "local_id": "2a004577-dd0d-56f6-b5a1-59dad9c37f66",
      "note": "emergency mtg thurs 6pm hall re: contractor pulling crews off the westside job, EVERYONE needs to be there this is the third time"
    },
    "notes": "AI_PROVIDER=fake works without a key. With GEMINI_API_KEY set, live Gemini is called."
  },
  "approve_example": {
    "method": "POST",
    "path": "/api/announcements/{id}/approve/",
    "headers": {
      "Authorization": "Token <token>"
    },
    "notes": "Approves a draft announcement. Required before an AI-sourced announcement can be sent."
  },
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
      "roles": "any authenticated member"
    },
    {
      "method": "GET",
      "path": "/api/announcements/",
      "auth": "Authorization: Token <token>",
      "roles": "leader only"
    },
    {
      "method": "POST",
      "path": "/api/announcements/",
      "auth": "Authorization: Token <token>",
      "headers": "Idempotency-Key: <unique-key> (optional)",
      "roles": "leader only",
      "notes": "Creates a draft announcement scoped to caller's local. Retries with same key return 200 with the existing announcement."
    },
    {
      "method": "POST",
      "path": "/api/announcements/ai-draft/",
      "auth": "Authorization: Token <token>",
      "headers": "Idempotency-Key: <unique-key> (optional)",
      "roles": "leader of that local only — 404 if wrong local, 403 for member",
      "notes": "Generates AI draft with title (<=100), body (<=2000), push_preview (<=120), and warnings. AI_PROVIDER=fake works without a key."
    },
    {
      "method": "GET",
      "path": "/api/announcements/{id}/",
      "auth": "Authorization: Token <token>",
      "roles": "leader only — 404 if id belongs to another local"
    },
    {
      "method": "PATCH",
      "path": "/api/announcements/{id}/",
      "auth": "Authorization: Token <token>",
      "roles": "leader only — only while status=draft; text edits clear approved_at"
    },
    {
      "method": "POST",
      "path": "/api/announcements/{id}/approve/",
      "auth": "Authorization: Token <token>",
      "roles": "leader of that local only — 404 if wrong local, 403 for member",
      "notes": "One UPDATE setting approved_by and approved_at. Required before sending AI draft."
    },
    {
      "method": "POST",
      "path": "/api/announcements/{id}/send/",
      "auth": "Authorization: Token <token>",
      "roles": "leader of that local only — 404 if wrong local, 403 for member",
      "notes": "Compare-and-set draft -> sending. Enqueues one fan-out task. 202 on first send, 200 on retry."
    },
    {
      "method": "GET",
      "path": "/api/announcements/{id}/stats/",
      "auth": "Authorization: Token <token>",
      "roles": "leader of that local only — 404 if wrong local, 403 for member",
      "notes": "Single query stats cached in shared Redis for 10s. If closed, returns final_counts without hitting DB."
    },
    {
      "method": "GET",
      "path": "/api/me/inbox/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member — returns own rows only",
      "notes": "Cursor paginated; supports ?since=<ISO-8601>"
    },
    {
      "method": "POST",
      "path": "/api/me/receipts/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member — only touches own rows",
      "notes": "Body: {announcement_ids: [...], delivered: bool, read: bool}"
    },
    {
      "method": "POST",
      "path": "/api/me/announcements/{id}/respond/",
      "auth": "Authorization: Token <token>",
      "roles": "any authenticated member — only touches own row",
      "notes": "Body: {response: 'coming' | 'cant_make_it'}. Returns 409 if announcement is closed."
    }
  ]
}
```

---

## CONCURRENCY HAMMER SCRIPT

A concurrency hammer script is available at `scripts/hammer_send.py`.

### How to run:
1. Ensure the backend services are running (`docker compose up` or local server + celery worker).
2. Run the script:
```bash
python scripts/hammer_send.py
```
Or with custom BASE_URL:
```bash
BASE_URL=http://localhost:8000 python scripts/hammer_send.py
```

The script:
- Logs in as the Local 27 leader.
- Creates a draft with a unique `Idempotency-Key`.
- Fires 20 parallel Send requests (verifying exactly 1 returns 202, 19 return 200).
- Fires 20 parallel Create requests with the same key (verifying all return 200 with the same ID).
- Verifies recipient count, distinct push_log members, and executes `python manage.py check_invariants`.
