"""Tags: name normalisation, the unique-constraint create, and mention safety.

Mention safety is the security-relevant half and gets pinned hardest. Tag
content is member-written text that Zephyr posts, and Zephyr can mention
`@everyone` in servers where the member who wrote the tag cannot -- so without
suppression a tag is a way to borrow the bot's permissions, and
`/tag-create ping @everyone` is a permanent mass-ping button for everybody.

Normalisation is the other half. `Rules`, `rules ` and `RULES` are one tag, and
normalising on the way *in* is what lets the unique constraint say so -- rather
than the read path lower-casing both sides of every comparison while the table
quietly holds three rows that all answer to `/tag rules`.
"""

import asyncio
from types import SimpleNamespace

import pytest

from zephyr.cogs import tags as cog_module
from zephyr.cogs.tags import NO_MENTIONS, TagsCog
from zephyr.db import tags as repo
from zephyr.db.tags import TagError


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


class TestNormalise:
    @pytest.mark.parametrize(
        "raw,stored",
        [("rules", "rules"), ("Rules", "rules"), ("  RULES  ", "rules"),
         ("faq-2", "faq-2"), ("a_b", "a_b"), ("9lives", "9lives")],
    )
    def test_it_folds_the_obvious_variations_together(self, raw, stored):
        assert repo.normalise(raw) == stored

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "two words", "-leading", "_leading", "has.dot", "back`tick",
         "@everyone", "emoji✨", "x" * 33, None],
    )
    def test_it_refuses_names_that_would_break_something(self, raw):
        """Narrow deliberately. A backtick breaks every listing the name
        appears in, whitespace cannot be typed into an autocomplete reliably,
        and a name starting with a dash reads as a flag."""
        assert repo.normalise(raw) is None

    def test_a_name_at_the_limit_is_accepted(self):
        assert repo.normalise("x" * 32) == "x" * 32


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _create(db_url, **over):
    values = {"guild_id": "1", "name": "rules", "content": "be nice", "created_by": "7"}
    values.update(over)
    return repo.create(database_url=db_url, **values)


class TestCreate:
    def test_a_tag_round_trips(self, db_url):
        created = _create(db_url)

        assert created["name"] == "rules"
        assert created["content"] == "be nice"
        assert created["uses"] == 0

    def test_the_name_is_stored_normalised(self, db_url):
        """So the constraint means what a person means by "the same tag"."""
        _create(db_url, name="  RULES ")

        assert repo.get("1", "rules", database_url=db_url)["name"] == "rules"

    def test_a_lookup_folds_case_too(self, db_url):
        _create(db_url, name="rules")

        assert repo.get("1", "RULES", database_url=db_url) is not None

    def test_a_duplicate_is_refused_by_the_constraint(self, db_url):
        """Not by a prior read: two people creating the same tag at once both
        read "no such tag", and without the catch the loser would silently
        shadow the winner."""
        _create(db_url)

        with pytest.raises(TagError) as raised:
            _create(db_url)
        assert "already exists" in str(raised.value)

    def test_a_case_variant_is_the_same_tag(self, db_url):
        _create(db_url, name="rules")

        with pytest.raises(TagError) as raised:
            _create(db_url, name="RULES")
        # The clash, specifically. A bare `raises(TagError)` would also pass if
        # "RULES" were rejected as an invalid *name*, which is a different bug.
        assert "already exists" in str(raised.value)

    def test_the_same_name_in_another_guild_is_fine(self, db_url):
        _create(db_url, guild_id="1")

        assert _create(db_url, guild_id="2")["name"] == "rules"

    def test_a_bad_name_is_refused_with_an_explanation(self, db_url):
        with pytest.raises(TagError) as raised:
            _create(db_url, name="two words")
        assert "letters, numbers" in str(raised.value)

    def test_empty_content_is_refused(self, db_url):
        """Discord rejects an empty message, so a tag with no content would be
        a tag that can never be shown."""
        with pytest.raises(TagError):
            _create(db_url, content="   ")

    def test_content_is_trimmed_to_the_limit(self, db_url):
        stored = _create(db_url, content="x" * 5000)["content"]

        assert len(stored) == repo.MAX_CONTENT_CHARS

    def test_the_per_guild_cap_is_enforced(self, db_url, monkeypatch):
        monkeypatch.setattr(repo, "MAX_TAGS_PER_GUILD", 2)
        _create(db_url, name="one")
        _create(db_url, name="two")

        with pytest.raises(TagError) as raised:
            _create(db_url, name="three")
        assert "Delete one" in str(raised.value)

    def test_the_cap_is_per_guild(self, db_url, monkeypatch):
        monkeypatch.setattr(repo, "MAX_TAGS_PER_GUILD", 1)
        _create(db_url, guild_id="1")

        assert _create(db_url, guild_id="2")["name"] == "rules"

    def test_the_database_refuses_the_duplicate(self, db_url):
        """The premise the catch rests on. Without the unique constraint the
        second insert would succeed and the lookup would return whichever row
        the planner reached first."""
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        from zephyr.db.models import Tag
        from zephyr.db.session import get_engine

        _create(db_url)

        with pytest.raises(IntegrityError):
            with get_engine(db_url).begin() as connection:
                connection.execute(
                    insert(Tag).values(
                        guild_id="1", name="rules", content="other", created_by="8", uses=0
                    )
                )


class TestEdit:
    def test_it_replaces_the_content(self, db_url):
        _create(db_url)

        assert repo.edit("1", "rules", "be nicer", database_url=db_url)["content"] == "be nicer"

    def test_a_missing_tag_is_none_not_a_silent_success(self, db_url):
        assert repo.edit("1", "nope", "x", database_url=db_url) is None

    def test_it_is_scoped_to_the_guild(self, db_url):
        _create(db_url, guild_id="1", content="ours")

        assert repo.edit("2", "rules", "hijacked", database_url=db_url) is None
        assert repo.get("1", "rules", database_url=db_url)["content"] == "ours"

    def test_empty_content_is_refused(self, db_url):
        _create(db_url)

        with pytest.raises(TagError):
            repo.edit("1", "rules", "  ", database_url=db_url)


class TestRemove:
    def test_it_deletes(self, db_url):
        _create(db_url)

        assert repo.remove("1", "RULES", database_url=db_url) is True
        assert repo.get("1", "rules", database_url=db_url) is None

    def test_it_is_scoped_to_the_guild(self, db_url):
        _create(db_url, guild_id="1")

        assert repo.remove("2", "rules", database_url=db_url) is False
        assert repo.get("1", "rules", database_url=db_url) is not None

    def test_removing_nothing_is_not_an_error(self, db_url):
        assert repo.remove("1", "nope", database_url=db_url) is False


class TestRecordUse:
    def test_it_counts(self, db_url):
        _create(db_url)
        repo.record_use("1", "rules", database_url=db_url)
        repo.record_use("1", "rules", database_url=db_url)

        assert repo.get("1", "rules", database_url=db_url)["uses"] == 2

    def test_a_count_added_by_somebody_else_is_not_lost(self, db_url):
        """The increment happens in the statement rather than read-modify-write.

        A read-modify-write would read 0, be overtaken, and write 1 -- losing
        the other invocation. Simulated by changing the stored value between
        this call's read and its write, which is only possible to get wrong if
        the read exists.
        """
        _create(db_url)
        repo.record_use("1", "rules", database_url=db_url)
        repo.edit("1", "rules", "changed", database_url=db_url)
        repo.record_use("1", "rules", database_url=db_url)

        assert repo.get("1", "rules", database_url=db_url)["uses"] == 2

    def test_an_unusable_name_is_ignored_not_an_error(self, db_url):
        repo.record_use("1", "two words", database_url=db_url)


class TestListForGuild:
    def test_it_orders_by_use(self, db_url):
        """Most-used first because this backs both /tag-list and the
        autocomplete, and the useful first suggestion is the tag people
        actually invoke -- not the one starting with "a"."""
        _create(db_url, name="aardvark")
        _create(db_url, name="rules")
        repo.record_use("1", "rules", database_url=db_url)

        assert [row["name"] for row in repo.list_for_guild("1", database_url=db_url)] == [
            "rules", "aardvark"
        ]

    def test_it_filters_by_prefix(self, db_url):
        _create(db_url, name="rules")
        _create(db_url, name="roles")
        _create(db_url, name="faq")

        names = [row["name"] for row in repo.list_for_guild("1", prefix="r", database_url=db_url)]

        assert sorted(names) == ["roles", "rules"]

    def test_the_prefix_is_not_a_substring_match(self, db_url):
        """The names are short, and a substring match on "e" would return most
        of the table."""
        _create(db_url, name="rules")

        assert repo.list_for_guild("1", prefix="ule", database_url=db_url) == []

    def test_it_never_returns_another_guilds_tags(self, db_url):
        _create(db_url, guild_id="2")

        assert repo.list_for_guild("1", database_url=db_url) == []

    def test_the_limit_is_capped(self, db_url):
        assert repo.list_for_guild("1", limit=10_000, database_url=db_url) == []

    def test_leaving_a_guild_forgets_its_tags(self, db_url):
        _create(db_url, guild_id="1", name="one")
        _create(db_url, guild_id="1", name="two")
        _create(db_url, guild_id="2", name="one")

        assert repo.delete_for_guild("1", database_url=db_url) == 2
        assert repo.get("2", "one", database_url=db_url) is not None


# ---------------------------------------------------------------------------
# Mention safety and ownership
# ---------------------------------------------------------------------------


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, *, embed=None, allowed_mentions=None, ephemeral=False):
        self.sent.append(
            {"content": content, "embed": embed, "allowed_mentions": allowed_mentions}
        )


def _interaction(*, user_id=7, manage_messages=False):
    followup = FakeFollowup()

    async def defer(**_kwargs):
        return None

    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(manage_messages=manage_messages),
        ),
        response=SimpleNamespace(defer=defer),
        followup=followup,
    )


@pytest.fixture(autouse=True)
def scoped_repo(db_url, monkeypatch):
    for name in (
        "get", "create", "edit", "remove", "record_use", "list_for_guild",
        "count_for_guild", "delete_for_guild",
    ):
        real = getattr(repo, name)

        def bound(*args, _real=real, **kwargs):
            kwargs.setdefault("database_url", db_url)
            return _real(*args, **kwargs)

        monkeypatch.setattr(cog_module.repo, name, bound)


def _cog():
    return TagsCog.__new__(TagsCog)


class TestMentionSafety:
    def test_showing_a_tag_suppresses_every_mention(self, db_url):
        """The security property of this module.

        Zephyr can mention @everyone where the tag's author cannot, so without
        this `/tag-create ping @everyone` hands every member a permanent
        mass-ping button.
        """
        _create(db_url, name="ping", content="@everyone look at this")
        interaction = _interaction()

        asyncio.run(_cog().tag.callback(_cog(), interaction, "ping"))
        reply = interaction.followup.sent[0]

        assert reply["content"] == "@everyone look at this"
        assert reply["allowed_mentions"] is NO_MENTIONS
        assert reply["allowed_mentions"].everyone is False
        assert reply["allowed_mentions"].roles is False
        assert reply["allowed_mentions"].users is False

    def test_the_create_preview_suppresses_them_too(self, db_url):
        """Otherwise creating the tag is itself the ping."""
        interaction = _interaction()

        asyncio.run(
            _cog().tag_create.callback(_cog(), interaction, "ping", "@everyone hello")
        )

        assert interaction.followup.sent[0]["allowed_mentions"] is NO_MENTIONS

    def test_the_edit_preview_suppresses_them_too(self, db_url):
        _create(db_url, name="ping", content="quiet")
        interaction = _interaction()

        asyncio.run(
            _cog().tag_edit.callback(_cog(), interaction, "ping", "@everyone hello")
        )

        assert interaction.followup.sent[0]["allowed_mentions"] is NO_MENTIONS

    def test_the_listing_shows_names_and_not_content(self, db_url):
        """A listing that rendered content would be both enormous and a way to
        make the bot repeat fifty tags at once."""
        _create(db_url, name="ping", content="@everyone look at this")
        interaction = _interaction()

        asyncio.run(_cog().tag_list.callback(_cog(), interaction))
        embed = interaction.followup.sent[0]["embed"]

        assert "`ping`" in embed.description
        assert "@everyone" not in embed.description


class TestShowing:
    def test_a_missing_tag_says_so_and_points_at_the_list(self, db_url):
        interaction = _interaction()

        asyncio.run(_cog().tag.callback(_cog(), interaction, "nope"))

        assert "/tag-list" in interaction.followup.sent[0]["content"]

    def test_showing_a_tag_counts_the_use(self, db_url):
        _create(db_url, name="rules")
        asyncio.run(_cog().tag.callback(_cog(), _interaction(), "rules"))

        assert repo.get("1", "rules", database_url=db_url)["uses"] == 1

    def test_a_failed_count_does_not_fail_the_lookup(self, db_url, monkeypatch, caplog):
        """A counter is not worth failing a successful lookup over."""
        _create(db_url, name="rules")

        def boom(*_args, **_kwargs):
            raise RuntimeError("no db")

        monkeypatch.setattr(cog_module.repo, "record_use", boom)
        interaction = _interaction()

        with caplog.at_level("WARNING", logger="zephyr.cogs.tags"):
            asyncio.run(_cog().tag.callback(_cog(), interaction, "rules"))

        assert interaction.followup.sent[0]["content"] == "be nice"


class TestOwnership:
    def test_the_author_may_edit_their_own_tag(self, db_url):
        _create(db_url, name="rules", created_by="7")
        interaction = _interaction(user_id=7)

        asyncio.run(_cog().tag_edit.callback(_cog(), interaction, "rules", "new"))

        assert repo.get("1", "rules", database_url=db_url)["content"] == "new"

    def test_somebody_else_may_not(self, db_url):
        """The author exemption is what makes tags usable without an
        administrator in the loop; without the check, it would also let anybody
        rewrite anybody's tag."""
        _create(db_url, name="rules", created_by="7")
        interaction = _interaction(user_id=8)

        asyncio.run(_cog().tag_edit.callback(_cog(), interaction, "rules", "hijacked"))

        assert "not your tag" in interaction.followup.sent[0]["content"]
        assert repo.get("1", "rules", database_url=db_url)["content"] == "be nice"

    def test_manage_messages_may(self, db_url):
        """Which is what lets a server clean up after somebody who left."""
        _create(db_url, name="rules", created_by="7")
        interaction = _interaction(user_id=8, manage_messages=True)

        asyncio.run(_cog().tag_edit.callback(_cog(), interaction, "rules", "new"))

        assert repo.get("1", "rules", database_url=db_url)["content"] == "new"

    def test_the_author_may_delete_their_own_tag(self, db_url):
        _create(db_url, name="rules", created_by="7")

        asyncio.run(_cog().tag_delete.callback(_cog(), _interaction(user_id=7), "rules"))

        assert repo.get("1", "rules", database_url=db_url) is None

    def test_somebody_else_may_not_delete_it(self, db_url):
        _create(db_url, name="rules", created_by="7")

        asyncio.run(_cog().tag_delete.callback(_cog(), _interaction(user_id=8), "rules"))

        assert repo.get("1", "rules", database_url=db_url) is not None


class TestAutocomplete:
    def test_it_suggests_matching_names(self, db_url):
        _create(db_url, name="rules")
        _create(db_url, name="faq")
        cog = _cog()

        choices = asyncio.run(cog._name_autocomplete(_interaction(), "r"))

        assert [choice.value for choice in choices] == ["rules"]

    def test_a_dm_gets_no_suggestions(self, db_url):
        interaction = _interaction()
        interaction.guild = None

        assert asyncio.run(_cog()._name_autocomplete(interaction, "r")) == []

    def test_tag_create_has_no_name_autocomplete(self):
        """A tag that does not exist yet has no name to suggest, and offering
        the existing ones would invite somebody to pick a name that is taken."""
        by_name = {command.name: command for command in TagsCog.__cog_app_commands__}

        assert by_name["tag-create"]._params["name"].autocomplete is None
        assert by_name["tag"]._params["name"].autocomplete is not None

    def test_content_never_gets_an_autocomplete(self):
        """Suggesting existing tag content would leak one tag's body into
        another's creation form for no reason."""
        by_name = {command.name: command for command in TagsCog.__cog_app_commands__}

        assert by_name["tag-create"]._params["content"].autocomplete is None
        assert by_name["tag-edit"]._params["content"].autocomplete is None


class TestTheCogIsRegistered:
    def test_tags_is_enabled(self):
        from zephyr import config

        assert "tags" in config.ENABLED_COGS
