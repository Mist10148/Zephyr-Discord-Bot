"""Discord OAuth2 authorization-code flow.

/auth/login and /auth/callback are browser *navigations*, not fetches, so every
failure is a 302 back into the SPA with an ?error= code rather than a JSON body.
website/spa.py already serves index.html for /login, so nothing extra is needed
for the redirect target to resolve.

Success carries no query parameter. The SPA boots, calls GET /me and routes on the
result -- a ?login=1 would linger in history and in shared URLs for no benefit.
"""

import hmac
import secrets

from flask import current_app, redirect, request

from website import discord_api
from website.api import api, error
from website.api.guard import clear_auth_cookies, current_session, rate_limit_ip, set_csrf_cookie, set_session_cookie
from website.repo import upsert_web_user
from website.session import SessionStoreError, consume_state, create_session, destroy, store_state
from zephyr.core.logging import get_logger


log = get_logger(__name__)
MAX_NEXT_LENGTH = 256


def _login_redirect(code: str | None = None):
    target = current_app.config["SPA_LOGIN_PATH"]
    response = redirect(f"{target}?error={code}" if code else target)
    response.headers["Cache-Control"] = "no-store"
    return response


def safe_next(value: str | None) -> str | None:
    """Accept only same-site absolute paths.

    Rejects protocol-relative //evil.com, backslash tricks that some browsers
    normalise to a slash, and anything absurdly long.
    """
    if not value or len(value) > MAX_NEXT_LENGTH:
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    if "\\" in value:
        return None
    return value


def _state_cookie_name() -> str:
    return current_app.config["OAUTH_STATE_COOKIE_NAME"]


@api.get("/auth/login")
def auth_login():
    if not current_app.config["AUTH_ENABLED"]:
        return _login_redirect("not_configured")

    # The tightest limit on the public surface, and the reason is below: every
    # call mints a Redis state key, so this is the cheapest way to fill the
    # session store. Answered with a redirect rather than the JSON envelope,
    # because this endpoint is reached by a browser *navigation* -- returning
    # JSON here would put a raw error object on screen.
    if not rate_limit_ip("auth_login", limit=10, window=300):
        return _login_redirect("rate_limited")

    state = secrets.token_urlsafe(32)
    try:
        store_state(
            state,
            {"next": safe_next(request.args.get("next"))},
            ttl=current_app.config["OAUTH_STATE_TTL"],
            redis_url=current_app.config["REDIS_URL"],
        )
    except SessionStoreError:
        return _login_redirect("session_unavailable")

    response = redirect(
        discord_api.authorize_url(
            client_id=current_app.config["DISCORD_CLIENT_ID"],
            redirect_uri=current_app.config["DISCORD_REDIRECT_URI"],
            scopes=current_app.config["DISCORD_OAUTH_SCOPES"],
            state=state,
        )
    )
    # The state also goes in a cookie. Redis alone does not bind the state to this
    # browser, which leaves login-CSRF open: an attacker starts a flow and feeds
    # the victim their own state+code, signing the victim into the attacker's
    # Discord account. Requiring both closes it.
    response.set_cookie(
        _state_cookie_name(),
        state,
        max_age=current_app.config["OAUTH_STATE_TTL"],
        httponly=True,
        secure=current_app.config["AUTH_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _clear_state_cookie(response):
    response.set_cookie(
        _state_cookie_name(),
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=current_app.config["AUTH_COOKIE_SECURE"],
        samesite="Lax",
        path="/",
    )
    return response


@api.get("/auth/callback")
def auth_callback():
    if not current_app.config["AUTH_ENABLED"]:
        return _login_redirect("not_configured")

    def fail(code):
        return _clear_state_cookie(_login_redirect(code))

    upstream_error = request.args.get("error")
    if upstream_error:
        # Only echo the one value that means something to the UI.
        return fail("access_denied" if upstream_error == "access_denied" else "oauth_error")

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return fail("invalid_request")

    cookie_state = request.cookies.get(_state_cookie_name(), "")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        return fail("state_mismatch")

    try:
        stored = consume_state(state, redis_url=current_app.config["REDIS_URL"])
    except SessionStoreError:
        return fail("session_unavailable")
    if stored is None:
        return fail("state_expired")

    try:
        token = discord_api.exchange_code(
            code,
            client_id=current_app.config["DISCORD_CLIENT_ID"],
            client_secret=current_app.config["DISCORD_CLIENT_SECRET"],
            redirect_uri=current_app.config["DISCORD_REDIRECT_URI"],
        )
    except discord_api.DiscordRateLimited:
        return fail("discord_rate_limited")
    except discord_api.DiscordTimeoutError:
        return fail("discord_unavailable")
    except discord_api.DiscordError:
        return fail("token_exchange_failed")

    access_token = token.get("access_token")
    granted = set((token.get("scope") or "").split())
    if not access_token:
        return fail("token_exchange_failed")
    # A user can untick scopes on the consent screen.
    if not set(current_app.config["DISCORD_OAUTH_SCOPES"].split()) <= granted:
        return fail("insufficient_scope")

    try:
        user = discord_api.get_current_user(access_token)
        guilds = discord_api.get_current_user_guilds(access_token)
    except discord_api.DiscordRateLimited:
        return fail("discord_rate_limited")
    except discord_api.DiscordTimeoutError:
        return fail("discord_unavailable")
    except discord_api.DiscordError:
        return fail("discord_unavailable")

    manageable = [
        {
            "id": str(guild["id"]),
            "name": guild.get("name") or "",
            "icon": guild.get("icon"),
            "owner": bool(guild.get("owner")),
        }
        for guild in guilds
        if discord_api.can_manage(guild)
    ]

    # A failed audit row must not fail the login: web_users records who signed in,
    # while the session is the authorization source. This keeps the dashboard
    # usable through a brief database outage.
    try:
        upsert_web_user(user, database_url=current_app.config["DATABASE_URL"])
    except Exception as exc:
        log.exception("Could not record the web_users row")

    try:
        # Rotate: destroy whatever session the browser presented before minting a
        # new id. This is the session-fixation defence.
        previous = request.cookies.get(current_app.config["AUTH_COOKIE_NAME"])
        if previous:
            destroy(previous, redis_url=current_app.config["REDIS_URL"])
        session = create_session(
            user,
            manageable,
            ttl=current_app.config["AUTH_SESSION_TTL"],
            redis_url=current_app.config["REDIS_URL"],
        )
    except SessionStoreError:
        return fail("session_unavailable")

    destination = stored.get("next") or current_app.config["SPA_DEFAULT_PATH"]
    response = redirect(safe_next(destination) or current_app.config["SPA_DEFAULT_PATH"])
    response.headers["Cache-Control"] = "no-store"
    set_session_cookie(response, session.sid)
    set_csrf_cookie(response, session.csrf)
    return _clear_state_cookie(response)


@api.post("/auth/logout")
def auth_logout():
    """Idempotent sign-out.

    With no session this still answers 204 with cleared cookies and performs no
    CSRF check: there is no token to compare against, and returning 403 would leak
    whether a session existed. With a session, the blueprint-wide CSRF hook has
    already rejected a missing or wrong token before this runs.
    """
    try:
        session = current_session()
    except SessionStoreError as exc:
        return error("session_store_unavailable", str(exc), 503)

    if session is not None:
        try:
            destroy(session.sid, redis_url=current_app.config["REDIS_URL"])
        except SessionStoreError as exc:
            return error("session_store_unavailable", str(exc), 503)

    response = current_app.response_class(status=204)
    clear_auth_cookies(response)
    return response
