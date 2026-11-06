"""Session loading, CSRF enforcement and cache headers for the whole API.

The hooks here are registered on the ``api`` blueprint, so every endpoint added in
this and later phases inherits them without opting in.
"""

import hmac
import time
from functools import wraps
from urllib.parse import urlsplit

from flask import current_app, g, request

from website.api import api, error, is_snowflake
from website.session import SessionStoreError, load_session
from zephyr.services import bridge, redis_client

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


BOT_AUTHORIZES = "_zephyr_bot_authorizes"


def bot_authorizes(view):
    """Mark a guild-scoped mutation that the bot authorizes itself.

    Such a view sends its change over the bridge, and the bot checks the
    actor's live permissions before acting, so a second round trip from
    ``guild_scoped`` would only add latency.  Apply it *below* ``guild_scoped``.
    """
    setattr(view, BOT_AUTHORIZES, True)
    return view


def confirm_still_admin(guild_id, actor_id):
    """Ask the bot whether the actor still has Manage Server.

    Returns ``None`` when they do, otherwise the error response to send.  This
    fails closed: the session's guild list was captured at sign-in and lives up
    to AUTH_SESSION_MAX_AGE, so without the bot there is no current answer, and
    someone who lost the permission would keep it for weeks.
    """
    redis_url = current_app.config["REDIS_URL"]
    if not redis_url:
        return error("bridge_not_configured", "This deployment has no Redis, so the bot cannot be reached.", 503)
    try:
        bridge.send_command("auth.manage_guild", guild_id=guild_id, actor_id=actor_id, url=redis_url)
    except bridge.BridgeTimeout:
        return error("bot_unreachable", "Zephyr must be online to change server settings.", 503)
    except bridge.BridgeError as exc:
        return error("forbidden", str(exc), 403)
    except Exception as exc:
        print(f"[Guard] Could not confirm Manage Server for {actor_id} in {guild_id}: {exc}")
        return error("bridge_unavailable", "Could not reach Zephyr to confirm your permissions.", 503)
    return None


def guild_scoped(view):
    """Require a session and a guild the caller administers.

    Wraps ``require_session``, so a single decorator covers the whole preamble
    every per-guild endpoint was otherwise going to repeat: validate the id,
    load the session, check membership, hand the view the guild.

    Reads trust the session's guild list.  Mutations also ask the bot, via
    ``confirm_still_admin``: the web tier writes settings, weather subscriptions
    and AI data on its own authority, and the list is only as fresh as the
    sign-in.  Views marked ``bot_authorizes`` skip that, because the bot checks
    the actor itself before acting on them.
    """
    live_check = not getattr(view, BOT_AUTHORIZES, False)

    @wraps(view)
    @require_session
    def wrapper(guild_id, *args, **kwargs):
        if not is_snowflake(guild_id):
            return error("invalid_guild_id", "That is not a Discord guild id.", 400)
        session = current_session()
        guild = next((entry for entry in session.guilds if str(entry["id"]) == str(guild_id)), None)
        if guild is None:
            return error("forbidden", "You do not manage that server.", 403)
        if live_check and request.method not in SAFE_METHODS:
            refusal = confirm_still_admin(guild_id, session.user_id)
            if refusal is not None:
                return refusal
        g.zephyr_guild = guild
        return view(guild_id, *args, **kwargs)

    return wrapper


def rate_limit(bucket: str, *, limit: int, window: int) -> bool:
    """Fixed-window counter, per session, in Redis.  True when within budget.

    Fixed window rather than a sliding log because it is two commands and no
    stored history; the worst case is 2x the limit across a window boundary,
    which for "don't hammer the bot with skips" is entirely acceptable.

    Shared across gunicorn workers by construction -- a per-process counter
    would give each worker its own full budget and enforce nothing.  Fails open:
    a Redis blip must not lock the player, and the session store already answers
    503 for a genuine outage.
    """
    session = g.get("zephyr_session")
    if session is None:
        return True
    key = f"zephyr:web:rl:{bucket}:{session.sid}:{int(time.time()) // window}"
    try:
        client = redis_client.get_client(current_app.config["REDIS_URL"])
        used = client.incr(key)
        if used == 1:
            client.expire(key, window + 1)
    except Exception as exc:
        print(f"[RateLimit] Could not count {bucket}: {exc}")
        return True
    return used <= limit


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
    session = g.get("zephyr_session")
    if session is not None:
        _slide_cookies(response, session)
        response.headers["Cache-Control"] = "no-store, private"
        vary = response.headers.get("Vary")
        if not vary:
            response.headers["Vary"] = "Cookie"
        elif "cookie" not in vary.lower():
            response.headers["Vary"] = f"{vary}, Cookie"
    return response


def _slide_cookies(response, session) -> None:
    """Renew the cookies' expiry alongside the session's sliding Redis TTL.

    load_session extends the server-side TTL on every request, but the cookies
    were only ever set at login with max_age=AUTH_SESSION_TTL -- so the browser
    dropped them exactly that long after sign-in however active the user was,
    and AUTH_SESSION_MAX_AGE could never be reached.  Skipped when the view has
    already set or cleared a cookie itself (login, logout).
    """
    names = (current_app.config["AUTH_COOKIE_NAME"], current_app.config["CSRF_COOKIE_NAME"])
    if any(header.split("=", 1)[0].strip() in names for header in response.headers.getlist("Set-Cookie")):
        return
    set_session_cookie(response, session.sid)
    set_csrf_cookie(response, session.csrf)


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
