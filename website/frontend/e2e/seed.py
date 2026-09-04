"""Seed the state an end-to-end run needs, and print it as JSON.

This is the same seam ``tests/conftest.py::logged_in`` uses -- write a session
straight into Redis, then set the two cookies -- and it exists for the same
reason: the alternative is driving Discord's OAuth consent screen in CI, which
needs a real application, a real account and a real browser session, and would
make the suite depend on Discord being up.

Deliberately *not* a test-only endpoint on the Flask app. A backdoor that mints
a session is a backdoor whether or not it is guarded by a config flag, and this
achieves the same thing without shipping one.

Run from the repository root:

    python website/frontend/e2e/seed.py
"""

from __future__ import annotations

import json
import os
import sys
import time

# Importable when run from anywhere: the repo root is three directories up.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

REDIS_URL = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/9"

# A throwaway SQLite file, rebuilt every run. Not the developer's `data/zephyr.db`:
# that one is whatever schema it happened to stop at, and a stale copy made every
# guild endpoint answer 500 with "no such column: guilds.tts_language" -- which
# the suite reported as a missing element, three layers away from the cause.
# `should_auto_create` (17.3) allows create_all for SQLite, so the fresh file
# gets the current schema without running Alembic.
DATABASE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), ".e2e", "zephyr.db")
)
DATABASE_URL = os.environ.get("E2E_DATABASE_URL") or f"sqlite:///{DATABASE_PATH}"

USER = {
    "id": "900000000000000001",
    "username": "e2e-tester",
    "global_name": "E2E Tester",
    "avatar": "avatar-hash",
}
GUILDS = [
    {"id": "100000000000000001", "name": "E2E Server", "icon": None, "owner": True},
]


def main() -> int:
    # The directory only. Deleting the file is `global-setup.ts`'s job, because
    # it has to happen *before* Flask opens it -- this script runs per test, by
    # which time the server is already holding the handle.
    if DATABASE_URL.startswith("sqlite:///"):
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ.setdefault("REDIS_URL", REDIS_URL)
    os.environ.setdefault("DISCORD_CLIENT_ID", "100000000000000001")
    os.environ.setdefault("DISCORD_CLIENT_SECRET", "e2e-secret")
    os.environ.setdefault("OPENWEATHER_API_KEY", "e2e-not-a-real-key")

    from website.app import app
    from website.session import create_session
    from zephyr.services import bridge

    with app.app_context():
        session = create_session(
            USER,
            GUILDS,
            ttl=app.config["AUTH_SESSION_TTL"],
            redis_url=app.config["REDIS_URL"],
        )

    # A presence heartbeat, so `/status` reports the bot online and the home
    # page's status pill has something to render. Published directly rather
    # than by running a bot: the *bridge* is what the web tier reads, so
    # writing to it is a faithful stub -- and a spec that needed a live gateway
    # connection would not run in CI at all.
    bridge.write_presence(
        {"online": True, "guild_count": 1, "latency_ms": 42, "uptime_s": 120},
        url=REDIS_URL,
    )
    bridge.write_guild_snapshot(
        [{"id": GUILDS[0]["id"], "name": GUILDS[0]["name"], "icon": None}],
        url=REDIS_URL,
    )

    print(
        json.dumps(
            {
                "database_url": DATABASE_URL,
                "sid": session.sid,
                "csrf": session.csrf,
                "auth_cookie": app.config["AUTH_COOKIE_NAME"],
                "csrf_cookie": app.config["CSRF_COOKIE_NAME"],
                "guild_id": GUILDS[0]["id"],
                "guild_name": GUILDS[0]["name"],
                "seeded_at": int(time.time()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
