"""Durable Gemini quota counters, shared across processes.

The limiter's state was five module-level dicts. Three consequences, all real on
the free-tier key this project runs on:

* A restart handed out a **fresh daily allowance the key does not have**, so the
  next burst hit Google's 429 rather than the local limiter that exists to
  prevent exactly that. ``/token``'s own embed admitted it: "This tracker is
  local to this bot process and resets when the bot restarts."
* The bot process and the web process each kept their own copy, so a web tier
  that ever computed quota itself would diverge from the bot's view.
* ``asyncio.Lock`` protects a read-check-write inside one event loop and nothing
  at all across two processes, so the "reserve" was never atomic in the case
  that matters.

## Why fixed windows and claim-then-refund

The obvious implementation is a sorted set per window and a Lua script to make
the check-and-claim atomic. Both are deliberately avoided: ``tests/conftest.py``
hand-writes its Redis double rather than depending on ``fakeredis``, and states
that CI running no service containers is "a hard requirement rather than a
convenience". A design needing ``ZADD`` or ``EVAL`` would mean either extending
that double with a second implementation of Redis semantics, or taking the
dependency the conftest argues against.

So this uses only verbs the double already implements -- ``incr``, ``expire``,
``get``, ``mget``, ``setex``, ``delete`` -- and gets atomicity from the shape of
the protocol instead of from a script. (``TTL`` is not on that list either,
which is why a cooldown stores its own deadline in the value rather than being
read back off the key's expiry.)

**Claim first, refund on refusal.** ``INCR`` is atomic and returns the new
value, so a caller that increments and *then* compares is never racing: two
processes incrementing concurrently get different numbers, and at most one of
them sees a number within the limit. A caller that is over the limit decrements
back. The cost is that a crash between claim and refund leaks one slot until the
window expires, which for a per-minute window is a minute of being one request
stricter than necessary -- the safe direction to be wrong in.

The windows are fixed rather than sliding: the key embeds the minute (or the
Pacific date), so expiry is Redis's job and there is nothing to prune. A fixed
window admits up to 2x the rate across a boundary, which the previous sliding
implementation did not. That is an accepted trade -- these limits exist to stay
*under* Google's, and Google's own are fixed windows too.

When ``REDIS_URL`` is unset there is no Redis to be durable in, and the caller
falls back to the in-memory implementation in ``gemini.py``.
"""

from __future__ import annotations

import time

from zephyr.core.logging import get_logger
from zephyr.services import redis_client

log = get_logger(__name__)

PREFIX = "zephyr:quota"
# A minute window plus slack, so a key cannot expire while its own window is
# still the current one.
MINUTE_TTL = 120
# Two days, so "yesterday" is still readable for a moment after midnight and a
# clock skew between processes cannot lose a count.
DAY_TTL = 172_800
COOLDOWN_MAX_TTL = 86_400


def _client(url: str | None = None):
    return redis_client.get_client(url)


def minute_key(model: str, minute: int, kind: str) -> str:
    return f"{PREFIX}:{kind}:{model}:{minute}"


def day_key(model: str, day: str) -> str:
    return f"{PREFIX}:rpd:{model}:{day}"


def cooldown_key(model: str) -> str:
    return f"{PREFIX}:cooldown:{model}"


def totals_key(model: str, field: str) -> str:
    return f"{PREFIX}:totals:{model}:{field}"


TOTAL_FIELDS = ("prompt_tokens", "output_tokens", "total_tokens", "successful_requests", "session_requests")


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def claim(model: str, *, minute: int, day: str, tokens: int, limits: dict, url: str | None = None) -> tuple[bool, str | None, int]:
    """Try to take one request's worth of quota.

    Returns ``(allowed, limit_name, retry_after_seconds)``. ``limit_name`` is
    one of ``rpm``/``tpm``/``rpd`` and names which ceiling refused, so the
    caller can build the same message the in-memory version did.

    Increments before comparing, then refunds what it took if the answer is no
    -- see the module docstring. The refunds are best-effort: a failed decrement
    leaves the window one request stricter until it expires, which is the safe
    direction.
    """
    client = _client(url)

    remaining_cooldown = cooldown_remaining(model, url=url)
    if remaining_cooldown > 0:
        return False, "cooldown", remaining_cooldown

    rpm_key = minute_key(model, minute, "rpm")
    tpm_key = minute_key(model, minute, "tpm")
    rpd_key = day_key(model, day)

    requests_now = _as_int(client.incr(rpm_key))
    if requests_now == 1:
        client.expire(rpm_key, MINUTE_TTL)
    if requests_now > limits["rpm"]:
        _refund(client, rpm_key, 1)
        return False, "rpm", _seconds_left_in_minute(minute)

    tokens_now = _as_int(client.incr(tpm_key, tokens)) if tokens else _as_int(client.get(tpm_key))
    if tokens and tokens_now == tokens:
        client.expire(tpm_key, MINUTE_TTL)
    if tokens_now > limits["tpm"]:
        _refund(client, tpm_key, tokens)
        _refund(client, rpm_key, 1)
        return False, "tpm", _seconds_left_in_minute(minute)

    daily_now = _as_int(client.incr(rpd_key))
    if daily_now == 1:
        client.expire(rpd_key, DAY_TTL)
    if daily_now > limits["rpd"]:
        _refund(client, rpd_key, 1)
        _refund(client, tpm_key, tokens)
        _refund(client, rpm_key, 1)
        # The caller computes this one: it needs the next Pacific midnight,
        # which is a calendar question rather than a Redis one.
        return False, "rpd", 0

    client.incr(totals_key(model, "session_requests"))
    return True, None, 0


def _refund(client, key: str, amount: int) -> None:
    if not amount:
        return
    try:
        client.incr(key, -amount)
    except Exception:
        # One leaked slot until the window expires. Logged rather than raised:
        # failing the *request* because a refund failed would be worse than
        # being briefly one request stricter.
        log.warning("Could not refund %d on %s", amount, key)


def _seconds_left_in_minute(minute: int) -> int:
    """Whole seconds until this fixed window rolls over."""
    return max(1, int((minute + 1) * 60 - time.time()))


def set_cooldown(model: str, seconds: int, *, url: str | None = None) -> None:
    """Park a model until now + `seconds`.

    The *value* is the deadline as a unix timestamp, and the key's TTL is only
    housekeeping. Storing the deadline rather than reading it back off the TTL
    keeps this to `setex` and `get` -- see the module docstring on which verbs
    are available -- and has the side benefit that every process computes the
    same remaining time from the same number.
    """
    if seconds <= 0:
        return
    ttl = min(int(seconds), COOLDOWN_MAX_TTL)
    deadline = int(time.time()) + ttl
    _client(url).setex(cooldown_key(model), ttl + 5, str(deadline))


def cooldown_remaining(model: str, *, url: str | None = None) -> int:
    raw = _client(url).get(cooldown_key(model))
    if not raw:
        return 0
    return max(0, _as_int(raw) - int(time.time()))


def add_totals(model: str, fields: dict[str, int], *, url: str | None = None) -> None:
    client = _client(url)
    for field, amount in fields.items():
        if amount:
            client.incr(totals_key(model, field), amount)


def snapshot(model: str, *, minute: int, day: str, url: str | None = None) -> dict:
    """Every counter ``/token`` and the dashboard show, in one round trip."""
    client = _client(url)
    keys = [
        minute_key(model, minute, "rpm"),
        minute_key(model, minute, "tpm"),
        day_key(model, day),
        *(totals_key(model, field) for field in TOTAL_FIELDS),
    ]
    values = client.mget(*keys)
    rpm, tpm, rpd, *totals = [_as_int(value) for value in values]
    return {
        "rpm": rpm,
        "tpm": tpm,
        "rpd": rpd,
        "cooldown_seconds": cooldown_remaining(model, url=url),
        "totals": dict(zip(TOTAL_FIELDS, totals)),
    }


def clear(model: str | None = None, *, minute: int | None = None, day: str | None = None, url: str | None = None) -> None:
    """Forget a model's counters. Used by the data-deletion path and by tests."""
    client = _client(url)
    if model is None:
        return
    keys = [cooldown_key(model), *(totals_key(model, field) for field in TOTAL_FIELDS)]
    if minute is not None:
        keys += [minute_key(model, minute, "rpm"), minute_key(model, minute, "tpm")]
    if day is not None:
        keys.append(day_key(model, day))
    for key in keys:
        client.delete(key)
