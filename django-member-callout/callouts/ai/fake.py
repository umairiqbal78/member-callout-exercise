"""
Fake AI provider for local development, CI, and deterministic testing.
"""

from typing import Any, Dict, List, Optional
from .interfaces import AIProvider


SAMPLE_NOTE = (
    "emergency mtg thurs 6pm hall re: contractor pulling crews off the westside job, "
    "EVERYONE needs to be there this is the third time"
)


class FakeProvider(AIProvider):
    """
    Deterministic mock provider that requires no API keys or internet connection.
    Used by default in Docker Compose and testing environments.
    """

    def __init__(self, canned_response: Optional[Dict[str, Any]] = None):
        self.canned_response = canned_response

    def generate(
        self,
        note: str,
        audience_classifications: Optional[List[str]] = None,
        timeout: float = 8.0,
    ) -> Dict[str, Any]:
        if self.canned_response is not None:
            return dict(self.canned_response)

        note_clean = note.strip()
        note_lower = note_clean.lower()

        # Handle the canonical sample note
        if "contractor pulling crews" in note_lower and "emergency mtg" in note_lower:
            return {
                "title": "EMERGENCY MEETING: Westside Job Crew Pulling",
                "body": (
                    "Emergency meeting Thursday at 6:00 PM at the union hall regarding contractor "
                    "pulling crews off the westside job. Everyone needs to be there — this is the third time."
                ),
                "push_preview": "Emergency meeting Thurs 6pm at the hall re: contractor pulling crews. Everyone must attend.",
                "warnings": ["Missing specific calendar date (e.g. MM/DD)"],
            }

        # Generic deterministic synthesis
        lines = [line.strip() for line in note_clean.splitlines() if line.strip()]
        first_line = lines[0] if lines else "Announcement"
        title = first_line[:80].capitalize()
        if len(first_line) > 80:
            title = title[:77] + "..."

        body = note_clean
        push_preview = body[:100]
        if len(body) > 100:
            push_preview = push_preview[:97] + "..."

        return {
            "title": title,
            "body": body,
            "push_preview": push_preview,
            "warnings": [],
        }
