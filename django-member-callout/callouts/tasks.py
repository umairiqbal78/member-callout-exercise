"""
Celery tasks for the CrewLink send path.

Every task is idempotent — safe to run twice. Workers use acks_late so a
crashed task is automatically redelivered by the broker.

Tasks:
- fan_out_task: create recipient rows for all active members (chunked, ON CONFLICT DO NOTHING)
- push_task: claim unclaimed recipients, send via FakePushProvider, maybe mark as sent
- sweeper: re-enqueue fan-out for announcements stuck in 'sending'
- closer: freeze final_counts and close announcements past their meeting time
- retention_cleanup: delete old closed-announcement data in batches
- invariant_check: log ERROR if any Rule 2 invariant is violated
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import connection, models, transaction
from django.utils import timezone

from .models import Announcement, AnnouncementRecipient, Member, PushLog

logger = logging.getLogger(__name__)

FANOUT_CHUNK_SIZE = 2000
PUSH_RANGE_SIZE = 500


@shared_task(name="callouts.tasks.fan_out")
def fan_out_task(announcement_id):
    """
    Idempotent fan-out: create one recipient row per active member of the
    announcement's local. Uses bulk_create with ignore_conflicts=True.
    Then enqueues push tasks over id ranges of ~500 recipients.
    """
    try:
        ann = Announcement.objects.get(pk=announcement_id)
    except Announcement.DoesNotExist:
        logger.error("fan_out: announcement %s not found", announcement_id)
        return

    if ann.status not in (Announcement.Status.SENDING, Announcement.Status.SENT):
        logger.warning(
            "fan_out: announcement %s has status %s, skipping",
            announcement_id, ann.status,
        )
        return

    # Build member queryset — optionally narrowed by audience_classifications
    member_qs = Member.objects.filter(
        local_id=ann.local_id,
        status=Member.Status.ACTIVE,
    )
    if ann.audience_classifications:
        member_qs = member_qs.filter(
            classification__in=ann.audience_classifications,
        )

    member_ids = list(member_qs.values_list("id", flat=True))

    # Chunked bulk_create with ON CONFLICT DO NOTHING
    for i in range(0, len(member_ids), FANOUT_CHUNK_SIZE):
        chunk = member_ids[i: i + FANOUT_CHUNK_SIZE]
        rows = [
            AnnouncementRecipient(
                announcement_id=ann.id,
                member_id=mid,
                local_id=ann.local_id,
            )
            for mid in chunk
        ]
        AnnouncementRecipient.objects.bulk_create(rows, ignore_conflicts=True)

    # Set recipient_count from actual row count
    count = AnnouncementRecipient.objects.filter(
        announcement_id=ann.id
    ).count()
    Announcement.objects.filter(pk=ann.id).update(recipient_count=count)

    if count == 0:
        # No recipients — mark as sent immediately
        Announcement.objects.filter(
            pk=ann.id, status=Announcement.Status.SENDING,
        ).update(status=Announcement.Status.SENT)
        logger.info(
            "fan_out: announcement %s has zero recipients, marked sent",
            announcement_id,
        )
        return

    # Enqueue push tasks over id ranges of ~PUSH_RANGE_SIZE
    # Get the min and max recipient ids for this announcement
    bounds = AnnouncementRecipient.objects.filter(
        announcement_id=ann.id
    ).aggregate(
        min_id=models.Min("id"),
        max_id=models.Max("id"),
    )
    min_id = bounds["min_id"]
    max_id = bounds["max_id"]

    if min_id is None:
        return

    current = min_id
    while current <= max_id:
        range_end = current + PUSH_RANGE_SIZE - 1
        push_task.delay(str(ann.id), current, min(range_end, max_id))
        current = range_end + 1

    logger.info(
        "fan_out: announcement %s — %d recipients, push tasks enqueued",
        announcement_id, count,
    )


@shared_task(name="callouts.tasks.push")
def push_task(announcement_id, id_start, id_end):
    """
    Idempotent push: claim unclaimed recipient rows with raw SQL
    UPDATE ... SET push_sent_at=now() WHERE push_sent_at IS NULL RETURNING member_id,
    then send via FakePushProvider for only the claimed rows.
    """
    from .push_provider import send_push_batch

    # Claim rows atomically — only rows with push_sent_at IS NULL
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE announcement_recipients
               SET push_sent_at = now()
             WHERE announcement_id = %s
               AND id BETWEEN %s AND %s
               AND push_sent_at IS NULL
         RETURNING member_id
            """,
            [announcement_id, id_start, id_end],
        )
        claimed_member_ids = [row[0] for row in cursor.fetchall()]

    if claimed_member_ids:
        send_push_batch(announcement_id, claimed_member_ids)

    # Check if all recipients are now claimed (push_sent_at is not NULL)
    unclaimed = AnnouncementRecipient.objects.filter(
        announcement_id=announcement_id,
        push_sent_at__isnull=True,
    ).exists()

    if not unclaimed:
        # Compare-and-set: sending → sent
        updated = Announcement.objects.filter(
            pk=announcement_id,
            status=Announcement.Status.SENDING,
        ).update(status=Announcement.Status.SENT)
        if updated:
            logger.info(
                "push: announcement %s all pushes complete, marked sent",
                announcement_id,
            )


@shared_task(name="callouts.tasks.sweeper")
def sweeper():
    """
    Re-enqueue fan-out for announcements stuck in 'sending' more than
    2 minutes after sent_at. Safe because fan_out is idempotent.
    """
    cutoff = timezone.now() - timedelta(minutes=2)
    stuck = Announcement.objects.filter(
        status=Announcement.Status.SENDING,
        sent_at__lt=cutoff,
    ).values_list("id", flat=True)

    for ann_id in stuck:
        fan_out_task.delay(str(ann_id))
        logger.info("sweeper: re-enqueued fan_out for announcement %s", ann_id)


@shared_task(name="callouts.tasks.closer")
def closer():
    """
    Close 'sent' announcements:
    - starts_at has passed, OR
    - 7 days after sent_at when starts_at is null.

    Stores final_counts from stats and compare-and-sets status to 'closed'.
    """
    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)

    to_close = Announcement.objects.filter(
        status=Announcement.Status.SENT,
    ).filter(
        models.Q(starts_at__isnull=False, starts_at__lt=now)
        | models.Q(starts_at__isnull=True, sent_at__lt=seven_days_ago)
    )

    for ann in to_close:
        # Compute final counts
        final = _compute_stats_from_db(ann.id)
        final["status"] = Announcement.Status.CLOSED
        final["as_of"] = now.isoformat()
        Announcement.objects.filter(
            pk=ann.id,
            status=Announcement.Status.SENT,
        ).update(
            status=Announcement.Status.CLOSED,
            final_counts=final,
        )
        logger.info("closer: closed announcement %s", ann.id)


@shared_task(name="callouts.tasks.retention_cleanup")
def retention_cleanup():
    """
    Delete recipient rows and push_log rows of announcements closed more than
    RETENTION_DAYS ago, in batches of 10,000 so no long locks.
    """
    days = getattr(settings, "RETENTION_DAYS", 365)
    cutoff = timezone.now() - timedelta(days=days)

    closed_ids = list(
        Announcement.objects.filter(
            status=Announcement.Status.CLOSED,
            sent_at__lt=cutoff,
        ).values_list("id", flat=True)
    )

    if not closed_ids:
        return

    batch_size = 10_000
    for ann_id in closed_ids:
        # Delete push_log rows in batches
        while True:
            ids = list(
                PushLog.objects.filter(announcement_id=ann_id)
                .values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            PushLog.objects.filter(id__in=ids).delete()

        # Delete recipient rows in batches
        while True:
            ids = list(
                AnnouncementRecipient.objects.filter(announcement_id=ann_id)
                .values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            AnnouncementRecipient.objects.filter(id__in=ids).delete()

    logger.info(
        "retention_cleanup: cleaned %d closed announcements", len(closed_ids),
    )


@shared_task(name="callouts.tasks.invariant_check")
def invariant_check():
    """
    Check Rule 2 invariants:
    1. No announcement has more push_log rows than recipient_count.
    2. No (announcement_id, member_id) has more than one push_log row.
    3. No recipient's local_id differs from its member's or announcement's local_id.

    Returns "OK" or a list of error messages.
    """
    errors = []

    # 1. push_log count > recipient_count
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT pl.announcement_id, COUNT(*) as push_count, a.recipient_count
              FROM push_log pl
              JOIN announcements a ON a.id = pl.announcement_id
             GROUP BY pl.announcement_id, a.recipient_count
            HAVING COUNT(*) > a.recipient_count
        """)
        for row in cursor.fetchall():
            errors.append(
                f"announcement {row[0]}: push_log count ({row[1]}) > "
                f"recipient_count ({row[2]})"
            )

    # 2. duplicate (announcement_id, member_id) in push_log
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT announcement_id, member_id, COUNT(*)
              FROM push_log
             GROUP BY announcement_id, member_id
            HAVING COUNT(*) > 1
        """)
        for row in cursor.fetchall():
            errors.append(
                f"push_log duplicate: announcement {row[0]}, "
                f"member {row[1]}, count {row[2]}"
            )

    # 3. recipient local_id mismatch
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT ar.id, ar.local_id, m.local_id AS member_local, a.local_id AS ann_local
              FROM announcement_recipients ar
              JOIN members m ON m.id = ar.member_id
              JOIN announcements a ON a.id = ar.announcement_id
             WHERE ar.local_id != m.local_id
                OR ar.local_id != a.local_id
             LIMIT 10
        """)
        for row in cursor.fetchall():
            errors.append(
                f"recipient {row[0]}: local_id={row[1]}, "
                f"member.local_id={row[2]}, announcement.local_id={row[3]}"
            )

    if errors:
        for e in errors:
            logger.error("INVARIANT VIOLATION: %s", e)
        return errors

    logger.info("invariant_check: OK")
    return "OK"


def _compute_stats_from_db(announcement_id):
    """
    Compute stats for an announcement using a single query with
    COUNT(*) FILTER.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS targeted,
                COUNT(*) FILTER (WHERE push_sent_at IS NOT NULL) AS sent,
                COUNT(*) FILTER (WHERE push_error IS NOT NULL AND push_error != '') AS push_failed,
                COUNT(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered,
                COUNT(*) FILTER (WHERE read_at IS NOT NULL) AS "read",
                COUNT(*) FILTER (WHERE acknowledged_at IS NOT NULL) AS acknowledged,
                COUNT(*) FILTER (WHERE response = 'coming') AS coming,
                COUNT(*) FILTER (WHERE response = 'cant_make_it') AS cant_make_it,
                COUNT(*) FILTER (WHERE response IS NULL) AS no_response
              FROM announcement_recipients
             WHERE announcement_id = %s
            """,
            [announcement_id],
        )
        row = cursor.fetchone()
        return {
            "targeted": row[0],
            "sent": row[1],
            "push_failed": row[2],
            "delivered": row[3],
            "read": row[4],
            "acknowledged": row[5],
            "coming": row[6],
            "cant_make_it": row[7],
            "no_response": row[8],
        }
