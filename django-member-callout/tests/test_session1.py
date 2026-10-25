"""
CrewLink test suite — Session 1.

Covers:
1. Cross-local isolation (Rule 1): leader/member from Local 104 gets 404 on Local 27 resources.
2. Role enforcement: members calling leader endpoints get 403; no token gets 401.
3. Permission completeness: every registered URL has an explicit permission class.
4. Database integrity: Postgres rejects a recipient row with a mismatched local.
5. seed_demo idempotency: running twice leaves identical counts and IDs.
6. README sanity: the JSON block in README.md is parseable, every login works,
   every referenced ID exists and belongs to the stated local.
"""

import json
import os
import re
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.urls import get_resolver, reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from callouts.models import (
    Announcement,
    AnnouncementRecipient,
    LeadershipRole,
    Local,
    Member,
)
from callouts.management.commands.seed_demo import (
    ANNOUNCEMENT_27_ID,
    LOCAL_104_ID,
    LOCAL_27_ID,
    NAMESPACE,
)

User = get_user_model()


# ── Helpers & fixtures ────────────────────────────────────────────────────────


def _uuid5(label: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, label)


LEADER27_ID = _uuid5("crewlink:member:leader_local27")
MEMBER27_ID = _uuid5("crewlink:member:member_local27")
LEADER104_ID = _uuid5("crewlink:member:leader_local104")
MEMBER104_ID = _uuid5("crewlink:member:member_local104")


@pytest.fixture(scope="session")
def django_db_setup():
    """Use the already-migrated test database (created by pytest-django)."""
    pass


def _token(user) -> str:
    token, _ = Token.objects.get_or_create(user=user)
    return token.key


def _client_for(username: str) -> APIClient:
    user = User.objects.get(username=username)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {_token(user)}")
    return client


def _anon_client() -> APIClient:
    return APIClient()


# ── Test: cross-local isolation ───────────────────────────────────────────────


@pytest.mark.django_db
class TestCrossLocalIsolation:
    """Local 104's users must get 404 on Local 27's resources."""

    def test_leader104_cannot_get_announcement27(self):
        client = _client_for("leader_local104")
        url = f"/api/announcements/{ANNOUNCEMENT_27_ID}/"
        response = client.get(url)
        assert response.status_code == 404, (
            f"Expected 404, got {response.status_code}"
        )

    def test_leader104_cannot_patch_announcement27(self):
        client = _client_for("leader_local104")
        url = f"/api/announcements/{ANNOUNCEMENT_27_ID}/"
        response = client.patch(url, {"title": "Hacked"}, format="json")
        assert response.status_code == 404

    def test_member104_cannot_get_announcement27_via_inbox(self):
        """
        Local 104 member's inbox must only return their own local's rows.
        They should never see Local 27's announcement.
        """
        client = _client_for("member_local104")
        response = client.get("/api/me/inbox/")
        assert response.status_code == 200
        ids = [
            str(item["announcement_id"])
            for item in response.data.get("results", response.data)
        ]
        assert str(ANNOUNCEMENT_27_ID) not in ids

    def test_member104_cannot_respond_to_announcement27(self):
        """Member 104 POSTing respond on ann27 must get 404 (they have no row)."""
        client = _client_for("member_local104")
        url = f"/api/me/announcements/{ANNOUNCEMENT_27_ID}/respond/"
        response = client.post(url, {"response": "coming"}, format="json")
        assert response.status_code == 404

    def test_member104_receipts_cannot_touch_announcement27(self):
        """
        POSTing Local 27's announcement ID in a batch receipt call
        must silently update 0 rows (the filter won't find any row
        for a member from Local 104).
        """
        client = _client_for("member_local104")
        response = client.post(
            "/api/me/receipts/",
            {"announcement_ids": [str(ANNOUNCEMENT_27_ID)], "delivered": True},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["updated"] == 0


# ── Test: role enforcement ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestRoleEnforcement:
    """Members calling leader endpoints get 403; anonymous callers get 401."""

    def test_member_cannot_list_announcements(self):
        client = _client_for("member_local27")
        response = client.get("/api/announcements/")
        assert response.status_code == 403

    def test_member_cannot_create_announcement(self):
        client = _client_for("member_local27")
        response = client.post(
            "/api/announcements/",
            {"title": "Nope", "body": "Nope"},
            format="json",
        )
        assert response.status_code == 403

    def test_member_cannot_get_announcement_detail(self):
        client = _client_for("member_local27")
        url = f"/api/announcements/{ANNOUNCEMENT_27_ID}/"
        response = client.get(url)
        assert response.status_code == 403

    def test_anonymous_login_required(self):
        """No token → 401 on any authenticated endpoint."""
        client = _anon_client()
        response = client.get("/api/me/")
        assert response.status_code == 401

    def test_anonymous_announcements_401(self):
        client = _anon_client()
        response = client.get("/api/announcements/")
        assert response.status_code == 401


# ── Test: leader & member positive functional workflows ──────────────────────


@pytest.mark.django_db
class TestLeaderEndpoints:
    """Leader functional workflows in their own local."""

    def test_leader_me(self):
        client = _client_for("leader_local27")
        response = client.get("/api/me/")
        assert response.status_code == 200
        assert response.data["local"]["name"] == "Local 27"
        assert len(response.data["leadership_roles"]) == 1
        assert response.data["leadership_roles"][0]["role"] == "leader"

    def test_leader_list_announcements(self):
        client = _client_for("leader_local27")
        response = client.get("/api/announcements/")
        assert response.status_code == 200
        results = response.data.get("results", response.data)
        assert len(results) >= 1
        assert any(item["id"] == str(ANNOUNCEMENT_27_ID) for item in results)

    def test_leader_get_announcement_detail(self):
        client = _client_for("leader_local27")
        response = client.get(f"/api/announcements/{ANNOUNCEMENT_27_ID}/")
        assert response.status_code == 200
        assert response.data["title"] == "Emergency Meeting — Q4 Contract Talks"

    def test_leader_create_and_patch_draft(self):
        client = _client_for("leader_local27")
        # Create draft
        resp = client.post(
            "/api/announcements/",
            {
                "title": "Draft Callout",
                "body": "Callout body",
                "audience_classifications": ["Foreman"],
                "push_preview": "Short preview",
            },
            format="json",
        )
        assert resp.status_code == 201
        new_id = resp.data["id"]
        assert resp.data["status"] == "draft"

        # Patch draft
        patch_resp = client.patch(
            f"/api/announcements/{new_id}/",
            {"title": "Updated Title"},
            format="json",
        )
        assert patch_resp.status_code == 200
        assert patch_resp.data["title"] == "Updated Title"

    def test_leader_cannot_patch_sent_announcement(self):
        client = _client_for("leader_local27")
        resp = client.patch(
            f"/api/announcements/{ANNOUNCEMENT_27_ID}/",
            {"title": "Cannot edit sent"},
            format="json",
        )
        assert resp.status_code == 403


@pytest.mark.django_db
class TestMemberEndpoints:
    """Member functional workflows in their own local."""

    def test_member_me(self):
        client = _client_for("member_local27")
        response = client.get("/api/me/")
        assert response.status_code == 200
        assert response.data["local"]["name"] == "Local 27"
        assert response.data["leadership_roles"] == []

    def test_member_inbox_and_receipts_and_respond(self):
        client = _client_for("member_local27")
        # Inbox
        inbox_resp = client.get("/api/me/inbox/")
        assert inbox_resp.status_code == 200
        results = inbox_resp.data.get("results", inbox_resp.data)
        assert len(results) >= 1
        assert any(str(item["announcement_id"]) == str(ANNOUNCEMENT_27_ID) for item in results)

        # Receipts batch
        receipt_resp = client.post(
            "/api/me/receipts/",
            {
                "announcement_ids": [str(ANNOUNCEMENT_27_ID)],
                "delivered": True,
                "read": True,
            },
            format="json",
        )
        assert receipt_resp.status_code == 200
        assert receipt_resp.data["updated"] >= 1

        # Respond
        respond_resp = client.post(
            f"/api/me/announcements/{ANNOUNCEMENT_27_ID}/respond/",
            {"response": "coming"},
            format="json",
        )
        assert respond_resp.status_code == 200
        assert respond_resp.data["detail"] == "Response recorded."


# ── Test: permission completeness ─────────────────────────────────────────────


@pytest.mark.django_db
class TestPermissionCompleteness:
    """
    Every URL registered in the project must have an explicit permission_classes
    attribute on its view class. If any view omits it, it inherits DenyAll
    and this test still passes — but the intent is to catch views that forget
    to declare permissions entirely (i.e., permission_classes is not defined
    at all on the view class, only on the CBV base).

    We verify that the view *class* (not the instance) has permission_classes
    explicitly set, meaning the developer made a deliberate choice.
    """

    def _iter_views(self):
        resolver = get_resolver()
        for pattern in resolver.url_patterns:
            yield from self._walk(pattern)

    def _walk(self, pattern):
        from django.urls import URLPattern, URLResolver

        if isinstance(pattern, URLPattern):
            yield pattern
        elif isinstance(pattern, URLResolver):
            for sub in pattern.url_patterns:
                yield from self._walk(sub)

    def test_every_url_has_explicit_permission_class(self):
        from rest_framework.views import APIView

        missing = []
        for pattern in self._iter_views():
            callback = pattern.callback
            # For class-based views, callback.view_class holds the class.
            view_cls = getattr(callback, "view_class", None)
            if view_cls is None or not issubclass(view_cls, APIView):
                continue  # non-API view (e.g. Django admin redirect) — skip
            # Check that permission_classes is declared directly on the class,
            # not just inherited from a parent.
            if "permission_classes" not in view_cls.__dict__:
                missing.append(
                    f"{view_cls.__name__} at {pattern.pattern}"
                )
        assert not missing, (
            "These views have no explicit permission_classes:\n"
            + "\n".join(missing)
        )


# ── Test: database integrity ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestDatabaseIntegrity:
    """
    The database must reject a recipient row whose member belongs to a different
    local than the announcement.
    """

    def test_composite_fk_rejects_cross_local_recipient(self):
        """
        Insert a recipient for Local 104's member into Local 27's announcement.
        The composite FK constraint (ar_announcement_local_fk) must reject this.
        Because the constraint is DEFERRABLE INITIALLY DEFERRED, we force
        immediate constraint evaluation inside the atomic block.
        """
        ann = Announcement.objects.get(id=ANNOUNCEMENT_27_ID)
        member104 = Member.objects.get(id=MEMBER104_ID)
        local104 = Local.objects.get(id=LOCAL_104_ID)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                AnnouncementRecipient.objects.create(
                    announcement=ann,
                    member=member104,
                    local=local104,  # different local than the announcement
                )
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


# ── Test: seed_demo idempotency ───────────────────────────────────────────────


@pytest.mark.django_db
class TestSeedDemoIdempotency:
    """Running seed_demo twice must leave identical counts and IDs."""

    def test_locals_are_stable(self):
        assert Local.objects.filter(id=LOCAL_27_ID).exists()
        assert Local.objects.filter(id=LOCAL_104_ID).exists()

    def test_named_member_ids_are_stable(self):
        for mid in [LEADER27_ID, MEMBER27_ID, LEADER104_ID, MEMBER104_ID]:
            assert Member.objects.filter(id=mid).exists(), f"Missing member {mid}"

    def test_announcement_id_is_stable(self):
        assert Announcement.objects.filter(id=ANNOUNCEMENT_27_ID).exists()

    def test_second_run_changes_nothing(self, settings):
        """
        Running seed_demo a second time must not change row counts.
        """
        settings.DEBUG = True
        from io import StringIO

        from django.core.management import call_command

        before_locals = Local.objects.count()
        before_members = Member.objects.count()
        before_anns = Announcement.objects.count()
        before_recipients = AnnouncementRecipient.objects.count()
        before_roles = LeadershipRole.objects.count()

        out = StringIO()
        call_command("seed_demo", stdout=out)

        assert Local.objects.count() == before_locals
        assert Member.objects.count() == before_members
        assert Announcement.objects.count() == before_anns
        assert AnnouncementRecipient.objects.count() == before_recipients
        assert LeadershipRole.objects.count() == before_roles


# ── Test: README sanity ───────────────────────────────────────────────────────


@pytest.mark.django_db
class TestReadmeSanity:
    """
    Parse the JSON block in README.md and verify every claim.
    """

    README_PATH = (
        "/Users/admin/Documents/member-callout-exercise/README.md"
    )

    def _load_readme_json(self) -> dict:
        candidates = [
            self.README_PATH,
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "README.md")),
            "/app/README.md",
            os.path.abspath("README.md"),
        ]
        readme_path = None
        for p in candidates:
            if os.path.exists(p):
                readme_path = p
                break
        if not readme_path:
            raise FileNotFoundError(f"Could not find README.md in candidates: {candidates}")

        with open(readme_path) as f:
            content = f.read()
        # Find the first fenced JSON block under ## TEST ACCOUNTS
        match = re.search(
            r"## TEST ACCOUNTS.*?```json\s*(\{.*?\})\s*```",
            content,
            re.DOTALL,
        )
        assert match, "Could not find a JSON block under ## TEST ACCOUNTS in README.md"
        return json.loads(match.group(1))

    def test_all_logins_work(self):
        data = self._load_readme_json()
        client = APIClient()
        for local_info in data["locals"]:
            for role in ("leader", "member"):
                login = local_info[role]
                resp = client.post(
                    "/api/auth/login/",
                    {
                        "username": login["username"],
                        "password": login["password"],
                    },
                    format="json",
                )
                assert resp.status_code == 200, (
                    f"Login failed for {login['username']}: {resp.data}"
                )
                assert "token" in resp.data
                assert resp.data["member_id"] == login["member_id"]

    def test_announcement_id_exists_and_belongs_to_local27(self):
        data = self._load_readme_json()
        ann_id = data["announcement_local27_id"]
        ann = Announcement.objects.get(id=ann_id)
        assert str(ann.local_id) == data["locals"][0]["id"]

    def test_local104_member_id_exists_and_belongs_to_local104(self):
        data = self._load_readme_json()
        member_id = data["member_local104_id"]
        member = Member.objects.get(id=member_id)
        assert str(member.local_id) == data["locals"][1]["id"]
