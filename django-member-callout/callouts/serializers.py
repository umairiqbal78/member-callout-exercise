"""
Serializers for CrewLink.

All fields are listed explicitly — never Meta.fields = '__all__'.
No serializer exposes or accepts local_id, leadership_roles, or user_id directly;
those are read from the authenticated user's session, not from client input.
"""

from rest_framework import serializers

from .models import Announcement, AnnouncementRecipient, LeadershipRole, Local, Member


class LocalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Local
        fields = ["id", "name"]
        read_only_fields = ["id", "name"]


class LeadershipRoleSerializer(serializers.ModelSerializer):
    local_name = serializers.CharField(source="local.name", read_only=True)

    class Meta:
        model = LeadershipRole
        fields = ["id", "local_id", "local_name", "role", "granted_at"]
        read_only_fields = ["id", "local_id", "local_name", "role", "granted_at"]


class MeSerializer(serializers.ModelSerializer):
    """
    GET /api/me/ — returns the caller's member id, local, and leadership roles.
    Never exposes or accepts role mutations.
    """

    local = LocalSerializer(read_only=True)
    leadership_roles = LeadershipRoleSerializer(many=True, read_only=True)

    class Meta:
        model = Member
        fields = [
            "id",
            "full_name",
            "email",
            "classification",
            "status",
            "local",
            "leadership_roles",
        ]
        read_only_fields = [
            "id",
            "full_name",
            "email",
            "classification",
            "status",
            "local",
            "leadership_roles",
        ]


class AnnouncementListSerializer(serializers.ModelSerializer):
    """
    POST /api/announcements/ and GET /api/announcements/ (leader).

    local_id is set from the authenticated leader's local, never from the request.
    """

    class Meta:
        model = Announcement
        fields = [
            "id",
            "local_id",
            "title",
            "body",
            "needs_ack",
            "starts_at",
            "status",
            "source",
            "audience_classifications",
            "push_preview",
            "approved_by_id",
            "approved_at",
            "recipient_count",
            "sent_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "local_id",
            "status",
            "approved_by_id",
            "approved_at",
            "recipient_count",
            "sent_at",
            "created_at",
        ]


class AnnouncementDetailSerializer(serializers.ModelSerializer):
    """
    GET /api/announcements/{id}/ and PATCH /api/announcements/{id}/ (leader).

    PATCH is only allowed while status == draft; any text edit clears approved_at.
    """

    class Meta:
        model = Announcement
        fields = [
            "id",
            "local_id",
            "title",
            "body",
            "needs_ack",
            "starts_at",
            "status",
            "source",
            "audience_classifications",
            "push_preview",
            "approved_by_id",
            "approved_at",
            "recipient_count",
            "final_counts",
            "sent_at",
            "ai_note",
            "ai_model",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "local_id",
            "status",
            "approved_by_id",
            "approved_at",
            "recipient_count",
            "final_counts",
            "sent_at",
            "ai_note",
            "ai_model",
            "created_at",
        ]

    def update(self, instance, validated_data):
        # Any text edit (title/body/push_preview) clears approved_at and approved_by.
        text_fields = {"title", "body", "push_preview"}
        if text_fields & set(validated_data.keys()):
            validated_data["approved_at"] = None
            validated_data["approved_by"] = None
        return super().update(instance, validated_data)


class InboxItemSerializer(serializers.ModelSerializer):
    """
    GET /api/me/inbox/ — a member's own receipt rows with the announcement summary.
    """

    announcement_title = serializers.CharField(
        source="announcement.title", read_only=True
    )
    announcement_body = serializers.CharField(
        source="announcement.body", read_only=True
    )
    announcement_starts_at = serializers.DateTimeField(
        source="announcement.starts_at", read_only=True
    )
    announcement_needs_ack = serializers.BooleanField(
        source="announcement.needs_ack", read_only=True
    )

    class Meta:
        model = AnnouncementRecipient
        fields = [
            "id",
            "announcement_id",
            "announcement_title",
            "announcement_body",
            "announcement_starts_at",
            "announcement_needs_ack",
            "delivered_at",
            "read_at",
            "acknowledged_at",
            "response",
            "responded_at",
        ]
        read_only_fields = [
            "id",
            "announcement_id",
            "announcement_title",
            "announcement_body",
            "announcement_starts_at",
            "announcement_needs_ack",
            "delivered_at",
            "read_at",
            "acknowledged_at",
            "response",
            "responded_at",
        ]


class ReceiptBatchSerializer(serializers.Serializer):
    """
    POST /api/me/receipts/ — batch of announcement ids to mark delivered/read.
    """

    announcement_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=500,
    )
    kind = serializers.ChoiceField(
        choices=["delivered", "read"],
        required=False,
        default=None,
    )
    # Kept for backward compatibility with older callers/tests
    delivered = serializers.BooleanField(required=False, default=None)
    read = serializers.BooleanField(required=False, default=None)
    acknowledged = serializers.BooleanField(required=False, default=None)



class RespondSerializer(serializers.Serializer):
    """
    POST /api/me/announcements/{id}/respond/ — member RSVP.
    """

    response = serializers.ChoiceField(
        choices=AnnouncementRecipient.Response.choices
    )
