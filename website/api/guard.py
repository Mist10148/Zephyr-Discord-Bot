"""Session loading, CSRF enforcement and cache headers for the whole API.

The hooks here are registered on the ``api`` blueprint, so every endpoint added in
this and later phases inherits them without opting in.
"""

import hmac
from functools import wraps
from urllib.parse import urlsplit

from flask import current_app, g, request

from website.api import api, error
from website.session import SessionStoreError, load_session

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
