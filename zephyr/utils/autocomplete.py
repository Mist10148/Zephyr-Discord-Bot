"""Shared autocomplete machinery.

Every command took free text. `/play` took a raw search string, `/setlocation`
took a raw city name, and the playlist commands took a raw name -- so the
information needed to type them correctly (what the search will actually find,
how the geocoder spells the city, which playlists exist) was only available
*after* getting it wrong.

The hard constraint is that Discord closes an autocomplete after **3 seconds**
and shows nothing at all if the callback is slower. Every upstream lookup here
therefore goes through a short-lived cache keyed on the term, so a person typing
"man-i-l-a" produces one upstream call rather than six -- the same lesson as
8.3's debounce on the web, arrived at from the other direction.

The cache is intentionally per-process and tiny. It exists to survive the
keystrokes of one person composing one command, not to be a data store.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from zephyr.core.logging import get_logger

log = get_logger(__name__)

# Discord's own ceiling. Returning more is an error, not a truncation.
MAX_CHOICES = 25
# Long enough to cover composing one command, short enough that a renamed
# playlist or a moved city is not remembered.
CACHE_TTL_SECONDS = 30
# Bounded so a spelling-mistake storm cannot grow it without limit.
CACHE_MAX_ENTRIES = 256
# Under Discord's 3s ceiling with room for the reply itself. A lookup that
# misses this deadline is answered with an empty list rather than nothing at
# all -- the client shows "no options" instead of hanging.
LOOKUP_TIMEOUT_SECONDS = 2.0

_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _prune(now: float) -> None:
    expired = [key for key, (stamp, _) in _cache.items() if now - stamp > CACHE_TTL_SECONDS]
    for key in expired:
        _cache.pop(key, None)
    if len(_cache) > CACHE_MAX_ENTRIES:
        # Oldest first. A dict preserves insertion order, so this is the cheap
        # approximation of an LRU that a 256-entry cache deserves.
        for key in list(_cache)[: len(_cache) - CACHE_MAX_ENTRIES]:
            _cache.pop(key, None)


async def cached(namespace: str, term: str, loader: Callable[[], Awaitable[Any]], *, default: Any = None) -> Any:
    """Run ``loader`` for ``term``, at most once per ``CACHE_TTL_SECONDS``.

    Returns ``default`` on timeout or failure. An autocomplete that raises shows
    the user nothing and gives no clue why, so every failure here degrades to
    "no suggestions" instead -- the command still accepts free text.
    """
    key = (namespace, term)
    now = time.monotonic()
    _prune(now)

    hit = _cache.get(key)
    if hit is not None:
        return hit[1]

    try:
        value = await asyncio.wait_for(loader(), timeout=LOOKUP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.info("Autocomplete lookup for %s:%r exceeded %ss", namespace, term, LOOKUP_TIMEOUT_SECONDS)
        return default
    except Exception:
        log.warning("Autocomplete lookup for %s:%r failed", namespace, term, exc_info=True)
        return default

    _cache[key] = (now, value)
    # Pruned *after* the insert, not before: pruning first lets the cache reach
    # MAX+1, so the bound would not actually hold.
    _prune(now)
    return value


def clear_cache() -> None:
    """For tests, and for the data-deletion path."""
    _cache.clear()


def truncate(text: str, limit: int = 100) -> str:
    """Discord rejects a choice name over 100 characters outright."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
