"""Centralized slash-command help data.

All help commands (/help, /helpmusic, /helpchat, /helpweather) render from the
ordered registry below so categories, ordering, and descriptions stay consistent.
"""

from dataclasses import dataclass, field

import discord

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
            HelpEntry("/247", "Toggle 24/7 mode (no auto-disconnect)"),
        ],
    ),
    HelpCategory(
        key="weather",
        emoji="🌦️",
        title="Weather",
        commands=[
            HelpEntry("/weather <city>", "Current weather, air quality & precipitation"),
            HelpEntry("/forecast <city>", "3-day weather and air quality forecast"),
            HelpEntry("/temperature <city>", "Current temperature"),
            HelpEntry("/description <city>", "Weather description"),
            HelpEntry("/humidity <city>", "Humidity"),
            HelpEntry("/pressure <city>", "Atmospheric pressure"),
            HelpEntry("/windspeed <city>", "Wind speed"),
            HelpEntry("/air <city>", "Air quality"),
            HelpEntry("/precipitation <city>", "Precipitation details"),
            HelpEntry("/typhoon", "Latest typhoon alert for Iloilo City"),
            HelpEntry("/search <city>", "Search current weather & air quality"),
            HelpEntry("/class", "Class suspension forecast from heat index"),
        ],
    ),
    HelpCategory(
        key="chat",
        emoji="💬",
        title="Chat & AI",
        commands=[
            HelpEntry("/prompt <message>", "Ask Gemini a question (supports images)"),
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
        key="utility",
        emoji="ℹ️",
        title="Utility & Info",
        commands=[
            HelpEntry("/ping", "Show the bot's latency"),
            HelpEntry("/use", "Link to the web app"),
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
    color: discord.Color,
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
        toc = discord.Embed(
            title=f"📖 {title}",
            description="Browse commands by category using the buttons below.",
            color=color,
        )
        for cat, _ in filtered:
            toc.add_field(name=f"{cat.emoji} {cat.title}", value="\u200b", inline=False)
        embeds.append(toc)

    for cat, commands in filtered:
        embed = discord.Embed(title=f"{cat.emoji} {title} — {cat.title}", color=color)
        for cmd in commands:
            embed.add_field(name=cmd.name, value=cmd.value, inline=False)
        embeds.append(embed)

    return embeds


async def _send_categorized_help(
    interaction: discord.Interaction,
    categories: list[HelpCategory],
    title: str,
    color: discord.Color,
    *,
    include_toc: bool = False,
    seen: set[str] | None = None,
) -> None:
    """Send categorized help pages with pagination."""
    embeds = _category_embeds(categories, title, color, include_toc=include_toc, seen=seen)
    if not embeds:
        await interaction.response.send_message("No commands to display.", ephemeral=True)
        return
    await _send_paginated_embeds(interaction, embeds)


def categories_by_key(*keys: str) -> list[HelpCategory]:
    """Return categories whose keys match the requested set, preserving order."""
    return [cat for cat in HELP_CATEGORIES if cat.key in keys]
