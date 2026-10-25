"""
Celery app for CrewLink.

Celery settings for reliability:
- task_acks_late: tasks are acknowledged only after completion.
- task_reject_on_worker_lost: tasks are requeued if a worker dies.
- worker_prefetch_multiplier=1: only one task prefetched per worker.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "member_callout_exercise.settings")

app = Celery("member_callout_exercise")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
