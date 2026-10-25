"""
Views for CrewLink API — Session 1 (foundation + Rule 1).

Rule 1 enforcement:
- Every view declares permission_classes explicitly (no view inherits DenyAll).
- The LocalScopedMixin filters every queryset to the caller's local.
  Another local's IDs return 404, not 403 — leaking whether an id exists
  would itself be a privacy violation.
- local_id is never read from the request body; it is always taken from
  the authenticated user's Member row.
"""

import time

from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models import DateTimeField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Announcement, AnnouncementRecipient, Member
from .permissions import IsLeaderOfLocal, IsMember
from .serializers import (
    AnnouncementDetailSerializer,
    AnnouncementListSerializer,
    InboxItemSerializer,
    MeSerializer,
    ReceiptBatchSerializer,
    RespondSerializer,
)
from .tasks import _compute_stats_from_db, fan_out_task


# ── Pagination classes ────────────────────────────────────────────────────────


class InboxCursorPagination(CursorPagination):
    """
    Cursor pagination for member inbox, ordered by recipient ID desc.
    Uses the ar_member_id_desc_idx (member_id, -id) index.
    """

    page_size = 50
    ordering = "-id"


class AnnouncementCursorPagination(CursorPagination):
    """
    Cursor pagination for announcements, ordered by created_at desc.
    """

    page_size = 50
    ordering = "-created_at"


# ── Shared helpers ────────────────────────────────────────────────────────────


class LocalScopedMixin:
    """
    Mixin that restricts every queryset to the authenticated caller's local.

    Override `get_queryset()` in subclasses and call `super().get_queryset()`
    then filter to `local_id=self.request.user.member.local_id`.
    Using this mixin means a wrong-local id always produces a 404.
    """

    def get_local_id(self):
        return self.request.user.member.local_id


# ── Auth views ────────────────────────────────────────────────────────────────


class LoginView(APIView):
    """
    POST /api/auth/login/

    Body: {"username": "...", "password": "..."}
    Returns: {"token": "...", "member_id": "..."}

    No auth required (obviously).
    """

    authentication_classes = []
    permission_classes = []  # explicitly open — no auth needed to log in

    def post(self, request):
        from django.contrib.auth import authenticate
        from rest_framework.authtoken.models import Token

        username = request.data.get("username", "").strip()
        password = request.data.get("password", "").strip()
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
            )
        token, _ = Token.objects.get_or_create(user=user)
        member_id = None
        if hasattr(user, "member"):
            member_id = str(user.member.id)
        return Response({"token": token.key, "member_id": member_id})


class MeView(APIView):
    """
    GET /api/me/

    Returns the caller's member profile and leadership roles.
    """

    permission_classes = [IsMember]

    def get(self, request):
        member = (
            Member.objects.select_related("local")
            .prefetch_related("leadership_roles")
            .get(pk=request.user.member.pk)
        )
        serializer = MeSerializer(member)
        return Response(serializer.data)


# ── Leader: Announcements ─────────────────────────────────────────────────────


class AnnouncementListCreateView(LocalScopedMixin, generics.ListCreateAPIView):
    """
    GET  /api/announcements/ — list announcements for the caller's local
    POST /api/announcements/ — create a draft announcement

    Only leaders of the local may access this view.
    """

    permission_classes = [IsLeaderOfLocal]
    serializer_class = AnnouncementListSerializer
    pagination_class = AnnouncementCursorPagination

    def get_queryset(self):
        return Announcement.objects.filter(
            local_id=self.get_local_id()
        ).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY")
        if key:
            key = key.strip()
            if key:
                existing = Announcement.objects.filter(
                    local_id=self.get_local_id(),
                    idempotency_key=key,
                ).first()
                if existing:
                    serializer = self.get_serializer(existing)
                    return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = self.request.user.member

        if key:
            try:
                with transaction.atomic():
                    instance = serializer.save(
                        local_id=member.local_id,
                        created_by=member,
                        idempotency_key=key,
                    )
            except IntegrityError:
                existing = Announcement.objects.filter(
                    local_id=self.get_local_id(),
                    idempotency_key=key,
                ).first()
                if existing:
                    serializer = self.get_serializer(existing)
                    return Response(serializer.data, status=status.HTTP_200_OK)
                raise
        else:
            instance = serializer.save(
                local_id=member.local_id,
                created_by=member,
            )

        headers = self.get_success_headers(serializer.data)
        return Response(
            self.get_serializer(instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )


class AnnouncementDetailView(LocalScopedMixin, generics.RetrieveUpdateAPIView):
    """
    GET   /api/announcements/{id}/ — retrieve one announcement
    PATCH /api/announcements/{id}/ — edit draft fields (only while status==draft)

    Only leaders of the local may access this view.
    Another local's id returns 404.
    """

    permission_classes = [IsLeaderOfLocal]
    serializer_class = AnnouncementDetailSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return Announcement.objects.filter(local_id=self.get_local_id())

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != Announcement.Status.DRAFT:
            raise PermissionDenied(
                "Only draft announcements may be edited via PATCH."
            )
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)


class AnnouncementSendView(LocalScopedMixin, APIView):
    """
    POST /api/announcements/{id}/send/

    Send an announcement:
    - Leader of that local only, 404 otherwise (403 for member).
    - If source='ai' and approved_at is null: 409 {"error": "approval_required"}.
    - Atomic compare-and-set:
      UPDATE announcements SET status='sending', sent_at=now()
      WHERE id=%s AND status='draft'
      - Rows changed = 1: enqueue ONE fan-out task with transaction.on_commit and return 202.
      - Rows changed = 0 (retry / concurrent): return 200 with current state.
    """

    permission_classes = [IsLeaderOfLocal]

    def post(self, request, pk):
        ann = Announcement.objects.filter(
            pk=pk,
            local_id=self.get_local_id(),
        ).first()
        if not ann:
            raise NotFound("Announcement not found.")

        if (
            ann.status == Announcement.Status.DRAFT
            and ann.source == Announcement.Source.AI
            and ann.approved_at is None
        ):
            return Response(
                {"error": "approval_required"},
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE announcements
                       SET status = 'sending', sent_at = now()
                     WHERE id = %s AND status = 'draft'
                    """,
                    [ann.id],
                )
                rows_changed = cursor.rowcount

            if rows_changed == 1:
                transaction.on_commit(lambda: fan_out_task.delay(str(ann.id)))
                ann.refresh_from_db()
                return Response(
                    AnnouncementDetailSerializer(ann).data,
                    status=status.HTTP_202_ACCEPTED,
                )
            else:
                ann.refresh_from_db()
                return Response(
                    AnnouncementDetailSerializer(ann).data,
                    status=status.HTTP_200_OK,
                )


class AnnouncementStatsView(LocalScopedMixin, APIView):
    """
    GET /api/announcements/{id}/stats/

    Stats for an announcement:
    - Leader of that local only, 404 otherwise (403 for member).
    - Return targeted, sent, push_failed, delivered, read, acknowledged,
      coming, cant_make_it, no_response, status, and as_of.
    - Single query with COUNT(*) FILTER.
    - Cached in shared Redis cache for 10 seconds. On expiry only one request
      recomputes (cache.add short lock); others serve previous value.
    - If status is closed, return final_counts and never read recipient table.
    """

    permission_classes = [IsLeaderOfLocal]

    def get(self, request, pk):
        ann = Announcement.objects.filter(
            pk=pk,
            local_id=self.get_local_id(),
        ).first()
        if not ann:
            raise NotFound("Announcement not found.")

        if ann.status == Announcement.Status.CLOSED:
            counts = dict(ann.final_counts or {})
            counts.setdefault("status", ann.status)
            counts.setdefault("as_of", timezone.now().isoformat())
            return Response(counts)

        data_key = f"stats:{ann.id}:data"
        lock_key = f"stats:{ann.id}:lock"

        cached = cache.get(data_key)
        now_ts = time.time()
        if cached and now_ts < cached.get("expires_at", 0):
            return Response(cached["data"])

        acquired = cache.add(lock_key, "1", timeout=5)
        if not acquired and cached:
            return Response(cached["data"])

        try:
            stats = _compute_stats_from_db(ann.id)
            stats["status"] = ann.status
            stats["as_of"] = timezone.now().isoformat()
            cache.set(data_key, {"data": stats, "expires_at": now_ts + 10}, timeout=300)
            return Response(stats)
        finally:
            if acquired:
                cache.delete(lock_key)


# ── Member: Inbox & Receipts ──────────────────────────────────────────────────


class InboxView(LocalScopedMixin, generics.ListAPIView):
    """
    GET /api/me/inbox/

    Returns the caller's announcement_recipients rows, newest-first.
    Supports cursor pagination and a `since` filter (ISO-8601 datetime).
    Members only see their own rows — no other local's data is reachable.
    """

    permission_classes = [IsMember]
    serializer_class = InboxItemSerializer
    pagination_class = InboxCursorPagination

    def get_queryset(self):
        member = self.request.user.member
        qs = (
            AnnouncementRecipient.objects.filter(
                member_id=member.pk,
                local_id=member.local_id,
            )
            .select_related("announcement")
            .order_by("-id")
        )
        since = self.request.query_params.get("since")
        if since:
            try:
                from django.utils.dateparse import parse_datetime

                dt = parse_datetime(since)
                if dt:
                    qs = qs.filter(announcement__created_at__gt=dt)
            except Exception:
                pass
        return qs


from .services import record_receipts, record_response


class ReceiptBatchView(LocalScopedMixin, APIView):
    """
    POST /api/me/receipts/

    Body: {"announcement_ids": [...], "delivered": true, "read": false}

    Sets delivered_at and/or read_at with COALESCE semantics (set only once,
    a repeated event can't undo). Fills earlier funnel steps automatically.
    Members can only touch their own rows.
    """

    permission_classes = [IsMember]

    def post(self, request):
        serializer = ReceiptBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        member = request.user.member

        updated = record_receipts(
            member_id=member.pk,
            local_id=member.local_id,
            announcement_ids=data["announcement_ids"],
            delivered=data.get("delivered", True),
            read=data.get("read", False),
            acknowledged=data.get("acknowledged", False),
        )
        return Response({"updated": updated})


class RespondView(LocalScopedMixin, APIView):
    """
    POST /api/me/announcements/{id}/respond/

    Body: {"response": "coming" | "cant_make_it"}

    Sets response, responded_at, read_at, acknowledged_at using COALESCE
    (set only once — repeated submissions don't undo earlier ones).
    Members can only respond to their own row for the given announcement.
    """

    permission_classes = [IsMember]

    def post(self, request, pk):
        serializer = RespondSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response_value = serializer.validated_data["response"]
        member = request.user.member

        recipient = (
            AnnouncementRecipient.objects.filter(
                announcement_id=pk,
                member_id=member.pk,
                local_id=member.local_id,
            )
            .select_related("announcement")
            .first()
        )
        if not recipient:
            raise NotFound("Recipient row not found.")

        if recipient.announcement.status == Announcement.Status.CLOSED:
            return Response(
                {"error": "callout_closed"},
                status=status.HTTP_409_CONFLICT,
            )

        record_response(
            recipient=recipient,
            response_value=response_value,
        )
        return Response({"detail": "Response recorded."})

