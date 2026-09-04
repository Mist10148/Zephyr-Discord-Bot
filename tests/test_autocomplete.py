"""Autocomplete on the commands that took free text.

Every one of these took a raw string, so the information needed to type it
correctly -- what the search will find, how the geocoder spells the city, which
playlists exist -- was only available *after* getting it wrong.

The hard constraint is Discord's 3-second ceiling: a slower callback shows the
user nothing at all, with no indication why. Hence the cache and the timeout,
which are what most of this file is about.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.utils import autocomplete
from zephyr.utils.autocomplete import MAX_CHOICES, cached, clear_cache, truncate


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _interaction(user_id=900000000000000001, guild_id=7):
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    if guild_id:
        interaction.guild = MagicMock()
        interaction.guild.id = guild_id
    else:
        interaction.guild = None
    return interaction


class TestTheCache:
    @pytest.mark.asyncio
    async def test_one_term_costs_one_lookup(self):
        """A person typing "manila" fires six autocompletes. Without this that
        is six upstream calls for five prefixes nobody submitted."""
        calls = []

        async def loader():
            calls.append(1)
            return ["result"]

        for _ in range(6):
            assert await cached("ns", "manila", loader) == ["result"]
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_different_terms_are_different_entries(self):
        calls = []

        async def loader():
            calls.append(1)
            return []

        await cached("ns", "man", loader)
        await cached("ns", "manila", loader)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_namespaces_do_not_collide(self):
        await cached("a", "term", AsyncMock(return_value=["a"]))
        assert await cached("b", "term", AsyncMock(return_value=["b"])) == ["b"]

    @pytest.mark.asyncio
    async def test_an_entry_expires(self, monkeypatch):
        await cached("ns", "term", AsyncMock(return_value=["first"]))
        # Advance past the TTL.
        base = autocomplete.time.monotonic()
        monkeypatch.setattr(autocomplete.time, "monotonic", lambda: base + autocomplete.CACHE_TTL_SECONDS + 1)
        assert await cached("ns", "term", AsyncMock(return_value=["second"])) == ["second"]

    @pytest.mark.asyncio
    async def test_it_is_bounded(self):
        """A spelling-mistake storm must not grow it without limit."""
        for index in range(autocomplete.CACHE_MAX_ENTRIES + 40):
            await cached("ns", f"term{index}", AsyncMock(return_value=[]))
        assert len(autocomplete._cache) <= autocomplete.CACHE_MAX_ENTRIES


class TestFailureIsNotSilence:
    @pytest.mark.asyncio
    async def test_a_slow_lookup_returns_the_default_rather_than_hanging(self, monkeypatch, caplog):
        """Past 3s Discord shows nothing at all, so an empty list -- which
        renders as "no options" -- is strictly better than a hang."""
        monkeypatch.setattr(autocomplete, "LOOKUP_TIMEOUT_SECONDS", 0.02)

        async def slow():
            await asyncio.sleep(1)
            return ["never"]

        with caplog.at_level("INFO", logger="zephyr.utils.autocomplete"):
            assert await cached("ns", "term", slow, default=[]) == []
        assert "exceeded" in caplog.text

    @pytest.mark.asyncio
    async def test_a_raising_lookup_returns_the_default(self, caplog):
        """An autocomplete that raises shows the user nothing and gives no clue
        why. The command still accepts free text, so degrading is safe."""
        async def boom():
            raise RuntimeError("upstream is down")

        with caplog.at_level("WARNING", logger="zephyr.utils.autocomplete"):
            assert await cached("ns", "term", boom, default=[]) == []
        assert "failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_failure_is_not_cached(self):
        """Otherwise one blip would suppress suggestions for the whole TTL."""
        attempts = []

        async def flaky():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("blip")
            return ["ok"]

        assert await cached("ns", "term", flaky, default=[]) == []
        assert await cached("ns", "term", flaky, default=[]) == ["ok"]


class TestTruncate:
    def test_it_respects_discords_hundred_character_limit(self):
        # Discord rejects a longer choice name outright rather than trimming it.
        assert len(truncate("x" * 300)) == 100

    def test_it_collapses_whitespace(self):
        assert truncate("a\n  b\tc") == "a b c"


class TestCityAutocomplete:
    @pytest.mark.asyncio
    async def test_it_offers_the_resolved_name_with_its_region(self, monkeypatch):
        """There are four Manilas in the geocoder's results and eight
        Springfields; a country alone does not separate them."""
        from zephyr.cogs.weather import WeatherCog

        cog = WeatherCog.__new__(WeatherCog)
        monkeypatch.setattr(
            "zephyr.cogs.weather.geocode_search",
            lambda term, count: [
                {"name": "Manila", "admin1": "Metro Manila", "country": "Philippines"},
                {"name": "Manila", "admin1": "Utah", "country": "United States"},
            ],
        )
        choices = await cog._city_autocomplete(_interaction(), "manila")

        assert [choice.name for choice in choices] == [
            "Manila, Metro Manila, Philippines",
            "Manila, Utah, United States",
        ]
        # The *value* is the plain name, because that is what these commands
        # re-geocode -- a label with the region in it would not resolve.
        assert {choice.value for choice in choices} == {"Manila"}

    @pytest.mark.asyncio
    async def test_one_letter_is_not_worth_a_lookup(self, monkeypatch):
        from zephyr.cogs.weather import WeatherCog

        cog = WeatherCog.__new__(WeatherCog)
        called = []
        monkeypatch.setattr("zephyr.cogs.weather.geocode_search", lambda *a: called.append(1) or [])
        assert await cog._city_autocomplete(_interaction(), "M") == []
        assert called == []

    @pytest.mark.asyncio
    async def test_it_caps_at_discords_limit(self, monkeypatch):
        from zephyr.cogs.weather import WeatherCog

        cog = WeatherCog.__new__(WeatherCog)
        monkeypatch.setattr(
            "zephyr.cogs.weather.geocode_search",
            lambda term, count: [{"name": f"City{index}", "country": "X"} for index in range(60)],
        )
        assert len(await cog._city_autocomplete(_interaction(), "city")) == MAX_CHOICES


class TestSearchAutocomplete:
    def _cog(self):
        from zephyr.cogs.music import MusicCog

        cog = MusicCog.__new__(MusicCog)
        cog.bot = MagicMock()
        cog.bot.loop = None
        return cog

    @pytest.mark.asyncio
    async def test_it_offers_the_url_so_play_does_not_search_twice(self, monkeypatch):
        track = MagicMock()
        track.title = "Bohemian Rhapsody"
        track.uploader = "Queen"
        track.url = "https://y.tld/bohemian"
        monkeypatch.setattr(
            "zephyr.cogs.music.YTDLSource.search_tracks", AsyncMock(return_value=[track])
        )
        choices = await self._cog()._search_autocomplete(_interaction(), "bohemian")

        assert choices[0].name == "Bohemian Rhapsody · Queen"
        assert choices[0].value == "https://y.tld/bohemian"

    @pytest.mark.asyncio
    async def test_a_pasted_url_is_not_searched(self, monkeypatch):
        """There is nothing to search for, and offering to search a URL the
        user already pasted is noise."""
        called = []
        monkeypatch.setattr(
            "zephyr.cogs.music.YTDLSource.search_tracks",
            AsyncMock(side_effect=lambda *a, **k: called.append(1) or []),
        )
        assert await self._cog()._search_autocomplete(_interaction(), "https://y.tld/x") == []
        assert called == []

    @pytest.mark.asyncio
    async def test_two_letters_are_not_worth_a_lookup(self, monkeypatch):
        """A one- or two-letter YouTube search is a random sample."""
        called = []
        monkeypatch.setattr(
            "zephyr.cogs.music.YTDLSource.search_tracks",
            AsyncMock(side_effect=lambda *a, **k: called.append(1) or []),
        )
        assert await self._cog()._search_autocomplete(_interaction(), "bo") == []
        assert called == []


class TestPlaylistAutocomplete:
    def _cog(self):
        from zephyr.cogs.music import MusicCog

        return MusicCog.__new__(MusicCog)

    @pytest.mark.asyncio
    async def test_it_lists_the_callers_own_playlists(self, monkeypatch):
        """There was no way to see the names without running /playlists first
        and reading them back."""
        monkeypatch.setattr(
            "zephyr.cogs.music.list_playlists",
            lambda owner_id, guild_id=None: [
                {"name": "Focus", "track_count": 12},
                {"name": "Road trip", "track_count": 40},
            ],
        )
        choices = await self._cog()._playlist_autocomplete(_interaction(), "")
        assert [choice.value for choice in choices] == ["Focus", "Road trip"]
        assert "12 track(s)" in choices[0].name

    @pytest.mark.asyncio
    async def test_it_filters_locally_rather_than_refetching(self, monkeypatch):
        """The list is small, so it is fetched once per user and filtered in
        memory instead of once per keystroke."""
        calls = []
        monkeypatch.setattr(
            "zephyr.cogs.music.list_playlists",
            lambda owner_id, guild_id=None: calls.append(1) or [
                {"name": "Focus", "track_count": 1},
                {"name": "Road trip", "track_count": 2},
            ],
        )
        cog = self._cog()
        assert len(await cog._playlist_autocomplete(_interaction(), "")) == 2
        assert [c.value for c in await cog._playlist_autocomplete(_interaction(), "road")] == ["Road trip"]
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_one_users_playlists_are_not_offered_to_another(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            "zephyr.cogs.music.list_playlists",
            lambda owner_id, guild_id=None: seen.append(owner_id) or [],
        )
        cog = self._cog()
        await cog._playlist_autocomplete(_interaction(user_id=1), "")
        await cog._playlist_autocomplete(_interaction(user_id=2), "")
        assert seen == ["1", "2"]
