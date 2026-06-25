"""Time-parsing/formatting helpers for the music player.

From the original bot.py: ``_parse_user_time`` (907-926) and
``_format_timestamp`` (929-935).
"""

import re


def _parse_user_time(value: str) -> int:
    """Parse a user time string (e.g. 1:30, 90s, 2m) into seconds."""
    value = value.strip()
    if re.match(r"^\d+$", value):
        return int(value)
    m = re.match(r"^(?:(\d+):)?(\d+):(\d+)$", value)
    if m:
        hours, minutes, seconds = m.groups()
        return (int(hours or 0) * 3600) + (int(minutes) * 60) + int(seconds)
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([hms])$", value, re.IGNORECASE)
    if m:
        num, unit = m.groups()
        num = float(num)
        unit = unit.lower()
        if unit == 'h':
            return int(num * 3600)
        if unit == 'm':
            return int(num * 60)
        return int(num)
    raise ValueError(f"Could not parse `{value}` as a time.")


def _format_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
