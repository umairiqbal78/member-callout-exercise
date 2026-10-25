"""
Add push_log table for Session 2.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("callouts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PushLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "announcement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_logs",
                        to="callouts.announcement",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_logs",
                        to="callouts.member",
                    ),
                ),
                ("sent_at", models.DateTimeField()),
                (
                    "simulated_outcome",
                    models.CharField(
                        choices=[
                            ("ok", "OK"),
                            ("error", "Error"),
                            ("silent_drop", "Silent drop"),
                        ],
                        default="ok",
                        max_length=20,
                    ),
                ),
            ],
            options={"db_table": "push_log"},
        ),
        migrations.AddConstraint(
            model_name="pushlog",
            constraint=models.UniqueConstraint(
                fields=["announcement_id", "member_id"],
                name="push_log_announcement_member_uniq",
            ),
        ),
    ]
