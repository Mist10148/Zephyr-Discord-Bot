"""Server-side session store backed by Redis.

Hand-rolled rather than Flask-Session, and with no signed cookie and no
SECRET_KEY:

* Flask-Session would add a production dependency, couple us to flask.session's
  dict-proxy semantics and its SECRET_KEY-signed id, and still need the same
  timeout and decode tuning. It buys about sixty lines.
* A signed cookie carrying a Redis pointer is the same design with extra steps.
  A 32-byte token_urlsafe id already has ~256 bits of entropy, so an HMAC adds no
  forgery resistance -- only a cheap pre-Redis reject for junk cookies, which is
  not worth a required secret.

Leaving app.secret_key unset is therefore a feature: any future accidental use of
flask.session raises loudly instead of quietly signing with a weak key.

Failure behaviour is the deliberate opposite of RedisStorage's
``except Exception: print(...)``. That soft-fail is right for settings and
catastrophic for sessions -- a Redis blip would look like a silent logout, and a
failed write would look like a successful login. Everything here raises
SessionStoreError instead, and callers map it to a 503 or to an error redirect.
"""

import json
import secrets
import time
from dataclasses import dataclass, field

# Imported as a module, not `from ... import get_client`, so that patching
# redis_client.get_client redirects every call site at once.
from zephyr.services import redis_client

SESSION_PREFIX = "zephyr:web:session:"
STATE_PREFIX = "zephyr:web:oauth_state:"


class SessionStoreError(RuntimeError):
    """Redis was unreachable or refused the operation."""


@dataclass
class Session:
    """A signed-in user, as stored in Redis.

    ``guilds`` lives here rather than in website/api/cache.py's TTLCache for three
    reasons: that cache is per-worker and gunicorn runs two, a shared LRU is the
    wrong shape for per-user data, and -- decisively -- since no Discord token is
    stored the list cannot be re-fetched on demand. Only manageable guilds are
    kept, trimmed to four fields, so a session is 1-4 KB.
    """

    sid: str
    user_id: str
    username: str
    global_name: str | None
    avatar_hash: str | None
    csrf: str
    created_at: int
    guilds: list[dict] = field(default_factory=list)
    guilds_fetched_at: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_id": self.user_id,
                "username": self.username,
                "global_name": self.global_name,
                "avatar_hash": self.avatar_hash,
                "csrf": self.csrf,
                "created_at": self.created_at,
                "guilds": self.guilds,
                "guilds_fetched_at": self.guilds_fetched_at,
            }
        )

    @classmethod
    def from_json(cls, sid: str, raw: str) -> "Session":
        data = json.loads(raw)
        return cls(
            sid=sid,
            user_id=data["user_id"],
            username=data["username"],
            global_name=data.get("global_name"),
            avatar_hash=data.get("avatar_hash"),
            csrf=data["csrf"],
            created_at=int(data["created_at"]),
            guilds=data.get("guilds") or [],
            guilds_fetched_at=int(data.get("guilds_fetched_at") or 0),
        )

    def manageable_ids(self) -> set[str]:
        return {str(guild["id"]) for guild in self.guilds}


def _client(redis_url: str | None):
    try:
        return redis_client.get_client(redis_url)
    except Exception as exc:  # RuntimeError from get_client, or a redis error
        raise SessionStoreError(str(exc)) from exc


def create_session(
    user: dict,
    guilds: list[dict],
    *,
    ttl: int,
    redis_url: str | None = None,
) -> Session:
    """Mint a brand-new session and store it.

    Always called with a fresh id, and the caller destroys any previously
    presented session first -- rotating on login is the session-fixation defence.
    """
    now = int(time.time())
    session = Session(
        sid=secrets.token_urlsafe(32),
        user_id=str(user["id"]),
        username=user.get("username") or "",
        global_name=user.get("global_name"),
        avatar_hash=user.get("avatar"),
        csrf=secrets.token_urlsafe(32),
        created_at=now,
        guilds=guilds,
        guilds_fetched_at=now,
    )
    client = _client(redis_url)
    try:
        client.set(SESSION_PREFIX + session.sid, session.to_json(), ex=ttl)
    except Exception as exc:
        raise SessionStoreError(f"Could not store the session: {exc}") from exc
    return session


def load_session(
    sid: str | None,
    *,
    ttl: int,
    max_age: int,
    redis_url: str | None = None,
) -> Session | None:
    """Return the session for ``sid``, renewing its TTL, or None.

    Renewal uses a single GETEX so there is no read-then-EXPIRE race. A session
    older than ``max_age`` is deleted regardless of activity, so an idle-extended
    session still has a hard ceiling.

    Returning None means unauthenticated. Corrupt JSON is unrecoverable for that
    one key, so it is deleted and reported as no session -- the one case where not
    raising is correct, because there is no outage to report.
    """
    if not sid:
        return None
    key = SESSION_PREFIX + sid
    client = _client(redis_url)
    try:
        raw = client.getex(key, ex=ttl)
    except Exception as exc:
        raise SessionStoreError(f"Could not read the session: {exc}") from exc
    if not raw:
        return None
    try:
        session = Session.from_json(sid, raw)
    except (ValueError, KeyError, TypeError):
        destroy(sid, redis_url=redis_url)
        return None
    if int(time.time()) - session.created_at > max_age:
        destroy(sid, redis_url=redis_url)
        return None
    return session


def save_session(session: Session, *, ttl: int, redis_url: str | None = None) -> None:
    """Persist mutations to an existing session, preserving its id."""
    client = _client(redis_url)
    try:
        client.set(SESSION_PREFIX + session.sid, session.to_json(), ex=ttl)
    except Exception as exc:
        raise SessionStoreError(f"Could not update the session: {exc}") from exc


def destroy(sid: str | None, *, redis_url: str | None = None) -> None:
    """Delete a session. Missing ids are not an error."""
    if not sid:
        return
    client = _client(redis_url)
    try:
        client.delete(SESSION_PREFIX + sid)
    except Exception as exc:
        raise SessionStoreError(f"Could not delete the session: {exc}") from exc


# ---------------------------------------------------------------------------
# OAuth state
# ---------------------------------------------------------------------------


def store_state(state: str, payload: dict, *, ttl: int, redis_url: str | None = None) -> None:
    client = _client(redis_url)
    try:
        client.set(STATE_PREFIX + state, json.dumps(payload), ex=ttl)
    except Exception as exc:
        raise SessionStoreError(f"Could not store the OAuth state: {exc}") from exc


def consume_state(state: str, *, redis_url: str | None = None) -> dict | None:
    """Fetch and delete an OAuth state in one round trip.

    GETDEL makes it single-use, so replaying a callback URL fails.
    """
    client = _client(redis_url)
    try:
        raw = client.getdel(STATE_PREFIX + state)
    except Exception as exc:
        raise SessionStoreError(f"Could not read the OAuth state: {exc}") from exc
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
