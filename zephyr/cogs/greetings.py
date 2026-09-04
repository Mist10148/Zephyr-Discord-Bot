"""Welcome and farewell messages.

Two things in here are worth reading before the commands.

**`render` uses replacement, not `str.format`.** A greeting is free text written
by a server administrator and rendered against a live `discord.Member`, and
`"{user.guild.me._state.http.token}".format(user=member)` is a working token
exfiltration through `str.format`'s attribute traversal. Format strings are not
safe over untrusted templates, and "the template author is an administrator" is
not the same as "the template author is trusted with the bot's credentials" --
they administer *their* server, not this deployment. So the placeholder set is
fixed and substituted literally.

**This feature silently needs a privileged intent.** `on_member_join` and
`on_member_remove` never fire without Server Members, and the failure mode is a
greeting that simply does not happen with nothing in any log. `cog_load` says so
once, at startup, rather than leaving somebody to debug silence in six months.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks

from zephyr.core.logging import get_logger
from zephyr.db import greetings as repo

log = get_logger(__name__)

# What an administrator may put in a greeting. A fixed set, substituted
# literally -- see the module docstring for why this is not str.format.
PLACEHOLDERS = ("{user}", "{mention}", "{username}", "{server}", "{count}")

# A greeting longer than this is not a greeting. Discord's own message limit is
# 2000; this leaves room for the substitutions to grow the text.
MAX_MESSAGE_CHARS = 1200

DEFAULT_WELCOME = "Welcome to **{server}**, {mention}! You are member #{count}."
DEFAULT_FAREWELL = "**{user}** has left **{server}**."


def render(template: str, member, guild) -> str:
    """Substitute the fixed placeholder set into ``template``.

    Literal replacement rather than ``str.format``: see the module docstring.
    The consequence worth knowing is that a stray brace in a greeting is simply
    left alone instead of raising, which is the right outcome for text somebody
    typed into a Discord modal.
    """
    # display_name, then name, then str(): a discord.User has no nickname, and
    # a raw payload object may have neither -- but str() of an object with no
    # __str__ is a repr, which must never reach a channel.
    display = getattr(member, "display_name", None) or getattr(member, "name", None) or str(member)
    values = {
        "{user}": str(display),
        "{mention}": getattr(member, "mention", str(display)),
        "{username}": str(getattr(member, "name", display)),
        "{server}": str(getattr(guild, "name", "this server")),
        "{count}": str(getattr(guild, "member_count", None) or "?"),
    }
    text = str(template)
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    return text[:2000]


class GreetingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Guild id -> the stored row. Consulted on every join and every leave,
        # which is why it is cached rather than read per event.
        self._cache: dict[str, dict] = {}

    async def cog_load(self):
        self._refresh_loop.start()
        if not self.bot.intents.members:
            # Said once, loudly, because the alternative is a greeting that
            # never fires with nothing anywhere explaining why.
            log.error(
                "Greetings are enabled but the Server Members intent is off. "
                "on_member_join will never fire. Enable it in the Discord "
                "Developer Portal under Bot > Privileged Gateway Intents."
            )

    def cog_unload(self):
        self._refresh_loop.cancel()

    @tasks.loop(minutes=10)
    async def _refresh_loop(self):
        try:
            self._cache = await asyncio.to_thread(repo.read_all)
        except Exception:
            # The old cache is kept rather than cleared: serving a slightly
            # stale greeting beats serving none because one read failed.
            log.exception("Could not refresh the greetings cache")

    @_refresh_loop.before_loop
    async def _before_refresh_loop(self):
        await self.bot.wait_until_ready()

    # ---- listeners -------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self._greet(member, kind="welcome")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._greet(member, kind="farewell")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Forget a removed guild's greetings.

        Not merely tidiness: a channel id is a pointer into a server this
        deployment can no longer see, and keeping it means the row is still in
        `read_all`'s result -- so the cache carries a guild the bot cannot post
        in, forever.
        """
        try:
            await asyncio.to_thread(repo.delete_for_guild, str(guild.id))
        except Exception:
            log.exception("Could not forget greetings for guild %s", guild.id)
        self._cache.pop(str(guild.id), None)

    async def _greet(self, member, *, kind: str) -> bool:
        """Post one greeting. Every failure is contained.

        Contained and returning, not raising: an unhandled exception in a
        listener is logged by discord.py and otherwise invisible, and a guild
        whose greeting channel was deleted must not turn every subsequent join
        into a traceback.
        """
        guild = getattr(member, "guild", None)
        if guild is None:
            return False
        config = self._cache.get(str(guild.id))
        if not config or not config.get(f"{kind}_enabled"):
            return False

        channel_id = config.get(f"{kind}_channel_id")
        if not channel_id:
            # Enabled with no channel is a half-finished setup, not an error.
            # The commands refuse to leave it in that state; a dashboard save
            # or a deleted channel can still produce it.
            log.info("%s is enabled in guild %s with no channel", kind, guild.id)
            return False

        channel = guild.get_channel(int(channel_id))
        if channel is None:
            log.warning("The %s channel %s is gone in guild %s", kind, channel_id, guild.id)
            return False

        template = config.get(f"{kind}_message") or (
            DEFAULT_WELCOME if kind == "welcome" else DEFAULT_FAREWELL
        )
        try:
            await channel.send(
                render(template, member, guild),
                # A greeting names somebody, and mentioning them is the point of
                # a welcome. Everything else is suppressed: a template
                # containing @everyone would otherwise let one saved setting
                # ping the whole server on every join.
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=True
                ),
            )
        except discord.Forbidden:
            log.warning("Cannot post the %s greeting in %s", kind, channel_id)
            return False
        except Exception:
            log.exception("Could not post the %s greeting in guild %s", kind, guild.id)
            return False
        return True

    # ---- commands --------------------------------------------------------

    @app_commands.command(name="welcome", description="Set up the welcome message.")
    @app_commands.describe(
        channel="Where to post it. Leave empty to turn welcomes off.",
        message="The text. Placeholders: {user} {mention} {username} {server} {count}",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def welcome(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        message: str | None = None,
    ):
        await self._configure(interaction, kind="welcome", channel=channel, message=message)

    @app_commands.command(name="farewell", description="Set up the farewell message.")
    @app_commands.describe(
        channel="Where to post it. Leave empty to turn farewells off.",
        message="The text. Placeholders: {user} {username} {server} {count}",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def farewell(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        message: str | None = None,
    ):
        await self._configure(interaction, kind="farewell", channel=channel, message=message)

    async def _configure(self, interaction, *, kind, channel, message):
        await interaction.response.defer(ephemeral=True)

        if channel is None:
            await asyncio.to_thread(
                repo.write, str(interaction.guild.id), {f"{kind}_enabled": False}
            )
            await self._reload_cache()
            await interaction.followup.send(f"✅ {kind.title()} messages are off.", ephemeral=True)
            return

        if not channel.permissions_for(interaction.guild.me).send_messages:
            # Refused now rather than discovered at the first join: a greeting
            # pointed at an unreachable channel fails silently forever, and
            # nobody has a reason to look.
            await interaction.followup.send(f"❌ I cannot post in {channel.mention}.", ephemeral=True)
            return

        text = (message or "").strip()
        if len(text) > MAX_MESSAGE_CHARS:
            await interaction.followup.send(
                f"❌ That is too long — keep it under {MAX_MESSAGE_CHARS} characters.",
                ephemeral=True,
            )
            return

        stored = await asyncio.to_thread(
            repo.write,
            str(interaction.guild.id),
            {
                f"{kind}_enabled": True,
                f"{kind}_channel_id": str(channel.id),
                # None, not "", when nothing was given: NULL means "use the
                # default", and an empty string would mean "post nothing",
                # which Discord rejects.
                f"{kind}_message": text or None,
            },
        )
        await self._reload_cache()

        preview = render(
            stored.get(f"{kind}_message")
            or (DEFAULT_WELCOME if kind == "welcome" else DEFAULT_FAREWELL),
            interaction.user,
            interaction.guild,
        )
        await interaction.followup.send(
            f"✅ {kind.title()} messages will be posted in {channel.mention}.\n\n"
            f"**Preview**\n{preview}",
            # The preview renders a mention, and a preview that pinged somebody
            # would be a greeting fired by a settings command.
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True,
        )

    @app_commands.command(name="greeting-preview", description="See how your greetings will look.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def greeting_preview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        config = await asyncio.to_thread(repo.read, str(interaction.guild.id))
        if not config or not (config["welcome_enabled"] or config["farewell_enabled"]):
            await interaction.followup.send(
                "No greetings are set up. Use `/welcome` or `/farewell`.", ephemeral=True
            )
            return

        embed = discord.Embed(title="Greetings", color=discord.Color.blurple())
        for kind, default in (("welcome", DEFAULT_WELCOME), ("farewell", DEFAULT_FAREWELL)):
            if not config[f"{kind}_enabled"]:
                embed.add_field(name=kind.title(), value="*off*", inline=False)
                continue
            channel_id = config[f"{kind}_channel_id"]
            embed.add_field(
                name=f"{kind.title()} · <#{channel_id}>" if channel_id else kind.title(),
                value=render(
                    config[f"{kind}_message"] or default, interaction.user, interaction.guild
                ),
                inline=False,
            )
        await interaction.followup.send(
            embed=embed, allowed_mentions=discord.AllowedMentions.none(), ephemeral=True
        )

    async def _reload_cache(self):
        try:
            self._cache = await asyncio.to_thread(repo.read_all)
        except Exception:
            log.exception("Could not refresh the greetings cache after a save")


async def setup(bot: commands.Bot):
    await bot.add_cog(GreetingsCog(bot))
