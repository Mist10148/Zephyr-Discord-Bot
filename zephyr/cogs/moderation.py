"""Moderation commands, a numbered case log, and a modlog channel.

The interesting code in here is ``_can_act``, and it is worth saying why up
front: it is a **security boundary**, not a courtesy check. Discord's own
permission system stops a moderator kicking somebody above them *in the client*,
but a bot acts with the bot's permissions, so a `/ban` routed through Zephyr is
checked against Zephyr's role, not the caller's. Without an explicit hierarchy
comparison, a junior moderator with `Ban Members` could ban an administrator --
the API would allow it, because the bot is allowed to. Every command that acts
on a member goes through it, and it is tested as the boundary it is.

The other decision worth recording is that a case is written *after* the action
succeeds, never before. A case log that records attempts is not a case log; and
if the record fails, the moderator is told the action was taken but not written
down, because silently discarding it would make "three prior warnings" a
statement nobody can rely on.
"""

import asyncio
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.core.logging import get_logger
from zephyr.db import mod_cases as repo
from zephyr.db.guild_settings import read_guild_settings, write_guild_settings

log = get_logger(__name__)

# Discord's own ceiling on a communication timeout. Enforced here so the
# refusal names the real limit instead of surfacing a 400 from the API.
MAX_TIMEOUT_SECONDS = 28 * 86400
MIN_TIMEOUT_SECONDS = 60

# `purge` is bulk_delete, which Discord refuses for messages older than 14 days
# and caps at 100 per call.
MAX_PURGE = 100

# How many recent cases /cases shows. An embed caps at 25 fields, and a
# moderator deciding what to do needs the recent ones, not all of them.
CASES_SHOWN = 10

# One colour per action, so a modlog channel is skimmable. Kept local: 16.1
# introduces the shared embed factory that collapses these, and inventing a
# global palette here would be the thing 16.1 then has to undo.
_COLOURS = {
    "warn": discord.Color.gold(),
    "timeout": discord.Color.orange(),
    "untimeout": discord.Color.green(),
    "kick": discord.Color.orange(),
    "ban": discord.Color.red(),
    "unban": discord.Color.green(),
    "purge": discord.Color.blurple(),
}
_VERBS = {
    "warn": "Warned",
    "timeout": "Timed out",
    "untimeout": "Timeout removed",
    "kick": "Kicked",
    "ban": "Banned",
    "unban": "Unbanned",
    "purge": "Purged messages",
}

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}


def parse_duration(value: str) -> int | None:
    """"10m", "2h", "1d" -> seconds. None when unreadable.

    A second, smaller parser than ``reminders.parse_delay`` on purpose: a
    timeout does not want compound forms, and accepting "1h30m" here would
    invite a moderator to type a duration Discord then rounds.
    """
    text = str(value).strip().lower().replace(" ", "")
    for index, character in enumerate(text):
        if not character.isdigit():
            amount, unit = text[:index], text[index:]
            if not amount or unit not in _UNITS:
                return None
            return int(amount) * _UNITS[unit]
    return None


def hierarchy_refusal(actor, target, me) -> str | None:
    """Why ``actor`` may not moderate ``target``, or None if they may.

    A free function so the boundary can be tested without a gateway. The order
    matters: the identity checks come first because they are true regardless of
    roles, and the owner exemption comes before the role comparison because the
    owner's roles do not necessarily outrank anybody's.
    """
    guild = getattr(target, "guild", None)
    if actor.id == target.id:
        return "You cannot moderate yourself."
    if me is not None and target.id == me.id:
        return "I am not going to moderate myself."
    if guild is not None and target.id == getattr(guild, "owner_id", None):
        return "That is the server owner."

    # The bot's own reach is checked before the caller's: if Zephyr cannot act,
    # saying so is more useful than a permission lecture the caller cannot fix
    # by having more permissions.
    if me is not None and target.top_role >= me.top_role:
        return (
            "My highest role is not above theirs, so Discord will not let me act. "
            "Move Zephyr's role up."
        )

    # The whole point of this function. Discord checks the *bot's* hierarchy,
    # not the caller's, so without this a junior moderator could act on an
    # administrator through Zephyr.
    if guild is not None and actor.id == getattr(guild, "owner_id", None):
        return None
    if target.top_role >= actor.top_role:
        return "Their highest role is not below yours."
    return None


def humanise_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    for unit, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{seconds} seconds"


def case_embed(case: dict) -> discord.Embed:
    """The modlog entry, and also what /case renders.

    One function for both so a case cannot look like two different things
    depending on where it is read.
    """
    verb = _VERBS.get(case["action"], case["action"].title())
    embed = discord.Embed(
        title=f"Case #{case['case_number']} · {verb}",
        color=_COLOURS.get(case["action"], discord.Color.greyple()),
    )
    target = case.get("target_tag") or f"<@{case['target_id']}>"
    embed.add_field(name="User", value=f"{target}\n`{case['target_id']}`", inline=True)
    embed.add_field(name="Moderator", value=f"<@{case['moderator_id']}>", inline=True)
    if case.get("duration_seconds"):
        embed.add_field(name="Duration", value=humanise_duration(case["duration_seconds"]), inline=True)
    embed.add_field(
        name="Reason",
        # The distinction is deliberate: no reason yet is a thing /reason fixes,
        # and rendering it as "—" would hide that it is still outstanding.
        value=case.get("reason") or f"*None given — add one with `/reason {case['case_number']}`*",
        inline=False,
    )
    return embed


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def bridge_actions(self):
        """Read-only, deliberately.

        The dashboard has no moderation surface yet, and exposing `mod.ban`
        over the bridge before there is a UI to drive it would add a
        privileged action with no consumer. It is also the case that a REST
        moderation action can exceed the bridge's 5s COMMAND_TIMEOUT, so
        whoever adds one must answer immediately and do the REST work in a
        `create_task` -- a caller that gets no reply reports the bot as
        offline, which is a much worse diagnosis than the real error.
        """
        return {"mod.cases": self._bridge_cases}

    async def _bridge_cases(self, guild, actor_id, args):
        if guild is None:
            raise LookupError("Zephyr is not in that server.")
        args = args or {}
        return await asyncio.to_thread(
            repo.read,
            str(guild.id),
            limit=int(args.get("limit") or repo.DEFAULT_LIMIT),
            before_number=args.get("before_number"),
            action=args.get("action"),
            target_id=args.get("target_id"),
        )

    # ---- the shared spine of every command -------------------------------

    async def _record_and_log(
        self,
        interaction: discord.Interaction,
        *,
        action: str,
        target_id: str,
        target_tag: str | None,
        reason: str | None,
        duration_seconds: int | None = None,
    ) -> dict | None:
        """Write the case, then post it to the modlog. Returns the case.

        Called only after the action itself succeeded. A failure to record is
        reported rather than swallowed -- see the module docstring -- but a
        failure to *post* is not, because the case is already durable and the
        modlog is a convenience.
        """
        case = await asyncio.to_thread(
            repo.record,
            guild_id=str(interaction.guild.id),
            action=action,
            target_id=str(target_id),
            target_tag=target_tag,
            moderator_id=str(interaction.user.id),
            reason=reason,
            duration_seconds=duration_seconds,
        )
        await self._post_to_modlog(interaction.guild, case)
        return case

    async def _post_to_modlog(self, guild: discord.Guild, case: dict) -> bool:
        try:
            settings = await asyncio.to_thread(read_guild_settings, str(guild.id))
        except Exception:
            log.exception("Could not read the modlog channel for %s", guild.id)
            return False

        channel_id = (settings or {}).get("modlog_channel_id")
        if not channel_id:
            return False
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            log.warning("Modlog channel %s is gone in guild %s", channel_id, guild.id)
            return False
        try:
            await channel.send(embed=case_embed(case))
        except discord.HTTPException:
            # Contained: the case is already written, and failing the command
            # now would tell a moderator their ban did not happen.
            log.warning("Could not post case #%s to the modlog", case["case_number"], exc_info=True)
            return False
        return True

    async def _guard(self, interaction: discord.Interaction, target) -> bool:
        """Refuse and explain, or allow. Assumes the response is deferred."""
        refusal = hierarchy_refusal(interaction.user, target, interaction.guild.me)
        if refusal is None:
            return True
        await interaction.followup.send(f"❌ {refusal}", ephemeral=True)
        return False

    async def _notify_target(self, member, *, guild_name: str, action: str, reason: str | None):
        """Tell the person what happened, best effort.

        A moderated user with closed DMs is the normal case, not an error, so
        this never raises -- and it is done *before* a kick or ban, because
        afterwards there is no shared server left to DM through.
        """
        verb = _VERBS.get(action, action)
        text = f"{verb} in **{guild_name}**."
        if reason:
            text += f"\nReason: {reason}"
        try:
            await member.send(text)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            log.info("Could not DM %s about a %s", getattr(member, "id", "?"), action)

    # ---- commands --------------------------------------------------------

    @app_commands.command(name="warn", description="Warn a member and record a case.")
    @app_commands.describe(member="Who to warn", reason="Why")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction, member):
            return

        await self._notify_target(
            member, guild_name=interaction.guild.name, action="warn", reason=reason
        )
        case = await self._record_and_log(
            interaction, action="warn", target_id=member.id, target_tag=str(member), reason=reason
        )
        prior = await asyncio.to_thread(
            repo.count_for_target, str(interaction.guild.id), str(member.id), action="warn"
        )
        await interaction.followup.send(
            f"✅ Warned {member.mention} — case **#{case['case_number']}** "
            f"({prior} warning{'s' if prior != 1 else ''} on record).",
            ephemeral=True,
        )

    @app_commands.command(name="timeout", description="Time a member out.")
    @app_commands.describe(member="Who", duration="How long — e.g. 10m, 2h, 1d", reason="Why")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.followup.send(
                "❌ I could not read that duration. Try `10m`, `2h` or `1d`.", ephemeral=True
            )
            return
        if not MIN_TIMEOUT_SECONDS <= seconds <= MAX_TIMEOUT_SECONDS:
            # Named rather than clamped: silently shortening a 60-day timeout to
            # 28 days would look like the command worked as asked.
            await interaction.followup.send(
                f"❌ A timeout has to be between {humanise_duration(MIN_TIMEOUT_SECONDS)} "
                f"and {humanise_duration(MAX_TIMEOUT_SECONDS)}.",
                ephemeral=True,
            )
            return
        if not await self._guard(interaction, member):
            return

        try:
            await member.timeout(timedelta(seconds=seconds), reason=_audit_reason(interaction, reason))
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord refused that. Check that I have **Moderate Members**.", ephemeral=True
            )
            return

        await self._notify_target(
            member, guild_name=interaction.guild.name, action="timeout", reason=reason
        )
        case = await self._record_and_log(
            interaction, action="timeout", target_id=member.id, target_tag=str(member),
            reason=reason, duration_seconds=seconds,
        )
        await interaction.followup.send(
            f"✅ Timed out {member.mention} for {humanise_duration(seconds)} — "
            f"case **#{case['case_number']}**.",
            ephemeral=True,
        )

    @app_commands.command(name="untimeout", description="Lift a member's timeout.")
    @app_commands.describe(member="Who", reason="Why")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(
        self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction, member):
            return
        try:
            await member.timeout(None, reason=_audit_reason(interaction, reason))
        except discord.Forbidden:
            await interaction.followup.send("❌ Discord refused that.", ephemeral=True)
            return

        case = await self._record_and_log(
            interaction, action="untimeout", target_id=member.id, target_tag=str(member),
            reason=reason,
        )
        await interaction.followup.send(
            f"✅ Timeout lifted for {member.mention} — case **#{case['case_number']}**.",
            ephemeral=True,
        )

    @app_commands.command(name="kick", description="Kick a member.")
    @app_commands.describe(member="Who", reason="Why")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction, member):
            return

        # Before the kick: afterwards there is no shared server to DM through.
        await self._notify_target(
            member, guild_name=interaction.guild.name, action="kick", reason=reason
        )
        try:
            await member.kick(reason=_audit_reason(interaction, reason))
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord refused that. Check that I have **Kick Members**.", ephemeral=True
            )
            return

        case = await self._record_and_log(
            interaction, action="kick", target_id=member.id, target_tag=str(member), reason=reason
        )
        await interaction.followup.send(
            f"✅ Kicked {member} — case **#{case['case_number']}**.", ephemeral=True
        )

    @app_commands.command(name="ban", description="Ban a user, whether or not they are here.")
    @app_commands.describe(
        user="Who — a member, or a user id for somebody who already left",
        reason="Why",
        delete_message_days="Delete their messages from the last N days (0–7)",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str | None = None,
        delete_message_days: app_commands.Range[int, 0, 7] = 0,
    ):
        await interaction.response.defer(ephemeral=True)

        # A User, not a Member, so somebody who already left can be banned by
        # id -- which is the case a raid actually produces. The hierarchy check
        # only applies when they are still here: a non-member has no roles in
        # this guild to compare, and Discord itself imposes no hierarchy on
        # banning somebody who is not present.
        member = interaction.guild.get_member(user.id)
        if member is not None and not await self._guard(interaction, member):
            return

        if member is not None:
            await self._notify_target(
                member, guild_name=interaction.guild.name, action="ban", reason=reason
            )
        try:
            await interaction.guild.ban(
                user,
                reason=_audit_reason(interaction, reason),
                delete_message_days=int(delete_message_days),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord refused that. Check that I have **Ban Members**.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Discord refused that: {exc.text}", ephemeral=True)
            return

        case = await self._record_and_log(
            interaction, action="ban", target_id=user.id, target_tag=str(user), reason=reason
        )
        await interaction.followup.send(
            f"✅ Banned {user} — case **#{case['case_number']}**.", ephemeral=True
        )

    @app_commands.command(name="unban", description="Lift a ban by user id.")
    @app_commands.describe(user_id="The banned account's id", reason="Why")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(
        self, interaction: discord.Interaction, user_id: str, reason: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        # A string, not an int: a snowflake exceeds what Discord's integer
        # option type round-trips safely, and pasting an id is how this command
        # is always used.
        if not user_id.strip().isdigit():
            await interaction.followup.send("❌ That is not a user id.", ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=_audit_reason(interaction, reason))
        except discord.NotFound:
            await interaction.followup.send(
                "❌ No such account, or it is not banned here.", ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ Discord refused that.", ephemeral=True)
            return

        case = await self._record_and_log(
            interaction, action="unban", target_id=user.id, target_tag=str(user), reason=reason
        )
        await interaction.followup.send(
            f"✅ Unbanned {user} — case **#{case['case_number']}**.", ephemeral=True
        )

    @app_commands.command(name="purge", description="Bulk-delete recent messages in this channel.")
    @app_commands.describe(
        amount="How many messages to scan (1–100)",
        member="Only delete this person's messages",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, MAX_PURGE],
        member: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(
                limit=int(amount),
                check=(lambda message: message.author.id == member.id) if member else None,
                reason=_audit_reason(interaction, None),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Discord refused that. Check that I have **Manage Messages**.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            # The 14-day bulk-delete limit lands here, and the raw error names
            # it better than a guess would.
            await interaction.followup.send(f"❌ Discord refused that: {exc.text}", ephemeral=True)
            return

        # The target is the channel, not a person: a purge is an action on a
        # place, and recording a member as the "target" of a channel-wide purge
        # would put a case on somebody's record that is not about them.
        case = await self._record_and_log(
            interaction,
            action="purge",
            target_id=member.id if member else interaction.channel_id,
            target_tag=str(member) if member else f"#{interaction.channel}",
            reason=f"{len(deleted)} message(s) in #{interaction.channel}",
        )
        await interaction.followup.send(
            f"🗑️ Deleted {len(deleted)} message(s) — case **#{case['case_number']}**.",
            ephemeral=True,
        )

    @app_commands.command(name="cases", description="A member's moderation history here.")
    @app_commands.describe(member="Who")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def cases(self, interaction: discord.Interaction, member: discord.User):
        await interaction.response.defer(ephemeral=True)
        page = await asyncio.to_thread(
            repo.read, str(interaction.guild.id), limit=CASES_SHOWN, target_id=str(member.id)
        )
        total = await asyncio.to_thread(
            repo.count_for_target, str(interaction.guild.id), str(member.id)
        )
        if not page["entries"]:
            await interaction.followup.send(f"{member} has no cases here.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Cases for {member}",
            description=f"{total} in total; showing the {len(page['entries'])} most recent.",
            color=discord.Color.greyple(),
        )
        for case in page["entries"]:
            embed.add_field(
                name=f"#{case['case_number']} · {_VERBS.get(case['action'], case['action'])}",
                value=f"{case['reason'] or '*no reason recorded*'}\n"
                      f"by <@{case['moderator_id']}> · {case['created_at'][:10]}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="case", description="Look up one case by number.")
    @app_commands.describe(number="The case number")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def case(self, interaction: discord.Interaction, number: int):
        await interaction.response.defer(ephemeral=True)
        found = await asyncio.to_thread(repo.get, str(interaction.guild.id), int(number))
        if found is None:
            await interaction.followup.send(f"❌ No case #{number} here.", ephemeral=True)
            return
        await interaction.followup.send(embed=case_embed(found), ephemeral=True)

    @app_commands.command(name="reason", description="Add or replace a case's reason.")
    @app_commands.describe(number="The case number", reason="The reason to record")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def reason(self, interaction: discord.Interaction, number: int, reason: str):
        await interaction.response.defer(ephemeral=True)
        updated = await asyncio.to_thread(
            repo.set_reason, str(interaction.guild.id), int(number), reason
        )
        if updated is None:
            await interaction.followup.send(f"❌ No case #{number} here.", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Case **#{number}** updated.", embed=case_embed(updated), ephemeral=True
        )

    @app_commands.command(name="modlog", description="Choose where moderation cases are posted.")
    @app_commands.describe(channel="The channel, or leave empty to turn the modlog off")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def modlog(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        if channel is not None:
            # Checked now rather than discovered at the first case: a modlog
            # pointed at a channel the bot cannot post in fails silently
            # forever, and the person who set it has no reason to look.
            if not channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.followup.send(
                    f"❌ I cannot post in {channel.mention}.", ephemeral=True
                )
                return

        await asyncio.to_thread(
            write_guild_settings,
            str(interaction.guild.id),
            {"modlog_channel_id": str(channel.id) if channel else None},
        )
        await interaction.followup.send(
            f"✅ Moderation cases will be posted in {channel.mention}." if channel
            else "✅ The modlog is off. Cases are still recorded and readable with `/case`.",
            ephemeral=True,
        )


def _audit_reason(interaction: discord.Interaction, reason: str | None) -> str:
    """What Discord's own audit log shows.

    Zephyr's name alone would make every action look like the bot's decision,
    so the moderator is named -- this is the only trace in Discord's audit log
    of who actually asked.
    """
    who = f"{interaction.user} ({interaction.user.id})"
    return f"{who}: {reason}" if reason else who


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
