#!/usr/bin/env python3
"""
scripts/hammer_send.py — Concurrency hammer test for CrewLink send path & Rule 2.

Steps:
1. Parse leader credentials from README.md.
2. Log in as leader to obtain an auth token.
3. Create a draft announcement with an Idempotency-Key.
4. Fire 20 parallel Send requests for that draft.
5. Fire 20 parallel Create requests with the same Idempotency-Key.
6. Wait for background send path processing to complete.
7. Print:
   - Status codes seen for Sends (expect: 1x 202, 19x 200)
   - Status codes seen for Creates (expect: 20x 200)
   - Recipient count
   - Distinct push_log members
   - Invariant check result (OK)
"""

import collections
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
import uuid

try:
    import httpx
except ImportError:
    print("httpx is required. Install with: pip install httpx")
    sys.exit(1)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
README_PATH = os.path.join(REPO_ROOT, "README.md")
DJANGO_DIR = os.path.join(REPO_ROOT, "django-member-callout")


def load_readme_config():
    with open(README_PATH) as f:
        content = f.read()
    match = re.search(r"## TEST ACCOUNTS.*?```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if not match:
        raise ValueError("Could not find ## TEST ACCOUNTS JSON in README.md")
    return json.loads(match.group(1))


def main():
    config = load_readme_config()
    base_url = os.environ.get("BASE_URL", config.get("base_url", "http://localhost:8000")).rstrip("/")
    leader_info = config["locals"][0]["leader"]
    username = leader_info["username"]
    password = leader_info["password"]

    print(f"Connecting to {base_url} as leader '{username}'...")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        # 1. Login
        login_resp = client.post(
            "/api/auth/login/",
            json={"username": username, "password": password},
        )
        if login_resp.status_code != 200:
            print(f"Login failed: {login_resp.status_code} {login_resp.text}")
            sys.exit(1)

        token = login_resp.json()["token"]
        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        }

        # 2. Create a draft announcement with an Idempotency-Key
        idem_key = f"hammer-{uuid.uuid4()}"
        draft_payload = {
            "title": f"Hammer Test Callout {uuid.uuid4().hex[:6]}",
            "body": "Concurrency hammer test verifying idempotency and Rule 2.",
            "needs_ack": True,
            "source": "manual",
        }
        create_headers = dict(headers)
        create_headers["Idempotency-Key"] = idem_key

        create_resp = client.post("/api/announcements/", json=draft_payload, headers=create_headers)
        if create_resp.status_code not in (200, 201):
            print(f"Draft creation failed: {create_resp.status_code} {create_resp.text}")
            sys.exit(1)

        announcement = create_resp.json()
        ann_id = announcement["id"]
        print(f"Created draft announcement {ann_id} with Idempotency-Key '{idem_key}'")

        # 3. Fire 20 parallel Send requests
        print("\nFiring 20 parallel Send requests...")
        send_url = f"/api/announcements/{ann_id}/send/"

        def send_request():
            with httpx.Client(base_url=base_url, timeout=30.0) as c:
                r = c.post(send_url, headers=headers)
                return r.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            send_codes = list(executor.map(lambda _: send_request(), range(20)))

        send_counts = collections.Counter(send_codes)
        print(f"Send status codes seen: {dict(send_counts)}")

        # 4. Fire 20 parallel Create requests with the same Idempotency-Key
        print("\nFiring 20 parallel Create requests with the same Idempotency-Key...")

        def create_request():
            with httpx.Client(base_url=base_url, timeout=30.0) as c:
                r = c.post("/api/announcements/", json=draft_payload, headers=create_headers)
                return r.status_code, r.json().get("id")

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            create_results = list(executor.map(lambda _: create_request(), range(20)))

        create_codes = [code for code, _ in create_results]
        created_ids = {aid for _, aid in create_results}
        create_counts = collections.Counter(create_codes)
        print(f"Create status codes seen: {dict(create_counts)}")
        print(f"Distinct announcement IDs returned on retry creates: {len(created_ids)} (expected: 1)")

        # 5. Wait for background processing (polling stats until 'sent' or 15s)
        print("\nWaiting for background processing to complete...")
        stats = {}
        for _ in range(30):
            time.sleep(0.5)
            stats_resp = client.get(f"/api/announcements/{ann_id}/stats/", headers=headers)
            if stats_resp.status_code == 200:
                stats = stats_resp.json()
                if stats.get("status") in ("sent", "closed"):
                    break

        print(f"Final announcement stats: {stats}")
        recipient_count = stats.get("targeted", 0)
        sent_count = stats.get("sent", 0)
        print(f"Recipient count (targeted): {recipient_count}")
        print(f"Pushed count (sent): {sent_count}")

        # 6. Check invariant via management command or direct query
        print("\nRunning invariant check...")
        check_cmd = [sys.executable, "manage.py", "check_invariants"]
        res = subprocess.run(
            check_cmd,
            cwd=DJANGO_DIR,
            capture_output=True,
            text=True,
            env=os.environ,
        )
        invariant_result = res.stdout.strip() or res.stderr.strip()
        print(f"Invariant check result: {invariant_result}")

        # 7. Summary
        print("\n=== HAMMER TEST SUMMARY ===")
        print(f"- Send status codes: {dict(send_counts)} (expected exactly one 202)")
        print(f"- Create status codes: {dict(create_counts)} (expected all 200)")
        print(f"- Recipient count: {recipient_count}")
        print(f"- Invariant check: {invariant_result}")

        if send_counts.get(202) == 1 and all(code == 200 for code in create_codes) and "OK" in invariant_result:
            print("\nHAMMER TEST PASSED!")
        else:
            print("\nHAMMER TEST FAILED!")
            sys.exit(1)


if __name__ == "__main__":
    main()
