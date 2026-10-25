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

    def perform_create(self, serializer):
        member = self.request.user.member
        serializer.save(
            local_id=member.local_id,
            created_by=member,
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


class ReceiptBatchView(LocalScopedMixin, APIView):
    """
    POST /api/me/receipts/

    Body: {"announcement_ids": [...], "delivered": true, "read": false}

    Sets delivered_at and/or read_at with COALESCE semantics (set only once,
    a repeated event can't undo). Members can only touch their own rows.
    """

    permission_classes = [IsMember]

    def post(self, request):
        serializer = ReceiptBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        member = request.user.member

        now = timezone.now()
        update_kwargs = {}
        if data["delivered"]:
            # COALESCE: sets delivered_at only if it is currently NULL.
            update_kwargs["delivered_at"] = Coalesce(
                "delivered_at",
                Value(now, output_field=DateTimeField()),
            )
        if data["read"]:
            update_kwargs["read_at"] = Coalesce(
                "read_at",
                Value(now, output_field=DateTimeField()),
            )

        if not update_kwargs:
            return Response({"updated": 0})

        # Scope to: this member's rows + correct local + only targeted announcements.
        updated = (
            AnnouncementRecipient.objects.filter(
                member_id=member.pk,
                local_id=member.local_id,
                announcement_id__in=data["announcement_ids"],
            ).update(**update_kwargs)
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

        now = timezone.now()
        updated = (
            AnnouncementRecipient.objects.filter(
                announcement_id=pk,
                member_id=member.pk,
                local_id=member.local_id,
            ).update(
                response=response_value,
                responded_at=now,
                # COALESCE: only set if not already set
                read_at=_coalesce_now("read_at", now),
                acknowledged_at=_coalesce_now("acknowledged_at", now),
            )
        )
        if not updated:
            raise NotFound("Recipient row not found.")
        return Response({"detail": "Response recorded."})


# ── Utilities ─────────────────────────────────────────────────────────────────


def _coalesce_now(field_name, now):
    """COALESCE(field, now) — sets the field only if it is currently NULL."""
    return Coalesce(field_name, Value(now, output_field=DateTimeField()))
