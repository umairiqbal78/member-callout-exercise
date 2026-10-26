"""
CrewLink test suite — Session 3A: AI Drafting Backend (Gemini).

Tests:
1. Provider success & draft creation formatting (title <= 100, body <= 2000, push_preview <= 120).
2. Slow provider exceeds budget and returns 503 within budget plus small margin.
3. 500 then success: retries once, one draft created.
4. 500 twice: returns 503, no draft created.
5. 4xx not retried: returns 503, single provider call.
6. 429 returns 503 without retry.
7. Malformed JSON returns 502, no draft created, does not trip breaker.
8. 121-character push_preview returns 502, no draft created, does not trip breaker.
9. Circuit breaker: trips after configured failures, fast-fails during cooldown, allows one probe after cooldown.
10. Throttle: 11th call in a minute returns 429.
11. Idempotency: same key returns same draft, provider called once.
12. Fact check: missing date warning on sample note; invented time produces warning.
13. Approval gate: unapproved AI draft cannot send (409); approve sets approved_by and approved_at;
    PATCH clears approval; send CAS enforces approval; 20 parallel sends on approved AI draft give exactly one 202.
14. Database CHECK constraint rejects direct SQL update of unapproved AI draft to 'sending'.
15. Fan-out task refuses unapproved AI announcement (logs error, creates 0 rows).
16. Rule 1: another local's leader gets 404 on ai-draft with wrong local_id and on approve; member gets 403;
    URL walker passes.
17. No key configured returns 503 ai_not_configured when AI_PROVIDER=gemini.
18. Manual drafts send without approval; provider failure never affects send/stats/inbox.
"""

import collections
import concurrent.futures
import json
import time
from unittest import mock
import uuid

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from callouts.ai.breaker import CircuitBreaker
from callouts.ai.fake import FakeProvider, SAMPLE_NOTE
from callouts.ai.gemini import GeminiProvider
from callouts.ai.interfaces import AIConfigError, AIInvalidOutputError, AIUnavailableError
from callouts.ai.safety import deterministic_fact_check, validate_draft_output
from callouts.ai.service import draft_callout
from callouts.management.commands.seed_demo import (
    ANNOUNCEMENT_27_ID,
    LOCAL_104_ID,
    LOCAL_27_ID,
)
from callouts.models import (
    Announcement,
    AnnouncementRecipient,
    LeadershipRole,
    Local,
    Member,
)
from callouts.tasks import fan_out_task

User = get_user_model()


def _token(user) -> str:
    token, _ = Token.objects.get_or_create(user=user)
    return token.key


def _client_for(username: str) -> APIClient:
    user = User.objects.get(username=username)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {_token(user)}")
    return client


def _gemini_response_payload(title: str, body: str, push_preview: str, warnings: list[str]) -> dict:
    """Build a mock Gemini generateContent response JSON."""
    inner_json = json.dumps({
        "title": title,
        "body": body,
        "push_preview": push_preview,
        "warnings": warnings,
    })
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": inner_json}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }


# ── Test 1: Success & formatting ─────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_success():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    resp = client.post(
        "/api/announcements/ai-draft/",
        {
            "local_id": str(LOCAL_27_ID),
            "note": SAMPLE_NOTE,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.data
    data = resp.data
    assert "announcement" in data
    assert "warnings" in data
    ann = data["announcement"]
    assert ann["source"] == "ai"
    assert ann["status"] == "draft"
    assert ann["approved_at"] is None
    assert ann["approved_by_id"] is None
    assert ann["ai_note"] == SAMPLE_NOTE
    assert ann["ai_model"] == "fake"
    assert len(ann["title"]) <= 100
    assert len(ann["body"]) <= 2000
    assert len(ann["push_preview"]) <= 120
    assert len(ann["push_preview"]) > 0


# ── Test 2: Slow provider exceeds budget ──────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_slow_provider_budget_exceeded():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")

    def slow_handler(request: httpx.Request):
        time.sleep(0.5)
        return httpx.Response(200, json=_gemini_response_payload("T", "B", "P", []))

    mock_client = httpx.Client(transport=httpx.MockTransport(slow_handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    initial_count = Announcement.objects.count()
    start = time.monotonic()
    with mock.patch("callouts.views.draft_callout") as mock_draft:
        with mock.patch("django.conf.settings.AI_TOTAL_BUDGET_SECONDS", 0.25):
            with mock.patch("django.conf.settings.AI_TIMEOUT_SECONDS", 0.15):
                # Run real draft_callout using the mock provider
                def run_draft(**kwargs):
                    return draft_callout(
                        note="Slow test note",
                        user_id=kwargs.get("user_id"),
                        local_id=kwargs.get("local_id"),
                        custom_provider=provider,
                    )
                mock_draft.side_effect = run_draft
                resp = client.post(
                    "/api/announcements/ai-draft/",
                    {"local_id": str(LOCAL_27_ID), "note": "Slow test note"},
                    format="json",
                )
    elapsed = time.monotonic() - start

    assert resp.status_code == 503
    assert resp.data["error"] == "ai_unavailable"
    assert resp.data["message"] == "Write the callout by hand or try again shortly."
    assert elapsed < 0.8, f"Call took {elapsed}s, exceeded budget + margin"
    assert Announcement.objects.count() == initial_count


# ── Test 3: 500 then success (retry once) ─────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_500_then_success():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, json={"error": "Internal Server Error"})
        return httpx.Response(200, json=_gemini_response_payload("Recovered Title", "Recovered Body", "Preview", []))

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note="Retry note",
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft
        resp = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Retry note"},
            format="json",
        )

    assert resp.status_code == 201
    assert len(calls) == 2  # exactly one retry
    assert resp.data["announcement"]["title"] == "Recovered Title"


# ── Test 4: 500 twice gives 503 ───────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_500_twice_gives_503():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(500, json={"error": "Server error"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)
    initial_count = Announcement.objects.count()

    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note="Failing note",
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft
        resp = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Failing note"},
            format="json",
        )

    assert resp.status_code == 503
    assert resp.data["error"] == "ai_unavailable"
    assert len(calls) == 2  # one retry then given up
    assert Announcement.objects.count() == initial_count


# ── Test 5: 4xx not retried ───────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_4xx_not_retried():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(400, json={"error": "Bad request"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note="4xx note",
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft
        resp = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "4xx note"},
            format="json",
        )

    assert resp.status_code == 503
    assert resp.data["error"] == "ai_unavailable"
    assert len(calls) == 1  # 4xx is NOT retried


# ── Test 6: 429 rate limit gives 503 without retry ───────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_429_treated_as_unavailable_not_retried():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(429, json={"error": "Too Many Requests"})

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note="Rate limit note",
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft
        resp = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Rate limit note"},
            format="json",
        )

    assert resp.status_code == 503
    assert resp.data["error"] == "ai_unavailable"
    assert len(calls) == 1  # 429 is NOT retried


# ── Test 7: Malformed JSON gives 502 ──────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_malformed_json_gives_502():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")

    def handler(request: httpx.Request):
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "this is not json {{"}]}}]
        })

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)
    initial_count = Announcement.objects.count()

    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note="Malformed note",
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft
        resp = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Malformed note"},
            format="json",
        )

    assert resp.status_code == 502
    assert resp.data["error"] == "ai_invalid_output"
    assert Announcement.objects.count() == initial_count
    # Malformed output does NOT count toward breaker
    assert cache.get("ai:breaker:open_until") is None


# ── Test 8: 121 character push preview gives 502 ──────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_121_char_push_preview_gives_502():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")

    long_preview = "x" * 121
    def handler(request: httpx.Request):
        return httpx.Response(200, json=_gemini_response_payload("Title", "Body", long_preview, []))

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)
    initial_count = Announcement.objects.count()

    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note="121 char preview note",
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft
        resp = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "121 char preview note"},
            format="json",
        )

    assert resp.status_code == 502
    assert resp.data["error"] == "ai_invalid_output"
    assert Announcement.objects.count() == initial_count
    assert cache.get("ai:breaker:open_until") is None


# ── Test 9: Circuit breaker ───────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_circuit_breaker():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    calls = []

    def failing_handler(request: httpx.Request):
        calls.append(request)
        return httpx.Response(500, json={"error": "Provider down"})

    mock_client = httpx.Client(transport=httpx.MockTransport(failing_handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    # Configure breaker for 3 failures, 1s cooldown
    with mock.patch("django.conf.settings.AI_BREAKER_FAILURES", 3):
        with mock.patch("django.conf.settings.AI_BREAKER_COOLDOWN_SECONDS", 1):
            with mock.patch("django.conf.settings.AI_BREAKER_WINDOW_SECONDS", 60):
                with mock.patch("callouts.views.draft_callout") as mock_draft:
                    def run_draft(**kwargs):
                        return draft_callout(
                            note="Breaker note",
                            user_id=kwargs.get("user_id"),
                            local_id=kwargs.get("local_id"),
                            custom_provider=provider,
                        )
                    mock_draft.side_effect = run_draft

                    # Call 1: 500 (attempts 1 & 2 -> fails)
                    resp1 = client.post("/api/announcements/ai-draft/", {"local_id": str(LOCAL_27_ID), "note": "Note 1"}, format="json")
                    assert resp1.status_code == 503

                    # Call 2: fails
                    resp2 = client.post("/api/announcements/ai-draft/", {"local_id": str(LOCAL_27_ID), "note": "Note 2"}, format="json")
                    assert resp2.status_code == 503

                    # Call 3: fails -> trips breaker!
                    resp3 = client.post("/api/announcements/ai-draft/", {"local_id": str(LOCAL_27_ID), "note": "Note 3"}, format="json")
                    assert resp3.status_code == 503

                    call_count_when_tripped = len(calls)

                    # Call 4: Breaker is open! Fails immediately with 503 without calling provider
                    resp4 = client.post("/api/announcements/ai-draft/", {"local_id": str(LOCAL_27_ID), "note": "Note 4"}, format="json")
                    assert resp4.status_code == 503
                    assert resp4.data["error"] == "ai_unavailable"
                    assert len(calls) == call_count_when_tripped, "Provider was called while breaker was open"

                    # Wait for cooldown to elapse
                    time.sleep(1.1)

                    # Now allow one probe
                    # Configure handler to succeed for the probe
                    def succeeding_handler(request: httpx.Request):
                        calls.append(request)
                        return httpx.Response(200, json=_gemini_response_payload("Probe Success", "Probe Body", "Probe Preview", []))

                    provider._custom_client = httpx.Client(transport=httpx.MockTransport(succeeding_handler))

                    resp5 = client.post("/api/announcements/ai-draft/", {"local_id": str(LOCAL_27_ID), "note": "Probe Note"}, format="json")
                    assert resp5.status_code == 201
                    assert resp5.data["announcement"]["title"] == "Probe Success"

                    # Breaker is now closed!
                    assert cache.get("ai:breaker:open_until") is None


# ── Test 10: Per-user throttle (10/min) ────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_throttle_11th_call_returns_429():
    CircuitBreaker.reset()
    cache.clear()
    client = _client_for("leader_local27")

    with mock.patch("django.conf.settings.REST_FRAMEWORK", {
        "DEFAULT_PERMISSION_CLASSES": ["callouts.permissions.DenyAll"],
        "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
        "DEFAULT_THROTTLE_RATES": {"ai_draft": "10/min"},
    }):
        for i in range(10):
            resp = client.post(
                "/api/announcements/ai-draft/",
                {"local_id": str(LOCAL_27_ID), "note": f"Throttle note {i}"},
                format="json",
            )
            assert resp.status_code in (200, 201), f"Call {i+1} failed: {resp.status_code}"

        # 11th call within the minute must return 429
        resp11 = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Throttle note 11"},
            format="json",
        )
        assert resp11.status_code == 429


# ── Test 11: Idempotency-Key header ───────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_idempotency_key():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")
    provider_calls = []

    def mock_gen(note, **kwargs):
        provider_calls.append(note)
        return {
            "title": "Idempotent Title",
            "body": "Idempotent Body",
            "push_preview": "Preview",
            "warnings": [],
        }

    provider = FakeProvider()
    provider.generate = mock_gen

    key = f"idem-key-{uuid.uuid4().hex}"
    with mock.patch("callouts.views.draft_callout") as mock_draft:
        def run_draft(**kwargs):
            return draft_callout(
                note=kwargs["note"],
                user_id=kwargs.get("user_id"),
                local_id=kwargs.get("local_id"),
                custom_provider=provider,
            )
        mock_draft.side_effect = run_draft

        resp1 = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Idempotent test note"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        assert resp1.status_code == 201
        ann_id = resp1.data["announcement"]["id"]
        assert len(provider_calls) == 1

        # Second call with same key
        resp2 = client.post(
            "/api/announcements/ai-draft/",
            {"local_id": str(LOCAL_27_ID), "note": "Idempotent test note"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        assert resp2.status_code == 200
        assert resp2.data["announcement"]["id"] == ann_id
        # Provider must NOT be called a second time
        assert len(provider_calls) == 1


# ── Test 12: Deterministic fact check ─────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_fact_check():
    # 1. Sample note produces warning about missing date
    out1 = {
        "title": "Emergency Meeting",
        "body": "Meeting on Thursday at 6pm at the hall",
        "push_preview": "Meeting Thurs 6pm",
        "warnings": [],
    }
    warnings1 = deterministic_fact_check(SAMPLE_NOTE, out1)
    assert any("date" in w.lower() for w in warnings1)

    # 2. Invented time produces a warning
    note2 = "Meeting this Thursday at the hall re: contract update"
    out2 = {
        "title": "Contract Meeting",
        "body": "Meeting this Thursday at 7pm at the union hall",
        "push_preview": "Meeting Thurs 7pm",
        "warnings": [],
    }
    warnings2 = deterministic_fact_check(note2, out2)
    assert any("7pm" in w and "does not appear in your note" in w for w in warnings2)


# ── Test 13: Approval gate & send path ────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_ai_draft_approval_gate_and_send():
    CircuitBreaker.reset()
    client = _client_for("leader_local27")

    # Create AI draft
    resp = client.post(
        "/api/announcements/ai-draft/",
        {"local_id": str(LOCAL_27_ID), "note": SAMPLE_NOTE},
        format="json",
    )
    assert resp.status_code == 201
    ann_id = resp.data["announcement"]["id"]

    # 1. Try sending unapproved draft -> 409
    send_resp = client.post(f"/api/announcements/{ann_id}/send/")
    assert send_resp.status_code == 409
    assert send_resp.data["error"] == "approval_required"

    # 2. Approve the announcement
    app_resp = client.post(f"/api/announcements/{ann_id}/approve/")
    assert app_resp.status_code == 200
    assert app_resp.data["approved_at"] is not None
    assert app_resp.data["approved_by_id"] is not None

    # 3. PATCH after approve clears approval
    patch_resp = client.patch(
        f"/api/announcements/{ann_id}/",
        {"title": "Edited Title After Approval"},
        format="json",
    )
    assert patch_resp.status_code == 200
    assert patch_resp.data["approved_at"] is None
    assert patch_resp.data["approved_by_id"] is None

    # Send returns 409 again
    send_resp2 = client.post(f"/api/announcements/{ann_id}/send/")
    assert send_resp2.status_code == 409

    # 4. Re-approve and send
    app_resp2 = client.post(f"/api/announcements/{ann_id}/approve/")
    assert app_resp2.status_code == 200
    assert app_resp2.data["approved_at"] is not None

    with mock.patch("callouts.views.fan_out_task.delay"):
        send_resp3 = client.post(f"/api/announcements/{ann_id}/send/")
        assert send_resp3.status_code == 202
        assert send_resp3.data["status"] == "sending"


@pytest.mark.django_db(transaction=True)
def test_20_parallel_sends_on_approved_ai_draft():
    client = _client_for("leader_local27")
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Parallel Approved AI Send",
        body="Body",
        push_preview="Preview",
        source=Announcement.Source.AI,
        status=Announcement.Status.DRAFT,
        approved_at=timezone.now(),
        approved_by=leader27,
    )

    url = f"/api/announcements/{ann.id}/send/"
    enqueued = []

    with mock.patch("callouts.views.fan_out_task.delay", side_effect=lambda x: enqueued.append(x)):
        def worker(_):
            c = _client_for("leader_local27")
            return c.post(url).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            status_codes = list(executor.map(worker, range(20)))

    counts = collections.Counter(status_codes)
    assert counts[202] == 1
    assert counts[200] == 19
    assert len(enqueued) == 1


# ── Test 14: DB CHECK constraint rejects unapproved non-draft AI announcement ─


@pytest.mark.django_db(transaction=True)
def test_db_check_constraint_rejects_unapproved_ai_announcement():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="CHECK constraint test",
        body="Body",
        source=Announcement.Source.AI,
        status=Announcement.Status.DRAFT,
        approved_at=None,
    )

    with pytest.raises(IntegrityError):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE announcements SET status = 'sending' WHERE id = %s",
                [ann.id],
            )


# ── Test 15: Fan-out task refuses unapproved AI announcement ──────────────────


@pytest.mark.django_db(transaction=True)
def test_fan_out_refuses_unapproved_ai_announcement():
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    ann = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Unapproved fan-out test",
        body="Body",
        source=Announcement.Source.AI,
        status=Announcement.Status.DRAFT,
        approved_at=None,
    )

    initial_recipients = AnnouncementRecipient.objects.filter(announcement_id=ann.id).count()
    fan_out_task(str(ann.id))
    new_recipients = AnnouncementRecipient.objects.filter(announcement_id=ann.id).count()
    assert initial_recipients == 0
    assert new_recipients == 0


# ── Test 16: Rule 1 permissions & URL walker ──────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_rule_1_permissions_and_isolation():
    leader104_client = _client_for("leader_local104")
    member27_client = _client_for("member_local27")

    # 1. Leader of Local 104 calls ai-draft with Local 27 -> 404
    resp = leader104_client.post(
        "/api/announcements/ai-draft/",
        {"local_id": str(LOCAL_27_ID), "note": "Cross local test"},
        format="json",
    )
    assert resp.status_code == 404

    # 2. Member calls ai-draft -> 403
    resp_mem = member27_client.post(
        "/api/announcements/ai-draft/",
        {"local_id": str(LOCAL_27_ID), "note": "Member test"},
        format="json",
    )
    assert resp_mem.status_code == 403

    # 3. Create draft in Local 27
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")
    draft = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Local 27 Draft",
        body="Body",
        source=Announcement.Source.AI,
        status=Announcement.Status.DRAFT,
    )

    # Leader 104 tries to approve Local 27 draft -> 404
    resp_app_104 = leader104_client.post(f"/api/announcements/{draft.id}/approve/")
    assert resp_app_104.status_code == 404

    # Member tries to approve Local 27 draft -> 403
    resp_app_mem = member27_client.post(f"/api/announcements/{draft.id}/approve/")
    assert resp_app_mem.status_code == 403


# ── Test 17: No API key configured returns 503 ai_not_configured ──────────────


@pytest.mark.django_db(transaction=True)
def test_ai_not_configured_returns_503():
    client = _client_for("leader_local27")
    with mock.patch("django.conf.settings.AI_PROVIDER", "gemini"):
        with mock.patch("django.conf.settings.GEMINI_API_KEY", ""):
            resp = client.post(
                "/api/announcements/ai-draft/",
                {"local_id": str(LOCAL_27_ID), "note": "Test note"},
                format="json",
            )
            assert resp.status_code == 503
            assert resp.data["error"] == "ai_not_configured"


# ── Test 18: Manual draft unconstrained & provider failure isolation ──────────


@pytest.mark.django_db(transaction=True)
def test_manual_draft_send_and_provider_failure_isolation():
    client = _client_for("leader_local27")
    local27 = Local.objects.get(id=LOCAL_27_ID)
    leader27 = Member.objects.get(user__username="leader_local27")

    # Trip the circuit breaker for AI provider calls
    cache.set("ai:breaker:open_until", time.time() + 60, timeout=120)

    # 1. Manual draft sends cleanly without approval even when provider/breaker is down
    manual_draft = Announcement.objects.create(
        local=local27,
        created_by=leader27,
        title="Manual Announcement",
        body="Manual body",
        source=Announcement.Source.MANUAL,
        status=Announcement.Status.DRAFT,
        approved_at=None,
    )

    with mock.patch("callouts.views.fan_out_task.delay"):
        send_resp = client.post(f"/api/announcements/{manual_draft.id}/send/")
        assert send_resp.status_code == 202
        assert send_resp.data["status"] == "sending"

    # 2. Stats endpoint works cleanly
    stats_resp = client.get(f"/api/announcements/{manual_draft.id}/stats/")
    assert stats_resp.status_code == 200

    # 3. Member inbox works cleanly
    mem_client = _client_for("member_local27")
    inbox_resp = mem_client.get("/api/me/inbox/")
    assert inbox_resp.status_code == 200


def test_gemini_retries_shorter_preview_when_over_120():
    """When Gemini returns a 125-char preview on attempt 1, it retries with a shorter instruction."""
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) == 1:
            # 125 chars (e.g. what the evaluator bot encountered)
            preview_125 = "Meeting tomorrow at 6pm for all journeymen and foremen regarding contract talks and job assignments. Be there on time now!"
            assert len(preview_125) > 120
            return httpx.Response(200, json=_gemini_response_payload("Title", "Body", preview_125, []))
        else:
            # Retry returns a short, valid preview
            short_preview = "Meeting tomorrow at 6pm for contract talks. Please attend."
            assert len(short_preview) <= 120
            return httpx.Response(200, json=_gemini_response_payload("Title", "Body", short_preview, []))

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    result = provider.generate("Note content")
    assert len(calls) == 2
    assert "CORRECTION" in calls[1].read().decode("utf-8")
    assert len(result["push_preview"]) <= 120
    assert result["push_preview"] == "Meeting tomorrow at 6pm for contract talks. Please attend."


def test_gemini_trims_word_boundary_when_over_120():
    """If retry still returns over 120 chars with natural words, it trims at a word boundary."""
    def handler(request: httpx.Request):
        # Always returns 124 chars with words
        preview_124 = "Emergency callout tomorrow at 7am for all apprentices and journeymen regarding critical site maintenance. Be on time!"
        assert len(preview_124) == 118 or len(preview_124) > 110
        # Let's create an exact 125-char string with words
        words_125 = "Meeting tomorrow at 6pm for all journeymen and foremen regarding contract talks and job assignments. Be there on time today!"
        return httpx.Response(200, json=_gemini_response_payload("Title", "Body", words_125, []))

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash", client=mock_client)

    result = provider.generate("Note content")
    assert len(result["push_preview"]) <= 120
    # Should end at a complete word, not mid-word
    assert not result["push_preview"].endswith(" ")
