"""Attach the correlation reference to the error-tracking event.

13.1 gives a user an apology carrying a short reference — `ZP-3F9A2C` — and logs
the traceback under the same reference. That works when somebody is reading the
log stream. It does not work when the report arrives three days later, because
`install_error_tracking` (13.2) ships the reference in Sentry's `extra`, and
`extra` is *displayed* but not *searchable*: finding the event means opening
candidates one at a time.

A **tag** is searchable. `zephyr:reference:ZP-3F9A2C` is one query, which is the
whole difference between a reference that means something and one that is
decoration.

Everything here is optional at every level, following
`install_error_tracking`'s reasoning: no DSN means no tags, a missing
`sentry_sdk` means no tags, and a failure inside the tagging means no tags. A
bot that will not answer a command because an observability library is absent is
worse than one whose events are harder to search.
"""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger(__name__)

# Tags Sentry indexes. Deliberately few: a tag with high cardinality (a message
# body, a query string) makes the index useless, and these four are the
# questions actually asked of a production error -- which command, whose
# server, which reference, which process.
TAG_PREFIX = "zephyr"


@contextlib.contextmanager
def error_context(
    *,
    reference: str | None = None,
    command: str | None = None,
    guild_id: str | None = None,
    user_id: str | None = None,
):
    """Tag whatever is reported inside this block, and nothing outside it.

    An isolated scope rather than `set_tag` on the current one: the bot is a
    single long-lived process handling every guild's commands, so a tag set
    globally would attach one guild's id to the next guild's unrelated error
    until something overwrote it. That is worse than no tag — it is a wrong tag,
    and a wrong tag on an error report sends somebody to the wrong server.
    """
    scope = _enter_scope()
    if scope is None:
        yield
        return
    with scope as active:
        try:
            if reference:
                active.set_tag(f"{TAG_PREFIX}.reference", reference)
            if command:
                active.set_tag(f"{TAG_PREFIX}.command", command)
            if guild_id:
                active.set_tag(f"{TAG_PREFIX}.guild_id", str(guild_id))
            if user_id:
                # An id, not a username: `send_default_pii=False` is set for a
                # reason, and an id is what a support conversation can act on
                # anyway.
                active.set_user({"id": str(user_id)})
        except Exception:
            # Tagging must never be the reason an error goes unreported.
            log.debug("Could not tag the error context", exc_info=True)
        yield


def _enter_scope():
    """Sentry's isolated-scope context manager, or None when unavailable.

    `new_scope` is 2.x; `push_scope` is 1.x. Both are tried because the pin is
    `>=2.0.0` and an environment that resolved an older one should degrade to no
    tags rather than raising from inside an error handler.
    """
    try:
        import sentry_sdk
    except ImportError:
        return None
    # No DSN configured means no client is recording, so skip the scope
    # entirely rather than paying for one on every handled error. `get_client`
    # is 2.x; the `Hub` shim it replaced is deprecated and may be gone in 3.x,
    # so a missing accessor degrades to "open the scope anyway" rather than
    # raising from inside an error handler.
    try:
        client = sentry_sdk.get_client()
        if client is not None and not client.is_active():
            return None
    except Exception:
        log.debug("Could not determine whether Sentry is active", exc_info=True)
    for name in ("new_scope", "push_scope"):
        factory = getattr(sentry_sdk, name, None)
        if factory is not None:
            try:
                return factory()
            except Exception:
                log.debug("Could not open a Sentry scope via %s", name, exc_info=True)
                return None
    return None
