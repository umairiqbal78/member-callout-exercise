"""
Data models for CrewLink.

Design is defined in DESIGN.md. Key decisions:
- leadership_roles is its own table (not a column on members) so member serializers
  can never accidentally expose or mutate it.
- announcement_recipients carries local_id and composite FK constraints enforced
  via RunSQL so Postgres rejects a recipient from a different local at the DB level.
- All timestamped fields use COALESCE so out-of-order events can't undo earlier ones.
"""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.postgres.fields import ArrayField
from django.db import models

User = get_user_model()


class Local(models.Model):
    """A union local (chapter). All data is scoped to a local."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "locals"

    def __str__(self) -> str:
        return self.name


class Member(models.Model):
    """A union member. Belongs to exactly one local."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    local = models.ForeignKey(Local, on_delete=models.PROTECT, related_name="members")
    # user_id is nullable: seeded demo members without a login account are fine.
    user = models.OneToOneField(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="member",
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    classification = models.CharField(max_length=120)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "members"
        indexes = [
            models.Index(fields=["local_id", "status"], name="members_local_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.local})"


class LeadershipRole(models.Model):
    """
    Grants a member a leadership role within a specific local.

    Kept in a separate table (not a column on Member) so no member-facing
    serializer can ever read or mutate roles. Rule 1 enforcement by design.
    """

    id = models.BigAutoField(primary_key=True)
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="leadership_roles",
    )
    local = models.ForeignKey(
        Local,
        on_delete=models.CASCADE,
        related_name="leadership_roles",
    )
    role = models.CharField(max_length=80, default="leader")
    granted_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "leadership_roles"
        constraints = [
            models.UniqueConstraint(
                fields=["member_id", "local_id"],
                name="leadership_roles_member_local_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.member} — {self.role} @ {self.local}"


class MemberDevice(models.Model):
    """FCM/APNS push token for a member's device."""

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="devices")
    push_token = models.CharField(max_length=500)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "member_devices"


class Announcement(models.Model):
    """
    A callout from a leader to all active members of a local.

    idempotency_key is unique per (local_id, key) so a retried send finds the
    same announcement without creating a duplicate (Rule 2).
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        CLOSED = "closed", "Closed"

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AI = "ai", "AI"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    local = models.ForeignKey(Local, on_delete=models.PROTECT, related_name="announcements")
    created_by = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name="created_announcements",
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    needs_ack = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)  # meeting time
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL
    )
    # audience_classifications: text array — which member classifications this targets
    audience_classifications = ArrayField(
        models.CharField(max_length=120),
        blank=True,
        default=list,
    )
    # push_preview: short push notification preview text, max 120 chars
    push_preview = models.CharField(max_length=120, blank=True)
    approved_by = models.ForeignKey(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_announcements",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.IntegerField(default=0)
    final_counts = models.JSONField(null=True, blank=True)  # jsonb, frozen when closed
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # idempotency_key: unique per (local_id, key) — a retry finds the same row
    idempotency_key = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "announcements"
        constraints = [
            models.UniqueConstraint(
                fields=["local_id", "idempotency_key"],
                condition=models.Q(idempotency_key__gt=""),
                name="announcements_local_idempotency_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.local})"


class AnnouncementRecipient(models.Model):
    """
    One row per announcement per member — the source of truth for delivery.

    local_id is stored redundantly so composite FKs can enforce at the DB level
    that a recipient can never belong to a different local than the announcement
    or the member (Rule 1 enforced by the database, not just application logic).

    Composite FK constraints are added via RunSQL in the migration because Django's
    ORM doesn't support composite foreign keys natively.

    fillfactor 80 keeps a free-space buffer for the many in-place updates
    (delivered_at, read_at, push_sent_at, etc.) without needing VACUUM to reclaim
    space between HOT updates.
    """

    class Response(models.TextChoices):
        COMING = "coming", "Coming"
        CANT_MAKE_IT = "cant_make_it", "Can't Make It"

    id = models.BigAutoField(primary_key=True)
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="announcement_receipts",
    )
    # local_id stored here so the DB can enforce composite FKs (added via RunSQL).
    local = models.ForeignKey(
        Local,
        on_delete=models.CASCADE,
        related_name="+",
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    response = models.CharField(
        max_length=20, choices=Response.choices, null=True, blank=True
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    push_sent_at = models.DateTimeField(null=True, blank=True)
    push_error = models.TextField(blank=True)

    class Meta:
        db_table = "announcement_recipients"
        constraints = [
            models.UniqueConstraint(
                fields=["announcement_id", "member_id"],
                name="announcement_recipients_announcement_member_uniq",
            )
        ]
        indexes = [
            # Inbox query: get a member's receipts newest-first
            models.Index(
                fields=["member_id", "-id"],
                name="ar_member_id_desc_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Recipient {self.member_id} for announcement {self.announcement_id}"


class PushLog(models.Model):
    """
    Development stand-in for FCM delivery records.

    One row per (announcement_id, member_id) — enforced by a unique constraint.
    The simulated_outcome column records what happened:
      - ok: push sent successfully
      - error: push failed (push_error is also set on the recipient row)
      - silent_drop: push sent but silently dropped (simulates FCM silent failure)

    This table is what proves Rule 2: no member is pushed twice.
    """

    class Outcome(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"
        SILENT_DROP = "silent_drop", "Silent drop"

    id = models.BigAutoField(primary_key=True)
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="push_logs",
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="push_logs",
    )
    sent_at = models.DateTimeField()
    simulated_outcome = models.CharField(
        max_length=20, choices=Outcome.choices, default=Outcome.OK
    )

    class Meta:
        db_table = "push_log"
        constraints = [
            models.UniqueConstraint(
                fields=["announcement_id", "member_id"],
                name="push_log_announcement_member_uniq",
            )
        ]

