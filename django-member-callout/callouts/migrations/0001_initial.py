"""
Initial migration for the callouts app.

Key notes beyond what Django generates automatically:
1. RunSQL adds composite foreign key constraints on announcement_recipients so
   Postgres rejects any recipient row whose local_id doesn't match both the
   announcement's local_id and the member's local_id. This is Rule 1 at the
   database level — application code alone isn't enough.
2. RunSQL sets fillfactor=80 on announcement_recipients so in-place HOT updates
   (delivered_at, read_at, push_sent_at, push_error) can reuse page slots and
   avoid bloat without needing frequent VACUUM.
"""

import django.contrib.postgres.fields
import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── locals ──────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Local",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "locals"},
        ),
        # ── members ─────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Member",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "local",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="members",
                        to="callouts.local",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="member",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("full_name", models.CharField(max_length=255)),
                ("email", models.EmailField(blank=True)),
                ("classification", models.CharField(max_length=120)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("retired", "Retired"),
                            ("suspended", "Suspended"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "members"},
        ),
        migrations.AddIndex(
            model_name="member",
            index=models.Index(
                fields=["local_id", "status"],
                name="members_local_status_idx",
            ),
        ),
        # ── leadership_roles ────────────────────────────────────────────────
        migrations.CreateModel(
            name="LeadershipRole",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="leadership_roles",
                        to="callouts.member",
                    ),
                ),
                (
                    "local",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="leadership_roles",
                        to="callouts.local",
                    ),
                ),
                ("role", models.CharField(default="leader", max_length=80)),
                (
                    "granted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "leadership_roles"},
        ),
        migrations.AddConstraint(
            model_name="leadershiprole",
            constraint=models.UniqueConstraint(
                fields=["member_id", "local_id"],
                name="leadership_roles_member_local_uniq",
            ),
        ),
        # ── member_devices ──────────────────────────────────────────────────
        migrations.CreateModel(
            name="MemberDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="devices",
                        to="callouts.member",
                    ),
                ),
                ("push_token", models.CharField(max_length=500)),
                (
                    "platform",
                    models.CharField(
                        choices=[("android", "Android"), ("ios", "iOS")],
                        max_length=10,
                    ),
                ),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("disabled_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "member_devices"},
        ),
        # ── announcements ────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Announcement",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "local",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="announcements",
                        to="callouts.local",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_announcements",
                        to="callouts.member",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("needs_ack", models.BooleanField(default=False)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("sending", "Sending"),
                            ("sent", "Sent"),
                            ("closed", "Closed"),
                        ],
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[("manual", "Manual"), ("ai", "AI")],
                        default="manual",
                        max_length=10,
                    ),
                ),
                (
                    "audience_classifications",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=120),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                ("push_preview", models.CharField(blank=True, max_length=120)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_announcements",
                        to="callouts.member",
                    ),
                ),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("recipient_count", models.IntegerField(default=0)),
                ("final_counts", models.JSONField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("idempotency_key", models.CharField(blank=True, max_length=255)),
            ],
            options={"db_table": "announcements"},
        ),
        migrations.AddConstraint(
            model_name="announcement",
            constraint=models.UniqueConstraint(
                condition=models.Q(idempotency_key__gt=""),
                fields=["local_id", "idempotency_key"],
                name="announcements_local_idempotency_uniq",
            ),
        ),
        # ── announcement_recipients ──────────────────────────────────────────
        migrations.CreateModel(
            name="AnnouncementRecipient",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "announcement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recipients",
                        to="callouts.announcement",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="announcement_receipts",
                        to="callouts.member",
                    ),
                ),
                (
                    "local",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="callouts.local",
                    ),
                ),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                (
                    "response",
                    models.CharField(
                        blank=True,
                        choices=[("coming", "Coming"), ("cant_make_it", "Can't Make It")],
                        max_length=20,
                        null=True,
                    ),
                ),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("push_sent_at", models.DateTimeField(blank=True, null=True)),
                ("push_error", models.TextField(blank=True)),
            ],
            options={"db_table": "announcement_recipients"},
        ),
        migrations.AddConstraint(
            model_name="announcementrecipient",
            constraint=models.UniqueConstraint(
                fields=["announcement_id", "member_id"],
                name="announcement_recipients_announcement_member_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="announcementrecipient",
            index=models.Index(
                fields=["member_id", "-id"],
                name="ar_member_id_desc_idx",
            ),
        ),
        # ── fillfactor 80 on announcement_recipients ─────────────────────────
        # Django's ORM cannot set storage parameters directly, so we use RunSQL.
        migrations.RunSQL(
            sql="ALTER TABLE announcement_recipients SET (fillfactor = 80);",
            reverse_sql="ALTER TABLE announcement_recipients RESET (fillfactor);",
        ),
        # ── Unique indexes needed for composite FKs (must come BEFORE the FKs) ──
        # Postgres requires a unique constraint on the referenced columns first.
        migrations.RunSQL(
            sql="""
            CREATE UNIQUE INDEX IF NOT EXISTS announcements_id_local_uniq
                ON announcements (id, local_id);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS announcements_id_local_uniq;
            """,
        ),
        migrations.RunSQL(
            sql="""
            CREATE UNIQUE INDEX IF NOT EXISTS members_id_local_uniq
                ON members (id, local_id);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS members_id_local_uniq;
            """,
        ),
        # ── Composite FK: recipients.(announcement_id, local_id) → announcements ──
        # Ensures a recipient's local matches the announcement's local.
        migrations.RunSQL(
            sql="""
            ALTER TABLE announcement_recipients
                ADD CONSTRAINT ar_announcement_local_fk
                FOREIGN KEY (announcement_id, local_id)
                REFERENCES announcements (id, local_id)
                DEFERRABLE INITIALLY DEFERRED;
            """,
            reverse_sql="""
            ALTER TABLE announcement_recipients
                DROP CONSTRAINT IF EXISTS ar_announcement_local_fk;
            """,
        ),
        # ── Composite FK: recipients.(member_id, local_id) → members ─────────
        # Ensures a recipient's local matches the member's local.
        migrations.RunSQL(
            sql="""
            ALTER TABLE announcement_recipients
                ADD CONSTRAINT ar_member_local_fk
                FOREIGN KEY (member_id, local_id)
                REFERENCES members (id, local_id)
                DEFERRABLE INITIALLY DEFERRED;
            """,
            reverse_sql="""
            ALTER TABLE announcement_recipients
                DROP CONSTRAINT IF EXISTS ar_member_local_fk;
            """,
        ),
    ]
