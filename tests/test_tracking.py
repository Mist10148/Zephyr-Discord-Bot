"""Error-tracking context.

The point of `error_context` is that `ZP-3F9A2C` becomes *searchable*. 13.2
already shipped the reference to Sentry in `extra`, which is displayed but not
indexed — finding the event for a three-day-old report meant opening candidates
one at a time.

The specs below are mostly about it never being the reason an error goes
unreported: no DSN, no `sentry_sdk`, a scope that will not open, a tag that will
not set. Each degrades to "no tags", never to a raise, because this code runs
*inside* an error handler where an exception has nowhere left to go.
"""

import logging
from types import SimpleNamespace

import pytest

from zephyr.core import tracking

sentry_sdk = pytest.importorskip("sentry_sdk", reason="optional dependency")


@pytest.fixture(autouse=True)
def quiet_sentry():
    """Point Sentry at nothing, and put it back afterwards.

    An `init` with no DSN leaves a non-recording client, which is exactly the
    state a deployment without tracking is in.
    """
    sentry_sdk.init(dsn=None)
    yield
    sentry_sdk.init(dsn=None)


def _recording():
    # A syntactically valid DSN pointing at a host that does not resolve: the
    # client records and tags, and the transport's eventual failure is not this
    # module's business.
    sentry_sdk.init(dsn="https://key@example.invalid/1", traces_sample_rate=0.0)


def _tags():
    return dict(sentry_sdk.get_current_scope()._tags)


class TestTagging:
    def test_the_reference_becomes_a_searchable_tag(self):
        """The whole reason this module exists. `extra` is displayed; a tag is
        indexed, so `zephyr.reference:ZP-ABC123` is one query."""
        _recording()

        with tracking.error_context(reference="ZP-ABC123"):
            assert _tags()["zephyr.reference"] == "ZP-ABC123"

    def test_the_command_and_guild_are_tagged(self):
        _recording()

        with tracking.error_context(command="play", guild_id=42):
            tags = _tags()

        assert tags["zephyr.command"] == "play"
        assert tags["zephyr.guild_id"] == "42"

    def test_the_user_is_an_id_and_not_a_name(self):
        """`send_default_pii=False` is set deliberately, and an id is what a
        support conversation can act on anyway."""
        _recording()

        with tracking.error_context(user_id=7):
            user = sentry_sdk.get_current_scope()._user

        assert user == {"id": "7"}

    def test_nothing_is_tagged_when_nothing_is_given(self):
        _recording()

        with tracking.error_context():
            assert _tags() == {}


class TestTheScopeIsIsolated:
    def test_tags_do_not_leak_past_the_block(self):
        """The bot is one long-lived process handling every guild.

        A tag set on the global scope would attach one guild's id to the next
        guild's unrelated error until something overwrote it -- which is worse
        than no tag, because a wrong tag sends somebody to the wrong server.
        """
        _recording()

        with tracking.error_context(guild_id="1", reference="ZP-1"):
            pass

        assert "zephyr.guild_id" not in _tags()
        assert "zephyr.reference" not in _tags()

    def test_two_blocks_do_not_see_each_others_tags(self):
        _recording()

        with tracking.error_context(guild_id="1"):
            first = _tags()["zephyr.guild_id"]
        with tracking.error_context(guild_id="2"):
            second = _tags()["zephyr.guild_id"]

        assert (first, second) == ("1", "2")


class TestItNeverRaises:
    def test_no_dsn_is_a_no_op(self):
        """A deployment without tracking must pay nothing and see nothing."""
        with tracking.error_context(reference="ZP-1", guild_id="1"):
            pass

    def test_a_missing_sentry_sdk_is_a_no_op(self):
        """The package is optional. A bot that will not answer a command
        because an observability library is absent is worse than one whose
        events are harder to search.

        Restored by hand rather than with `monkeypatch`: a None entry in
        `sys.modules` also breaks sentry's own lazy integration imports, and
        those run during this fixture's *teardown* -- which pytest finalises
        after monkeypatch has already put the entry back or not, depending on
        setup order. try/finally does not depend on that ordering.
        """
        import sys

        sentinel = object()
        previous = sys.modules.get("sentry_sdk", sentinel)
        # A None entry makes `import sentry_sdk` raise ImportError, which is
        # precisely the state of an environment without it.
        sys.modules["sentry_sdk"] = None
        try:
            with tracking.error_context(reference="ZP-1"):
                pass
        finally:
            if previous is sentinel:
                del sys.modules["sentry_sdk"]
            else:
                sys.modules["sentry_sdk"] = previous

    def test_an_unopenable_scope_means_no_tags_and_no_raise(self, monkeypatch):
        """The observable consequence of every unavailability branch above."""
        monkeypatch.setattr(tracking, "_enter_scope", lambda: None)
        ran = []

        with tracking.error_context(reference="ZP-1", guild_id="1"):
            ran.append(True)

        assert ran == [True]

    def test_a_scope_that_will_not_open_is_a_no_op(self, monkeypatch):
        def boom():
            raise RuntimeError("no scope")

        monkeypatch.setattr(sentry_sdk, "new_scope", boom, raising=False)
        monkeypatch.setattr(sentry_sdk, "push_scope", boom, raising=False)
        _recording()

        with tracking.error_context(reference="ZP-1"):
            pass

    def test_a_tag_that_will_not_set_is_a_no_op(self, monkeypatch, caplog):
        """Tagging must never be the reason an error goes unreported."""
        _recording()

        class _Refusing:
            def set_tag(self, *_args):
                raise RuntimeError("nope")

            def set_user(self, *_args):
                raise RuntimeError("nope")

        class _Scope:
            def __enter__(self):
                return _Refusing()

            def __exit__(self, *_exc):
                return False

        monkeypatch.setattr(sentry_sdk, "new_scope", _Scope, raising=False)

        with caplog.at_level(logging.DEBUG, logger="zephyr.core.tracking"):
            with tracking.error_context(reference="ZP-1"):
                pass

    def test_the_body_still_runs_when_tracking_is_unavailable(self):
        """The block wraps a `log.error` call. If `error_context` swallowed its
        body, the traceback would be lost -- which is the opposite of the
        point.
        """
        ran = []
        with tracking.error_context(reference="ZP-1"):
            ran.append(True)

        assert ran == [True]

    def test_the_body_still_runs_when_tracking_is_available(self):
        _recording()
        ran = []

        with tracking.error_context(reference="ZP-1"):
            ran.append(True)

        assert ran == [True]


class TestTheHandlersUseIt:
    def test_the_slash_handler_tags_its_reference(self, monkeypatch):
        """End to end through `errors.report`: the reference in the user's
        apology and the reference on the event have to be the same string, or
        quoting it achieves nothing.
        """
        import asyncio

        from zephyr.core import errors

        _recording()
        seen = {}

        real_context = tracking.error_context

        def capture(**kwargs):
            seen.update(kwargs)
            return real_context(**kwargs)

        monkeypatch.setattr(errors, "error_context", capture)

        replies = []
        interaction = SimpleNamespace(
            command=SimpleNamespace(qualified_name="play"),
            guild_id=42,
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(is_done=lambda: True),
            followup=SimpleNamespace(
                send=lambda message, **kwargs: _resolved(replies.append(message))
            ),
        )

        asyncio.run(errors.report(interaction, RuntimeError("boom")))

        assert seen["command"] == "play"
        assert seen["guild_id"] == "42"
        assert seen["user_id"] == "7"
        assert seen["reference"] in replies[0]


def _resolved(value):
    async def coro():
        return value

    return coro()
