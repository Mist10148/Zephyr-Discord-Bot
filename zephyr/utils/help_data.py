"""Centralized slash-command help data.

All help commands (/help, /helpmusic, /helpchat, /helpweather) render from the
ordered registry below so categories, ordering, and descriptions stay consistent.
The dashboard's command palette reads the same registry (website/api/commands.py).

Conventions, which tests/test_help_registry.py enforces:

* ``<arg>`` is required and ``[arg]`` optional, named exactly as the slash
  command's parameter, so what /help shows is what Discord prompts for;
* aliases are separated by two spaces, a slash and two spaces (``"/now  /  /np"``);
* every command appears in exactly one category, in the order a newcomer would
  reach for it: Weather first (Zephyr's home turf), then Music, Voice, AI and
  the utilities.
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
    blurb: str = ""


# ---------------------------------------------------------------------------
# Ordered command registry
# ---------------------------------------------------------------------------
HELP_CATEGORIES = [
    # ---------------------------------------------------------------- Weather
    HelpCategory(
        key="weather",
        emoji="🌦️",
        title="Weather — Right Now",
        blurb="Live conditions for any city. Leave the city out to use your `/setlocation` default.",
        commands=[
            HelpEntry("/weather [city]", "The full picture: temperature, feels-like, wind, air quality, sunrise & sunset"),
            HelpEntry("/temperature [city]", "Temperature, feels-like and today's high / low"),
            HelpEntry("/description [city]", "Current conditions in a word or two"),
            HelpEntry("/humidity [city]", "Humidity, dew point and how muggy it feels"),
            HelpEntry("/windspeed [city]", "Wind speed, direction and gusts"),
            HelpEntry("/precipitation [city]", "Rain or snow in the last hour"),
            HelpEntry("/pressure [city]", "Atmospheric pressure and what it suggests"),
            HelpEntry("/air [city]", "Air-quality index and pollutant levels"),
            HelpEntry("/search [city]", "Same card as /weather — look up any place"),
        ],
    ),
    HelpCategory(
        key="weather_forecast",
        emoji="📅",
        title="Weather — Forecast & Advisories",
        blurb="What's coming, plus the Iloilo City advisories.",
        commands=[
            HelpEntry("/forecast [city]", "3-day overview, then a page per day"),
            HelpEntry("/class", "Class-suspension outlook from the heat index (Iloilo City)"),
            HelpEntry("/typhoon", "Active typhoon / cyclone alerts and TCWS signal (Iloilo City)"),
        ],
    ),
    HelpCategory(
        key="weather_location",
        emoji="📍",
        title="Weather — Your Location",
        blurb="Save a default city so you can skip typing it.",
        commands=[
            HelpEntry("/setlocation [city]", "Set your default city (leave empty to clear it)"),
            HelpEntry("/mylocation", "Show your default city"),
        ],
    ),
    HelpCategory(
        key="weather_alerts",
        emoji="🔔",
        title="Weather — Alerts & Subscriptions",
        blurb="Automatic posts to a channel. Creating and removing needs **Manage Server**.",
        commands=[
            HelpEntry("/weather-subscribe <kind> <location> [channel] [at] [tz]",
                      "Daily digest, severe-weather watch or class-suspension watch"),
            HelpEntry("/weather-subs", "List this server's subscriptions"),
            HelpEntry("/weather-preview <subscription_id>", "Show what a subscription would post right now"),
            HelpEntry("/weather-unsubscribe <subscription_id>", "Remove a subscription"),
        ],
    ),
    # ---------------------------------------------------------------- Music
    HelpCategory(
        key="music_playback",
        emoji="▶️",
        title="Music — Playback",
        blurb="YouTube links, searches and Spotify tracks, playlists & albums.",
        commands=[
            HelpEntry("/play <search>", "Play a song or playlist from YouTube or Spotify"),
            HelpEntry("/playnext <search>", "Queue a song to play next"),
            HelpEntry("/playskip <search>", "Play a song right now, skipping the current one"),
            HelpEntry("/msearch <query>", "Search YouTube and pick from the results"),
            HelpEntry("/now  /  /np", "Show the song that's playing"),
            HelpEntry("/pause", "Pause playback"),
            HelpEntry("/resume", "Resume playback"),
            HelpEntry("/skip", "Skip the song (a vote, unless you requested it)"),
            HelpEntry("/stop", "Stop playback and clear the queue"),
            HelpEntry("/seek <position>", "Jump to a timestamp, e.g. 1:30"),
            HelpEntry("/forward <amount>", "Skip ahead, e.g. 30s"),
            HelpEntry("/rewind <amount>", "Go back, e.g. 10s"),
            HelpEntry("/lyrics [query]", "Lyrics for the current song, or any song"),
        ],
    ),
    HelpCategory(
        key="music_queue",
        emoji="📋",
        title="Music — Queue",
        commands=[
            HelpEntry("/queue [page]", "Show what's coming up"),
            HelpEntry("/jump <index>", "Skip straight to a queued track"),
            HelpEntry("/move <from_index> <to_index>", "Move a track to another position"),
            HelpEntry("/remove <index> [count]", "Remove one or more tracks"),
            HelpEntry("/shuffle", "Shuffle the queue"),
            HelpEntry("/clear", "Clear the queue (keeps the current song)"),
            HelpEntry("/loop [mode]", "Loop off / track / queue"),
            HelpEntry("/loopqueue", "Toggle queue loop"),
            HelpEntry("/autoplay", "Keep playing a YouTube Mix when the queue runs out"),
        ],
    ),
    HelpCategory(
        key="music_effects",
        emoji="🎚️",
        title="Music — Volume & Effects",
        commands=[
            HelpEntry("/volume <volume>", "Set the volume (0–1000%)"),
            HelpEntry("/bassboost  /  /bass_boost <amount>", "Boost or cut bass in dB ('reset' to turn off)"),
            HelpEntry("/pitch <new_pitch>", "Change pitch, 0.5–2.0 ('reset' to restore)"),
            HelpEntry("/nightcore", "Toggle nightcore"),
            HelpEntry("/vaporwave", "Toggle vaporwave"),
            HelpEntry("/slowed", "Toggle slowed"),
            HelpEntry("/reverb", "Toggle reverb"),
            HelpEntry("/slownrev", "Toggle slowed + reverb"),
            HelpEntry("/16d", "Toggle 16D audio"),
            HelpEntry("/reset_effects", "Turn every effect off"),
        ],
    ),
    HelpCategory(
        key="music_playlists",
        emoji="📀",
        title="Music — Playlists",
        commands=[
            HelpEntry("/save <name> [public]", "Save the queue as a playlist (public = anyone here can load it)"),
            HelpEntry("/load <name>", "Queue a saved playlist"),
            HelpEntry("/playlists", "List your playlists and this server's public ones"),
            HelpEntry("/playlist-delete <name>", "Delete one of your playlists"),
        ],
    ),
    HelpCategory(
        key="music_voice",
        emoji="🎙️",
        title="Music — Voice Channel & 24/7",
        blurb="Who can control the player: the DJ role if the server has one, otherwise anyone in Zephyr's voice channel.",
        commands=[
            HelpEntry("/join", "Join your voice channel"),
            HelpEntry("/summon [channel]", "Move Zephyr to a channel (Manage Server)"),
            HelpEntry("/leave", "Leave voice and clear the queue (Manage Server)"),
            HelpEntry("/247", "Toggle 24/7 mode — stay connected when idle"),
        ],
    ),
    # ---------------------------------------------------------------- Voice
    HelpCategory(
        key="tts",
        emoji="🗣️",
        title="Voice — Text-to-Speech",
        commands=[
            HelpEntry("/say <text>", "Speak a message in your voice channel"),
            HelpEntry("/language <lang>", "Change this server's TTS language, e.g. en, tl, ja"),
            HelpEntry("/disconnect", "Disconnect Zephyr from voice (Manage Server)"),
        ],
    ),
    # ---------------------------------------------------------------- AI
    HelpCategory(
        key="chat",
        emoji="💬",
        title="AI — Chat & Images",
        commands=[
            HelpEntry("/prompt [message] [attachment]", "Ask Gemini anything — images and .txt files welcome"),
            HelpEntry("/summarize [messages]", "Privately summarize recent messages in this channel"),
            HelpEntry("/translate <text> <target_language>", "Translate text into another language"),
            HelpEntry("/image-gen <prompt>", "Generate an image with Gemini"),
            HelpEntry("/generate <prompt>", "Generate an image (legacy)"),
            HelpEntry("/forget", "Make the AI forget this channel's conversation"),
            HelpEntry("/settings <ai_model> <response_format>", "Choose the Gemini model and reply format"),
            HelpEntry("/output <format>", "Switch between embed and plain-text replies"),
            HelpEntry("/token", "Show Gemini usage and rate limits"),
        ],
    ),
    # ---------------------------------------------------------------- Utility
    HelpCategory(
        key="utility",
        emoji="🧰",
        title="Utility",
        commands=[
            HelpEntry("/use", "Open the Zephyr web dashboard"),
            HelpEntry("/ping", "Show the bot's latency"),
        ],
    ),
    HelpCategory(
        key="help",
        emoji="❓",
        title="Help",
        commands=[
            HelpEntry("/help", "Every command, by category"),
            HelpEntry("/helpweather", "Weather commands only"),
            HelpEntry("/helpmusic", "Music commands only"),
            HelpEntry("/helpchat", "AI and text-to-speech commands only"),
        ],
    ),
]

#: Category groups used by the focused help commands.
WEATHER_KEYS = ("weather", "weather_forecast", "weather_location", "weather_alerts")
MUSIC_KEYS = ("music_playback", "music_queue", "music_effects", "music_playlists", "music_voice")
CHAT_KEYS = ("chat", "tts")

LEGEND = "<required>  ·  [optional]"


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _category_page(cat: HelpCategory, commands: list[HelpEntry], title: str,
                   color: discord.Color, number: int, total: int) -> discord.Embed:
    lines = [f"*{cat.blurb}*", ""] if cat.blurb else []
    for cmd in commands:
        lines.append(f"**`{cmd.name}`**")
        lines.append(f"╰ {cmd.value}")
    embed = discord.Embed(title=f"{cat.emoji}  {cat.title}", description="\n".join(lines), color=color)
    embed.set_author(name=f"{title} · {number}/{total}")
    embed.set_footer(text=LEGEND)
    return embed


def _category_embeds(
    categories: list[HelpCategory],
    title: str,
    color: discord.Color,
    *,
    include_toc: bool = False,
    seen: set[str] | None = None,
) -> list[discord.Embed]:
    """Build a list of embed pages from the requested categories.

    If ``seen`` is provided, commands whose names were already seen are skipped,
    so a command can never be listed twice in one help session.
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
    offset = 2 if include_toc else 1

    if include_toc:
        count = sum(len(commands) for _, commands in filtered)
        lines = [f"**{count} commands** in {len(filtered)} categories. Use ◀️ ▶️ to browse.", ""]
        for index, (cat, commands) in enumerate(filtered):
            lines.append(f"`{index + offset:>2}`  {cat.emoji} **{cat.title}** — {len(commands)}")
        toc = discord.Embed(title=f"📖 {title}", description="\n".join(lines), color=color)
        toc.set_footer(text=f"Page numbers on the left · {LEGEND}")
        embeds.append(toc)

    for index, (cat, commands) in enumerate(filtered):
        embeds.append(_category_page(cat, commands, title, color, index + 1, len(filtered)))

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


def text_summary(keys: tuple[str, ...]) -> str:
    """A plain-text list of the given categories, for prefix-command help."""
    blocks = []
    for cat in categories_by_key(*keys):
        rows = "\n".join(f"`{cmd.name}` — {cmd.value}" for cmd in cat.commands)
        blocks.append(f"**{cat.emoji} {cat.title}**\n{rows}")
    return "\n\n".join(blocks)
