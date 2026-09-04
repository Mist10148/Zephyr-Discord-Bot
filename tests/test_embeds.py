"""The embed factory.

The specs worth reading are the ceiling ones. Discord answers a 400 to an embed
with an empty field value, a title over 256 characters or a description over
4096, and before this factory each of those was a latent failure in whichever
cog happened to compute one — surfacing at the reply rather than at the
computation. Clipping in one place is most of the value of having one place.
"""

import discord
import pytest

from zephyr.utils import embeds


@pytest.fixture(autouse=True)
def clean_identity():
    embeds.reset()
    yield
    embeds.reset()


class TestTheAccents:
    def test_each_role_has_its_own_colour(self):
        """Six roles, indexed by what the message is.

        The point of the palette: an error cannot be orange in one cog and red
        in another, because `error()` is one function.
        """
        assert len(set(embeds.ACCENTS.values())) == len(embeds.ACCENTS)

    @pytest.mark.parametrize(
        "helper,accent",
        [
            (embeds.success, "success"), (embeds.error, "error"),
            (embeds.warning, "warning"), (embeds.info, "info"),
            (embeds.neutral, "neutral"), (embeds.brand, "brand"),
        ],
    )
    def test_a_helper_uses_its_own_accent(self, helper, accent):
        assert helper("x").colour.value == embeds.ACCENTS[accent]

    def test_an_unknown_accent_falls_back_rather_than_raising(self):
        """A typo'd accent name should render in the default colour, not fail
        the command that was trying to report something."""
        assert embeds.build(accent="chartreuse").colour.value == embeds.ACCENTS["info"]


class TestTheTimestamp:
    def test_every_embed_carries_one_by_default(self):
        """Which no embed in this package had. It is the cheapest answer to
        "when did this happen" on an error reported hours later."""
        assert embeds.info("x").timestamp is not None

    def test_it_can_be_turned_off(self):
        assert embeds.info("x", timestamp=False).timestamp is None


class TestTheFooter:
    def test_it_names_the_bot_by_default(self):
        assert embeds.info("x").footer.text == "Zephyr"

    def test_extra_text_is_appended_not_substituted(self):
        """A cog's own footer note must not cost the shared identity."""
        assert embeds.info("x", footer="page 2 of 5").footer.text == "page 2 of 5 · Zephyr"

    def test_the_icon_comes_from_configure(self):
        """The avatar URL does not exist until the gateway hands it over, so it
        is set once at on_ready rather than threaded through 103 call sites."""
        embeds.configure(icon_url="http://avatar")

        assert embeds.info("x").footer.icon_url == "http://avatar"

    def test_an_unconfigured_icon_renders_rather_than_raising(self):
        assert embeds.info("x").footer.icon_url is None

    def test_a_configured_name_is_used(self):
        embeds.configure(name="Zephyr Dev")

        assert embeds.info("x").footer.text == "Zephyr Dev"

    def test_configure_ignores_empty_values(self):
        """So a partial call cannot blank the identity."""
        embeds.configure(name="Zephyr Dev", icon_url="http://avatar")
        embeds.configure(name=None)

        assert embeds.info("x").footer.text == "Zephyr Dev"
        assert embeds.info("x").footer.icon_url == "http://avatar"


class TestFields:
    def test_fields_are_added_from_tuples(self):
        embed = embeds.info(fields=[("A", "1"), ("B", "2", True)])

        assert [(field.name, field.value, field.inline) for field in embed.fields] == [
            ("A", "1", False), ("B", "2", True)
        ]

    def test_an_empty_field_value_becomes_a_zero_width_space(self):
        """Discord answers 400 to an empty field value, so a cog that computed
        one would fail at the reply rather than at the computation."""
        embed = embeds.info(fields=[("A", "")])

        assert embed.fields[0].value == "​"

    def test_a_none_field_value_does_too(self):
        assert embeds.info(fields=[("A", None)]).fields[0].value == "​"

    def test_extra_fields_are_dropped_rather_than_raising(self):
        """Discord's ceiling is 25. Twenty-six is a 400, and a listing that
        silently shows 25 is better than one that shows nothing."""
        embed = embeds.info(fields=[(str(index), "x") for index in range(40)])

        assert len(embed.fields) == embeds.MAX_FIELDS


class TestTheCeilings:
    def test_a_long_title_is_clipped(self):
        assert len(embeds.info(title="x" * 500).title) == embeds.MAX_TITLE

    def test_a_long_description_is_clipped(self):
        assert len(embeds.info("x" * 9000).description) == embeds.MAX_DESCRIPTION

    def test_a_long_field_value_is_clipped(self):
        embed = embeds.info(fields=[("A", "x" * 4000)])

        assert len(embed.fields[0].value) == embeds.MAX_FIELD_VALUE

    def test_a_clipped_value_says_so(self):
        """An ellipsis rather than a hard cut, so truncation is visible instead
        of looking like the content simply ended."""
        assert embeds.info("x" * 9000).description.endswith("…")

    def test_a_value_at_the_limit_is_untouched(self):
        text = "x" * embeds.MAX_DESCRIPTION

        assert embeds.info(text).description == text

    def test_a_long_footer_is_clipped(self):
        assert len(embeds.info("x", footer="y" * 4000).footer.text) == embeds.MAX_FOOTER


class TestTheRest:
    def test_a_url_title_link_is_set(self):
        assert embeds.info("x", title="T", url="http://t").url == "http://t"

    def test_an_empty_url_is_none_not_an_empty_string(self):
        """discord.py sends "" as a URL and Discord answers 400."""
        assert embeds.info("x", url="").url is None

    def test_a_thumbnail_and_an_image_are_both_settable(self):
        embed = embeds.info("x", thumbnail="http://thumb", image="http://img")

        assert embed.thumbnail.url == "http://thumb"
        assert embed.image.url == "http://img"

    def test_an_author_can_be_set_without_an_icon(self):
        assert embeds.info("x", author=("Someone",)).author.name == "Someone"

    def test_it_returns_a_real_embed(self):
        assert isinstance(embeds.info("x"), discord.Embed)
