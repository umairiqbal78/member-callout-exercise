# CrewLink

## How to Run
1. **Backend:** `docker compose up` starts all services and seeds demo data on `http://localhost:8000`.
2. **Frontend (Flutter Web):**
   ```bash
   cd flutter_member_callout
   flutter run -d chrome
   ```

## Logins & UI
A Flutter Web UI is provided for both leader and member workflows:
- **Local 27:** Leader: `leader_local27` / `Demo1234!` | Member: `member_local27` / `Demo1234!`
- **Local 104:** Leader: `leader_local104` / `Demo1234!` | Member: `member_local104` / `Demo1234!`
- *Features:* AI drafting & approval gate, send callouts, live delivery stats, inbox with pull-to-refresh, automatic receipts, Got It acknowledgement, and RSVP (Coming / Can't make it).

## Verification (Rules 1 & 2)
- **Rule 1 (Isolation):** Cross-local queries strictly return 404 (scoped at DB and permission levels).
- **Rule 2 (No Double-Delivery):** Atomic CAS (`UPDATE ... WHERE status='draft'`) guarantees exactly-once send.
  - Backend tests: `docker compose exec api pytest`
  - Concurrency hammer: `python scripts/hammer_send.py` (20 parallel retried sends -> 1 succeeds, 0 duplicate pushes)
  - Frontend tests: `cd flutter_member_callout && flutter test`
