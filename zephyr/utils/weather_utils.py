"""Shared weather helpers (from the original bot.py, lines 154-172)."""

from datetime import datetime, timezone, timedelta


def get_tcws_description(wind_speed):
    if wind_speed >= 220:
        return "TCWS Level 5: Extremely Dangerous"
    elif wind_speed >= 185:
        return "TCWS Level 4: Very Destructive"
    elif wind_speed >= 118:
        return "TCWS Level 3: Destructive Typhoon"
    elif wind_speed >= 62:
        return "TCWS Level 2: Threatening Typhoon"
    elif wind_speed >= 30:
        return "TCWS Level 1: Tropical Cyclone Winds"
    return "No Tropical Cyclone Wind Signal"


def _format_datetime_pht(timestamp: int) -> str:
    pht_tz = timezone(timedelta(hours=8))
    utc_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    pht_dt = utc_dt.astimezone(pht_tz)
    return pht_dt.strftime('%B %d, %Y at %I:%M %p PHT')
