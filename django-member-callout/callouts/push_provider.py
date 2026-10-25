"""
FakePushProvider — development stand-in for FCM / APNS.

Writes a push_log row for each member pushed and simulates ~7% failure:
- ~4% "error": push_error is set on the recipient row.
- ~3% "silent_drop": push appears sent but is silently dropped.

Outcomes are deterministic given (announcement_id, member_id) so reruns are
consistent and tests are repeatable.
"""

import hashlib
import logging

from django.utils import timezone

from .models import AnnouncementRecipient, PushLog

logger = logging.getLogger(__name__)


def _outcome(announcement_id, member_id):
    """Deterministic outcome based on hash of (announcement_id, member_id)."""
    digest = hashlib.md5(
        f"{announcement_id}:{member_id}".encode()
    ).hexdigest()
    # Take last 4 hex chars → 0..65535
    val = int(digest[-4:], 16)
    if val < 2621:      # ~4% error
        return PushLog.Outcome.ERROR
    elif val < 4587:    # ~3% silent_drop
        return PushLog.Outcome.SILENT_DROP
    return PushLog.Outcome.SENT


def send_push_batch(announcement_id, member_ids):
    """
    Simulate sending push notifications to a batch of member_ids.

    The log is append-only and records duplicates honestly for invariant checks.
    """
    if not member_ids:
        return 0

    now = timezone.now()
    logs_to_create = []
    error_member_ids = []

    for mid in member_ids:
        outcome = _outcome(announcement_id, mid)
        logs_to_create.append(
            PushLog(
                announcement_id=announcement_id,
                member_id=mid,
                sent_at=now,
                simulated_outcome=outcome,
            )
        )
        if outcome == PushLog.Outcome.ERROR:
            error_member_ids.append(mid)

    # Append-only log without conflict ignoring — records dispatches honestly
    created = PushLog.objects.bulk_create(logs_to_create)

    # Set push_error on recipient rows for "error" outcomes.
    if error_member_ids:
        AnnouncementRecipient.objects.filter(
            announcement_id=announcement_id,
            member_id__in=error_member_ids,
        ).update(push_error="FCM: SIMULATED_ERROR")

    return len(created)
