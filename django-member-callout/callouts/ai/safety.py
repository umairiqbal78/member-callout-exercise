"""
Safety validation and deterministic fact checking for AI-generated drafts.
"""

import re
from typing import Any, Dict, List, Set
from .interfaces import AIInvalidOutputError

# Weekday normalization
WEEKDAY_PATTERNS = {
    "monday": ["monday", "mon"],
    "tuesday": ["tuesday", "tue", "tues"],
    "wednesday": ["wednesday", "wed"],
    "thursday": ["thursday", "thu", "thur", "thurs"],
    "friday": ["friday", "fri"],
    "saturday": ["saturday", "sat"],
    "sunday": ["sunday", "sun"],
}

TIME_REGEX = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b|\b[0-2]?\d:[0-5]\d\b", re.IGNORECASE)
NUMBER_REGEX = re.compile(r"\b\d+\b")
CALENDAR_DATE_REGEX = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{1,2}-\d{1,2}(?:-\d{2,4})?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?|\d{1,2}(?:st|nd|rd|th)?\s+of\s+[a-z]+)\b",
    re.IGNORECASE,
)


def validate_draft_output(output: Any) -> Dict[str, Any]:
    """
    Validate that output is a dictionary meeting length and schema requirements.
    Never silently truncate; failing validation raises AIInvalidOutputError.
    """
    if not isinstance(output, dict):
        raise AIInvalidOutputError("AI output must be a JSON object")

    title = output.get("title")
    body = output.get("body")
    push_preview = output.get("push_preview")
    warnings = output.get("warnings", [])

    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise AIInvalidOutputError(f"Title must be a non-empty string <= 100 chars (got {len(title) if isinstance(title, str) else type(title)})")

    if not isinstance(body, str) or not body.strip() or len(body) > 2000:
        raise AIInvalidOutputError(f"Body must be a non-empty string <= 2000 chars (got {len(body) if isinstance(body, str) else type(body)})")

    if not isinstance(push_preview, str) or not push_preview.strip() or len(push_preview) > 120:
        raise AIInvalidOutputError(f"Push preview must be a non-empty string <= 120 chars (got {len(push_preview) if isinstance(push_preview, str) else type(push_preview)})")

    if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
        warnings = [str(w) for w in warnings] if isinstance(warnings, list) else []

    return {
        "title": title.strip(),
        "body": body.strip(),
        "push_preview": push_preview.strip(),
        "warnings": warnings,
    }


def deterministic_fact_check(note: str, output: Dict[str, Any]) -> List[str]:
    """
    Find numbers, times and weekday names in the output.
    Any that do not appear in the note become extra entries in warnings
    (for example '7pm does not appear in your note').
    Also checks if essential details (like calendar date) are missing in the note.
    """
    note_lower = note.lower()
    combined_output = f"{output.get('title', '')} {output.get('body', '')} {output.get('push_preview', '')}".lower()
    extra_warnings: List[str] = []

    # 1. Weekday check
    for day_name, aliases in WEEKDAY_PATTERNS.items():
        found_in_output = any(re.search(rf"\b{re.escape(alias)}\b", combined_output) for alias in aliases)
        if found_in_output:
            found_in_note = any(re.search(rf"\b{re.escape(alias)}\b", note_lower) for alias in aliases)
            if not found_in_note:
                extra_warnings.append(f"{day_name.capitalize()} does not appear in your note")

    # 2. Time check
    output_times = TIME_REGEX.findall(combined_output)
    for raw_time in output_times:
        clean_time = raw_time.strip().lower()
        # Look for the time in the note: try exact match or without space (e.g. 6 pm vs 6pm)
        compact_time = clean_time.replace(" ", "")
        time_digits = re.search(r"\d+", clean_time).group() if re.search(r"\d+", clean_time) else ""
        
        # Check if the time representation appears in note
        in_note = (
            clean_time in note_lower or
            compact_time in note_lower or
            (f"{time_digits}pm" in note_lower and "pm" in compact_time) or
            (f"{time_digits}am" in note_lower and "am" in compact_time) or
            (f"{time_digits}:00" in note_lower)
        )
        if not in_note:
            extra_warnings.append(f"{raw_time} does not appear in your note")

    # 3. Numbers check
    # Check standalone numbers in output (excluding numbers that are part of time expressions)
    time_number_parts = set()
    for raw_time in output_times:
        time_number_parts.update(re.findall(r"\d+", raw_time))

    output_numbers = NUMBER_REGEX.findall(combined_output)
    all_note_digits = set(re.findall(r"\d+", note_lower))

    # Also extract numbers written as words if relevant
    word_to_num = {
        "one": "1", "first": "1",
        "two": "2", "second": "2",
        "three": "3", "third": "3",
        "four": "4", "fourth": "4",
        "five": "5", "fifth": "5",
        "six": "6", "sixth": "6",
        "seven": "7", "seventh": "7",
        "eight": "8", "eighth": "8",
        "nine": "9", "ninth": "9",
        "ten": "10", "tenth": "10",
    }
    for word, num in word_to_num.items():
        if re.search(rf"\b{word}\b", note_lower):
            all_note_digits.add(num)
            all_note_digits.add(word)

    for num in output_numbers:
        # If this number was part of a time expression, skip checking it as a standalone number
        if num in time_number_parts:
            continue
        # If the number exists as digits in the note, skip
        if num in all_note_digits:
            continue
        warning_msg = f"{num} does not appear in your note"
        if warning_msg not in extra_warnings:
            extra_warnings.append(warning_msg)

    # 4. Check missing essential information from note
    # If a weekday or meeting is mentioned without a calendar date, add a warning
    has_weekday_in_note = any(
        any(re.search(rf"\b{re.escape(alias)}\b", note_lower) for alias in aliases)
        for aliases in WEEKDAY_PATTERNS.values()
    )
    has_date_in_note = bool(CALENDAR_DATE_REGEX.search(note_lower))
    if has_weekday_in_note and not has_date_in_note:
        date_warning = "Missing specific calendar date (e.g. MM/DD)"
        if date_warning not in extra_warnings and not any("date" in w.lower() for w in output.get("warnings", [])):
            extra_warnings.append(date_warning)

    # Deduplicate while preserving order
    combined_warnings = list(output.get("warnings", []))
    for w in extra_warnings:
        if w not in combined_warnings:
            combined_warnings.append(w)

    return combined_warnings
