"""
pytest configuration for CrewLink.
"""

import django
from django.conf import settings


def pytest_configure():
    pass  # decouple sets settings via DJANGO_SETTINGS_MODULE
