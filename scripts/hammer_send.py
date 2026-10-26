#!/usr/bin/env python3
"""
scripts/hammer_send.py — Concurrency hammer test for CrewLink send path, Rule 2,
and the Funnel Rule.

Steps:
1. Parse leader and member credentials from README.md.
2. 20 parallel creates with a brand-new Idempotency-Key:
   - Expect: exactly one 201, nineteen 200, one distinct announcement ID.
3. 20 parallel sends on that created draft:
   - Expect: exactly one 202, nineteen 200.
4. Wait for background send path processing to complete (status: 'sent').
5. Log in as the seeded Local 27 member and make:
   - One real HTTP receipts call (delivered=True, read=True).
   - One real HTTP respond call (response='coming').
6. Run simulate_activity through:
   - Environment override SIMULATE_ACTIVITY_CMD if set, OR
   - 'docker compose exec -T api python manage.py simulate_activity <id>', OR
   - local venv python manage.py simulate_activity <id>.
   - Parse ground-truth JSON line.
7. Wait for 10-second stats cache expiry, fetch stats, and assert:
   - stats match ground-truth JSON exactly.
   - delivered, read, acknowledged, coming, cant_make_it are non-zero.
   - targeted >= delivered >= read >= acknowledged >= coming + cant_make_it.
   - no_response == targeted - coming - cant_make_it.
   - Print small table.
8. Run invariant check and print PASS or FAIL.
"""

import collections
import concurrent.futures
import json
import os
import re
import shlex
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
    member_info = config["locals"][0]["member"]
    leader_user = leader_info["username"]
    leader_pass = leader_info["password"]
    member_user = member_info["username"]
    member_pass = member_info["password"]

    print(f"Connecting to {base_url} as leader '{leader_user}'...")

    with httpx.Client(base_url=base_url, timeout=30.0) as leader_client:
        # 1. Login as leader
        login_resp = leader_client.post(
            "/api/auth/login/",
            json={"username": leader_user, "password": leader_pass},
        )
        if login_resp.status_code != 200:
            print(f"Leader login failed: {login_resp.status_code} {login_resp.text}")
            sys.exit(1)

        leader_token = login_resp.json()["token"]
        leader_headers = {
            "Authorization": f"Token {leader_token}",
            "Content-Type": "application/json",
        }

        # 2. 20 parallel creates with a brand-new Idempotency-Key
        idem_key = f"hammer-create-{uuid.uuid4()}"
        draft_payload = {
            "title": f"Hammer Test Callout {uuid.uuid4().hex[:6]}",
            "body": "Concurrency hammer test verifying idempotency, send path, and funnel.",
            "needs_ack": True,
            "source": "manual",
        }
        create_headers = dict(leader_headers)
        create_headers["Idempotency-Key"] = idem_key

        print(f"\n1. Firing 20 parallel Create requests with brand-new Idempotency-Key '{idem_key}'...")

        def create_request():
            with httpx.Client(base_url=base_url, timeout=30.0) as c:
                r = c.post("/api/announcements/", json=draft_payload, headers=create_headers)
                return r.status_code, r.json().get("id")

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            create_results = list(executor.map(lambda _: create_request(), range(20)))

        create_codes = [code for code, _ in create_results]
        created_ids = {aid for _, aid in create_results if aid}
        create_counts = collections.Counter(create_codes)
        print(f"Create status codes seen: {dict(create_counts)} (expected: 1x 201, 19x 200)")
        print(f"Distinct announcement IDs: {len(created_ids)} (expected: 1)")

        if create_counts.get(201) != 1 or create_counts.get(200) != 19 or len(created_ids) != 1:
            print("FAILURE: Concurrent creation did not yield exactly one 201 and 19x 200 with 1 distinct ID.")
            sys.exit(1)

        ann_id = created_ids.pop()
        print(f"Using created draft announcement {ann_id}")

        # 3. 20 parallel Send requests for that draft
        print(f"\n2. Firing 20 parallel Send requests for announcement {ann_id}...")
        send_url = f"/api/announcements/{ann_id}/send/"

        def send_request():
            with httpx.Client(base_url=base_url, timeout=30.0) as c:
                r = c.post(send_url, headers=leader_headers)
                return r.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            send_codes = list(executor.map(lambda _: send_request(), range(20)))

        send_counts = collections.Counter(send_codes)
        print(f"Send status codes seen: {dict(send_counts)} (expected: 1x 202, 19x 200)")

        if send_counts.get(202) != 1 or send_counts.get(200) != 19:
            print("FAILURE: Concurrent send did not yield exactly one 202 and 19x 200.")
            sys.exit(1)

        # 4. Wait for background processing to reach 'sent'
        print("\n3. Waiting for background fan-out and push processing to complete...")
        stats = {}
        for _ in range(40):
            time.sleep(0.5)
            stats_resp = leader_client.get(f"/api/announcements/{ann_id}/stats/", headers=leader_headers)
            if stats_resp.status_code == 200:
                stats = stats_resp.json()
                if stats.get("status") in ("sent", "closed"):
                    break

        print(f"Announcement reached status: '{stats.get('status')}' (targeted: {stats.get('targeted')}, sent: {stats.get('sent')})")
        if stats.get("status") != "sent":
            print(f"FAILURE: Announcement did not reach 'sent' status within timeout. Current: {stats}")
            sys.exit(1)

        # 5. Seeded Local 27 member makes real HTTP receipts and respond calls
        print(f"\n4. Logging in as member '{member_user}' to record real receipts & response...")
        with httpx.Client(base_url=base_url, timeout=30.0) as member_client:
            mem_login_resp = member_client.post(
                "/api/auth/login/",
                json={"username": member_user, "password": member_pass},
            )
            if mem_login_resp.status_code != 200:
                print(f"Member login failed: {mem_login_resp.status_code} {mem_login_resp.text}")
                sys.exit(1)

            mem_token = mem_login_resp.json()["token"]
            mem_headers = {
                "Authorization": f"Token {mem_token}",
                "Content-Type": "application/json",
            }

            # Receipts batch
            receipt_resp = member_client.post(
                "/api/me/receipts/",
                json={"announcement_ids": [ann_id], "kind": "delivered"},
                headers=mem_headers,
            )
            print(f"Member receipts HTTP response: {receipt_resp.status_code} {receipt_resp.json()}")

            # Respond
            respond_resp = member_client.post(
                f"/api/me/announcements/{ann_id}/respond/",
                json={"response": "coming"},
                headers=mem_headers,
            )
            print(f"Member respond HTTP response: {respond_resp.status_code} {respond_resp.json()}")

        # 6. Run simulate_activity
        print(f"\n5. Running simulate_activity for announcement {ann_id}...")
        custom_cmd = os.environ.get("SIMULATE_ACTIVITY_CMD")
        ground_truth = None

        if custom_cmd:
            cmd = shlex.split(custom_cmd) + [str(ann_id)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                ground_truth = _parse_ground_truth(res.stdout)
        else:
            # Try docker compose first
            docker_cmd = [
                "docker", "compose", "exec", "-T", "api",
                "python", "manage.py", "simulate_activity", str(ann_id)
            ]
            res = subprocess.run(docker_cmd, cwd=REPO_ROOT, capture_output=True, text=True)
            if res.returncode == 0:
                ground_truth = _parse_ground_truth(res.stdout)
            else:
                # Fallback to local python in DJANGO_DIR
                local_cmd = [sys.executable, "manage.py", "simulate_activity", str(ann_id)]
                res = subprocess.run(local_cmd, cwd=DJANGO_DIR, capture_output=True, text=True)
                if res.returncode == 0:
                    ground_truth = _parse_ground_truth(res.stdout)

        if not ground_truth:
            manual_cmd = f"docker compose exec -T api python manage.py simulate_activity {ann_id}"
            print(f"Activity step skipped. Run manually via:\n  {manual_cmd}")
            sys.exit(1)

        print(f"Ground-truth counts from simulate_activity:\n{json.dumps(ground_truth, indent=2)}")

        # 7. Wait for 10-second stats cache to expire, fetch stats, and assert
        print("\n6. Waiting 10 seconds for stats cache to expire...")
        time.sleep(10.5)

        stats_resp = leader_client.get(f"/api/announcements/{ann_id}/stats/", headers=leader_headers)
        if stats_resp.status_code != 200:
            print(f"Failed to fetch stats: {stats_resp.status_code} {stats_resp.text}")
            sys.exit(1)

        fetched_stats = stats_resp.json()
        print("\n=== ANNOUNCEMENT STATS TABLE ===")
        print(f"{'Metric':<18} | {'Value':<8} | {'Ground Truth':<12}")
        print("-" * 44)
        for k in ["targeted", "sent", "push_failed", "delivered", "read", "acknowledged", "coming", "cant_make_it", "no_response"]:
            val = fetched_stats.get(k)
            gt_val = ground_truth.get(k)
            print(f"{k:<18} | {str(val):<8} | {str(gt_val):<12}")
            assert val == gt_val, f"Mismatch on {k}: {val} != {gt_val}"

        # Assert non-zero
        for metric in ["delivered", "read", "acknowledged", "coming", "cant_make_it"]:
            assert fetched_stats[metric] > 0, f"Expected {metric} > 0, got {fetched_stats[metric]}"

        # Assert nesting
        assert fetched_stats["targeted"] >= fetched_stats["delivered"], "targeted >= delivered failed"
        assert fetched_stats["delivered"] >= fetched_stats["read"], "delivered >= read failed"
        assert fetched_stats["read"] >= fetched_stats["acknowledged"], "read >= acknowledged failed"
        assert fetched_stats["acknowledged"] >= (fetched_stats["coming"] + fetched_stats["cant_make_it"]), "ack >= coming + cant_make_it failed"
        assert fetched_stats["no_response"] == (fetched_stats["targeted"] - fetched_stats["coming"] - fetched_stats["cant_make_it"]), "no_response equation failed"
        print("Stats equality, non-zero checks, and funnel nesting assertions all PASSED.")

        # 8. Invariant check
        print("\n7. Running invariant check...")
        check_cmd = ["docker", "compose", "exec", "-T", "api", "python", "manage.py", "check_invariants"]
        res = subprocess.run(check_cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback to local
            check_cmd = [sys.executable, "manage.py", "check_invariants"]
            res = subprocess.run(check_cmd, cwd=DJANGO_DIR, capture_output=True, text=True)

        inv_output = res.stdout.strip() or res.stderr.strip()
        print(f"Invariant check result: {inv_output}")

        if "OK" in inv_output:
            print("\nRESULT: PASS")
        else:
            print("\nRESULT: FAIL")
            sys.exit(1)


def _parse_ground_truth(stdout_text):
    for line in stdout_text.strip().splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return None


if __name__ == "__main__":
    main()
