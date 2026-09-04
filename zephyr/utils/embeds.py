"""One way to build an embed.

The web has `docs/DESIGN.md`; the bot's output had no equivalent, and it showed.
Before this module there were **103** `discord.Embed(...)` constructions across
the package using **eleven** distinct colours, no shared footer, no bot icon and
no timestamp anywhere — so Zephyr read as three bots sharing an avatar. Weather
answered in blue, music in green, AI in blurple, and an error was red in one cog
and orange in another.

## Six roles, not eleven colours

The palette below is indexed by *what the message is*, not by what colour
somebody felt like. That is the whole point: `error()` is one function, so an
error cannot be orange here and red there, and changing what "error" looks like
is one edit rather than thirty-eight.

The values are Discord's own semantic colours rather than `discord.Color`'s
named constants. `discord.Color.green()` is a bright web green that does not
match anything in the client; `0x3BA55D` is the green Discord itself uses for a
positive state, so an embed sits in the channel instead of on top of it.

## The timestamp

Every embed built here carries one, which no embed in this package had. It is
the cheapest possible answer to "when did this happen" on an error somebody is
reporting hours later, and on a listing it says how fresh the page is. Opt out
with ``timestamp=False`` where it would be noise.

## The icon

``configure`` is called once from ``on_ready``, because the bot's own avatar URL
does not exist until the gateway hands it over. A module-level value rather than
a parameter threaded through 103 call sites: it is process-wide identity, it
never changes while the process runs, and an unconfigured default renders
correctly rather than raising.
"""

from __future__ import annotations

from typing import Iterable

import discord

# Discord's own semantic colours. See the module docstring for why these are
# not `discord.Color`'s named constants.
ACCENTS = {
    "success": 0x3BA55D,
    "error": 0xED4245,
    "warning": 0xFAA61A,
    "info": 0x5865F2,
    "neutral": 0x4F545C,
    # The bot's own identity: /help, the join introduction, the web app card.
    "brand": 0xF0B232,
}
DEFAULT_ACCENT = "info"

# What the footer says when nothing else is passed. Set from on_ready.
_name = "Zephyr"
_icon_url: str | None = None

# Discord's own ceilings, enforced here so a long value is truncated in one
# place rather than raising a 400 from whichever cog happened to exceed it.
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_FIELDS = 25


def configure(*, name: str | None = None, icon_url: str | None = None) -> None:
    """Record the bot's own name and avatar, once, at startup."""
    global _name, _icon_url
    if name:
        _name = str(name)
    if icon_url:
        _icon_url = str(icon_url)


def reset() -> None:
    """Forget the configured identity. For tests."""
    global _name, _icon_url
    _name = "Zephyr"
    _icon_url = None


def build(
    *,
    title: str | None = None,
    description: str | None = None,
    accent: str = DEFAULT_ACCENT,
    fields: Iterable[tuple] = (),
    footer: str | None = None,
    timestamp: bool = True,
    url: str | None = None,
    thumbnail: str | None = None,
    image: str | None = None,
    author: tuple[str, str | None] | None = None,
) -> discord.Embed:
    """The one embed constructor.

    ``fields`` takes ``(name, value)`` or ``(name, value, inline)`` tuples, so a
    caller does not need to interleave ``add_field`` calls with the rest of the
    construction — which is what made the old call sites so hard to compare with
    each other.
    """
    embed = discord.Embed(
        title=_clip(title, MAX_TITLE),
        description=_clip(description, MAX_DESCRIPTION),
        colour=ACCENTS.get(accent, ACCENTS[DEFAULT_ACCENT]),
        url=url or None,
        # utcnow rather than a passed-in time: every caller wants "now", and
        # the one that does not can set embed.timestamp itself.
        timestamp=discord.utils.utcnow() if timestamp else None,
    )
    for field in list(fields)[:MAX_FIELDS]:
        name, value = field[0], field[1]
        inline = field[2] if len(field) > 2 else False
        embed.add_field(
            name=_clip(name, MAX_FIELD_NAME) or "​",
            # A zero-width space, not "": Discord rejects an empty field value
            # with a 400, and a cog that computed one would fail at the reply
            # rather than at the computation.
            value=_clip(value, MAX_FIELD_VALUE) or "​",
            inline=bool(inline),
        )
    embed.set_footer(text=_footer_text(footer), icon_url=_icon_url)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    if author:
        embed.set_author(name=author[0], icon_url=author[1] if len(author) > 1 else None)
    return embed


def success(description: str | None = None, **kwargs) -> discord.Embed:
    return build(description=description, accent="success", **kwargs)


def error(description: str | None = None, **kwargs) -> discord.Embed:
    return build(description=description, accent="error", **kwargs)


def warning(description: str | None = None, **kwargs) -> discord.Embed:
    return build(description=description, accent="warning", **kwargs)


def info(description: str | None = None, **kwargs) -> discord.Embed:
    return build(description=description, accent="info", **kwargs)


def neutral(description: str | None = None, **kwargs) -> discord.Embed:
    return build(description=description, accent="neutral", **kwargs)


def brand(description: str | None = None, **kwargs) -> discord.Embed:
    return build(description=description, accent="brand", **kwargs)


def recolour(embed: discord.Embed, accent: str) -> discord.Embed:
    """Change an already-built embed's accent, by role.

    For the legacy shape in ``weather``'s prefix commands, which build an embed
    and then turn it red if the city was not found. Restructuring those into
    early returns is a larger change than this module is for, and the thing
    worth keeping either way is that the colour is named by *role* rather than
    picked by hand -- so a "not found" in one command cannot be a different red
    from the next.
    """
    embed.colour = ACCENTS.get(accent, ACCENTS[DEFAULT_ACCENT])
    return embed


def footer_text(extra: str | None = None) -> str:
    """The composed footer, for a caller stamping an embed it did not build.

    ``pagination`` needs this: it is handed embeds by its callers and adds
    "Page 2/5" to them, and a bare `set_footer` would silently drop the bot's
    name from every paginated reply.
    """
    return _footer_text(extra)


def icon_url() -> str | None:
    """The configured avatar URL, for the same callers as ``footer_text``."""
    return _icon_url


def _footer_text(extra: str | None) -> str:
    text = f"{extra} · {_name}" if extra else _name
    return _clip(text, MAX_FOOTER) or _name


def _clip(value, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    # An ellipsis rather than a hard cut, so a truncated value is visibly
    # truncated instead of looking like the content simply ended.
    return text[: limit - 1] + "…"
