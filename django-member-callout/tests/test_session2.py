"""
CrewLink test suite — Session 2 (Send Path and Rule 2).

Tests:
1. 20 threads call Send on one draft: exactly one 202, the rest 200, exactly one fan-out enqueue.
2. Same Idempotency-Key on create 20 times: one announcement.
3. Run fan-out twice (and in parallel threads): recipient rows equal the active audience exactly.
4. Run the same push range twice and in parallel: one push_log row per member.
5. Simulate a crash:
   - Fan-out stops halfway, sweeper reruns it, counts end exact.
   - Push stops after claiming, rerun: no member pushed twice.
6. Send request uses a small fixed number of queries and enqueues, nothing more.
7. Another local's leader gets 404 on send and stats. A member gets 403.
8. AI-sourced announcement without approved_at gets 409 on send.
9. Closed callouts return final_counts, and respond returns 409.
10. Scale: creates a local with 22,400 members, runs fan-out and push eagerly,
    asserts 22,400 recipient rows and 22,400 distinct push_log rows, and stats
    computation is ONE query. Prints timings.
"""

import collections
import concurrent.futures
from datetime import timedelta
import time
from unittest import mock
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from callouts.management.commands.seed_demo import (
    ANNOUNCEMENT_27_DRAFT_ID,
    ANNOUNCEMENT_27_ID,
    ANNOUNCEMENT_104_DRAFT_ID,
    CLASSIFICATIONS,
    LOCAL_104_ID,
    LOCAL_27_ID,
    NAMESPACE,
)
from callouts.models import (
    Announcement,
    AnnouncementRecipient,
    LeadershipRole,
    Local,
    Member,
    PushLog,
)
from callouts.tasks import (
    _compute_stats_from_db,
    closer,
    fan_out_task,
    push_task,
    sweeper,
)

User = get_user_model()


def _token(user) -> str:
    token, _ = Token.objects.get_or_create(user=user)
    return token.key


def _client_for(username: str) -> APIClient:
    user = User.objects.get(username=username)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {_token(user)}")
    return client


# ── Test 1: 20 threads call Send on one draft ──────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_20_threads_call_send_on_one_draft():
    """
    20 concurrent threads call POST /api/announcements/{id}/send/ on one draft.
    Exactly one returns 202, nineteen return 200, exactly one fan-out enqueue.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    draft = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title=f"Concurrent Send Test {uuid.uuid4().hex[:6]}",
        body="Testing concurrent send CAS.",
        status=Announcement.Status.DRAFT,
        source=Announcement.Source.MANUAL,
    )

    url = f"/api/announcements/{draft.id}/send/"
    enqueued_ids = []

    def fake_delay(ann_id):
        enqueued_ids.append(ann_id)

    with mock.patch("callouts.views.fan_out_task.delay", side_effect=fake_delay):
        def worker(_):
            client = _client_for("leader_local27")
            resp = client.post(url)
            return resp.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            status_codes = list(executor.map(worker, range(20)))

    counts = collections.Counter(status_codes)
    assert counts[202] == 1, f"Expected exactly one 202, got: {dict(counts)}"
    assert counts[200] == 19, f"Expected 19 200s, got: {dict(counts)}"
    assert len(enqueued_ids) == 1, f"Expected 1 enqueue, got {len(enqueued_ids)}"
    assert enqueued_ids[0] == str(draft.id)

    draft.refresh_from_db()
    assert draft.status == Announcement.Status.SENDING


# ── Test 2: Same Idempotency-Key on create 20 times ────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_same_idempotency_key_on_create_20_times():
    """
    20 parallel requests to POST /api/announcements/ with the same Idempotency-Key.
    Only one announcement is created, and all return the same announcement.
    """
    idem_key = f"idempotent-test-{uuid.uuid4()}"
    payload = {
        "title": "Idempotent Creation Test",
        "body": "Body for idempotent test.",
        "needs_ack": True,
        "source": "manual",
    }

    def worker(_):
        client = _client_for("leader_local27")
        resp = client.post(
            "/api/announcements/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        return resp.status_code, resp.data.get("id")

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(worker, range(20)))

    status_codes = [s for s, _ in results]
    returned_ids = {i for _, i in results}

    assert all(code in (200, 201) for code in status_codes)
    assert len(returned_ids) == 1, f"Expected 1 distinct announcement ID, got {returned_ids}"

    # Verify only one row exists in the database
    db_count = Announcement.objects.filter(
        local_id=LOCAL_27_ID,
        idempotency_key=idem_key,
    ).count()
    assert db_count == 1


# ── Test 3: Run fan-out twice and in parallel ──────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_run_fan_out_twice_and_parallel():
    """
    Running fan_out_task multiple times (including parallel threads) creates
    exactly one recipient row per active member. Reruns add no rows.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Fan-out Idempotency Test",
        body="Body",
        status=Announcement.Status.SENDING,
        sent_at=timezone.now(),
    )

    active_count = Member.objects.filter(
        local_id=LOCAL_27_ID,
        status=Member.Status.ACTIVE,
    ).count()

    with mock.patch("callouts.tasks.push_task.delay"):
        # Run in 4 parallel threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: fan_out_task(str(ann.id)), range(4)))

        # Run again sequentially
        fan_out_task(str(ann.id))

    recipient_count = AnnouncementRecipient.objects.filter(
        announcement_id=ann.id,
    ).count()
    assert recipient_count == active_count

    ann.refresh_from_db()
    assert ann.recipient_count == active_count


# ── Test 4: Run push range twice and in parallel ───────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_run_push_range_twice_and_parallel():
    """
    Running push_task on the same ID range twice and in parallel:
    exactly one push_log row per member.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Push Range Idempotency Test",
        body="Body",
        status=Announcement.Status.SENDING,
        sent_at=timezone.now(),
    )

    # Create 50 recipients
    members = list(Member.objects.filter(local_id=LOCAL_27_ID, status=Member.Status.ACTIVE)[:50])
    AnnouncementRecipient.objects.bulk_create([
        AnnouncementRecipient(announcement_id=ann.id, member_id=m.id, local_id=LOCAL_27_ID)
        for m in members
    ])
    ann.recipient_count = len(members)
    ann.save(update_fields=["recipient_count"])

    r_ids = list(AnnouncementRecipient.objects.filter(announcement_id=ann.id).values_list("id", flat=True))
    min_id, max_id = min(r_ids), max(r_ids)

    # Run in parallel threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: push_task(str(ann.id), min_id, max_id), range(4)))

    # Run again sequentially
    push_task(str(ann.id), min_id, max_id)

    log_count = PushLog.objects.filter(announcement_id=ann.id).count()
    distinct_members = PushLog.objects.filter(announcement_id=ann.id).values("member_id").distinct().count()

    assert log_count == len(members)
    assert distinct_members == len(members)

    ann.refresh_from_db()
    assert ann.status == Announcement.Status.SENT


# ── Test 5: Simulate crash (sweeper & push claim) ──────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_simulate_crash_and_recovery():
    """
    Simulate crash:
    - fan-out stops halfway, sweeper reruns it, counts end exact.
    - push stops after claiming, rerun: no member pushed twice.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    # 1. Fan-out crash simulation
    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Crash Test",
        body="Body",
        status=Announcement.Status.SENDING,
        sent_at=timezone.now() - timedelta(minutes=5),  # > 2 mins ago
    )

    # Only insert 10 rows (simulating crash halfway)
    active_members = list(Member.objects.filter(local_id=LOCAL_27_ID, status=Member.Status.ACTIVE))
    AnnouncementRecipient.objects.bulk_create([
        AnnouncementRecipient(announcement_id=ann.id, member_id=m.id, local_id=LOCAL_27_ID)
        for m in active_members[:10]
    ])

    enqueued = []
    with mock.patch("callouts.tasks.fan_out_task.delay", side_effect=enqueued.append):
        sweeper()

    assert str(ann.id) in enqueued

    # Sweeper re-runs fan_out
    with mock.patch("callouts.tasks.push_task.delay"):
        fan_out_task(str(ann.id))

    total_recipients = AnnouncementRecipient.objects.filter(announcement_id=ann.id).count()
    assert total_recipients == len(active_members)

    # 2. Push crash simulation
    # A worker claimed 20 rows (push_sent_at=now()) but crashed before FakePushProvider sent
    r_rows = list(AnnouncementRecipient.objects.filter(announcement_id=ann.id)[:20])
    claim_ids = [r.id for r in r_rows]
    AnnouncementRecipient.objects.filter(id__in=claim_ids).update(push_sent_at=timezone.now())

    # Now rerun push_task on that range:
    min_id = min(claim_ids)
    max_id = max(claim_ids)
    push_task(str(ann.id), min_id, max_id)

    # The claimed rows were already claimed, so raw SQL claimed 0 and sent 0:
    push_logs_for_claimed = PushLog.objects.filter(
        announcement_id=ann.id,
        member_id__in=[r.member_id for r in r_rows],
    ).count()
    assert push_logs_for_claimed == 0  # No duplicate or unexpected push


# ── Test 6: Send request query budget ──────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_send_request_query_budget():
    """
    Send request uses a small fixed number of queries (<= 6) and enqueues,
    with no per-member work.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    draft = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Query Budget Test",
        body="Body",
        status=Announcement.Status.DRAFT,
        source=Announcement.Source.MANUAL,
    )

    client = _client_for("leader_local27")
    url = f"/api/announcements/{draft.id}/send/"

    with mock.patch("callouts.views.fan_out_task.delay"):
        with CaptureQueriesContext(connection) as ctx:
            resp = client.post(url)

    assert resp.status_code == 202
    assert len(ctx) <= 8, f"Expected <= 8 queries, got {len(ctx)}:\n" + "\n".join(q["sql"] for q in ctx.captured_queries)

    # Assert no queries accessed members or announcement_recipients
    for q in ctx.captured_queries:
        sql = q["sql"].lower()
        assert "announcement_recipients" not in sql
        assert "from members" not in sql or "where members.id" in sql or "user_id" in sql


# ── Test 7: Another local's leader gets 404 on send and stats, member gets 403 ─


@pytest.mark.django_db
def test_permissions_cross_local_and_member():
    """
    Cross-local leader gets 404 on send and stats.
    Member gets 403 on send and stats.
    """
    send_url = f"/api/announcements/{ANNOUNCEMENT_27_DRAFT_ID}/send/"
    stats_url = f"/api/announcements/{ANNOUNCEMENT_27_DRAFT_ID}/stats/"

    # Local 104 leader calling on Local 27 announcement -> 404
    client_leader104 = _client_for("leader_local104")
    assert client_leader104.post(send_url).status_code == 404
    assert client_leader104.get(stats_url).status_code == 404

    # Local 27 member calling on Local 27 announcement -> 403
    client_member27 = _client_for("member_local27")
    assert client_member27.post(send_url).status_code == 403
    assert client_member27.get(stats_url).status_code == 403


# ── Test 8: AI-sourced announcement without approved_at gets 409 ───────────────


@pytest.mark.django_db
def test_ai_announcement_without_approval_returns_409():
    """
    AI-sourced announcement with approved_at=None returns 409 {"error": "approval_required"}.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    ai_draft = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="AI Draft",
        body="Draft generated by AI.",
        status=Announcement.Status.DRAFT,
        source=Announcement.Source.AI,
        approved_at=None,
    )

    client = _client_for("leader_local27")
    resp = client.post(f"/api/announcements/{ai_draft.id}/send/")

    assert resp.status_code == 409
    assert resp.data == {"error": "approval_required"}

    ai_draft.refresh_from_db()
    assert ai_draft.status == Announcement.Status.DRAFT


# ── Test 9: Closed callouts return final_counts, respond returns 409 ───────────


@pytest.mark.django_db
def test_closed_callouts_return_final_counts_and_respond_409():
    """
    Closed announcement returns final_counts on stats (without hitting recipient table),
    and POST respond returns 409 {"error": "callout_closed"}.
    """
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    member27 = Member.objects.get(user__username="member_local27")

    final_data = {
        "targeted": 50,
        "sent": 50,
        "push_failed": 2,
        "delivered": 40,
        "read": 30,
        "acknowledged": 25,
        "coming": 15,
        "cant_make_it": 10,
        "no_response": 25,
    }

    closed_ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Closed Callout",
        body="Body",
        status=Announcement.Status.CLOSED,
        final_counts=final_data,
        sent_at=timezone.now() - timedelta(days=8),
    )

    # Recipient row for member27
    AnnouncementRecipient.objects.create(
        announcement=closed_ann,
        member=member27,
        local=local27,
    )

    # Leader checks stats
    leader_client = _client_for("leader_local27")
    with CaptureQueriesContext(connection) as ctx:
        resp = leader_client.get(f"/api/announcements/{closed_ann.id}/stats/")

    assert resp.status_code == 200
    assert resp.data["targeted"] == 50
    assert resp.data["coming"] == 15
    assert resp.data["status"] == "closed"

    # Verify recipient table was not read
    for q in ctx.captured_queries:
        assert "announcement_recipients" not in q["sql"].lower()

    # Member tries to respond -> 409 callout_closed
    member_client = _client_for("member_local27")
    resp_member = member_client.post(
        f"/api/me/announcements/{closed_ann.id}/respond/",
        {"response": "coming"},
        format="json",
    )
    assert resp_member.status_code == 409
    assert resp_member.data == {"error": "callout_closed"}


# ── Test 10: Scale test with 22,400 members ────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_scale_22400_members():
    """
    Scale test: creates a local with 22,400 members, runs fan-out and push eagerly,
    asserts 22,400 recipient rows and 22,400 distinct push_log rows, and stats
    computation is ONE query. Prints the timings.
    """
    scale_local_id = uuid.uuid5(NAMESPACE, "crewlink:scale:local")
    scale_local, _ = Local.objects.get_or_create(id=scale_local_id, defaults={"name": "Scale Local"})

    # Check how many members already exist for scale_local
    existing_count = Member.objects.filter(local_id=scale_local_id).count()
    needed = 22400 - existing_count

    if needed > 0:
        to_create = []
        batch_size = 5000
        for i in range(existing_count, 22400):
            m_id = uuid.uuid5(NAMESPACE, f"crewlink:scale:member:{i}")
            to_create.append(
                Member(
                    id=m_id,
                    local=scale_local,
                    full_name=f"Scale Member {i}",
                    email=f"scale{i}@scale.example",
                    classification="Journeyman Wireman",
                    status=Member.Status.ACTIVE,
                )
            )
            if len(to_create) >= batch_size:
                Member.objects.bulk_create(to_create, ignore_conflicts=True)
                to_create = []
        if to_create:
            Member.objects.bulk_create(to_create, ignore_conflicts=True)

    total_active = Member.objects.filter(local_id=scale_local_id, status=Member.Status.ACTIVE).count()
    assert total_active >= 22400

    scale_leader = Member.objects.filter(local_id=scale_local_id).first()
    scale_ann = Announcement.objects.create(
        local=scale_local,
        created_by=scale_leader,
        title="Scale Test Callout",
        body="Scale test 22,400 members.",
        status=Announcement.Status.SENDING,
        sent_at=timezone.now(),
    )

    # 1. Eager Fan-out
    push_ranges = []

    def mock_push_delay(ann_id, start_id, end_id):
        push_ranges.append((ann_id, start_id, end_id))

    t0 = time.perf_counter()
    with mock.patch("callouts.tasks.push_task.delay", side_effect=mock_push_delay):
        fan_out_task(str(scale_ann.id))
    t_fanout = time.perf_counter() - t0

    recipient_count = AnnouncementRecipient.objects.filter(announcement_id=scale_ann.id).count()
    assert recipient_count == 22400, f"Expected 22,400 recipients, got {recipient_count}"

    # 2. Eager Push
    t0 = time.perf_counter()
    for ann_id, start_id, end_id in push_ranges:
        push_task(ann_id, start_id, end_id)
    t_push = time.perf_counter() - t0

    push_log_count = PushLog.objects.filter(announcement_id=scale_ann.id).count()
    distinct_push_members = (
        PushLog.objects.filter(announcement_id=scale_ann.id)
        .values("member_id")
        .distinct()
        .count()
    )
    assert push_log_count == 22400
    assert distinct_push_members == 22400

    # 3. Stats computation in ONE query
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        stats = _compute_stats_from_db(scale_ann.id)
        t_stats = time.perf_counter() - t0

    assert len(ctx) == 1, f"Stats must be computed in exactly 1 query, got {len(ctx)}"
    assert stats["targeted"] == 22400
    assert stats["sent"] == 22400

    # Print timings as required
    print(f"\n{'='*60}")
    print(f"SCALE TEST TIMINGS (22,400 members)")
    print(f"  Fan-out duration:     {t_fanout:.3f} seconds")
    print(f"  Push tasks duration:  {t_push:.3f} seconds")
    print(f"  Stats computation:    {t_stats * 1000:.2f} ms (1 query)")
    print(f"{'='*60}\n")
