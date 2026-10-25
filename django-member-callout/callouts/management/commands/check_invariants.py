"""
check_invariants management command.

Runs Rule 2 invariant checks:
1. No announcement has more push_log rows than recipient_count.
2. No (announcement_id, member_id) has more than one push_log row.
3. No recipient's local_id differs from its member's or announcement's local_id.

Prints "OK" when clean, logs and prints errors otherwise.
"""

from django.core.management.base import BaseCommand

from callouts.tasks import invariant_check


class Command(BaseCommand):
    help = "Verify Rule 2 invariants across the database."

    def handle(self, *args, **options):
        result = invariant_check()
        if result == "OK":
            self.stdout.write(self.style.SUCCESS("OK"))
        else:
            for err in result:
                self.stderr.write(self.style.ERROR(f"INVARIANT VIOLATION: {err}"))
            raise SystemExit(1)
