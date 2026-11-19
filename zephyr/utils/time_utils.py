"""Time-parsing/formatting helpers for the music player.

From the original bot.py: ``_parse_user_time`` (907-926) and
``_format_timestamp`` (929-935).
"""

import re


# Longer than any track or sensible seek into a live stream.  Without a cap a
# few hundred digits overflowed float() to inf, int(inf) raised OverflowError,
# and a plain huge number went straight to FFmpeg's -ss on a live stream.
MAX_USER_SECONDS = 7 * 24 * 3600


def _parse_user_time(value: str) -> int:
    """Parse a user time string (e.g. 1:30, 90s, 2m) into seconds.

    Raises ``ValueError`` for anything unreadable or longer than a week.
    """
    value = value.strip()
    seconds = None
    if re.match(r"^\d+$", value):
        seconds = int(value) if len(value) <= 12 else None
    elif m := re.match(r"^(?:(\d+):)?(\d+):(\d+)$", value):
        hours, minutes, secs = m.groups()
        if max(len(hours or ""), len(minutes), len(secs)) <= 12:
            seconds = (int(hours or 0) * 3600) + (int(minutes) * 60) + int(secs)
    elif m := re.match(r"^(\d+(?:\.\d+)?)\s*([hms])$", value, re.IGNORECASE):
        num, unit = m.groups()
        number = float(num) * {"h": 3600, "m": 60, "s": 1}[unit.lower()]
        if number < float(MAX_USER_SECONDS + 1):
            seconds = int(number)
    else:
        raise ValueError(f"Could not parse `{value[:40]}` as a time.")
    if seconds is None or seconds > MAX_USER_SECONDS:
        raise ValueError("That time is too far ahead.")
    return seconds


def _format_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
