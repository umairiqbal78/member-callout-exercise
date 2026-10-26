"""
callouts.services — Shared service functions for recipient state mutations.

Enforces the Funnel Rule:
- read_at set implies delivered_at set.
- acknowledged_at set implies read_at set.
- response set implies acknowledged_at and responded_at set.
- delivered_at <= read_at <= acknowledged_at.

All writes to recipient lifecycle columns (delivered_at, read_at, acknowledged_at,
response, responded_at) MUST go through these functions.
"""

import logging
from datetime import datetime
from typing import Iterable, Optional
from uuid import UUID

from django.db.models import DateTimeField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import AnnouncementRecipient

logger = logging.getLogger(__name__)


def record_receipts(
    member_id: Optional[UUID] = None,
    local_id: Optional[UUID] = None,
    announcement_ids: Optional[Iterable[UUID]] = None,
    kind: Optional[str] = None,
    delivered: Optional[bool] = None,
    read: Optional[bool] = None,
    acknowledged: Optional[bool] = None,
    now: Optional[datetime] = None,
    delivered_at: Optional[datetime] = None,
    read_at: Optional[datetime] = None,
    acknowledged_at: Optional[datetime] = None,
    member_ids: Optional[Iterable[UUID]] = None,
) -> int:
    """
    Record delivery, read, or acknowledgement receipts for member(s).

    Uses COALESCE so timestamps are set only once and repeated/out-of-order
    events never undo or corrupt earlier timestamps.
    Automatically backfills earlier funnel steps to satisfy the funnel rule:
    - 'delivered' sets delivered_at only.
    - 'read' sets read_at, and backfills delivered_at if empty.
    - 'acknowledged' sets acknowledged_at, and backfills read_at and delivered_at if empty.
    Never fills later steps.
    """
    ann_ids = list(announcement_ids) if announcement_ids else []

    if kind is None:
        if acknowledged:
            kind = "acknowledged"
        elif read:
            kind = "read"
        elif delivered or (delivered is None and not read and not acknowledged):
            kind = "delivered"
        else:
            return 0

    if now is None:
        now = timezone.now()

    # Determine fallback timestamps maintaining delivered_at <= read_at <= acknowledged_at
    t_ack = acknowledged_at or now
    t_read = read_at or (t_ack if kind == "acknowledged" else now)
    t_del = delivered_at or (t_read if kind in ("read", "acknowledged") else now)

    update_kwargs = {}
    if kind == "acknowledged":
        update_kwargs["delivered_at"] = Coalesce(
            "delivered_at", Value(t_del, output_field=DateTimeField())
        )
        update_kwargs["read_at"] = Coalesce(
            "read_at", Value(t_read, output_field=DateTimeField())
        )
        update_kwargs["acknowledged_at"] = Coalesce(
            "acknowledged_at", Value(t_ack, output_field=DateTimeField())
        )
    elif kind == "read":
        update_kwargs["delivered_at"] = Coalesce(
            "delivered_at", Value(t_del, output_field=DateTimeField())
        )
        update_kwargs["read_at"] = Coalesce(
            "read_at", Value(t_read, output_field=DateTimeField())
        )
    elif kind == "delivered":
        update_kwargs["delivered_at"] = Coalesce(
            "delivered_at", Value(t_del, output_field=DateTimeField())
        )
    else:
        raise ValueError(f"Unknown kind: {kind}")

    qs = AnnouncementRecipient.objects.all()
    if ann_ids:
        qs = qs.filter(announcement_id__in=ann_ids)
    if member_ids:
        qs = qs.filter(member_id__in=member_ids)
    elif member_id:
        qs = qs.filter(member_id=member_id)
    if local_id:
        qs = qs.filter(local_id=local_id)

    return qs.update(**update_kwargs)


def record_response(
    recipient: Optional[AnnouncementRecipient] = None,
    response_value: str = "",
    now: Optional[datetime] = None,
    delivered_at: Optional[datetime] = None,
    read_at: Optional[datetime] = None,
    acknowledged_at: Optional[datetime] = None,
    responded_at: Optional[datetime] = None,
    overwrite: bool = True,
    recipient_id: Optional[int] = None,
    recipient_ids: Optional[Iterable[int]] = None,
) -> int:
    """
    Record a member's response ('coming' or 'cant_make_it') to an announcement.

    Funnel enforcement:
    - Setting response requires acknowledged_at and responded_at to be set.
    - Fills earlier steps (read_at, delivered_at) with COALESCE if not already set.
    - Guarantees delivered_at <= read_at <= acknowledged_at <= responded_at.
    - If overwrite is False, never overwrites a recipient who already responded.
    """
    if now is None:
        now = timezone.now()

    t_resp = responded_at or now
    t_ack = acknowledged_at or t_resp
    t_read = read_at or t_ack
    t_del = delivered_at or t_read

    qs = AnnouncementRecipient.objects.all()
    if recipient_ids is not None:
        qs = qs.filter(id__in=recipient_ids)
    elif recipient_id is not None:
        qs = qs.filter(id=recipient_id)
    elif recipient is not None:
        qs = qs.filter(pk=recipient.pk)
    else:
        return 0

    if not overwrite:
        qs = qs.filter(response__isnull=True)

    return qs.update(
        response=response_value,
        responded_at=Coalesce("responded_at", Value(t_resp, output_field=DateTimeField())),
        acknowledged_at=Coalesce("acknowledged_at", Value(t_ack, output_field=DateTimeField())),
        read_at=Coalesce("read_at", Value(t_read, output_field=DateTimeField())),
        delivered_at=Coalesce("delivered_at", Value(t_del, output_field=DateTimeField())),
    )
