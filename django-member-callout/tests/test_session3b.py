"""
Tests for Session 3B backend additions:
a) CORS preflight and allowed headers (including Idempotency-Key).
b) Receipts with kind "delivered" and "read" (with backwards compatibility).
c) Acknowledge endpoint: idempotent, fills earlier steps, own rows only, 409 when closed.
d) Classifications endpoint: leader only (404 for other locals, 403 for members).
e) Funnel rule: delivered, read, acknowledged remain separate; earlier steps filled with COALESCE;
   invariant check passes; URL walker passes.
"""

from datetime import timedelta
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from callouts.management.commands.seed_demo import LOCAL_104_ID, LOCAL_27_ID
from callouts.models import Announcement, AnnouncementRecipient, LeadershipRole, Local, Member
from callouts.tasks import invariant_check

User = get_user_model()


def _client_for(username: str) -> APIClient:
    user = User.objects.get(username=username)
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client



# ── CORS Preflight & Headers ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_cors_preflight_and_headers():
    client = APIClient()

    # Preflight OPTIONS request from localhost
    resp = client.options(
        "/api/announcements/",
        HTTP_ORIGIN="http://localhost:3000",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization, idempotency-key, content-type",
    )
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
    allow_headers = resp.headers.get("Access-Control-Allow-Headers", "").lower()
    assert "authorization" in allow_headers
    assert "idempotency-key" in allow_headers

    # Preflight from 127.0.0.1 on custom port
    resp2 = client.options(
        "/api/announcements/",
        HTTP_ORIGIN="http://127.0.0.1:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )
    assert resp2.status_code == 200
    assert resp2.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:8080"

    # Disallowed origin
    resp_evil = client.options(
        "/api/announcements/",
        HTTP_ORIGIN="http://malicious.example.com",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
    )
    assert "Access-Control-Allow-Origin" not in resp_evil.headers


# ── Receipts: kind "delivered" and "read" ─────────────────────────────────────


@pytest.mark.django_db
def test_receipts_delivered_sets_delivered_at_only():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    member27 = Member.objects.get(user__username="member_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Test Receipts Delivered",
        body="Body",
        status=Announcement.Status.SENT,
    )
    rec = AnnouncementRecipient.objects.create(
        announcement=ann,
        member=member27,
        local=local27,
    )

    client = _client_for("member_local27")
    resp = client.post(
        "/api/me/receipts/",
        {"announcement_ids": [str(ann.id)], "kind": "delivered"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["updated"] == 1

    rec.refresh_from_db()
    assert rec.delivered_at is not None
    assert rec.read_at is None
    assert rec.acknowledged_at is None
    assert rec.response is None


@pytest.mark.django_db
def test_receipts_read_sets_read_and_backfills_delivered():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    member27 = Member.objects.get(user__username="member_local27")

    # Case 1: delivered_at was empty -> read fills both delivered_at and read_at
    ann1 = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Test Read Empty Delivered",
        body="Body",
        status=Announcement.Status.SENT,
    )
    rec1 = AnnouncementRecipient.objects.create(
        announcement=ann1,
        member=member27,
        local=local27,
    )

    client = _client_for("member_local27")
    resp1 = client.post(
        "/api/me/receipts/",
        {"announcement_ids": [str(ann1.id)], "kind": "read"},
        format="json",
    )
    assert resp1.status_code == 200
    assert resp1.data["updated"] == 1

    rec1.refresh_from_db()
    assert rec1.delivered_at is not None
    assert rec1.read_at is not None
    assert rec1.acknowledged_at is None
    assert rec1.delivered_at <= rec1.read_at

    # Case 2: delivered_at was already set earlier -> keeps earlier delivered_at
    ann2 = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Test Read Keeps Earlier Delivered",
        body="Body",
        status=Announcement.Status.SENT,
    )
    earlier_time = timezone.now() - timedelta(minutes=10)
    rec2 = AnnouncementRecipient.objects.create(
        announcement=ann2,
        member=member27,
        local=local27,
        delivered_at=earlier_time,
    )

    resp2 = client.post(
        "/api/me/receipts/",
        {"announcement_ids": [str(ann2.id)], "kind": "read"},
        format="json",
    )
    assert resp2.status_code == 200

    rec2.refresh_from_db()
    assert rec2.delivered_at == earlier_time
    assert rec2.read_at is not None
    assert rec2.delivered_at < rec2.read_at
    assert rec2.acknowledged_at is None


@pytest.mark.django_db
def test_receipts_backward_compatibility():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    member27 = Member.objects.get(user__username="member_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Test Receipts Legacy",
        body="Body",
        status=Announcement.Status.SENT,
    )
    rec = AnnouncementRecipient.objects.create(
        announcement=ann,
        member=member27,
        local=local27,
    )

    client = _client_for("member_local27")

    # Default (no kind specified) defaults to delivered
    resp = client.post(
        "/api/me/receipts/",
        {"announcement_ids": [str(ann.id)]},
        format="json",
    )
    assert resp.status_code == 200
    rec.refresh_from_db()
    assert rec.delivered_at is not None
    assert rec.read_at is None

    # Legacy delivered=True, read=True
    resp = client.post(
        "/api/me/receipts/",
        {"announcement_ids": [str(ann.id)], "delivered": True, "read": True},
        format="json",
    )
    assert resp.status_code == 200
    rec.refresh_from_db()
    assert rec.read_at is not None


# ── Acknowledge endpoint ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_acknowledge_endpoint_funnel_and_idempotency():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    member27 = Member.objects.get(user__username="member_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Test Acknowledge",
        body="Body",
        needs_ack=True,
        status=Announcement.Status.SENT,
    )
    rec = AnnouncementRecipient.objects.create(
        announcement=ann,
        member=member27,
        local=local27,
    )

    client = _client_for("member_local27")

    # Acknowledge without prior delivered/read -> fills read_at and delivered_at
    resp = client.post(f"/api/me/announcements/{ann.id}/acknowledge/")
    assert resp.status_code == 200
    assert resp.data["detail"] == "Acknowledged."

    rec.refresh_from_db()
    assert rec.delivered_at is not None
    assert rec.read_at is not None
    assert rec.acknowledged_at is not None
    assert rec.delivered_at <= rec.read_at <= rec.acknowledged_at

    # Idempotent: repeated call does not alter timestamps
    first_ack = rec.acknowledged_at
    resp2 = client.post(f"/api/me/announcements/{ann.id}/acknowledge/")
    assert resp2.status_code == 200
    rec.refresh_from_db()
    assert rec.acknowledged_at == first_ack


@pytest.mark.django_db
def test_acknowledge_own_rows_only_and_closed_409():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    local104 = Local.objects.get(id=LOCAL_104_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    member27 = Member.objects.get(user__username="member_local27")
    member104 = Member.objects.get(user__username="member_local104")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Local 27 Only",
        body="Body",
        status=Announcement.Status.SENT,
    )
    rec = AnnouncementRecipient.objects.create(
        announcement=ann,
        member=member27,
        local=local27,
    )

    # Member 104 tries to acknowledge Local 27 announcement -> 404
    client104 = _client_for("member_local104")
    resp_cross = client104.post(f"/api/me/announcements/{ann.id}/acknowledge/")
    assert resp_cross.status_code == 404

    # Close the announcement
    ann.status = Announcement.Status.CLOSED
    ann.save(update_fields=["status"])

    # Member 27 tries to acknowledge closed announcement -> 409 callout_closed
    client27 = _client_for("member_local27")
    resp_closed = client27.post(f"/api/me/announcements/{ann.id}/acknowledge/")
    assert resp_closed.status_code == 409
    assert resp_closed.data.get("error") == "callout_closed"


# ── Classifications endpoint ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_classifications_endpoint_access_control():
    client_leader27 = _client_for("leader_local27")
    client_leader104 = _client_for("leader_local104")
    client_member27 = _client_for("member_local27")

    # 1. Leader of Local 27 gets distinct classifications of active members
    resp = client_leader27.get(f"/api/classifications/?local_id={LOCAL_27_ID}")
    assert resp.status_code == 200
    assert isinstance(resp.data, list)
    assert len(resp.data) > 0
    # Expected seeded classifications in Local 27
    assert "Foreman" in resp.data
    assert "Journeyman Wireman" in resp.data

    # 2. Leader of Local 104 querying Local 27 gets 404
    resp_cross = client_leader104.get(f"/api/classifications/?local_id={LOCAL_27_ID}")
    assert resp_cross.status_code == 404

    # 3. Regular member querying gets 403
    resp_mem = client_member27.get(f"/api/classifications/?local_id={LOCAL_27_ID}")
    assert resp_mem.status_code == 403

    # 4. Missing or invalid local_id returns 404
    resp_missing = client_leader27.get("/api/classifications/")
    assert resp_missing.status_code == 404
    resp_invalid = client_leader27.get("/api/classifications/?local_id=not-a-uuid")
    assert resp_invalid.status_code == 404


# ── Funnel Invariant Check ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_funnel_invariants_pass():
    res = invariant_check()
    assert res == "OK"

