"""
seed_demo — populate deterministic demo data for CrewLink.

Design decisions:
- All UUIDs are derived with uuid5(NAMESPACE_DNS, stable-string) so a second
  run produces identical IDs and bulk_create with update_conflicts=False leaves
  existing rows untouched (ON CONFLICT DO NOTHING semantics).
- Refuses to run unless DEBUG is True.
- --reset wipes only seeded rows identified by deterministic IDs.
- Two locals: Local 27 (~2,000 members) and Local 104 (200 members).
- Four classifications in each local.
- One leader login and one member login per local.
- One sent announcement in Local 27 with recipient rows for every active member.
  Realistic mix of delivery / read / response / push_error states.
"""

import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from callouts.models import (
    Announcement,
    AnnouncementRecipient,
    LeadershipRole,
    Local,
    Member,
)

User = get_user_model()

# ── Stable seeds ──────────────────────────────────────────────────────────────
NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_DNS

LOCAL_27_ID = uuid.uuid5(NAMESPACE, "crewlink:local:27")
LOCAL_104_ID = uuid.uuid5(NAMESPACE, "crewlink:local:104")

CLASSIFICATIONS = [
    "Journeyman Wireman",
    "Foreman",
    "Apprentice 1st Year",
    "Apprentice 3rd Year",
]

# Demo credentials — clearly labelled as demo only.
ACCOUNTS = {
    LOCAL_27_ID: {
        "leader": {
            "username": "leader_local27",
            "password": "Demo1234!",
            "full_name": "Jordan Rivera",
            "email": "leader27@demo.crewlink.example",
            "classification": "Foreman",
        },
        "member": {
            "username": "member_local27",
            "password": "Demo1234!",
            "full_name": "Alex Chen",
            "email": "member27@demo.crewlink.example",
            "classification": "Journeyman Wireman",
        },
    },
    LOCAL_104_ID: {
        "leader": {
            "username": "leader_local104",
            "password": "Demo1234!",
            "full_name": "Morgan Okafor",
            "email": "leader104@demo.crewlink.example",
            "classification": "Foreman",
        },
        "member": {
            "username": "member_local104",
            "password": "Demo1234!",
            "full_name": "Sam Vasquez",
            "email": "member104@demo.crewlink.example",
            "classification": "Apprentice 3rd Year",
        },
    },
}

ANNOUNCEMENT_27_ID = uuid.uuid5(NAMESPACE, "crewlink:announcement:27:emergency-meeting-q4")

# Statuses: mostly active, some retired, some suspended.
# Distribution: 80% active, 10% retired, 10% suspended
STATUS_WEIGHTS = ["active"] * 80 + ["retired"] * 10 + ["suspended"] * 10


def _uuid5(label: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, label)


def _get_or_create_user(username: str, password: str, email: str) -> User:
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "is_active": True},
    )
    if created:
        user.set_password(password)
        user.save()
    return user


def _get_or_create_local(local_id: uuid.UUID, name: str) -> Local:
    obj, _ = Local.objects.get_or_create(
        id=local_id,
        defaults={"name": name},
    )
    return obj


def _get_or_create_member(member_id: uuid.UUID, local: Local, defaults: dict) -> Member:
    obj, _ = Member.objects.get_or_create(
        id=member_id,
        defaults={"local": local, **defaults},
    )
    return obj


class Command(BaseCommand):
    help = "Seed deterministic demo data for CrewLink. Safe to run multiple times."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Wipe only seeded data before re-seeding.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo refuses to run unless DEBUG=True. "
                "Never seed demo data into production."
            )

        if options["reset"]:
            self._reset()

        with transaction.atomic():
            self._seed()

        self.stdout.write(self.style.SUCCESS("seed_demo complete."))

    # ── Reset ─────────────────────────────────────────────────────────────────

    def _reset(self):
        self.stdout.write("Resetting seeded data…")
        with transaction.atomic():
            # Delete in FK-safe order.
            AnnouncementRecipient.objects.filter(
                announcement_id=ANNOUNCEMENT_27_ID
            ).delete()
            Announcement.objects.filter(id=ANNOUNCEMENT_27_ID).delete()

            for local_id, accs in ACCOUNTS.items():
                for role_key in ("leader", "member"):
                    acc = accs[role_key]
                    username = acc["username"]
                    member_id = _uuid5(f"crewlink:member:{username}")
                    LeadershipRole.objects.filter(member_id=member_id).delete()
                    Member.objects.filter(id=member_id).delete()
                    User.objects.filter(username=username).delete()

            for local_id, name in [
                (LOCAL_27_ID, "Local 27"),
                (LOCAL_104_ID, "Local 104"),
            ]:
                # Delete rank-and-file members
                Member.objects.filter(local_id=local_id).delete()
                Local.objects.filter(id=local_id).delete()

        self.stdout.write("Reset done.")

    # ── Seed ──────────────────────────────────────────────────────────────────

    def _seed(self):
        rng = random.Random(42)  # deterministic

        # ── Locals ────────────────────────────────────────────────────────────
        local27 = _get_or_create_local(LOCAL_27_ID, "Local 27")
        local104 = _get_or_create_local(LOCAL_104_ID, "Local 104")

        # ── Named accounts (leaders + members) ────────────────────────────────
        leader27_member = self._seed_named_account(local27, "leader", rng)
        member27_member = self._seed_named_account(local27, "member", rng)
        leader104_member = self._seed_named_account(local104, "leader", rng)
        member104_member = self._seed_named_account(local104, "member", rng)

        # Grant leadership_roles
        self._grant_leadership(leader27_member, local27)
        self._grant_leadership(leader104_member, local104)

        # ── Rank-and-file members ──────────────────────────────────────────────
        self.stdout.write("Seeding Local 27 members (2,000)…")
        members27 = self._seed_bulk_members(local27, count=2000, rng=rng, offset=0)
        self.stdout.write("Seeding Local 104 members (200)…")
        members104 = self._seed_bulk_members(local104, count=200, rng=rng, offset=2000)

        # ── Announcement in Local 27 ──────────────────────────────────────────
        self.stdout.write("Seeding announcement and recipients for Local 27…")
        announcement = self._seed_announcement(local27, leader27_member)

        # All active members of Local 27 (including named member27).
        all_local27_active = list(
            Member.objects.filter(
                local_id=LOCAL_27_ID, status=Member.Status.ACTIVE
            ).values_list("id", flat=True)
        )
        self._seed_recipients(announcement, local27, all_local27_active, rng)

    def _seed_named_account(self, local: Local, role_key: str, rng) -> Member:
        acc = ACCOUNTS[local.id][role_key]
        user = _get_or_create_user(acc["username"], acc["password"], acc["email"])
        member_id = _uuid5(f"crewlink:member:{acc['username']}")
        member = _get_or_create_member(
            member_id,
            local,
            {
                "user": user,
                "full_name": acc["full_name"],
                "email": acc["email"],
                "classification": acc["classification"],
                "status": Member.Status.ACTIVE,
            },
        )
        # Ensure user is linked even if the Member already existed.
        if member.user_id is None:
            member.user = user
            member.save(update_fields=["user"])
        return member

    def _grant_leadership(self, member: Member, local: Local):
        LeadershipRole.objects.get_or_create(
            member=member,
            local=local,
            defaults={"role": "leader"},
        )

    def _seed_bulk_members(
        self, local: Local, count: int, rng, offset: int
    ) -> list:
        """
        Create `count` rank-and-file members deterministically.
        Uses bulk_create with update_conflicts=False (ON CONFLICT DO NOTHING).
        """
        existing_ids = set(
            Member.objects.filter(local=local).values_list("id", flat=True)
        )
        to_create = []
        for i in range(count):
            idx = offset + i
            member_id = _uuid5(f"crewlink:rankfile:member:{idx}")
            if member_id in existing_ids:
                continue
            classification = CLASSIFICATIONS[i % len(CLASSIFICATIONS)]
            # 80% active, 10% retired, 10% suspended
            status_val = STATUS_WEIGHTS[rng.randint(0, len(STATUS_WEIGHTS) - 1)]
            to_create.append(
                Member(
                    id=member_id,
                    local=local,
                    user=None,
                    full_name=f"Member {idx:05d}",
                    email=f"member{idx:05d}@demo.crewlink.example",
                    classification=classification,
                    status=status_val,
                )
            )
        if to_create:
            Member.objects.bulk_create(to_create, ignore_conflicts=True)
        return list(
            Member.objects.filter(local=local).values_list("id", flat=True)
        )

    def _seed_announcement(self, local: Local, created_by: Member) -> Announcement:
        announcement, _ = Announcement.objects.get_or_create(
            id=ANNOUNCEMENT_27_ID,
            defaults={
                "local": local,
                "created_by": created_by,
                "title": "Emergency Meeting — Q4 Contract Talks",
                "body": (
                    "All active members of Local 27 are required to attend an emergency "
                    "meeting regarding the Q4 contract negotiations. "
                    "Your attendance is critical."
                ),
                "needs_ack": True,
                "starts_at": timezone.now() + timedelta(days=3),
                "status": Announcement.Status.SENT,
                "source": Announcement.Source.MANUAL,
                "audience_classifications": CLASSIFICATIONS,
                "push_preview": "Emergency meeting — Q4 contract talks. Please attend.",
                "sent_at": timezone.now() - timedelta(hours=2),
                "idempotency_key": "emergency-meeting-q4-2026",
            },
        )
        return announcement

    def _seed_recipients(
        self,
        announcement: Announcement,
        local: Local,
        member_ids: list,
        rng,
    ):
        """
        Bulk-create recipient rows for every active member.
        Uses ON CONFLICT DO NOTHING so a second run is harmless.
        """
        existing_member_ids = set(
            AnnouncementRecipient.objects.filter(
                announcement_id=announcement.id
            ).values_list("member_id", flat=True)
        )

        now = timezone.now()
        to_create = []
        for member_id in member_ids:
            if member_id in existing_member_ids:
                continue
            # Assign a realistic state based on rng.
            roll = rng.random()
            delivered_at = None
            read_at = None
            acknowledged_at = None
            response = None
            responded_at = None
            push_error = ""

            if roll < 0.05:
                # ~5%: push failed, nothing received yet
                push_error = "FCM: UNREGISTERED"
            elif roll < 0.15:
                # ~10%: targeted but nothing received
                pass
            elif roll < 0.45:
                # ~30%: delivered only
                delivered_at = now - timedelta(hours=rng.randint(1, 120))
            elif roll < 0.60:
                # ~15%: delivered + read
                delivered_at = now - timedelta(hours=rng.randint(1, 120))
                read_at = delivered_at + timedelta(minutes=rng.randint(1, 30))
            elif roll < 0.72:
                # ~12%: delivered + read + acknowledged
                delivered_at = now - timedelta(hours=rng.randint(1, 120))
                read_at = delivered_at + timedelta(minutes=rng.randint(1, 30))
                acknowledged_at = read_at + timedelta(minutes=rng.randint(1, 20))
            elif roll < 0.84:
                # ~12%: coming
                delivered_at = now - timedelta(hours=rng.randint(1, 48))
                read_at = delivered_at + timedelta(minutes=rng.randint(1, 30))
                acknowledged_at = read_at + timedelta(minutes=rng.randint(1, 20))
                response = AnnouncementRecipient.Response.COMING
                responded_at = acknowledged_at + timedelta(minutes=rng.randint(1, 10))
            else:
                # ~16%: can't make it
                delivered_at = now - timedelta(hours=rng.randint(1, 48))
                read_at = delivered_at + timedelta(minutes=rng.randint(1, 30))
                response = AnnouncementRecipient.Response.CANT_MAKE_IT
                responded_at = read_at + timedelta(minutes=rng.randint(1, 10))

            push_sent_at = (
                None
                if push_error
                else (now - timedelta(hours=rng.randint(1, 120)) if rng.random() > 0.1 else None)
            )

            to_create.append(
                AnnouncementRecipient(
                    announcement=announcement,
                    member_id=member_id,
                    local=local,
                    delivered_at=delivered_at,
                    read_at=read_at,
                    acknowledged_at=acknowledged_at,
                    response=response,
                    responded_at=responded_at,
                    push_sent_at=push_sent_at,
                    push_error=push_error,
                )
            )

        chunk_size = 2000
        for i in range(0, len(to_create), chunk_size):
            AnnouncementRecipient.objects.bulk_create(
                to_create[i : i + chunk_size], ignore_conflicts=True
            )

        # Update recipient_count on the announcement.
        count = AnnouncementRecipient.objects.filter(
            announcement=announcement
        ).count()
        Announcement.objects.filter(id=announcement.id).update(
            recipient_count=count
        )
        self.stdout.write(f"  Seeded {count} recipient rows.")
