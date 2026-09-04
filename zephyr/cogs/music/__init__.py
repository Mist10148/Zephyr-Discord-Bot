"""The music cog: the command surface, the listeners and the bridge handlers.

The engine moved to ``zephyr/music/`` -- sources, queue, state and views -- and
is re-exported here. That re-export is not tidiness: twenty-odd tests import
these names from ``zephyr.cogs.music``, and three patch
``zephyr.cogs.music.discord.FFmpegPCMAudio``, so ``discord`` itself has to
remain an attribute of this module.

Two module-level names stay defined *here* rather than moving, and the reason is
worth knowing before moving them later: a module-level name is read through its
own module's globals, so patching a re-export would not affect a reader that had
moved elsewhere. ``EMPTY_CHANNEL_GRACE_SECONDS`` and ``list_playlists`` are both
patched by name in tests, and both readers are cog methods -- the voice-state
listener and the playlist autocomplete -- so this is where they belong anyway.
"""

import asyncio
import functools
import math
import random
import re
import time

import aiohttp
import discord
import requests
import spotipy
from async_timeout import timeout
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, Select, View

from zephyr.config import REDIS_URL, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
from zephyr.core.errors import Refused
from zephyr.core.ffmpeg import FFMPEG_PATH
from zephyr.core.logging import get_logger
from zephyr.db.guild_settings import (
    read_dj_roles,
    read_music_policies,
    write_guild_settings,
)
from zephyr.db.playlists import (
    PlaylistError,
    delete_playlist,
    find_playlist,
    get_playlist,
    list_playlists,
    save_playlist,
)
from zephyr.music.common import (
    AUTOPLAY_ADD,
    AUTOPLAY_FETCH,
    AUTOPLAY_MEMORY,
    _parse_spotify_id,
    _resolve_spotify_short_link,
    DEFAULT_SKIP_RATIO,
    DJ_EXEMPT_COMMANDS,
    MAX_SKIP_RATIO,
    MIN_SKIP_RATIO,
    NOW_PLAYING_REFRESH_SECONDS,
    SNAPSHOT_QUEUE_LIMIT,
    VoiceError,
    YTDLError,
    _apply_effects,
    _coerce_float,
    _format_duration,
    _is_audio_file_url,
    _is_spotify_playlist_input,
    _is_spotify_url,
    _is_url,
    _is_youtube_playlist,
    _is_youtube_url,
    _sanitize_search,
    _video_id,
)
from zephyr.music.queue import SongQueue
from zephyr.music.sources import Track, YTDLSource
from zephyr.music.state import VoiceState
from zephyr.music.views import NowPlayingView, QueueView, _QueueIndexModal
from zephyr.services import bridge
from spotipy.oauth2 import SpotifyClientCredentials

from zephyr.services.spotify import (
    is_spotify_url,
    parse_spotify_id,
    resolve_short_link,
)
from zephyr.utils import embeds
from zephyr.utils.autocomplete import MAX_CHOICES, cached, truncate
from zephyr.utils.time_utils import _format_timestamp, _parse_user_time

log = get_logger(__name__)

# Read by `on_voice_state_update` below, and patched by name in
# tests/test_music_voice_lifecycle.py -- see the module docstring.
EMPTY_CHANNEL_GRACE_SECONDS = 60

# The re-export surface, stated rather than left as a list of imports a linter
# calls unused. Every name here is imported by something outside this package --
# a test, or `zephyr/client.py` -- and removing one is a breaking change even
# though nothing in this file uses it.
__all__ = [
    "MusicCog",
    "setup",
    # The engine, re-exported so `from zephyr.cogs.music import X` keeps working.
    "NowPlayingView",
    "QueueView",
    "SongQueue",
    "Track",
    "VoiceError",
    "VoiceState",
    "YTDLError",
    "YTDLSource",
    "_QueueIndexModal",
    # Patched by name in tests, so they must resolve *here*.
    "EMPTY_CHANNEL_GRACE_SECONDS",
    "list_playlists",
    # Three tests patch `zephyr.cogs.music.discord.FFmpegPCMAudio`, which needs
    # `discord` to be an attribute of this module.
    "discord",
    # Read by tests/test_dj_controls.py.
    "DEFAULT_SKIP_RATIO",
    "DJ_EXEMPT_COMMANDS",
    "MAX_SKIP_RATIO",
    "MIN_SKIP_RATIO",
]


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}
        self._voice_connect_locks = {}
        # guild id -> DJ role id.  Absent means "no DJ role configured", which is
        # a different rule rather than a stricter one; see _authorize.
        self._dj_role_ids: dict[int, str] = {}
        # Guild id -> {dj_only, vote_skip_ratio, always_on, always_on_channel_id}.
        # Cached beside the DJ roles and refreshed by the same loop and the same
        # bridge action, because they are consulted on the same hot path.
        self._music_policy: dict[int, dict] = {}
        # guild_id -> the pending "everyone left" timer, so a rejoin can cancel it.
        self._empty_timers: dict[int, asyncio.Task] = {}
        creds = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET)
        self.sp = spotipy.Spotify(client_credentials_manager=creds)

    def _get_voice_lock(self, guild_id: int) -> asyncio.Lock:
        return self._voice_connect_locks.setdefault(guild_id, asyncio.Lock())

    def peek_voice_state(self, guild_id: int) -> VoiceState | None:
        """The live state for a guild, or None. Never creates one.

        Callers with no Context (the Redis bridge) need to be able to answer
        "nothing is playing" rather than silently spinning up a state.
        """
        state = self.voice_states.get(int(guild_id))
        return state if state and state.exists else None

    def ensure_voice_state(self, guild_id: int, *, channel_id: int | None = None) -> VoiceState:
        """Get or create the state for a guild, refreshing the sticky channel."""
        state = self.voice_states.get(int(guild_id))
        if not state or not state.exists:
            state = VoiceState(self.bot, guild_id, channel_id=channel_id)
            state.on_change = self.publish_state
            state.np_view_factory = functools.partial(NowPlayingView, self, int(guild_id))
            self.apply_policy(state)
            self.voice_states[int(guild_id)] = state
        elif channel_id is not None:
            state.np_channel_id = channel_id
        return state

    async def teardown_voice_state(self, guild_id: int) -> None:
        """Stop playback and forget the guild's state entirely.

        `stop()` alone leaves the entry in `self.voice_states`. That mattered
        because three callers popped the dict by hand -- /leave, TTS's
        /disconnect, and cog_unload -- and every other path did not, so the
        dict accumulated dead states for the life of the process. One method
        now owns both halves, and the snapshot is cleared so the dashboard stops
        showing a player for a guild the bot has left.
        """
        state = self.voice_states.pop(int(guild_id), None)
        if state is None:
            return
        try:
            await state.stop()
        except Exception:
            log.exception("Could not stop the player for %s during teardown", guild_id)
        await self._clear_snapshot(int(guild_id))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """React to the channel emptying, and to being removed from it.

        There was no listener of any kind in this package. The 180s idle timeout
        is armed by `async_timeout` around the *queue read* inside
        audio_player_task, so it only ever starts once the queue runs dry -- a
        long track playing to an empty channel never arms it at all, and the bot
        would keep streaming to nobody until the track ended.

        Two cases:

        * The bot itself left or was moved. `after.channel is None` means a
          moderator disconnected it, and without this the state stayed in the
          dict with `exists` True, so the dashboard kept showing a player and
          the next /play reused a state whose voice client was gone.
        * The last human left. Pause immediately (there is no point decoding
          audio for an empty room) and start a short grace timer rather than
          leaving at once, because the common case is somebody hopping between
          channels for a few seconds.
        """
        guild = member.guild
        state = self.peek_voice_state(guild.id)
        if state is None:
            return

        if member.id == self.bot.user.id:
            if after.channel is None:
                log.info("Disconnected from voice in %s; tearing down the player", guild.id)
                await self.teardown_voice_state(guild.id)
            return

        channel = state.voice.channel if state.voice else None
        if channel is None:
            return

        # Only movements involving the bot's own channel can change the answer.
        if before.channel != channel and after.channel != channel:
            return

        listeners = [m for m in channel.members if not m.bot]
        if listeners:
            self._cancel_empty_timer(guild.id)
            return

        if state.voice.is_playing():
            state.voice.pause()
        self._start_empty_timer(guild.id, channel)

    def _cancel_empty_timer(self, guild_id: int) -> None:
        timer = self._empty_timers.pop(int(guild_id), None)
        if timer and not timer.done():
            timer.cancel()

    def _start_empty_timer(self, guild_id: int, channel) -> None:
        self._cancel_empty_timer(guild_id)
        self._empty_timers[int(guild_id)] = asyncio.create_task(self._leave_if_still_empty(guild_id, channel))

    async def _leave_if_still_empty(self, guild_id: int, channel) -> None:
        """Wait out the grace period, then leave if nobody came back."""
        try:
            await asyncio.sleep(EMPTY_CHANNEL_GRACE_SECONDS)
            state = self.peek_voice_state(guild_id)
            if state is None or not state.voice:
                return
            if any(not m.bot for m in state.voice.channel.members):
                return
            # Announced, unlike the idle timeout, which says nothing at all --
            # coming back to a stopped player with no explanation reads as a
            # crash.
            await state._notify(embed=embeds.info(f'👋 Left {channel} — everyone had gone.'))
            await self.teardown_voice_state(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not leave the empty channel in %s", guild_id)
        finally:
            self._empty_timers.pop(int(guild_id), None)

    def get_voice_state(self, ctx: commands.Context):
        """Context-flavoured wrapper, so the existing command call sites are unchanged."""
        channel_id = ctx.channel.id if getattr(ctx, 'channel', None) else None
        return self.ensure_voice_state(ctx.guild.id, channel_id=channel_id)

    def cog_unload(self):
        self._snapshot_loop.cancel()
        self._settings_loop.cancel()
        self._restore_loop.cancel()
        self._now_playing_loop.cancel()
        for timer in list(self._empty_timers.values()):
            if not timer.done():
                timer.cancel()
        # One task per guild rather than two loops doing half the job each:
        # teardown_voice_state stops the player, forgets the state and clears
        # the snapshot together.
        for guild_id in list(self.voice_states):
            self.bot.loop.create_task(self.teardown_voice_state(guild_id))

    # ---------------- Web bridge ----------------

    async def cog_load(self):
        # The DJ role is read whether or not the dashboard exists -- it governs
        # the in-Discord now-playing buttons too.  Only the snapshot publishing
        # is conditional on there being somewhere to publish to.
        self._settings_loop.start()
        self._now_playing_loop.start()
        self._restore_loop.start()
        if REDIS_URL:
            self._snapshot_loop.start()

    async def _clear_snapshot(self, guild_id: int) -> None:
        if not REDIS_URL:
            return
        try:
            await asyncio.to_thread(bridge.clear_player_snapshot, guild_id)
        except Exception as exc:
            log.exception("Could not clear the player snapshot for %s", guild_id)

    async def publish_state(self, guild_id: int) -> None:
        """Push one guild's snapshot to Redis now.

        Called after every bridge action and at each playback transition, so a
        change made from the browser is visible on the next poll rather than on
        the next tick of the periodic loop.  Off the event loop via to_thread
        because redis-py is synchronous.  Failures are logged and swallowed: the
        dashboard going stale must never break playback.
        """
        if not REDIS_URL:
            return
        state = self.peek_voice_state(guild_id)
        if state is None:
            await self._clear_snapshot(guild_id)
            return
        try:
            await asyncio.to_thread(bridge.write_player_snapshot, guild_id, state.snapshot())
        except Exception as exc:
            log.exception("Could not publish the player snapshot for %s", guild_id)

    @tasks.loop(seconds=3)
    async def _snapshot_loop(self):
        """Keep the snapshot fresh for changes made in Discord.

        Three seconds to match the dashboard's poll interval; a slower loop would
        make a /volume typed in Discord take up to two polls to appear.  Only
        guilds with a live state are published -- an idle bot writes nothing.
        """
        for guild_id in list(self.voice_states):
            if self.peek_voice_state(guild_id) is not None:
                await self.publish_state(guild_id)

    @_snapshot_loop.before_loop
    async def _before_snapshot_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=NOW_PLAYING_REFRESH_SECONDS)
    async def _now_playing_loop(self):
        """Advance the progress bar on every live now-playing message.

        Ten seconds, not one: each tick is a message edit, and Discord's
        per-channel edit budget is small enough that a per-second bar would spend
        it all on a cosmetic detail.  Paused players are skipped -- the bar is
        not moving, so an edit would rewrite the message with identical content.
        """
        for guild_id in list(self.voice_states):
            state = self.peek_voice_state(guild_id)
            if state and state.np_message and state.voice and state.voice.is_playing():
                await state.refresh_now_playing()

    @_now_playing_loop.before_loop
    async def _before_now_playing_loop(self):
        await self.bot.wait_until_ready()

    def bridge_actions(self) -> dict:
        """The actions this cog serves on the web bridge.

        Discovered by ``ZephyrBot`` rather than registered with it, so a cog that
        wants a bridge endpoint only has to grow this method.

        Every mutating action republishes the snapshot before answering, so the
        browser's next poll shows the result of its own click instead of state
        from up to one loop tick ago.
        """
        mutating = {
            'player.pause': self._bridge_pause,
            'player.resume': self._bridge_resume,
            'player.skip': self._bridge_skip,
            'player.stop': self._bridge_stop,
            'player.clear': self._bridge_clear,
            'player.shuffle': self._bridge_shuffle,
            'player.seek': self._bridge_seek,
            'player.volume': self._bridge_volume,
            'player.loop': self._bridge_loop,
            'player.jump': self._bridge_jump,
            'player.remove': self._bridge_remove,
            'player.move': self._bridge_move,
            'player.play': self._bridge_play,
            'player.effects': self._bridge_effects,
            'player.autoplay': self._bridge_autoplay,
            'playlist.load': self._bridge_playlist_load,
        }
        actions = {name: self._publishing(handler) for name, handler in mutating.items()}
        actions['player.state'] = self._bridge_state
        actions['settings.reload'] = self._bridge_reload_settings
        return actions

    def _publishing(self, handler):
        async def wrapper(guild, actor_id, args):
            result = await handler(guild, actor_id, args)
            await self.publish_state(guild.id)
            return result

        return wrapper

    # The bot is the authority on permissions (see zephyr/services/bridge.py).
    # Everything below re-derives them from the live Discord cache; whatever the
    # web tier believed when it rendered a button is irrelevant here.

    def _authorize(self, guild: discord.Guild, actor_id, *, require_voice: bool = True) -> discord.Member:
        """Resolve the actor and confirm they may drive this guild's player.

        The rule is one sentence so it can be explained: **if a DJ role is
        configured you need it (or Manage Server); if one is not, you need to be
        in the voice channel the bot is in.**  Anything that changes what other
        people are hearing goes through here.
        """
        if guild is None:
            raise VoiceError("That server is not available.")
        try:
            member = guild.get_member(int(actor_id))
        except (TypeError, ValueError):
            member = None
        if member is None:
            # Not cached is treated as not present.  Fetching would let an
            # unauthenticated id force an API call per command.
            raise VoiceError("You are not a member of that server.")
        if member.guild_permissions.manage_guild:
            return member

        dj_role_id = self._dj_role_ids.get(int(guild.id))
        if dj_role_id:
            if any(str(role.id) == str(dj_role_id) for role in member.roles):
                return member
            raise VoiceError("You need the DJ role to control the player.")

        if require_voice:
            state = self.peek_voice_state(guild.id)
            bot_channel = state.voice.channel if state and state.voice and state.voice.is_connected() else None
            listening = member.voice.channel if member.voice else None
            if bot_channel is not None and listening != bot_channel:
                raise VoiceError("Join the voice channel Zephyr is in to control the player.")
            if bot_channel is None and listening is None:
                raise VoiceError("You are not connected to a voice channel.")
        return member

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """The DJ lock, for the Discord surface.

        This is the gap 15.4 closes, and it is worth naming: ``_authorize``
        gated the *bridge* -- every dashboard button and every now-playing
        button -- while the slash commands went through ``get_voice_state`` with
        no authorization at all. So a server that had configured a DJ role got a
        DJ-gated dashboard and an ungated ``/stop``, which is the opposite of a
        lock.

        ``dj_only`` is off by default, so this changes nothing for a server that
        has not asked for it. When it is on: Manage Server always passes, the DJ
        role passes if one is configured, and otherwise only Manage Server does
        -- "DJ-only with no DJ role" is a deliberate reading of "lock the
        player", not an oversight.
        """
        command = interaction.command
        if command is None or command.name in DJ_EXEMPT_COMMANDS:
            return True
        if interaction.guild is None:
            return True
        if not self.policy_for(interaction.guild.id).get("dj_only"):
            return True

        member = interaction.user
        if getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild:
            return True
        dj_role_id = self._dj_role_ids.get(int(interaction.guild.id))
        if dj_role_id:
            if any(str(role.id) == str(dj_role_id) for role in getattr(member, "roles", [])):
                return True
            raise Refused("DJ-only mode is on. You need the DJ role to control the player.")
        raise Refused(
            "DJ-only mode is on and no DJ role is set, so only Manage Server can "
            "control the player."
        )

    def policy_for(self, guild_id) -> dict:
        return self._music_policy.get(int(guild_id)) or {}

    def apply_policy(self, state: VoiceState) -> None:
        """Push the guild's stored policy onto a freshly created state.

        Applied at creation rather than read per use so ``_skip_threshold``
        needs nothing but the state, and so 24/7 survives a restart: the flag
        used to live only in memory, which meant "24/7 mode" lasted until the
        next deploy.
        """
        policy = self.policy_for(state.guild_id)
        ratio = policy.get("vote_skip_ratio")
        if ratio:
            state.skip_ratio = max(MIN_SKIP_RATIO, min(MAX_SKIP_RATIO, float(ratio)))
        # Assigned, not or-ed: the stored value is the truth, so turning 24/7
        # off in the dashboard has to be able to turn it off on a live state
        # too. A session-only toggle that failed to save is reverted by the next
        # refresh, which is the correct outcome -- it was never saved.
        state._247_enabled = bool(policy.get("always_on"))

    async def reload_music_policies(self) -> None:
        """Refresh the policy cache, and push it onto live states.

        Live states too, because the alternative is a dashboard change to the
        skip ratio that takes effect only after the player is torn down -- which
        reads as the save not having worked.
        """
        try:
            self._music_policy = {
                int(guild_id): policy
                for guild_id, policy in (await asyncio.to_thread(read_music_policies)).items()
            }
        except Exception:
            log.exception("Could not read music policies")
            return
        for state in list(self.voice_states.values()):
            self.apply_policy(state)

    async def reload_dj_roles(self) -> None:
        """Refresh the DJ-role cache from the database.

        Cached rather than read per command: ``_authorize`` runs on every button
        press and every bridge action, and a database round trip on that path
        would be paid constantly to answer a question that changes roughly never.
        The dashboard calls ``settings.reload`` after a save, so the cache is
        corrected immediately rather than at the next slow tick.
        """
        try:
            self._dj_role_ids = {
                int(guild_id): role_id
                for guild_id, role_id in (await asyncio.to_thread(read_dj_roles)).items()
            }
        except Exception as exc:
            log.exception("Could not read DJ roles")

    @tasks.loop(count=1)
    async def _restore_loop(self):
        """Rejoin the channels 24/7 was left on, once, at startup.

        The point of 24/7 is that the bot stays; a flag that only survived until
        the next deploy meant it did not. Each guild is contained separately for
        the reason every loop in this codebase contains per item: one guild whose
        stored channel has been deleted must not stop the rest from rejoining.
        """
        await self.reload_music_policies()
        for guild_id, policy in list(self._music_policy.items()):
            channel_id = policy.get("always_on_channel_id")
            if not policy.get("always_on") or not channel_id:
                continue
            try:
                guild = self.bot.get_guild(int(guild_id))
                if guild is None:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not isinstance(channel, discord.VoiceChannel):
                    log.warning("24/7 channel %s is gone in guild %s", channel_id, guild_id)
                    continue
                state = self.ensure_voice_state(guild.id)
                if state.voice and state.voice.is_connected():
                    continue
                state.voice = await channel.connect()
                # The player task waits on an empty queue with no timeout while
                # _247_enabled is set, which is exactly the intended idle state.
                state.start_player()
                log.info("Rejoined %s in guild %s for 24/7", channel_id, guild_id)
            except Exception:
                log.exception("Could not rejoin the 24/7 channel for guild %s", guild_id)

    @_restore_loop.before_loop
    async def _before_restore_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def _settings_loop(self):
        await self.reload_dj_roles()
        await self.reload_music_policies()

    @_settings_loop.before_loop
    async def _before_settings_loop(self):
        await self.bot.wait_until_ready()

    async def _bridge_reload_settings(self, guild, actor_id, args):
        await self.reload_dj_roles()
        await self.reload_music_policies()
        # The prefix and the TTS language are cached on the bot and the TTS cog
        # respectively, both on slow loops. Refreshing them here means a
        # dashboard save takes effect immediately rather than up to ten minutes
        # later, which would read as the save not having worked.
        reloader = getattr(self.bot, 'reload_prefixes', None)
        if reloader is not None:
            await reloader()
        tts_cog = self.bot.get_cog('TTSCog')
        if tts_cog is not None:
            await tts_cog.reload_languages()
        return {'reloaded': True}

    def _require_state(self, guild_id) -> VoiceState:
        state = self.peek_voice_state(guild_id)
        if state is None or not state.voice or not state.voice.is_connected():
            raise VoiceError("Zephyr is not connected to a voice channel.")
        return state

    async def _bridge_state(self, guild, actor_id, args):
        state = self.peek_voice_state(guild.id) if guild else None
        return state.snapshot() if state else {'guild_id': str(guild.id), 'connected': False}

    async def _bridge_pause(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.voice.is_playing():
            raise VoiceError("Nothing is playing.")
        state.voice.pause()
        state._current_position = state.elapsed
        state._current_start_time = None
        return {'paused': True}

    async def _bridge_resume(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.voice.is_paused():
            raise VoiceError("Nothing is paused.")
        state.voice.resume()
        state._current_start_time = time.time()
        return {'paused': False}

    async def _bridge_skip(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.is_playing:
            raise VoiceError("Nothing is playing.")
        skipped = state.current.title
        state.skip()
        return {'skipped': skipped}

    async def _bridge_stop(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        state.songs.clear()
        if state.is_playing:
            state.voice.stop()
        return {'stopped': True}

    async def _bridge_clear(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        removed = len(state.songs)
        state.songs.clear()
        return {'removed': removed}

    async def _bridge_shuffle(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        state.songs.shuffle()
        return {'queue_length': len(state.songs)}

    async def _bridge_seek(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        if not state.is_playing:
            raise VoiceError("Nothing is playing.")
        position = _coerce_float(args.get('position'), 'position')
        if state.current.duration_seconds and position > state.current.duration_seconds:
            raise VoiceError("That position is beyond the song length.")
        state._current_position = max(0.0, position)
        state._current_start_time = time.time()
        await state.restart_current(preserve_position=True)
        return {'position_s': state._current_position}

    async def _bridge_volume(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        volume = int(_coerce_float(args.get('volume'), 'volume'))
        if not 0 <= volume <= 1000:
            raise VoiceError("Volume must be between 0 and 1000.")
        state.volume = volume / 100
        return {'volume': volume}

    async def _bridge_loop(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        mode = str(args.get('mode') or '').lower()
        if mode not in {'off', 'track', 'queue'}:
            raise VoiceError("Loop mode must be off, track or queue.")
        state.loop = mode
        return {'loop': mode}

    async def _bridge_jump(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        index = int(_coerce_float(args.get('index'), 'index'))
        if not 0 <= index < len(state.songs):
            raise VoiceError("That track is not in the queue.")
        state.songs.move(index, 0)
        state.skip()
        return {'jumped_to': index}

    async def _bridge_remove(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        index = int(_coerce_float(args.get('index'), 'index'))
        if not 0 <= index < len(state.songs):
            raise VoiceError("That track is not in the queue.")
        removed = state.songs[index].title
        state.songs.remove(index)
        return {'removed': removed}

    async def _bridge_move(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        source = int(_coerce_float(args.get('from'), 'from'))
        target = int(_coerce_float(args.get('to'), 'to'))
        try:
            state.songs.move(source, target)
        except IndexError:
            raise VoiceError("That track is not in the queue.") from None
        return {'from': source, 'to': target}

    async def _bridge_effects(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        _apply_effects(state, args)
        if state.is_playing:
            await state.restart_current(preserve_position=True)
        return {'effects': state.effects()}

    async def _bridge_play(self, guild, actor_id, args):
        """Enqueue from the browser.

        The actor's own voice channel is where the bot goes -- deliberately not a
        channel id from the request, which would let anyone with a session pull
        the bot into a channel they cannot see.
        """
        member = self._authorize(guild, actor_id)
        query = str(args.get('query') or '').strip()
        if not query:
            raise VoiceError("Nothing to play.")
        destination = member.voice.channel if member.voice else None
        state = self.ensure_voice_state(guild.id)
        if state.voice and state.voice.is_connected():
            destination = state.voice.channel
        if destination is None:
            raise VoiceError("Join a voice channel first.")

        async with self._get_voice_lock(guild.id):
            if not state.voice or not state.voice.is_connected():
                state.voice = await destination.connect(self_deaf=True)
        state.start_player()

        tracks = await YTDLSource.resolve_tracks(
            query, requester_id=member.id, requester_mention=member.mention, loop=self.bot.loop)
        if not tracks:
            raise VoiceError("Nothing found for that query.")
        if str(args.get('mode')) == 'next':
            for track in reversed(tracks):
                state.songs.add_to_front(track)
        else:
            for track in tracks:
                state.songs.put_nowait(track)
        return {'added': len(tracks), 'title': tracks[0].title}

    @staticmethod
    async def _interaction_ctx(interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        return ctx

    def _require_voice(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            raise VoiceError("You are not connected to any voice channel.")

    # ---------------- Voice Connection ----------------

    @app_commands.command(name='join', description='Joins your voice channel.')
    async def join(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You are not connected to a voice channel.", ephemeral=True)
            return

        await interaction.response.defer()
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        # /join deliberately does NOT enable 24/7: it used to set _247_enabled here,
        # which disabled the idle timeout permanently for any guild that ever ran the
        # command, so the bot sat in an empty channel forever.  Use /247 for that.
        destination = interaction.user.voice.channel
        lock = self._get_voice_lock(interaction.guild.id)

        async with lock:
            vc = interaction.guild.voice_client

            if vc and not vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                try:
                    interaction.guild.voice_client = None
                except Exception:
                    pass
                vc = None
                await asyncio.sleep(0.5)

            if vc and vc.is_connected():
                if vc.channel.id == destination.id:
                    state.voice = vc
                    await interaction.followup.send(embed=embeds.success(f'✅ Already connected to {destination}'))
                    return
                try:
                    await vc.move_to(destination)
                    state.voice = vc
                    await interaction.followup.send(embed=embeds.success(f'➡️ Moved to {destination}'))
                    return
                except Exception as e:
                    log.warning("move_to failed, trying a fresh connect: %s", e)
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)

            try:
                state.voice = await destination.connect(self_deaf=True, timeout=30.0, reconnect=True)
                await interaction.followup.send(embed=embeds.success(f'🔊 Joined {destination}'))
            except Exception as e:
                log.exception("Could not join a voice channel")
                await interaction.followup.send(embed=embeds.error(f'❌ Failed to join {destination}: {e}'), ephemeral=True)

    @app_commands.command(name='summon', description='Summons the bot to a voice channel.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def summon(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not channel and not interaction.user.voice:
            await interaction.response.send_message("You are neither connected to a voice channel nor specified a channel to join.", ephemeral=True)
            return
        destination = channel or interaction.user.voice.channel
        await interaction.response.defer()
        lock = self._get_voice_lock(interaction.guild.id)
        async with lock:
            if state.voice:
                await state.voice.move_to(destination)
                await interaction.followup.send(embed=embeds.success(f'➡️ Moved to {destination}'))
            else:
                state.voice = await destination.connect(self_deaf=True)
                await interaction.followup.send(embed=embeds.success(f'🔊 Joined {destination}'))

    @app_commands.command(name='leave', description='Clears the queue and leaves the voice channel.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leave(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        await self.teardown_voice_state(ctx.guild.id)
        await interaction.response.send_message(embed=embeds.success('👋 Left voice channel.'))

    # ---------------- Playback Control ----------------

    async def _search_autocomplete(self, interaction: discord.Interaction, current: str):
        """What /play will actually find, before committing to it.

        Two things are deliberately *not* suggested. A URL is passed straight
        through -- there is nothing to search for, and offering to "search" a
        URL the user already pasted is noise. And a term under three characters
        is skipped, because a one-letter YouTube search is a random sample.

        Results are cached per term for a few seconds, so composing one query
        costs one upstream call rather than one per keystroke.
        """
        term = current.strip()
        if len(term) < 3 or _is_url(term):
            return []

        async def lookup():
            tracks = await YTDLSource.search_tracks(term, loop=self.bot.loop, max_results=MAX_CHOICES)
            return [
                app_commands.Choice(
                    name=truncate(f'{track.title} · {track.uploader}'),
                    # The URL, not the title: it is unambiguous, and it saves
                    # /play a second search for something already resolved.
                    value=track.url or track.title,
                )
                for track in tracks
                if track.url or track.title
            ]

        return (await cached('music:search', term.lower(), lookup, default=[]))[:MAX_CHOICES]

    async def _playlist_autocomplete(self, interaction: discord.Interaction, current: str):
        """The caller's own playlists. There was no way to see the names without
        running /playlists first and reading them back."""
        term = current.strip().lower()
        guild_id = str(interaction.guild.id) if interaction.guild else None

        async def lookup():
            rows = await asyncio.to_thread(
                list_playlists, str(interaction.user.id), guild_id=guild_id
            )
            return [
                app_commands.Choice(
                    name=truncate(f'{row["name"]} · {row["track_count"]} track(s)'),
                    value=row['name'],
                )
                for row in rows
            ]

        # Keyed on the user rather than the term: the list is small, so it is
        # fetched once and filtered locally instead of per keystroke.
        choices = await cached(f'music:playlists:{interaction.user.id}:{guild_id}', '', lookup, default=[])
        matches = [choice for choice in choices if not term or term in choice.value.lower()]
        return matches[:MAX_CHOICES]

    @app_commands.command(name='play', description='Plays a song from YouTube or Spotify.')
    @app_commands.describe(search="Song name, YouTube/video URL, YouTube playlist URL, or Spotify track/playlist/album URL")
    @app_commands.autocomplete(search=_search_autocomplete)
    async def play(self, interaction: discord.Interaction, search: str):
        await self._play_core(interaction, search, mode='normal')

    @app_commands.command(name='playskip', description='Adds a song and immediately skips to it.')
    @app_commands.describe(search="Song name, YouTube/video URL, YouTube playlist URL, or Spotify track/playlist/album URL")
    async def playskip(self, interaction: discord.Interaction, search: str):
        await self._play_core(interaction, search, mode='skip')

    @app_commands.command(name='playnext', description='Adds a song to the top of the queue.')
    @app_commands.describe(search="Song name, YouTube/video URL, YouTube playlist URL, or Spotify track/playlist/album URL")
    @app_commands.autocomplete(search=_search_autocomplete)
    async def playnext(self, interaction: discord.Interaction, search: str):
        await self._play_core(interaction, search, mode='next')

    async def _play_core(self, interaction: discord.Interaction, search: str, mode: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to play music.", ephemeral=True)
            return

        await interaction.response.defer()
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        lock = self._get_voice_lock(interaction.guild.id)

        async with lock:
            if not state.voice or not state.voice.is_connected():
                vc = interaction.guild.voice_client
                if vc and not vc.is_connected():
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    vc = None
                if vc and vc.is_connected():
                    await vc.move_to(interaction.user.voice.channel)
                    state.voice = vc
                else:
                    state.voice = await interaction.user.voice.channel.connect(self_deaf=True)

        state.start_player()

        async def _edit_status(description: str):
            embed = embeds.info(description)
            try:
                await interaction.edit_original_response(embed=embed)
            except Exception:
                # Fallback if the message has not been sent yet or is unavailable.
                await interaction.followup.send(embed=embed)

        songs = []
        requester_id = interaction.user.id
        requester_mention = interaction.user.mention
        try:
            # No ffmpeg_options here any more: the filter chain is applied when the
            # source is built at play time, so effects toggled while a track sits in
            # the queue now take effect on it rather than being baked in at enqueue.
            search = _sanitize_search(search)
            is_playlist_input = _is_youtube_playlist(search) or _is_spotify_playlist_input(search)

            if _is_spotify_url(search):
                track_ids = await self._get_spotify_tracks(search)
                if not track_ids:
                    await _edit_status('❌ Could not extract track information from the Spotify URL.')
                    return

                if len(track_ids) > 1:
                    await _edit_status(f'🔎 Resolving **{len(track_ids)}** Spotify tracks...')

                last_update = time.time()
                for i, track_id in enumerate(track_ids):
                    try:
                        track_info = await self.bot.loop.run_in_executor(
                            None, functools.partial(self.sp.track, track_id)
                        )
                        youtube_url = await self._search_youtube(track_info)
                        if youtube_url:
                            songs.extend(await YTDLSource.resolve_tracks(
                                youtube_url, requester_id=requester_id, requester_mention=requester_mention,
                                loop=self.bot.loop, max_entries=1))
                    except Exception as e:
                        log.exception("Could not resolve a Spotify track")
                        continue

                    # Throttle progress edits to once per second.
                    if len(track_ids) > 1 and time.time() - last_update >= 1:
                        await _edit_status(f'🔎 Resolved **{len(songs)} / {len(track_ids)}** Spotify tracks...')
                        last_update = time.time()

                for track in songs:
                    track.source = 'spotify'

                if not songs:
                    await _edit_status('❌ No tracks could be processed from Spotify.')
                    return
            else:
                if is_playlist_input:
                    await _edit_status('🔎 Resolving playlist, this may take a moment...')
                songs = await YTDLSource.resolve_tracks(
                    search, requester_id=requester_id, requester_mention=requester_mention, loop=self.bot.loop)

            if not songs:
                await _edit_status('❌ No tracks found.')
                return

            if mode == 'skip':
                for song in reversed(songs):
                    state.songs.add_to_front(song)
                state.skip()
                await _edit_status(f'⏭️ Playing **{songs[0].title}** now.')
            elif mode == 'next':
                for song in reversed(songs):
                    state.songs.add_to_front(song)
                if len(songs) > 1:
                    await _edit_status(f'🎶 Added **{len(songs)}** songs to the top of the queue.')
                else:
                    await _edit_status(f'🎶 Added to top: {songs[0]}')
            else:
                if len(songs) > 1:
                    for song in songs:
                        state.songs.put_nowait(song)
                    await _edit_status(f'🎶 Enqueued **{len(songs)}** songs.')
                else:
                    state.songs.put_nowait(songs[0])
                    await _edit_status(f'🎶 Enqueued {songs[0]}')

        except YTDLError as e:
            await _edit_status(f'❌ Error processing request: {e}')
        except Exception as e:
            log.exception("Could not process a play request")
            await _edit_status(f'❌ Unexpected error: {e}')

    @app_commands.command(name='msearch', description='Search YouTube and pick a track to play.')
    @app_commands.describe(query="What to search for")
    async def msearch(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel to play music.", ephemeral=True)
            return
        await interaction.response.defer()

        try:
            results = await YTDLSource.search_tracks(query, requester_id=interaction.user.id,
                                                     requester_mention=interaction.user.mention,
                                                     loop=self.bot.loop, max_results=10)
        except YTDLError as e:
            await interaction.followup.send(embed=embeds.error(f'❌ {e}'), ephemeral=True)
            return

        if not results:
            await interaction.followup.send(embed=embeds.error('❌ No results found.'), ephemeral=True)
            return

        options = []
        for i, track in enumerate(results[:10], start=1):
            label = f"{i}. {track.title}"[:100]
            description = f"{track.uploader} • {track.duration}"[:100]
            options.append(discord.SelectOption(label=label, description=description, value=str(i - 1)))

        select = Select(placeholder="Choose a track...", options=options)

        async def select_callback(interaction2: discord.Interaction):
            await interaction2.response.defer()
            track = results[int(select.values[0])]
            await self._enqueue_track(interaction2, track)
            await interaction2.followup.send(embed=embeds.success(f'🎶 Selected: {track}'), ephemeral=True)

        select.callback = select_callback
        view = View(timeout=60)
        view.add_item(select)
        await interaction.followup.send("🔎 Search results:", view=view)

    async def _enqueue_track(self, interaction: discord.Interaction, track: Track):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You need to be in a voice channel.", ephemeral=True)
            return
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        lock = self._get_voice_lock(interaction.guild.id)
        async with lock:
            if not state.voice or not state.voice.is_connected():
                vc = interaction.guild.voice_client
                if vc and vc.is_connected():
                    await vc.move_to(interaction.user.voice.channel)
                    state.voice = vc
                else:
                    state.voice = await interaction.user.voice.channel.connect(self_deaf=True)
        state.start_player()
        state.songs.put_nowait(track)

    @app_commands.command(name='now', description='Displays the currently playing song.')
    async def now(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.send_message(embed=state.current.create_embed(elapsed=state.elapsed))

    @app_commands.command(name='np', description='Alias for /now.')
    async def np(self, interaction: discord.Interaction):
        await self.now(interaction)

    @app_commands.command(name='pause', description='Pauses the currently playing song.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pause(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if state.is_playing and state.voice.is_playing():
            state.voice.pause()
            # Keep position so we can resume accurately
            state._current_position = state.elapsed
            state._current_start_time = None
            await interaction.response.send_message(embed=embeds.success('⏸️ Paused.'))
        else:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)

    @app_commands.command(name='resume', description='Resumes a currently paused song.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def resume(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if state.voice and state.voice.is_paused():
            state.voice.resume()
            state._current_start_time = time.time()
            await interaction.response.send_message(embed=embeds.success('▶️ Resumed.'))
        else:
            await interaction.response.send_message("Nothing is currently paused.", ephemeral=True)

    @app_commands.command(name='stop', description='Stops playing and clears the queue.')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stop(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        state.songs.clear()
        if state.is_playing:
            state.voice.stop()
            await interaction.response.send_message(embed=embeds.success('⏹️ Stopped and cleared queue.'))
        elif state.voice and state.voice.is_connected():
            await interaction.response.send_message(embed=embeds.warning('🧹 Queue cleared.'))
        else:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)

    @app_commands.command(name='clear', description='Clears the queue but keeps the current song playing.')
    async def clear(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        state.songs.clear()
        await interaction.response.send_message(embed=embeds.warning('🧹 Queue cleared.'))

    @app_commands.command(name='skip', description='Vote to skip the current song.')
    async def skip(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Not playing any music right now...", ephemeral=True)
            return
        voter = interaction.user
        # Track carries requester_id, never a Member -- a Member is not
        # serializable, which is the whole reason the queue is plain data.  The
        # old `state.current.requester` raised AttributeError, so every /skip by
        # the requester failed instead of skipping.
        if voter.id == state.current.requester_id:
            state.skip()
            await interaction.response.send_message(embed=embeds.success('⏭️ Skipped.'))
        elif voter.id not in state.skip_votes:
            state.skip_votes.add(voter.id)
            total = len(state.skip_votes)
            needed = state._skip_threshold()
            if total >= needed:
                state.skip()
                await interaction.response.send_message(embed=embeds.success('⏭️ Skip vote passed.'))
            else:
                await interaction.response.send_message(embed=embeds.warning(f'🗳️ Skip vote added ({total}/{needed})'))
        else:
            await interaction.response.send_message("You have already voted to skip this song.", ephemeral=True)

    @app_commands.command(name='seek', description='Seeks to a position in the current track.')
    @app_commands.describe(position="Timestamp e.g. 1:30, 90, 2m")
    async def seek(self, interaction: discord.Interaction, position: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        try:
            pos = _parse_user_time(position)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        if state.current.duration_seconds and pos > state.current.duration_seconds:
            await interaction.response.send_message("That position is beyond the song length.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            state._current_position = pos
            state._current_start_time = time.time()
            await state.restart_current(interaction, preserve_position=True)
            await interaction.followup.send(embed=embeds.success(f'⏩ Seeked to `{_format_timestamp(pos)}`.'))
        except Exception as e:
            await interaction.followup.send(embed=embeds.error(f'❌ Seek failed: {e}'), ephemeral=True)

    @app_commands.command(name='forward', description='Skips forward in the current track.')
    @app_commands.describe(amount="Time to skip forward e.g. 10s, 1:00")
    async def forward(self, interaction: discord.Interaction, amount: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        try:
            delta = _parse_user_time(amount)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        new_pos = state.elapsed + delta
        if state.current.duration_seconds:
            new_pos = min(new_pos, state.current.duration_seconds)
        await interaction.response.defer()
        try:
            state._current_position = new_pos
            state._current_start_time = time.time()
            await state.restart_current(interaction, preserve_position=True)
            await interaction.followup.send(embed=embeds.success(f'⏩ Forwarded `{_format_timestamp(delta)}`.'))
        except Exception as e:
            await interaction.followup.send(embed=embeds.error(f'❌ Forward failed: {e}'), ephemeral=True)

    @app_commands.command(name='rewind', description='Rewinds in the current track.')
    @app_commands.describe(amount="Time to rewind e.g. 10s, 1:00")
    async def rewind(self, interaction: discord.Interaction, amount: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        try:
            delta = _parse_user_time(amount)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        new_pos = max(0.0, state.elapsed - delta)
        await interaction.response.defer()
        try:
            state._current_position = new_pos
            state._current_start_time = time.time()
            await state.restart_current(interaction, preserve_position=True)
            await interaction.followup.send(embed=embeds.success(f'⏪ Rewound `{_format_timestamp(delta)}`.'))
        except Exception as e:
            await interaction.followup.send(embed=embeds.error(f'❌ Rewind failed: {e}'), ephemeral=True)

    # ---------------- Queue Management ----------------

    @app_commands.command(name='queue', description='Shows the player queue.')
    async def queue(self, interaction: discord.Interaction):
        """The queue, with the controls the dashboard has always had.

        The `page: int = 1` parameter is gone. It existed because there was no
        way to turn a page, so the page number had to be typed -- which meant
        re-running the command to read further, and gave no way to act on what
        you found.
        """
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0 and not state.is_playing:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return

        view = QueueView(self, interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view)
        # Kept so on_timeout can disable the buttons rather than leaving ones
        # that look live and do nothing.
        view.message = await interaction.original_response()

    @app_commands.command(name='shuffle', description='Shuffles the queue.')
    async def shuffle(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        state.songs.shuffle()
        await interaction.response.send_message(embed=embeds.success('🔀 Queue shuffled.'))

    @app_commands.command(name='remove', description='Removes one or more songs from the queue.')
    async def remove(self, interaction: discord.Interaction, index: int, count: int = 1):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        if index <= 0 or index > len(state.songs):
            await interaction.response.send_message("Invalid index.", ephemeral=True)
            return
        count = max(1, min(count, len(state.songs) - index + 1))
        removed = []
        for _ in range(count):
            song = state.songs[index - 1]
            removed.append(song.title)
            state.songs.remove(index - 1)
        await interaction.response.send_message(embed=embeds.warning(f'🗑️ Removed **{len(removed)}** track(s):\n' + '\n'.join(f'• {t}' for t in removed)))

    @app_commands.command(name='move', description='Moves a track to another position in the queue.')
    async def move(self, interaction: discord.Interaction, from_index: int, to_index: int):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if len(state.songs) == 0:
            await interaction.response.send_message("Empty queue.", ephemeral=True)
            return
        if from_index <= 0 or from_index > len(state.songs) or to_index <= 0 or to_index > len(state.songs):
            await interaction.response.send_message("Invalid index.", ephemeral=True)
            return
        state.songs.move(from_index - 1, to_index - 1)
        await interaction.response.send_message(embed=embeds.success(f'↔️ Moved track #{from_index} to #{to_index}.'))

    @app_commands.command(name='jump', description='Jumps to a track in the queue.')
    async def jump(self, interaction: discord.Interaction, index: int):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if index <= 0 or index > len(state.songs):
            await interaction.response.send_message("Invalid index.", ephemeral=True)
            return
        # Move target to front and skip current
        state.songs.move(index - 1, 0)
        state.skip()
        await interaction.response.send_message(embed=embeds.success(f'⏭️ Jumped to track #{index}.'))

    @app_commands.command(name='loop', description='Sets the loop mode for the player.')
    @app_commands.describe(mode="Loop mode: off, track, or queue")
    @app_commands.choices(mode=[
        app_commands.Choice(name='Off', value='off'),
        app_commands.Choice(name='Track', value='track'),
        app_commands.Choice(name='Queue', value='queue')
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str] = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is being played at the moment.", ephemeral=True)
            return
        if mode is None:
            # Cycle through modes
            modes = ['off', 'track', 'queue']
            current_idx = modes.index(state.loop)
            new_mode = modes[(current_idx + 1) % len(modes)]
        else:
            new_mode = mode.value
        state.loop = new_mode
        emoji = {'off': '⏹️', 'track': '🔂', 'queue': '🔁'}.get(new_mode, '🔁')
        await interaction.response.send_message(embed=embeds.success(f'{emoji} Loop mode set to **{new_mode}**.'))

    @app_commands.command(name='loopqueue', description='Toggles queue loop.')
    async def loopqueue(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is being played at the moment.", ephemeral=True)
            return
        state.loop = 'off' if state.loop == 'queue' else 'queue'
        status = "enabled" if state.loop == 'queue' else "disabled"
        await interaction.response.send_message(embed=embeds.success(f'🔁 Queue loop {status}.'))

    @app_commands.command(name='volume', description='Sets the volume of the player (0-1000).')
    async def volume(self, interaction: discord.Interaction, volume: int):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.is_playing:
            await interaction.response.send_message("Nothing is being played at the moment.", ephemeral=True)
            return
        if not 0 <= volume <= 1000:
            await interaction.response.send_message("Volume must be between 0 and 1000.", ephemeral=True)
            return
        state.volume = volume / 100
        await interaction.response.send_message(embed=embeds.success(f'🔊 Volume set to {volume}%'))

    # ---------------- Audio Effects ----------------

    async def _toggle_effect(self, interaction: discord.Interaction, attr_name: str, display_name: str, mutually_exclusive: list = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return

        current = getattr(state, attr_name)
        setattr(state, attr_name, not current)

        if not current and mutually_exclusive:
            for other in mutually_exclusive:
                setattr(state, other, False)

        enabled = getattr(state, attr_name)
        await interaction.response.send_message(
            embed=embeds.success(f'{display_name} is now {"enabled" if enabled else "disabled"}.')
        )

        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=embeds.success(f'🎚️ Applied {display_name} to current song.'))
            except Exception as e:
                await interaction.followup.send(embed=embeds.error(f"❌ Error reapplying effect: {e}"), ephemeral=True)

    @app_commands.command(name='16d', description='Toggles 16D audio effect.')
    async def sixteen_d(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_16d_enabled', '16D audio effect')

    @app_commands.command(name='nightcore', description='Toggles nightcore mode.')
    async def nightcore(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_nightcore_enabled', 'Nightcore mode', ['_vaporwave_enabled'])

    @app_commands.command(name='vaporwave', description='Toggles vaporwave mode.')
    async def vaporwave(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_vaporwave_enabled', 'Vaporwave mode', ['_nightcore_enabled'])

    @app_commands.command(name='reverb', description='Toggles reverb effect.')
    async def reverb(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_reverb_enabled', 'Reverb effect')

    @app_commands.command(name='slowed', description='Toggles slowed effect.')
    async def slowed(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_slowed_enabled', 'Slowed effect')

    @app_commands.command(name='slownrev', description='Toggles slowed + reverb effect.')
    async def slownrev(self, interaction: discord.Interaction):
        await self._toggle_effect(interaction, '_slownrev_enabled', 'Slowed + Reverb')

    @app_commands.command(name='pitch', description='Sets the player pitch (0.5-2.0 or reset).')
    async def pitch(self, interaction: discord.Interaction, new_pitch: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        if new_pitch.lower() == 'reset':
            state._pitch = 1.0
            await interaction.response.send_message(embed=embeds.success('Pitch reset.'))
        else:
            try:
                val = float(new_pitch)
                if 0.5 <= val <= 2.0:
                    state._pitch = val
                    await interaction.response.send_message(embed=embeds.success(f'Pitch set to {val}.'))
                else:
                    await interaction.response.send_message("Pitch must be between 0.5 and 2.0.", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("Invalid pitch value.", ephemeral=True)
                return
        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=embeds.success('Pitch applied to current song.'))
            except Exception as e:
                await interaction.followup.send(embed=embeds.error(f'❌ Error applying pitch: {e}'), ephemeral=True)

    @app_commands.command(name='bass_boost', description='Sets bass boost in dB (-20 to 20 or reset).')
    async def bass_boost(self, interaction: discord.Interaction, amount: str):
        await self._set_bass_boost(interaction, amount)

    @app_commands.command(name='bassboost', description='Alias for /bass_boost.')
    async def bassboost(self, interaction: discord.Interaction, amount: str):
        await self._set_bass_boost(interaction, amount)

    async def _set_bass_boost(self, interaction: discord.Interaction, amount: str):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        if amount.lower() == 'reset':
            state._bass_boost = None
            await interaction.response.send_message(embed=embeds.success('Bass boost disabled.'))
        else:
            try:
                val = int(amount)
                if -20 <= val <= 20:
                    state._bass_boost = val
                    await interaction.response.send_message(embed=embeds.success(f'Bass boost set to {val} dB.'))
                else:
                    await interaction.response.send_message("Bass boost must be between -20 and 20 dB.", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("Invalid value.", ephemeral=True)
                return
        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=embeds.success('Bass boost applied to current song.'))
            except Exception as e:
                await interaction.followup.send(embed=embeds.error(f'❌ Error applying bass boost: {e}'), ephemeral=True)

    @app_commands.command(name='247', description='Toggles 24/7 mode (no auto-disconnect).')
    async def twenty_four_seven(self, interaction: discord.Interaction):
        """24/7, persisted.

        The flag used to live only on the in-memory state, so "24/7 mode" meant
        "until the next deploy" -- and a server that had asked the bot to stay
        in a channel forever found it silently gone. It is now stored with the
        channel, and restored on startup by `_restore_loop`.
        """
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        await interaction.response.defer()
        enabled = not state._247_enabled
        state._247_enabled = enabled
        channel_id = str(state.voice.channel.id) if state.voice.channel else None
        try:
            await asyncio.to_thread(
                write_guild_settings,
                str(interaction.guild.id),
                {
                    "always_on": enabled,
                    # Cleared on disable rather than left behind, so a later
                    # restart cannot rejoin a channel nobody asked it to.
                    "always_on_channel_id": channel_id if enabled else None,
                },
            )
            self._music_policy.setdefault(int(interaction.guild.id), {}).update(
                {"always_on": enabled, "always_on_channel_id": channel_id if enabled else None}
            )
        except Exception:
            # The in-memory flag is already set, so the session behaves as asked
            # -- it just will not survive a restart, and saying so is better
            # than reporting a failure for something that did work.
            log.exception("Could not persist 24/7 mode for %s", interaction.guild.id)
            await interaction.followup.send(embed=embeds.warning(f'24/7 mode is now {"enabled" if enabled else "disabled"} for this session, '
                            'but could not be saved.'))
            return
        status = "enabled" if enabled else "disabled"
        await interaction.followup.send(embed=embeds.success(f'24/7 mode is now {status}.' + (
                f' Zephyr will rejoin {state.voice.channel.mention} after a restart.' if enabled else ''
            )))

    @app_commands.command(name='dj-only', description='Restrict the player to DJs (or Manage Server).')
    @app_commands.describe(enabled='On to lock the player, off to let everybody drive it')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def dj_only(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await asyncio.to_thread(
            write_guild_settings, str(interaction.guild.id), {"dj_only": bool(enabled)}
        )
        self._music_policy.setdefault(int(interaction.guild.id), {})["dj_only"] = bool(enabled)

        if not enabled:
            await interaction.followup.send(
                '🔓 DJ-only mode is off. Anybody in the voice channel can control the player.',
                ephemeral=True)
            return
        # Said at the point of setting it, because "locked" with no DJ role is a
        # much stricter setting than it sounds and is easy to enable by mistake.
        dj_role_id = self._dj_role_ids.get(int(interaction.guild.id))
        detail = (
            f'Only <@&{dj_role_id}> and Manage Server can control the player.' if dj_role_id
            else 'No DJ role is set, so **only Manage Server** can control the player. '
                 'Set a DJ role in the dashboard to widen that.'
        )
        await interaction.followup.send(f'🔒 DJ-only mode is on. {detail}', ephemeral=True)

    @app_commands.command(name='vote-skip-ratio', description='What fraction of listeners must agree to skip.')
    @app_commands.describe(percent='Percent of listeners needed, 5-100. Default is 50.')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def vote_skip_ratio(
        self, interaction: discord.Interaction, percent: app_commands.Range[int, 5, 100]
    ):
        await interaction.response.defer(ephemeral=True)
        ratio = int(percent) / 100
        await asyncio.to_thread(
            write_guild_settings, str(interaction.guild.id), {"vote_skip_ratio": ratio}
        )
        self._music_policy.setdefault(int(interaction.guild.id), {})["vote_skip_ratio"] = ratio
        # Applied to the live state as well: waiting for the next teardown would
        # read as the setting not having worked.
        state = self.peek_voice_state(interaction.guild.id)
        if state is not None:
            self.apply_policy(state)
        await interaction.followup.send(
            f'🗳️ {percent}% of listeners now have to agree to skip.', ephemeral=True
        )

    @app_commands.command(name='reset_effects', description='Resets all audio effects.')
    async def reset_effects(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        state._reverb_enabled = False
        state._slowed_enabled = False
        state._16d_enabled = False
        state._nightcore_enabled = False
        state._vaporwave_enabled = False
        state._pitch = 1.0
        state._bass_boost = None
        state._slownrev_enabled = False
        await interaction.response.send_message(embed=embeds.success('All audio effects reset.'))
        if state.is_playing:
            try:
                await state.restart_current(interaction, preserve_position=True)
                await interaction.followup.send(embed=embeds.success('Default audio settings reapplied.'))
            except Exception as e:
                await interaction.followup.send(embed=embeds.error(f'❌ Error resetting effects: {e}'), ephemeral=True)

    @app_commands.command(name='autoplay', description='Keeps the music going with a YouTube Mix when the queue runs out.')
    async def autoplay(self, interaction: discord.Interaction):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)
        if not state.voice:
            await interaction.response.send_message("Not connected to any voice channel.", ephemeral=True)
            return
        state._autoplay_enabled = not state._autoplay_enabled
        status = "enabled" if state._autoplay_enabled else "disabled"
        await interaction.response.send_message(
            embed=embeds.success(f'📻 Autoplay {status}.'))
        state.changed()

    # ---------------- Playlists ----------------

    def _queue_payload(self, state: VoiceState) -> list[dict]:
        """The current track followed by the queue, as storable rows.

        The current track is included because "save this" means what is playing
        plus what is coming, not just the part nobody has heard yet.
        """
        tracks = ([state.current] if state.current else []) + list(state.songs)
        return [
            {'title': track.title, 'url': track.url,
             'duration_s': track.duration_seconds, 'source': track.source}
            for track in tracks
        ]

    @app_commands.command(name='save', description='Saves the current queue as a playlist.')
    @app_commands.describe(name="A name to save it under", public="Let anyone in this server load it")
    async def save(self, interaction: discord.Interaction, name: str, public: bool = False):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        state = self.peek_voice_state(interaction.guild.id)
        if state is None or not (state.current or len(state.songs)):
            await interaction.response.send_message("There is nothing to save.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            saved = await asyncio.to_thread(
                save_playlist, str(interaction.user.id), name, self._queue_payload(state),
                guild_id=str(interaction.guild.id), is_public=public)
        except PlaylistError as exc:
            await interaction.followup.send(embed=embeds.error(f'❌ {exc}'), ephemeral=True)
            return
        except Exception as exc:
            log.exception("Could not save a playlist")
            await interaction.followup.send(
                embed=embeds.error(f'❌ Could not save the playlist: {exc}'), ephemeral=True)
            return

        await interaction.followup.send(embed=embeds.success(f'💾 Saved **{saved["track_count"]}** track(s) as **{saved["name"]}**.'))

    @app_commands.command(name='load', description='Loads a saved playlist into the queue.')
    @app_commands.describe(name="The playlist to load")
    @app_commands.autocomplete(name=_playlist_autocomplete)
    async def load(self, interaction: discord.Interaction, name: str):
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You need to be in a voice channel.", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            playlist = await asyncio.to_thread(
                find_playlist, str(interaction.user.id), name, guild_id=str(interaction.guild.id))
        except Exception as exc:
            log.exception("Could not load a playlist")
            await interaction.followup.send(
                embed=embeds.error(f'❌ Could not read your playlists: {exc}'), ephemeral=True)
            return
        if playlist is None:
            await interaction.followup.send(
                embed=embeds.error(f'❌ No playlist called **{name}**.'), ephemeral=True)
            return

        added = await self._enqueue_playlist(interaction.guild, interaction.user,
                                             interaction.user.voice.channel, playlist)
        await interaction.followup.send(embed=embeds.success(f'📀 Queued **{added}** track(s) from **{playlist["name"]}**.'))

    async def _enqueue_playlist(self, guild, member, destination, playlist: dict) -> int:
        """Connect if needed and append a stored playlist to the queue.

        Nothing is resolved here.  Stored rows become Tracks as they are -- some
        with only a title -- and ``YTDLSource.from_track`` does the lookup when
        each one actually plays.  Resolving 200 rows up front would take minutes
        and throw most of the work away the moment somebody ran /skip.
        """
        state = self.ensure_voice_state(guild.id)
        async with self._get_voice_lock(guild.id):
            if not state.voice or not state.voice.is_connected():
                state.voice = await destination.connect(self_deaf=True)
        state.start_player()

        for row in playlist['tracks']:
            state.songs.put_nowait(Track(
                title=row['title'], url=row['url'], duration_seconds=row['duration_s'] or 0,
                requester_id=member.id, requester_mention=member.mention, source=row['source']))
        state.changed()
        return len(playlist['tracks'])

    @app_commands.command(name='playlists', description='Lists your saved playlists.')
    async def playlists(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild.id) if interaction.guild else None
        try:
            saved = await asyncio.to_thread(list_playlists, str(interaction.user.id), guild_id=guild_id)
        except Exception as exc:
            log.exception("Could not list playlists")
            await interaction.followup.send(f'❌ Could not read your playlists: {exc}', ephemeral=True)
            return

        if not saved:
            await interaction.followup.send(
                "You have no saved playlists yet. Queue something up and run `/save`.", ephemeral=True)
            return

        embed = embeds.info(title='📀 Your playlists')
        for playlist in saved[:25]:
            owned = playlist['owner_id'] == str(interaction.user.id)
            label = playlist['name'] if owned else f"{playlist['name']} (shared)"
            embed.add_field(
                name=label,
                value=f"{playlist['track_count']} track(s) • {_format_timestamp(playlist['duration_s'])}",
                inline=False)
        if len(saved) > 25:
            embed.set_footer(
                text=embeds.footer_text(f'Showing 25 of {len(saved)}.'),
                icon_url=embeds.icon_url(),
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name='playlist-delete', description='Deletes one of your saved playlists.')
    @app_commands.describe(name="The playlist to delete")
    @app_commands.autocomplete(name=_playlist_autocomplete)
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            playlist = await asyncio.to_thread(find_playlist, str(interaction.user.id), name)
        except Exception as exc:
            log.exception("Could not delete a playlist")
            await interaction.followup.send(f'❌ Could not read your playlists: {exc}', ephemeral=True)
            return
        # find_playlist without a guild only ever returns your own, so ownership
        # is already established -- but assert it rather than rely on that.
        if playlist is None or playlist['owner_id'] != str(interaction.user.id):
            await interaction.followup.send(f'❌ You have no playlist called **{name}**.', ephemeral=True)
            return
        await asyncio.to_thread(delete_playlist, playlist['id'])
        await interaction.followup.send(f'🗑️ Deleted **{playlist["name"]}**.', ephemeral=True)

    async def _bridge_playlist_load(self, guild, actor_id, args):
        member = self._authorize(guild, actor_id)
        try:
            playlist_id = int(args.get('playlist_id'))
        except (TypeError, ValueError):
            raise VoiceError("That is not a playlist.") from None
        playlist = await asyncio.to_thread(get_playlist, playlist_id)
        if playlist is None:
            raise VoiceError("That playlist no longer exists.")
        # A private playlist is loadable only by its owner, wherever it was saved.
        if playlist['owner_id'] != str(member.id) and not playlist['is_public']:
            raise VoiceError("That playlist is not yours.")

        state = self.peek_voice_state(guild.id)
        destination = state.voice.channel if state and state.voice and state.voice.is_connected() else None
        if destination is None:
            destination = member.voice.channel if member.voice else None
        if destination is None:
            raise VoiceError("Join a voice channel first.")

        added = await self._enqueue_playlist(guild, member, destination, playlist)
        return {'added': added, 'name': playlist['name']}

    async def _bridge_autoplay(self, guild, actor_id, args):
        self._authorize(guild, actor_id)
        state = self._require_state(guild.id)
        state._autoplay_enabled = bool(args.get('enabled'))
        return {'autoplay': state._autoplay_enabled}

    # ---------------- Spotify Helpers ----------------

    # The two former staticmethods now live at module level (see the predicates at
    # the top of this file).  These aliases keep the ~4 existing `self.` call sites
    # working without touching them.
    _resolve_spotify_short_link = staticmethod(_resolve_spotify_short_link)
    _parse_spotify_id = staticmethod(_parse_spotify_id)

    async def _get_spotify_tracks(self, url: str):
        url = _sanitize_search(url)
        if 'spotify.link' in url:
            url = await asyncio.get_event_loop().run_in_executor(None, functools.partial(self._resolve_spotify_short_link, url))

        track_ids = []
        loop = asyncio.get_event_loop()

        try:
            track_id = self._parse_spotify_id(url, 'track')
            if track_id:
                track_ids.append(track_id)
                return track_ids

            playlist_id = self._parse_spotify_id(url, 'playlist')
            if playlist_id:
                offset = 0
                limit = 100
                while True:
                    results = await loop.run_in_executor(
                        None, functools.partial(self.sp.playlist_tracks, url, limit=limit, offset=offset)
                    )
                    items = results.get('items', [])
                    if not items:
                        break
                    for item in items:
                        track = item.get('track')
                        if track and track.get('id'):
                            track_ids.append(track['id'])
                    if len(items) < limit:
                        break
                    offset += limit
                    if len(track_ids) >= 200:
                        break
                return track_ids

            album_id = self._parse_spotify_id(url, 'album')
            if album_id:
                offset = 0
                limit = 50
                while True:
                    results = await loop.run_in_executor(
                        None, functools.partial(self.sp.album_tracks, url, limit=limit, offset=offset)
                    )
                    items = results.get('items', [])
                    if not items:
                        break
                    for item in items:
                        if item.get('id'):
                            track_ids.append(item['id'])
                    if len(items) < limit:
                        break
                    offset += limit
                    if len(track_ids) >= 200:
                        break
        except Exception as e:
            log.exception("Spotify lookup failed")
        return track_ids

    async def _search_youtube(self, track_info: dict):
        """Search YouTube for a Spotify track dict and return the best result URL."""
        try:
            name = track_info.get('name', '')
            artists = ' '.join(a.get('name', '') for a in track_info.get('artists', []))
            query = f"ytsearch:{name} {artists}".strip()
            loop = asyncio.get_event_loop()
            partial = functools.partial(YTDLSource.ytdl.extract_info, query, download=False, process=False)
            data = await loop.run_in_executor(None, partial)
            if data and 'entries' in data and data['entries']:
                entry = data['entries'][0]
                return entry.get('url') or entry.get('webpage_url') or (
                    f"https://www.youtube.com/watch?v={entry['id']}" if entry.get('id') else None
                )
            elif data and 'url' in data:
                return data['url']
        except Exception:
            pass
        return None

    # ---------------- Lyrics ----------------

    @app_commands.command(name='lyrics', description='Shows lyrics for the current song or a search query.')
    @app_commands.describe(query="Song title/artist (leave empty for current song)")
    async def lyrics(self, interaction: discord.Interaction, query: str = None):
        ctx = await self._interaction_ctx(interaction)
        state = self.get_voice_state(ctx)

        if not query:
            if not state.is_playing:
                await interaction.response.send_message("Nothing is playing. Provide a song name to search for lyrics.", ephemeral=True)
                return
            title = state.current.title
        else:
            title = query

        await interaction.response.defer()
        try:
            async with aiohttp.ClientSession() as session:
                # Try lyrics.ovh search endpoint first
                search_url = f"https://api.lyrics.ovh/suggest/{requests.utils.quote(title)}"
                async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get('data', [])[:5]
                        if not items:
                            await interaction.followup.send(embed=embeds.error('❌ No lyrics found.'), ephemeral=True)
                            return
                        # Use first result
                        artist = items[0]['artist']['name']
                        song_title = items[0]['title']
                        lyrics_url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(song_title)}"
                        async with session.get(lyrics_url, timeout=aiohttp.ClientTimeout(total=10)) as lyrics_resp:
                            if lyrics_resp.status == 200:
                                lyrics_data = await lyrics_resp.json()
                                lyrics_text = lyrics_data.get('lyrics', '')
                                if lyrics_text:
                                    await self._send_lyrics(interaction, f"{artist} - {song_title}", lyrics_text)
                                    return
            await interaction.followup.send(embed=embeds.error('❌ Could not find lyrics.'), ephemeral=True)
        except Exception as e:
            log.exception("Could not fetch lyrics")
            await interaction.followup.send(embed=embeds.error(f'❌ Error fetching lyrics: {e}'), ephemeral=True)

    async def _send_lyrics(self, interaction: discord.Interaction, title: str, lyrics: str):
        chunks = [lyrics[i:i + 4000] for i in range(0, len(lyrics), 4000)]
        for idx, chunk in enumerate(chunks):
            embed = embeds.info(
                chunk,
                title=f'🎤 Lyrics — {title}' if idx == 0 else None,
                # Through the factory rather than set_footer: a bare
                # set_footer replaces the shared footer, which is how
                # pagination silently lost the bot's name on every page.
                footer=f"Part {idx + 1}/{len(chunks)}",
            )
            if idx == 0:
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
