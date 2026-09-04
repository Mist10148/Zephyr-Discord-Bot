"""Centralized slash-command help data.

All help commands (/help, /helpmusic, /helpchat, /helpweather) render from the
ordered registry below so categories, ordering, and descriptions stay consistent.
"""

from dataclasses import dataclass, field

import discord

# Aliased: the rendering helpers below use `embeds` as a local list name, and
# an unaliased import would be shadowed inside them.
from zephyr.utils import embeds as embed_factory
from zephyr.utils.pagination import _send_paginated_embeds


@dataclass
class HelpEntry:
    name: str
    value: str


@dataclass
class HelpCategory:
    key: str
    emoji: str
    title: str
    commands: list[HelpEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ordered command registry
# ---------------------------------------------------------------------------
HELP_CATEGORIES = [
    HelpCategory(
        key="music_playback",
        emoji="▶️",
        title="Music — Playback",
        commands=[
            HelpEntry("/play <query>", "Play a song from YouTube or Spotify"),
            HelpEntry("/playskip <query>", "Add a song and skip straight to it"),
            HelpEntry("/playnext <query>", "Add a song to the top of the queue"),
            HelpEntry("/msearch <query>", "Search YouTube and pick a result"),
            HelpEntry("/now  /  /np", "Show the currently playing song"),
            HelpEntry("/pause", "Pause playback"),
            HelpEntry("/resume", "Resume playback"),
            HelpEntry("/stop", "Stop playback and clear the queue"),
            HelpEntry("/seek <time>", "Jump to a timestamp (e.g. 1:30)"),
            HelpEntry("/forward <time>", "Skip forward in the current track"),
            HelpEntry("/rewind <time>", "Rewind in the current track"),
            HelpEntry("/lyrics [query]", "Show lyrics for the current song or search"),
        ],
    ),
    HelpCategory(
        key="music_queue",
        emoji="📋",
        title="Music — Queue",
        commands=[
            HelpEntry("/queue [page]", "Show the song queue"),
            HelpEntry("/skip", "Vote to skip the current song"),
            HelpEntry("/jump <index>", "Jump to a track in the queue"),
            HelpEntry("/move <from> <to>", "Move a track to another position"),
            HelpEntry("/remove <index> [count]", "Remove track(s) from the queue"),
            HelpEntry("/clear", "Clear the queue (keeps the current song)"),
            HelpEntry("/shuffle", "Shuffle the queue"),
            HelpEntry("/loop [mode]", "Set loop mode: off / track / queue"),
            HelpEntry("/loopqueue", "Toggle queue loop"),
            HelpEntry("/autoplay", "Keep playing a YouTube Mix when the queue runs out"),
        ],
    ),
    HelpCategory(
        key="music_playlists",
        emoji="📀",
        title="Music — Playlists",
        commands=[
            HelpEntry("/save <name> [public]", "Save the current queue as a playlist"),
            HelpEntry("/load <name>", "Queue up a saved playlist"),
            HelpEntry("/playlists", "List your saved playlists"),
            HelpEntry("/playlist-delete <name>", "Delete one of your playlists"),
        ],
    ),
    HelpCategory(
        key="music_effects",
        emoji="🔊",
        title="Music — Effects & Audio",
        commands=[
            HelpEntry("/volume <0-1000>", "Set the player volume"),
            HelpEntry("/bassboost <dB>", "Boost or cut bass (use 'reset' to disable)"),
            HelpEntry("/pitch <0.5-2.0>", "Adjust pitch (use 'reset' to reset)"),
            HelpEntry("/nightcore", "Toggle nightcore mode"),
            HelpEntry("/vaporwave", "Toggle vaporwave mode"),
            HelpEntry("/slowed", "Toggle slowed effect"),
            HelpEntry("/reverb", "Toggle reverb effect"),
            HelpEntry("/slownrev", "Toggle slowed + reverb"),
            HelpEntry("/16d", "Toggle 16D audio effect"),
            HelpEntry("/reset_effects", "Reset all audio effects"),
        ],
    ),
    HelpCategory(
        key="music_voice",
        emoji="🎙️",
        title="Music — Voice & Connection",
        commands=[
            HelpEntry("/join", "Join your voice channel"),
            HelpEntry("/summon [channel]", "Summon the bot to a channel"),
            HelpEntry("/leave", "Leave the voice channel and clear the queue"),
            HelpEntry("/disconnect", "Disconnect the bot from voice"),
        ],
    ),
    HelpCategory(
        key="weather",
        emoji="🌦️",
        title="Weather",
        commands=[
            HelpEntry("/weather <city>", "Current weather, air quality & precipitation"),
            HelpEntry("/forecast <city>", "Clean 3-day forecast with temperature and feels-like"),
            HelpEntry("/temperature <city>", "Current temperature"),
            HelpEntry("/description <city>", "Weather description"),
            HelpEntry("/humidity <city>", "Humidity"),
            HelpEntry("/pressure <city>", "Atmospheric pressure"),
            HelpEntry("/windspeed <city>", "Wind speed"),
            HelpEntry("/air <city>", "Air quality"),
            HelpEntry("/precipitation <city>", "Precipitation details"),
            HelpEntry("/setlocation [city]", "Set your default city (leave empty to clear it)"),
            HelpEntry("/mylocation", "Show your default city"),
            HelpEntry("/typhoon", "Latest typhoon alert for Iloilo City"),
            HelpEntry("/search <city>", "Search current weather & air quality"),
            HelpEntry("/class", "Class suspension forecast from feels-like temperature"),
        ],
    ),
    HelpCategory(
        key="weather_alerts",
        emoji="🔔",
        title="Weather — Alerts",
        commands=[
            HelpEntry("/weather-subscribe <kind> <location>", "Post weather to a channel on a schedule or on a watch"),
            HelpEntry("/weather-subs", "List this server's weather subscriptions"),
            HelpEntry("/weather-unsubscribe <id>", "Remove a subscription"),
            HelpEntry("/weather-preview <id>", "Show what a subscription would post right now"),
        ],
    ),
    HelpCategory(
        key="music_dj",
        emoji="🎚️",
        title="Music — DJ controls",
        commands=[
            HelpEntry("/dj-only <on|off>", "Restrict the player to DJs (Manage Server)"),
            HelpEntry("/vote-skip-ratio <percent>", "What fraction of listeners must agree to skip"),
            HelpEntry("/247", "Toggle 24/7 mode — persists across restarts"),
        ],
    ),
    HelpCategory(
        key="chat",
        emoji="💬",
        title="Chat & AI",
        commands=[
            HelpEntry("/prompt <message>", "Ask Gemini a question (supports images)"),
            HelpEntry("/forget", "Make the AI forget this channel's conversation"),
            HelpEntry("/settings", "Customize AI model and response format"),
            HelpEntry("/output", "Quickly switch between embed and text replies"),
            HelpEntry("/token", "Show Gemini usage stats"),
            HelpEntry("/image-gen <prompt>", "Generate an image with Gemini"),
            HelpEntry("/generate <prompt>", "Generate an image (legacy)"),
        ],
    ),
    HelpCategory(
        key="tts",
        emoji="🔊",
        title="TTS & Voice",
        commands=[
            HelpEntry("/say <text>", "Make the bot speak in voice chat"),
            HelpEntry("/language <lang>", "Change the TTS language (e.g. en, ja)"),
            HelpEntry("/disconnect", "Disconnect the bot from voice"),
        ],
    ),
    HelpCategory(
        key="reminders",
        emoji="⏰",
        title="Reminders",
        commands=[
            HelpEntry("/remindme <when> <message>", "Remind you later — e.g. 20m, 2h, 1h30m, 3 days"),
            HelpEntry("/reminders", "List your pending reminders"),
            HelpEntry("/reminder-cancel <id>", "Cancel one of your reminders"),
        ],
    ),
    HelpCategory(
        key="greetings",
        emoji="👋",
        title="Welcome & Farewell",
        commands=[
            HelpEntry("/welcome [channel] [message]", "Set the welcome message (Manage Server)"),
            HelpEntry("/farewell [channel] [message]", "Set the farewell message (Manage Server)"),
            HelpEntry("/greeting-preview", "See how your greetings will look"),
        ],
    ),
    HelpCategory(
        key="tags",
        emoji="🏷️",
        title="Tags",
        commands=[
            HelpEntry("/tag <name>", "Show a tag"),
            HelpEntry("/tag-create <name> <content>", "Create a tag"),
            HelpEntry("/tag-edit <name> <content>", "Change what a tag says"),
            HelpEntry("/tag-delete <name>", "Delete a tag"),
            HelpEntry("/tag-list", "Every tag in this server"),
            HelpEntry("/tag-info <name>", "Who made a tag, and how often it is used"),
        ],
    ),
    HelpCategory(
        key="activity",
        emoji="📈",
        title="Activity & Levels",
        commands=[
            HelpEntry("/rank [member]", "Your level, XP and message count here"),
            HelpEntry("/leaderboard", "The most active members"),
            HelpEntry("/activity-today", "How busy this server is today"),
            HelpEntry("/activity <on|off>", "Turn tracking on or off (Manage Server)"),
            HelpEntry("/activity-ignore <channel>", "Stop counting a channel"),
        ],
    ),
    HelpCategory(
        key="starboard",
        emoji="⭐",
        title="Starboard",
        commands=[
            HelpEntry("/starboard [channel] [threshold] [emoji]", "Set up the starboard (Manage Server)"),
            HelpEntry("/starboard-ignore <channel>", "Stop the starboard reading a channel"),
        ],
    ),
    HelpCategory(
        key="moderation",
        emoji="🛡️",
        title="Moderation",
        commands=[
            HelpEntry("/warn <member> <reason>", "Warn a member and record a case"),
            HelpEntry("/timeout <member> <duration>", "Time a member out — e.g. 10m, 2h, 1d"),
            HelpEntry("/untimeout <member>", "Lift a member's timeout"),
            HelpEntry("/kick <member>", "Kick a member"),
            HelpEntry("/ban <user>", "Ban a user, whether or not they are here"),
            HelpEntry("/unban <user id>", "Lift a ban"),
            HelpEntry("/purge <amount> [member]", "Bulk-delete recent messages"),
            HelpEntry("/cases <member>", "A member's moderation history"),
            HelpEntry("/case <number>", "Look up one case"),
            HelpEntry("/reason <number> <reason>", "Add or replace a case's reason"),
            HelpEntry("/modlog [channel]", "Choose where cases are posted"),
        ],
    ),
    HelpCategory(
        key="utility",
        emoji="ℹ️",
        title="Utility & Info",
        commands=[
            HelpEntry("/ping", "Show the bot's latency"),
            HelpEntry("/use", "Link to the web app"),
            HelpEntry("/export-my-data", "Send you everything Zephyr holds about you"),
            HelpEntry("/delete-my-data", "Erase everything erasable"),
        ],
    ),
    HelpCategory(
        key="help",
        emoji="❓",
        title="Help",
        commands=[
            HelpEntry("/help", "Show all available commands"),
            HelpEntry("/helpmusic", "Music command help"),
            HelpEntry("/helpchat", "Chat & TTS command help"),
            HelpEntry("/helpweather", "Weather command help"),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _category_embeds(
    categories: list[HelpCategory],
    title: str,
    *,
    include_toc: bool = False,
    seen: set[str] | None = None,
) -> list[discord.Embed]:
    """Build a list of embed pages from the requested categories.

    If ``seen`` is provided, commands whose names were already seen are skipped.
    This lets /help deduplicate commands that logically belong to more than one
    category (e.g. /disconnect in both Music Voice and TTS).
    """
    seen = seen or set()
    filtered: list[tuple[HelpCategory, list[HelpEntry]]] = []

    for cat in categories:
        remaining = [cmd for cmd in cat.commands if cmd.name not in seen]
        if not remaining:
            continue
        seen.update(cmd.name for cmd in remaining)
        filtered.append((cat, remaining))

    if not filtered:
        return []

    embeds: list[discord.Embed] = []

    if include_toc:
        toc = embed_factory.brand(
            "Browse commands by category using the buttons below.",
            title=f"📖 {title}",
        )
        for cat, _ in filtered:
            toc.add_field(name=f"{cat.emoji} {cat.title}", value="\u200b", inline=False)
        embeds.append(toc)

    for cat, commands in filtered:
        embed = embed_factory.brand(title=f"{cat.emoji} {title} — {cat.title}")
        for cmd in commands:
            embed.add_field(name=cmd.name, value=cmd.value, inline=False)
        embeds.append(embed)

    return embeds


async def _send_categorized_help(
    interaction: discord.Interaction,
    categories: list[HelpCategory],
    title: str,
    *,
    include_toc: bool = False,
    seen: set[str] | None = None,
) -> None:
    """Send categorized help pages with pagination.

    The ``color`` parameter is gone.  The four help commands passed four
    different colours -- green, blurple, gold and blue -- for four views of the
    *same* command list, which is the inconsistency 16.1 exists to remove.  All
    of it is the bot describing itself, so all of it is the brand accent.
    """
    embeds = _category_embeds(categories, title, include_toc=include_toc, seen=seen)
    if not embeds:
        await interaction.response.send_message("No commands to display.", ephemeral=True)
        return
    await _send_paginated_embeds(interaction, embeds)


def categories_by_key(*keys: str) -> list[HelpCategory]:
    """Return categories whose keys match the requested set, preserving order."""
    return [cat for cat in HELP_CATEGORIES if cat.key in keys]
