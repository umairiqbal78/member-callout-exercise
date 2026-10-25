"""
pytest configuration for CrewLink.
"""

import django
from django.conf import settings


import pytest
from django.core.management import call_command

from callouts.management.commands.seed_demo import LOCAL_27_ID
from callouts.models import Local


@pytest.fixture(scope="session")
def django_db_setup():
    """Use the already-migrated and seeded database."""
    pass


@pytest.fixture(autouse=True)
def ensure_seed_data(db):
    """Ensure seed demo data exists even after transaction=True tests truncate tables."""
    if not Local.objects.filter(id=LOCAL_27_ID).exists():
        call_command("seed_demo")

