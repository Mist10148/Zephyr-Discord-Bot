"""Shared fixtures for the web tier's tests.

No network, no Redis and no Postgres: Redis is a hand-rolled in-memory double and
the database is a temporary SQLite file.  CI runs no service containers, so this
is a hard requirement rather than a convenience.

``fakeredis`` is deliberately not a dependency -- the surface used here is a dozen
methods, which is not worth a package with its own version drift.
"""

import time
from collections import deque

import pytest


class FakePubSub:
    """An in-memory subscription with redis-py's ``get_message`` semantics.

    ``get_message(timeout=t)`` blocks for up to ``t`` when the queue is empty,
    which matters: without it, ``bridge.send_command``'s wait loop would busy-spin
    for its whole timeout instead of parking.  Sleeping the requested interval
    also lets a timeout test assert on real elapsed behaviour with a small
    timeout rather than on a mock.
    """

    def __init__(self, client, ignore_subscribe_messages=False):
        self._client = client
        self._ignore_subscribe = ignore_subscribe_messages
        self.channels: set[str] = set()
        self.queue: deque[dict] = deque()
        self.closed = False

    def subscribe(self, *channels):
        for channel in channels:
            self.channels.add(channel)
            self._client.subscribers.append(self)
            if not self._ignore_subscribe:
                self.queue.append({"type": "subscribe", "channel": channel, "data": 1})

    def unsubscribe(self, *channels):
        for channel in channels or tuple(self.channels):
            self.channels.discard(channel)

    def get_message(self, timeout=None):
        if not self.queue and timeout:
            time.sleep(timeout)
        return self.queue.popleft() if self.queue else None

    def deliver(self, channel, data):
        self.queue.append({"type": "message", "channel": channel, "data": data})

    def close(self):
        self.closed = True
        self.channels.clear()
        if self in self._client.subscribers:
            self._client.subscribers.remove(self)


class FakePipeline:
    """Queues commands and replays them on execute(), like redis-py's pipeline."""

    def __init__(self, client):
        self._client = client
        self._queued = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self._queued.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        results = [getattr(self._client, name)(*args, **kwargs) for name, args, kwargs in self._queued]
        self._queued.clear()
        return results


class FakeRedis:
    """An in-memory stand-in with *real* TTL behaviour.

    Expiry is tracked against ``time.monotonic()`` rather than stubbed, so tests
    can assert on TTLs and on sliding renewal without sleeping.  Set ``raise_on``
    to an exception instance to simulate an outage.
    """

    def __init__(self):
        self.store: dict[str, tuple[str, float | None]] = {}
        self.raise_on: Exception | None = None
        self.subscribers: list[FakePubSub] = []
        # Called synchronously with (channel, data) on every publish. Tests use
        # it to stand in for the bot: replying from inside publish() is also what
        # proves send_command subscribed *before* it published -- a reply this
        # early is dropped entirely if the subscription came second.
        self.on_publish = None

    # -- test affordances ---------------------------------------------------
    def ttl_of(self, key: str) -> float | None:
        """Remaining lifetime in seconds, or None when the key never expires."""
        entry = self.store.get(key)
        if entry is None or entry[1] is None:
            return None
        return entry[1] - time.monotonic()

    def expire_now(self, key: str) -> None:
        """Force a key to be treated as expired without waiting for the clock."""
        if key in self.store:
            self.store[key] = (self.store[key][0], time.monotonic() - 1)

    # -- redis surface ------------------------------------------------------
    def _guard(self):
        if self.raise_on is not None:
            raise self.raise_on

    def _read(self, key):
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= time.monotonic():
            del self.store[key]
            return None
        return value

    def ping(self):
        self._guard()
        return True

    def get(self, key):
        self._guard()
        return self._read(key)

    def mget(self, *keys):
        self._guard()
        flat = keys[0] if len(keys) == 1 and isinstance(keys[0], (list, tuple)) else keys
        return [self._read(key) for key in flat]

    def set(self, key, value, ex=None):
        self._guard()
        self.store[key] = (value, None if ex is None else time.monotonic() + ex)
        return True

    def setex(self, key, ttl, value):
        return self.set(key, value, ex=ttl)

    def getex(self, key, ex=None):
        self._guard()
        value = self._read(key)
        if value is not None and ex is not None:
            self.store[key] = (value, time.monotonic() + ex)
        return value

    def getdel(self, key):
        self._guard()
        value = self._read(key)
        self.store.pop(key, None)
        return value

    def delete(self, *keys):
        self._guard()
        return sum(1 for key in keys if self.store.pop(key, None) is not None)

    def exists(self, *keys):
        self._guard()
        return sum(1 for key in keys if self._read(key) is not None)

    def pipeline(self, *_args, **_kwargs):
        return FakePipeline(self)

    def pubsub(self, ignore_subscribe_messages=False, **_kwargs):
        return FakePubSub(self, ignore_subscribe_messages=ignore_subscribe_messages)

    def publish(self, channel, data):
        self._guard()
        delivered = 0
        for subscriber in list(self.subscribers):
            if channel in subscriber.channels:
                subscriber.deliver(channel, data)
                delivered += 1
        if self.on_publish is not None:
            self.on_publish(channel, data)
        return delivered

    def close(self):
        return None


@pytest.fixture(autouse=True)
def inert_env(monkeypatch):
    """Make the ambient environment irrelevant to the tests.

    zephyr/config.py calls load_dotenv() and reads every variable at *import*
    time, and this repository has a real .env, so a developer's credentials would
    otherwise leak into assertions.  The real defence is architectural -- request
    handlers read current_app.config, never zephyr.config, and create_app()
    applies its overrides last -- and this fixture is the belt to that braces.
    """
    for name in (
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_REDIRECT_URI",
        "DISCORD_TOKEN",
        "REDIS_URL",
        "REDISCLOUD_URL",
        "DATABASE_URL",
        "WEB_PUBLIC_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("zephyr.config.REDIS_URL", None, raising=False)
    monkeypatch.setattr("zephyr.config.DATABASE_URL", None, raising=False)
    monkeypatch.setattr("zephyr.config.AUTH_ENABLED", False, raising=False)


@pytest.fixture
def fake_redis(monkeypatch):
    """Route every get_client() call at one shared FakeRedis.

    A single patch is enough because callers reach it as
    ``redis_client.get_client(...)`` rather than importing the function itself.
    """
    client = FakeRedis()
    monkeypatch.setattr("zephyr.services.redis_client.get_client", lambda url=None: client)
    return client


@pytest.fixture
def db_url(tmp_path):
    """A file-backed SQLite URL.

    Not sqlite:///:memory: -- build_engine passes check_same_thread=False with no
    StaticPool, so each connection would get a fresh empty database and the schema
    would vanish between statements.
    """
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


CLIENT_ID = "111111111111111111"
REDIRECT_URI = "http://localhost/api/v1/auth/callback"


@pytest.fixture
def app(db_url, fake_redis):
    """A fully configured app with auth switched on.

    Imported from ``website`` rather than ``wsgi`` (which validates config at
    import) or ``website.app`` (which builds an app at import).
    """
    from website import create_app

    return create_app(
        {
            "TESTING": True,
            "AUTH_ENABLED": True,
            "DISCORD_CLIENT_ID": CLIENT_ID,
            "DISCORD_CLIENT_SECRET": "client-secret",
            "DISCORD_REDIRECT_URI": REDIRECT_URI,
            "WEB_PUBLIC_URL": "http://localhost",
            "REDIS_URL": "redis://localhost:6379/0",
            "DATABASE_URL": db_url,
            "AUTH_COOKIE_SECURE": False,
            "TRUST_PROXY": False,
        }
    )


@pytest.fixture
def public_app(db_url, fake_redis):
    """An app with no OAuth application configured -- the weather-only deploy."""
    from website import create_app

    return create_app({"TESTING": True, "AUTH_ENABLED": False, "DATABASE_URL": db_url})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(app, client, fake_redis):
    """Write a session straight into Redis and attach its cookies to the client.

    Returns the Session so tests can assert against its csrf token and guilds.
    """
    from website.session import create_session

    with app.app_context():
        session = create_session(
            {"id": "900000000000000001", "username": "tester", "global_name": "Tester", "avatar": "av"},
            [{"id": "1", "name": "Managed Guild", "icon": "icon1", "owner": True}],
            ttl=app.config["AUTH_SESSION_TTL"],
            redis_url=app.config["REDIS_URL"],
        )
    client.set_cookie(app.config["AUTH_COOKIE_NAME"], session.sid, domain="localhost")
    client.set_cookie(app.config["CSRF_COOKIE_NAME"], session.csrf, domain="localhost")
    return session
