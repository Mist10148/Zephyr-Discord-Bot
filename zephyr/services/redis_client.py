"""Shared lazy Redis client.

Deliberately different from ``RedisStorage`` in two ways:

* Nothing connects at import time.  ``RedisStorage.__init__`` pings in its
  constructor and the module builds a singleton at import, which is fine for the
  bot but unacceptable in a web process where building the app must not touch the
  network.
* ``decode_responses=True``.  Everything this client stores is JSON text, so
  decoding once here removes a ``.decode("utf-8")`` from every call site.

``RedisStorage`` is intentionally left alone: different process, different
encoding contract, and touching the bot's settings path from an auth change is
unjustified risk.
"""

import threading
from typing import Any

from zephyr.config import REDIS_URL

_lock = threading.Lock()
_clients: dict[str, Any] = {}


def get_client(url: str | None = None):
    """Return the cached Redis client for ``url``.

    Raises ``RuntimeError`` when no URL is configured.  Callers must let Redis
    errors propagate: a session store that swallows them would report a Redis
    outage as a silent logout, or a failed write as a successful login.
    """
    target = url or REDIS_URL
    if not target:
        raise RuntimeError("REDIS_URL is not configured")
    with _lock:
        client = _clients.get(target)
        if client is not None:
            return client
        import redis  # imported lazily so the dependency stays optional

        client = redis.from_url(
            target,
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
            health_check_interval=30,
            # Render's free Redis plan caps connections, and gunicorn runs
            # 2 workers x 4 threads.
            max_connections=10,
        )
        _clients[target] = client
        return client


def close_clients() -> None:
    """Close every cached client.  Used by tests and graceful shutdown."""
    with _lock:
        for client in _clients.values():
            try:
                client.close()
            except Exception:
                pass
        _clients.clear()
