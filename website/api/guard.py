"""Session loading, CSRF enforcement and cache headers for the whole API.

The hooks here are registered on the ``api`` blueprint, so every endpoint added in
this and later phases inherits them without opting in.
"""

import hashlib
import hmac
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import current_app, g, request

from website.api import api, error
from website.session import SessionStoreError, load_session
from zephyr.services import redis_client
from zephyr.core.logging import get_logger


log = get_logger(__name__)
CSRF_HEADER = "X-Zephyr-CSRF"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def session_cookie() -> str | None:
    return request.cookies.get(current_app.config["AUTH_COOKIE_NAME"])


def current_session():
    """Return the request's session, loading it at most once per request."""
    if "zephyr_session" in g:
        return g.zephyr_session
    g.zephyr_session = load_session(
        session_cookie(),
        ttl=current_app.config["AUTH_SESSION_TTL"],
        max_age=current_app.config["AUTH_SESSION_MAX_AGE"],
        redis_url=current_app.config["REDIS_URL"],
    )
    return g.zephyr_session


def require_session(view):
    """Reject unauthenticated callers with a 401 and the standard envelope.

    A 401 and never a redirect: the SPA calls these endpoints with fetch, which
    follows redirects, and a cross-origin hop to discord.com would surface as an
    opaque status 0 rather than something the client can act on.
    """

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_app.config["AUTH_ENABLED"]:
            return error("auth_not_configured", "The dashboard is not configured on this server.", 503)
        try:
            session = current_session()
        except SessionStoreError as exc:
            return error("session_store_unavailable", str(exc), 503)
        if session is None:
            return error("unauthenticated", "Sign in to continue.", 401)
        return view(*args, **kwargs)

    return wrapper


def guild_scoped(view):
    """Require a session and a guild the caller administers.

    Wraps ``require_session``, so a single decorator covers the whole preamble
    every per-guild endpoint was otherwise going to repeat: validate the id,
    load the session, check membership, hand the view the guild.

    The membership check is UX, not authorization -- the plan is explicit that
    the bot re-validates the actor against its live Discord cache before doing
    anything.  A stale session here can only ever over-restrict.
    """

    @wraps(view)
    @require_session
    def wrapper(guild_id, *args, **kwargs):
        if not str(guild_id).isdigit():
            return error("invalid_guild_id", "That is not a Discord guild id.", 400)
        session = current_session()
        guild = next((entry for entry in session.guilds if str(entry["id"]) == str(guild_id)), None)
        if guild is None:
            return error("forbidden", "You do not manage that server.", 403)
        g.zephyr_guild = guild
        return view(guild_id, *args, **kwargs)

    return wrapper


def _fixed_window(key: str, *, limit: int, window: int) -> bool:
    """The Redis half, shared by the session and the per-IP limiter.

    Fixed window rather than a sliding log because it is two commands and no
    stored history; the worst case is 2x the limit across a window boundary.

    Shared across gunicorn workers by construction -- a per-process counter
    would give each worker its own full budget and enforce nothing.  Fails open:
    a Redis blip must not take the weather page down, and the session store
    already answers 503 for a genuine outage.
    """
    try:
        client = redis_client.get_client(current_app.config["REDIS_URL"])
        used = client.incr(key)
        if used == 1:
            client.expire(key, window + 1)
    except Exception as exc:
        log.warning("Could not count rate-limit bucket %s, failing open: %s", key, exc)
        return True
    return used <= limit


def rate_limit(bucket: str, *, limit: int, window: int) -> bool:
    """Per-session budget.  True when within it.

    Returns True for an anonymous caller, which is why it **cannot** guard a
    public endpoint -- there is no session to key on, so it would fail open for
    exactly the traffic a public limiter exists to bound. Use
    ``public_rate_limit`` there.
    """
    session = g.get("zephyr_session")
    if session is None:
        return True
    return _fixed_window(
        f"zephyr:web:rl:{bucket}:{session.sid}:{int(time.time()) // window}",
        limit=limit, window=window,
    )


def client_ip() -> str:
    """The caller's address, as far as it can be trusted.

    ``request.remote_addr`` and never ``X-Forwarded-For`` directly: ProxyFix is
    installed only when TRUST_PROXY is on (see website/__init__.py), so behind
    Render this is already the real client, and *without* a proxy a client could
    forge the header and mint itself an unlimited budget per request.
    """
    return request.remote_addr or "unknown"


def rate_limit_ip(bucket: str, *, limit: int, window: int) -> bool:
    """Per-IP budget, for endpoints with no session.

    The address is hashed and truncated, so Redis never holds a raw IP -- which
    is a claim the privacy policy can then make truthfully. A 16-hex-character
    prefix of SHA-256 is not a reversal risk for a value that expires within the
    window.
    """
    fingerprint = hashlib.sha256(client_ip().encode("utf-8")).hexdigest()[:16]
    return _fixed_window(
        f"zephyr:web:rl:{bucket}:ip:{fingerprint}:{int(time.time()) // window}",
        limit=limit, window=window,
    )


def public_rate_limit(bucket: str, *, limit: int, window: int):
    """Decorator form, for the public read endpoints.

    A 429 carries Retry-After. The frontend needs no change for it:
    ``lib/api.ts`` already throws ApiError(429) and ``lib/query.ts``'s retry
    predicate treats sub-500 as non-retryable, so it surfaces immediately as an
    error toast with the envelope's own message.
    """
    def decorate(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not rate_limit_ip(bucket, limit=limit, window=window):
                response, status = error(
                    "rate_limited", "Too many requests — try again shortly.", 429
                )
                response.headers["Retry-After"] = str(window)
                return response, status
            return view(*args, **kwargs)
        return wrapper
    return decorate


@api.before_request
def enforce_csrf():
    """Synchronizer-token CSRF for every mutating call on the API blueprint.

    The token is minted with the session and lives in Redis; the client learns it
    from the readable csrf cookie (and from GET /me) and echoes it in a header.
    The comparison is always against the *session's* stored value, never against
    the cookie, so the cookie needs no signing and no SECRET_KEY is required.

    SameSite=Lax already blocks cross-site form posts, so this is belt and braces
    -- but it is what the plan requires, and it is cheap.
    """
    if request.method in SAFE_METHODS:
        return None
    if not session_cookie():
        # No session to protect. Endpoints that need one answer 401 themselves.
        return None
    try:
        session = current_session()
    except SessionStoreError as exc:
        return error("session_store_unavailable", str(exc), 503)
    if session is None:
        return None
    presented = request.headers.get(CSRF_HEADER, "")
    if not presented or not hmac.compare_digest(presented, session.csrf):
        return error("csrf_failed", "Missing or invalid CSRF token.", 403)
    origin = request.headers.get("Origin")
    if origin and not _origin_allowed(origin):
        return error("csrf_failed", "Request origin is not allowed.", 403)
    return None


def _origin_allowed(origin: str) -> bool:
    """Compare scheme+host, ignoring path and default-port differences."""
    expected = urlsplit(current_app.config["WEB_PUBLIC_URL"] or "")
    actual = urlsplit(origin)
    if not actual.scheme or not actual.netloc:
        return False
    if expected.netloc and (actual.scheme, actual.netloc) == (expected.scheme, expected.netloc):
        return True
    # Behind a proxy or on a custom domain the configured origin can legitimately
    # differ from the one the browser used, so fall back to the request's own host.
    return actual.netloc == request.host


@api.after_request
def no_store_for_authenticated(response):
    """Keep per-user responses out of shared caches.

    Without this a CDN or corporate proxy could cache one user's /me -- including
    their CSRF token -- and hand it to somebody else.
    """
    if g.get("zephyr_session") is not None:
        response.headers["Cache-Control"] = "no-store, private"
        vary = response.headers.get("Vary")
        if not vary:
            response.headers["Vary"] = "Cookie"
        elif "cookie" not in vary.lower():
            response.headers["Vary"] = f"{vary}, Cookie"
    return response


def set_session_cookie(response, sid: str) -> None:
    response.set_cookie(
        current_app.config["AUTH_COOKIE_NAME"],
        sid,
        max_age=current_app.config["AUTH_SESSION_TTL"],
        httponly=True,
        secure=current_app.config["AUTH_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )


def set_csrf_cookie(response, token: str) -> None:
    """Readable on purpose: it is only a transport for the session's token."""
    response.set_cookie(
        current_app.config["CSRF_COOKIE_NAME"],
        token,
        max_age=current_app.config["AUTH_SESSION_TTL"],
        httponly=False,
        secure=current_app.config["AUTH_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )


def clear_auth_cookies(response) -> None:
    """Expire both cookies using the same attributes they were set with.

    Mismatched path/samesite/secure attributes leave the cookie alive in some
    browsers, which would look like a failed sign-out.
    """
    for name in (current_app.config["AUTH_COOKIE_NAME"], current_app.config["CSRF_COOKIE_NAME"]):
        response.set_cookie(
            name,
            "",
            max_age=0,
            expires=0,
            httponly=name == current_app.config["AUTH_COOKIE_NAME"],
            secure=current_app.config["AUTH_COOKIE_SECURE"],
            samesite="Lax",
            path="/",
        )
