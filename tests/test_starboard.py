"""Starboard: the idempotent claim, the cheap-guard ordering, and promotion.

Two things here would go wrong silently in production and are pinned hardest.

`claim` inserts and lets the unique constraint answer, because reactions are
independent gateway events with no ordering guarantee -- a read-then-post races
with itself and puts the same message in the starboard twice. The premise (that
the database refuses the duplicate) is asserted separately from the behaviour
(that the second caller is told somebody else won).

`relevant` is the performance contract: this listener runs on every reaction in
every guild the bot is in, and only reactions that survive every cheap guard are
allowed to reach `fetch_message`. It is a separate function so that ordering can
be tested without mocking a REST call -- a test that had to would not be testing
the ordering.
"""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from zephyr.cogs import starboard as cog_module
from zephyr.cogs.starboard import StarboardCog, build_embed, star_display
from zephyr.db import starboard as repo


def _config(**over):
    base = {
        "guild_id": "1", "enabled": True, "channel_id": "900", "threshold": 5,
        "emoji": "⭐", "allow_self_star": False, "ignored_channel_ids": [],
    }
    base.update(over)
    return base


def _payload(**over):
    base = {"guild_id": 1, "channel_id": 10, "message_id": 100, "emoji": "⭐", "user_id": 7}
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Configuration storage
# ---------------------------------------------------------------------------


class TestConfig:
    def test_an_unconfigured_guild_reads_as_none(self, db_url):
        assert repo.read_config("1", database_url=db_url) is None

    def test_the_defaults_are_filled_in_by_the_reader(self, db_url):
        """So no consumer has to know they exist. A listener deciding for itself
        whether five is the default is a listener that will disagree with the
        settings command about it.
        """
        repo.write_config("1", {"enabled": True, "channel_id": "900"}, database_url=db_url)

        config = repo.read_config("1", database_url=db_url)

        assert config["threshold"] == repo.DEFAULT_THRESHOLD
        assert config["emoji"] == repo.DEFAULT_EMOJI
        assert config["allow_self_star"] is False
        assert config["ignored_channel_ids"] == []

    def test_a_partial_write_does_not_blank_the_rest(self, db_url):
        repo.write_config("1", {"enabled": True, "threshold": 9}, database_url=db_url)
        repo.write_config("1", {"channel_id": "900"}, database_url=db_url)

        config = repo.read_config("1", database_url=db_url)

        assert config["threshold"] == 9
        assert config["channel_id"] == "900"

    def test_only_enabled_starboards_are_cached(self, db_url):
        """This backs the cheapest guard in the listener, so it holds as little
        as possible."""
        repo.write_config("1", {"enabled": True, "channel_id": "900"}, database_url=db_url)
        repo.write_config("2", {"threshold": 3}, database_url=db_url)

        assert set(repo.read_all_configs(database_url=db_url)) == {"1"}

    def test_ignored_channels_round_trip_as_strings(self, db_url):
        """A snowflake read back as an int would never match `str(payload.channel_id)`,
        so the ignore list would silently stop working."""
        repo.write_config(
            "1", {"enabled": True, "ignored_channel_ids": [11, 12]}, database_url=db_url
        )

        assert repo.read_all_configs(database_url=db_url)["1"]["ignored_channel_ids"] == ["11", "12"]


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


def _claim(db_url, **over):
    values = {
        "guild_id": "1", "source_channel_id": "10", "source_message_id": "100", "star_count": 5,
    }
    values.update(over)
    return repo.claim(database_url=db_url, **values)


class TestClaim:
    def test_the_first_claim_wins(self, db_url):
        claimed = _claim(db_url)

        assert claimed["source_message_id"] == "100"
        assert claimed["starboard_message_id"] is None

    def test_a_second_claim_on_the_same_message_is_refused(self, db_url):
        """The whole reason the constraint exists.

        Reactions are independent gateway events, so two arriving close
        together both read "not promoted yet". Without this the message appears
        in the starboard twice and the second row orphans the first.
        """
        assert _claim(db_url) is not None
        assert _claim(db_url) is None

    def test_the_same_message_id_in_another_guild_is_a_different_message(self, db_url):
        """Message ids are globally unique in practice, but the constraint is
        per guild so a shared id could never collide across servers."""
        assert _claim(db_url, guild_id="1") is not None
        assert _claim(db_url, guild_id="2") is not None

    def test_the_database_refuses_the_duplicate(self, db_url):
        """The premise the claim rests on, asserted directly. Without the unique
        constraint the second insert would succeed."""
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        from zephyr.db.models import StarboardEntry
        from zephyr.db.session import get_engine

        _claim(db_url)

        with pytest.raises(IntegrityError):
            with get_engine(db_url).begin() as connection:
                connection.execute(
                    insert(StarboardEntry).values(
                        guild_id="1", source_channel_id="10", source_message_id="100",
                        star_count=1,
                    )
                )


class TestEntryLifecycle:
    def test_a_message_id_is_attached_after_the_post(self, db_url):
        """Attached afterwards, so a failed post leaves a NULL that the next
        reaction retries rather than an id pointing at nothing."""
        _claim(db_url)
        repo.attach_message("1", "100", "555", database_url=db_url)

        assert repo.get_entry("1", "100", database_url=db_url)["starboard_message_id"] == "555"

    def test_the_count_is_set_not_incremented(self, db_url):
        """The caller has just read the live count off the message. An
        increment would drift permanently the first time a gateway event was
        missed or delivered twice."""
        _claim(db_url, star_count=5)
        repo.set_count("1", "100", 3, database_url=db_url)

        assert repo.get_entry("1", "100", database_url=db_url)["star_count"] == 3

    def test_an_entry_can_be_withdrawn_and_reclaimed(self, db_url):
        """Which is what makes falling below the threshold recoverable: the row
        goes, so a later reaction promotes it again rather than editing a
        message that no longer exists."""
        _claim(db_url)
        assert repo.remove_entry("1", "100", database_url=db_url) is True
        assert _claim(db_url) is not None

    def test_removing_nothing_is_not_an_error(self, db_url):
        assert repo.remove_entry("1", "404", database_url=db_url) is False

    def test_leaving_a_guild_forgets_its_starboard(self, db_url):
        repo.write_config("1", {"enabled": True, "channel_id": "900"}, database_url=db_url)
        _claim(db_url)
        _claim(db_url, source_message_id="101")
        _claim(db_url, guild_id="2", source_message_id="200")

        assert repo.delete_for_guild("1", database_url=db_url) == 2
        assert repo.read_config("1", database_url=db_url) is None
        assert repo.get_entry("2", "200", database_url=db_url) is not None


# ---------------------------------------------------------------------------
# The cheap guards
# ---------------------------------------------------------------------------


def _cog(cache=None):
    cog = StarboardCog.__new__(StarboardCog)
    cog.bot = SimpleNamespace(get_guild=lambda _id: None)
    cog._cache = cache if cache is not None else {"1": _config()}
    return cog


class TestRelevant:
    def test_a_reaction_in_a_configured_guild_is_relevant(self):
        assert _cog().relevant(_payload()) is not None

    def test_a_dm_reaction_is_not(self):
        assert _cog().relevant(_payload(guild_id=None)) is None

    def test_an_unconfigured_guild_is_not(self):
        """The cheapest guard, and the one that rejects almost every reaction
        the bot will ever see."""
        assert _cog(cache={}).relevant(_payload()) is None

    def test_a_disabled_starboard_is_not(self):
        assert _cog({"1": _config(enabled=False)}).relevant(_payload()) is None

    def test_an_enabled_starboard_with_no_channel_is_not(self):
        """Half-finished configuration, which a dashboard save can still
        produce -- and posting nowhere is not an error worth logging on every
        reaction."""
        assert _cog({"1": _config(channel_id=None)}).relevant(_payload()) is None

    def test_a_different_emoji_is_not(self):
        assert _cog().relevant(_payload(emoji="🍕")) is None

    def test_a_custom_emoji_matches_by_its_string_form(self):
        """`str(PartialEmoji)` is "<:name:id>", which is what the column stores
        -- comparing objects would never match across a restart."""
        cog = _cog({"1": _config(emoji="<:star:42>")})

        assert cog.relevant(_payload(emoji="<:star:42>")) is not None

    def test_an_ignored_channel_is_not(self):
        cog = _cog({"1": _config(ignored_channel_ids=["10"])})

        assert cog.relevant(_payload(channel_id=10)) is None

    def test_the_starboard_channel_itself_is_not(self):
        """Otherwise starring an entry promotes the entry into itself, and the
        promotion is then starrable again -- a two-message loop."""
        assert _cog().relevant(_payload(channel_id=900)) is None


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


class FakeReaction:
    def __init__(self, emoji, count, reactors=()):
        self.emoji = emoji
        self.count = count
        self._reactors = list(reactors)

    def users(self, *, limit=None):
        async def iterator():
            for user_id in self._reactors:
                yield SimpleNamespace(id=user_id)

        return iterator()


class FakeMessage:
    def __init__(self, *, author_id=7, reactions=(), content="hello", message_id=100):
        self.id = message_id
        self.content = content
        self.author = SimpleNamespace(
            id=author_id, display_name="Author", display_avatar=SimpleNamespace(url="http://a")
        )
        self.reactions = list(reactions)
        self.attachments = []
        self.jump_url = "http://jump"
        self.created_at = None
        self.edited = []
        self.deleted = False

    async def edit(self, *, content=None, embed=None):
        self.edited.append((content, embed))

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, *, message=None, fetch_raises=None, send_raises=None):
        self.id = 10
        self._message = message
        self._fetch_raises = fetch_raises
        self._send_raises = send_raises
        self.sent = []

    async def fetch_message(self, _id):
        if self._fetch_raises:
            raise self._fetch_raises
        if self._message is None:
            raise discord.NotFound(_Response(404), "gone")
        return self._message

    async def send(self, *, content=None, embed=None):
        if self._send_raises:
            raise self._send_raises
        posted = FakeMessage(message_id=555)
        self.sent.append((content, embed))
        return posted


class _Response:
    def __init__(self, status):
        self.status = status
        self.reason = "x"


def _promoting_cog(db_url, *, source, board, config=None):
    cog = StarboardCog.__new__(StarboardCog)
    guild = SimpleNamespace(id=1)
    guild.get_channel = lambda channel_id: board if int(channel_id) == 900 else source
    cog.bot = SimpleNamespace(get_guild=lambda _id: guild)
    cog._cache = {"1": config or _config()}
    return cog


@pytest.fixture(autouse=True)
def scoped_repo(db_url, monkeypatch):
    """Bind the cog's repository calls to the test database.

    The cog calls the module-level helpers with no database_url, which is how it
    is called in production; each is wrapped rather than the engine replaced, so
    what is under test is the cog and not a fixture's idea of storage.
    """
    for name in (
        "get_entry", "claim", "attach_message", "set_count", "remove_entry",
        "read_config", "write_config", "read_all_configs", "delete_for_guild",
    ):
        real = getattr(repo, name)
        def bound(*args, _real=real, **kwargs):
            # setdefault, not an override: these helpers call each other
            # internally with an explicit url, and the wrapper is patched in
            # place -- so forcing the keyword would collide with itself.
            kwargs.setdefault("database_url", db_url)
            return _real(*args, **kwargs)

        monkeypatch.setattr(cog_module.repo, name, bound)


def _handle(cog, payload=None):
    return asyncio.run(cog.handle_reaction(payload or _payload()))


class TestPromotion:
    def test_enough_stars_posts_to_the_starboard(self, db_url):
        message = FakeMessage(reactions=[FakeReaction("⭐", 5, reactors=[1, 2, 3, 4, 5])])
        source, board = FakeChannel(message=message), FakeChannel()
        cog = _promoting_cog(db_url, source=source, board=board)

        assert _handle(cog) is True
        assert len(board.sent) == 1
        assert repo.get_entry("1", "100", database_url=db_url)["starboard_message_id"] == "555"

    def test_too_few_stars_posts_nothing(self, db_url):
        message = FakeMessage(reactions=[FakeReaction("⭐", 4, reactors=[1, 2, 3, 4])])
        board = FakeChannel()
        cog = _promoting_cog(db_url, source=FakeChannel(message=message), board=board)

        assert _handle(cog) is False
        assert board.sent == []
        assert repo.get_entry("1", "100", database_url=db_url) is None

    def test_the_authors_own_star_does_not_count(self, db_url):
        """A starboard anybody can promote themselves into is not a starboard.

        `reaction.count` includes the author, and `reaction.me` reports whether
        the *bot* reacted -- so the reactor list is the only way to know.
        """
        message = FakeMessage(author_id=7, reactions=[FakeReaction("⭐", 5, reactors=[7, 1, 2, 3, 4])])
        board = FakeChannel()
        cog = _promoting_cog(db_url, source=FakeChannel(message=message), board=board)

        assert _handle(cog) is False
        assert board.sent == []

    def test_a_guild_may_allow_self_stars(self, db_url):
        message = FakeMessage(author_id=7, reactions=[FakeReaction("⭐", 5, reactors=[7, 1, 2, 3, 4])])
        board = FakeChannel()
        cog = _promoting_cog(
            db_url, source=FakeChannel(message=message), board=board,
            config=_config(allow_self_star=True),
        )

        assert _handle(cog) is True

    def test_a_second_reaction_edits_rather_than_reposting(self, db_url):
        posted = FakeMessage(message_id=555)
        board = FakeChannel()
        board.fetch_message = lambda _id: _resolved(posted)

        message = FakeMessage(reactions=[FakeReaction("⭐", 6, reactors=[1, 2, 3, 4, 5, 6])])
        cog = _promoting_cog(db_url, source=FakeChannel(message=message), board=board)
        repo.claim(
            guild_id="1", source_channel_id="10", source_message_id="100", star_count=5,
            database_url=db_url,
        )
        repo.attach_message("1", "100", "555", database_url=db_url)

        assert _handle(cog) is True
        assert board.sent == []
        assert posted.edited[0][0] == star_display(6, "⭐")
        assert repo.get_entry("1", "100", database_url=db_url)["star_count"] == 6

    def test_falling_below_the_threshold_withdraws_the_entry(self, db_url):
        """Rather than leaving it at a stale count: a starboard showing "3 ⭐"
        under a threshold of 5 is a visible contradiction."""
        posted = FakeMessage(message_id=555)
        board = FakeChannel()
        board.fetch_message = lambda _id: _resolved(posted)

        message = FakeMessage(reactions=[FakeReaction("⭐", 2, reactors=[1, 2])])
        cog = _promoting_cog(db_url, source=FakeChannel(message=message), board=board)
        repo.claim(
            guild_id="1", source_channel_id="10", source_message_id="100", star_count=5,
            database_url=db_url,
        )
        repo.attach_message("1", "100", "555", database_url=db_url)

        assert _handle(cog) is False
        assert posted.deleted is True
        assert repo.get_entry("1", "100", database_url=db_url) is None

    def test_a_deleted_source_message_drops_the_entry(self, db_url):
        """Or the starboard keeps an entry linking to nothing."""
        repo.claim(
            guild_id="1", source_channel_id="10", source_message_id="100", star_count=5,
            database_url=db_url,
        )
        cog = _promoting_cog(db_url, source=FakeChannel(message=None), board=FakeChannel())

        assert _handle(cog) is False
        assert repo.get_entry("1", "100", database_url=db_url) is None

    def test_a_hand_deleted_starboard_entry_is_forgotten(self, db_url):
        """So the next reaction re-promotes, rather than editing forever into a
        message that is gone."""
        board = FakeChannel()
        board.fetch_message = lambda _id: _raising(discord.NotFound(_Response(404), "gone"))

        message = FakeMessage(reactions=[FakeReaction("⭐", 6, reactors=[1, 2, 3, 4, 5, 6])])
        cog = _promoting_cog(db_url, source=FakeChannel(message=message), board=board)
        repo.claim(
            guild_id="1", source_channel_id="10", source_message_id="100", star_count=5,
            database_url=db_url,
        )
        repo.attach_message("1", "100", "555", database_url=db_url)

        assert _handle(cog) is False
        assert repo.get_entry("1", "100", database_url=db_url) is None

    def test_an_unreadable_source_channel_is_logged_not_raised(self, db_url, caplog):
        source = FakeChannel(fetch_raises=discord.Forbidden(_Response(403), "no"))
        cog = _promoting_cog(db_url, source=source, board=FakeChannel())

        with caplog.at_level("WARNING", logger="zephyr.cogs.starboard"):
            assert _handle(cog) is False

    def test_a_failed_post_leaves_a_retryable_row(self, db_url, caplog):
        """The claim is committed and the message id is not, so the next
        reaction tries the post again instead of double-posting."""
        board = FakeChannel(send_raises=discord.Forbidden(_Response(403), "no"))
        message = FakeMessage(reactions=[FakeReaction("⭐", 5, reactors=[1, 2, 3, 4, 5])])
        cog = _promoting_cog(db_url, source=FakeChannel(message=message), board=board)

        with caplog.at_level("ERROR", logger="zephyr.cogs.starboard"):
            assert _handle(cog) is False

        entry = repo.get_entry("1", "100", database_url=db_url)
        assert entry is not None
        assert entry["starboard_message_id"] is None

    def test_an_irrelevant_reaction_never_touches_the_api(self, db_url):
        """The point of the cheap guards. A source channel that raises on fetch
        proves nothing reached it."""
        source = FakeChannel(fetch_raises=AssertionError("fetch_message must not be called"))
        cog = _promoting_cog(db_url, source=source, board=FakeChannel())

        assert _handle(cog, _payload(emoji="🍕")) is False


def _resolved(value):
    async def coro():
        return value

    return coro()


def _raising(error):
    async def coro():
        raise error

    return coro()


# ---------------------------------------------------------------------------
# The embed
# ---------------------------------------------------------------------------


class TestBuildEmbed:
    def test_it_links_back_rather_than_copying(self):
        """The starboard is an index. A faithful copy would strip the thread,
        the replies and the reactions that made it worth starring."""
        embed = build_embed(FakeMessage(), count=5, emoji="⭐")

        assert "http://jump" in embed.fields[0].value

    def test_an_empty_message_still_renders(self):
        """An image-only post is a normal thing to star, and an embed with an
        empty description is rejected by Discord."""
        embed = build_embed(FakeMessage(content=""), count=5, emoji="⭐")

        assert embed.description

    def test_a_long_message_is_truncated(self):
        embed = build_embed(FakeMessage(content="x" * 5000), count=5, emoji="⭐")

        assert len(embed.description) == cog_module.MAX_PREVIEW_CHARS

    def test_only_an_image_attachment_becomes_the_preview(self):
        message = FakeMessage()
        message.attachments = [
            SimpleNamespace(content_type="application/pdf", url="http://doc"),
            SimpleNamespace(content_type="image/png", url="http://pic"),
        ]

        assert build_embed(message, count=5, emoji="⭐").image.url == "http://pic"

    def test_the_count_is_shown(self):
        assert "5" in build_embed(FakeMessage(), count=5, emoji="⭐").footer.text


class TestTheCogIsRegistered:
    def test_starboard_is_enabled(self):
        from zephyr import config

        assert "starboard" in config.ENABLED_COGS
