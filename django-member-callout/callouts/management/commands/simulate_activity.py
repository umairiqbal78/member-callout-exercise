"""
callouts.management.commands.simulate_activity

Dev-only management command to simulate realistic member activity on an
announcement. Deterministic, idempotent, respects existing responses,
and drives changes through the shared service functions (record_receipts,
record_response).
"""

import hashlib
import json
import sys
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from callouts.models import Announcement, AnnouncementRecipient
from callouts.services import record_receipts, record_response


class Command(BaseCommand):
    help = "Simulate realistic member activity on an announcement (Dev only)."

    def add_arguments(self, parser):
        parser.add_argument("announcement_id", type=str, help="Announcement UUID")

    def handle(self, *args, **options):
        if not settings.DEBUG and "pytest" not in sys.modules:
            raise CommandError("simulate_activity refuses to run unless DEBUG=True.")

        ann_id = options["announcement_id"]
        ann = Announcement.objects.filter(id=ann_id).first()
        if not ann:
            raise CommandError(f"Announcement '{ann_id}' not found.")

        # Fetch recipients for this announcement
        recipients = list(
            AnnouncementRecipient.objects.filter(announcement_id=ann.id)
            .values("id", "member_id", "local_id", "response", "responded_at")
        )

        sent_at = ann.sent_at or ann.created_at

        for r in recipients:
            # Never overwrite a recipient who already responded
            if r["response"] is not None or r["responded_at"] is not None:
                continue

            mid = r["member_id"]
            rid = r["id"]
            lid = r["local_id"]

            digest1 = hashlib.md5(f"{ann.id}:{mid}".encode()).hexdigest()
            digest2 = hashlib.md5(f"{ann.id}:{mid}:resp".encode()).hexdigest()

            score = int(digest1[:8], 16) / 0xFFFFFFFF
            resp_score = int(digest2[:8], 16) / 0xFFFFFFFF

            del_offset = int(digest1[8:12], 16) % 7200 + 60
            read_offset = del_offset + (int(digest1[12:16], 16) % 3600 + 30)
            ack_offset = read_offset + (int(digest2[8:12], 16) % 1800 + 15)
            resp_offset = ack_offset + (int(digest2[12:16], 16) % 1800 + 15)

            t_del = sent_at + timedelta(seconds=del_offset)
            t_read = sent_at + timedelta(seconds=read_offset)
            t_ack = sent_at + timedelta(seconds=ack_offset)
            t_resp = sent_at + timedelta(seconds=resp_offset)

            if score < 0.32:
                resp_val = (
                    AnnouncementRecipient.Response.COMING
                    if resp_score < 0.70
                    else AnnouncementRecipient.Response.CANT_MAKE_IT
                )
                record_response(
                    recipient_id=rid,
                    response_value=resp_val,
                    delivered_at=t_del,
                    read_at=t_read,
                    acknowledged_at=t_ack,
                    responded_at=t_resp,
                    overwrite=False,
                )
            elif score < 0.45:
                record_receipts(
                    member_id=mid,
                    local_id=lid,
                    announcement_ids=[ann.id],
                    delivered=True,
                    read=True,
                    acknowledged=True,
                    delivered_at=t_del,
                    read_at=t_read,
                    acknowledged_at=t_ack,
                )
            elif score < 0.65:
                record_receipts(
                    member_id=mid,
                    local_id=lid,
                    announcement_ids=[ann.id],
                    delivered=True,
                    read=True,
                    acknowledged=False,
                    delivered_at=t_del,
                    read_at=t_read,
                )
            elif score < 0.90:
                record_receipts(
                    member_id=mid,
                    local_id=lid,
                    announcement_ids=[ann.id],
                    delivered=True,
                    read=False,
                    acknowledged=False,
                    delivered_at=t_del,
                )

        # Invalidate stats cache keys
        cache.delete(f"stats:{ann.id}:data")
        cache.delete(f"stats:{ann.id}:lock")

        # Independent plain aggregate query for ground-truth counts
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS targeted,
                    COUNT(*) FILTER (WHERE push_sent_at IS NOT NULL) AS sent,
                    COUNT(*) FILTER (WHERE push_error IS NOT NULL AND push_error != '') AS push_failed,
                    COUNT(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered,
                    COUNT(*) FILTER (WHERE read_at IS NOT NULL) AS read,
                    COUNT(*) FILTER (WHERE acknowledged_at IS NOT NULL) AS acknowledged,
                    COUNT(*) FILTER (WHERE response = 'coming') AS coming,
                    COUNT(*) FILTER (WHERE response = 'cant_make_it') AS cant_make_it,
                    COUNT(*) FILTER (WHERE response IS NULL) AS no_response
                  FROM announcement_recipients
                 WHERE announcement_id = %s
                """,
                [str(ann.id)],
            )
            row = cursor.fetchone()
            ground_truth = {
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

        # Print ONE JSON line of ground-truth counts
        self.stdout.write(json.dumps(ground_truth))
