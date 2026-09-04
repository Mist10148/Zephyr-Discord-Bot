"""/export-my-data and /delete-my-data.

Discord requires a privacy policy for app verification, and a policy that
describes a deletion path has to have one. This is that path.

The dashboard's AI purge already implemented deletion for the largest single
data category, and `/forget` did it per channel -- but neither covered a person
asking about *themselves* across every store, and neither gave them a way to see
what was held in the first place.
"""

import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.config import REDIS_URL
from zephyr.core.logging import get_logger
from zephyr.db import personal_data

log = get_logger(__name__)

# Discord's attachment ceiling on a free server is 8 MiB. An export of this
# shape reaches a few hundred kilobytes at most, so this is a guard against a
# pathological account rather than an expected path.
MAX_EXPORT_BYTES = 7_000_000


class PrivacyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="export-my-data",
        description="Send you a copy of everything Zephyr has stored about you.",
    )
    async def export_my_data(self, interaction: discord.Interaction):
        """Delivered by DM, with an ephemeral fallback.

        This is the part that has to be right: an export contains DM
        transcripts and every server the person has configured, and answering
        non-ephemerally in a channel would publish it to everybody there. DM
        first because a file in a DM survives being read; ephemeral second
        because a closed DM must not mean no export at all.
        """
        await interaction.response.defer(ephemeral=True)

        try:
            payload = await self.bot.loop.run_in_executor(
                None, lambda: personal_data.export(str(interaction.user.id))
            )
        except Exception:
            log.exception("Could not build a data export for %s", interaction.user.id)
            await interaction.followup.send(
                "I could not build your export just now — try again shortly.", ephemeral=True
            )
            return

        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_EXPORT_BYTES:
            await interaction.followup.send(
                "Your export is too large to send as a file. Ask a server admin to "
                "contact the operator and it can be sent another way.",
                ephemeral=True,
            )
            return

        summary = _summarise(payload)
        try:
            await interaction.user.send(
                f"Here is everything I have stored about you.\n\n{summary}",
                file=discord.File(io.BytesIO(body), filename=f"zephyr-data-{interaction.user.id}.json"),
            )
        except discord.Forbidden:
            # DMs closed. The file still has to reach them, and an ephemeral
            # reply is visible only to the person who asked.
            await interaction.followup.send(
                "Your DMs are closed, so here it is privately instead — only you can see this.\n\n"
                + summary,
                file=discord.File(io.BytesIO(body), filename=f"zephyr-data-{interaction.user.id}.json"),
                ephemeral=True,
            )
            return
        except Exception:
            log.exception("Could not deliver a data export to %s", interaction.user.id)
            await interaction.followup.send(
                "I could not send your export — try again shortly.", ephemeral=True
            )
            return

        await interaction.followup.send("Sent to your DMs.", ephemeral=True)

    @app_commands.command(
        name="delete-my-data",
        description="Permanently delete everything Zephyr has stored about you.",
    )
    async def delete_my_data(self, interaction: discord.Interaction):
        """Confirmed, because it cannot be undone.

        A destructive action with no confirmation is the defect A1 fixed on the
        web with ConfirmSheet; the same reasoning applies to a slash command
        that erases everything.
        """
        await interaction.response.send_message(
            embed=_confirm_embed(),
            view=_ConfirmDeleteView(interaction.user.id),
            ephemeral=True,
        )


def _summarise(payload: dict) -> str:
    """A readable count, so the file does not have to be opened to see the shape."""
    lines = []
    if payload.get("dashboard_account"):
        lines.append("• a dashboard sign-in record")
    if payload.get("bot_preferences"):
        lines.append("• your weather and AI preferences")
    playlists = payload.get("playlists") or []
    if playlists:
        tracks = sum(len(item.get("tracks") or []) for item in playlists)
        lines.append(f"• {len(playlists)} playlist(s), {tracks} track(s)")
    audit = payload.get("audit_entries") or []
    if audit:
        lines.append(f"• {len(audit)} audit entr{'y' if len(audit) == 1 else 'ies'}")
    messages = payload.get("ai_messages") or []
    if messages:
        lines.append(f"• {len(messages)} AI message(s)")
    if not lines:
        return "It is empty — I have nothing stored about you."
    return "It contains:\n" + "\n".join(lines)


def _confirm_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Delete everything?",
        description=(
            "This removes your dashboard record, your weather and AI preferences, "
            "your saved playlists and your AI messages, and anonymises your entries "
            "in every server's audit log.\n\n"
            "**This cannot be undone.** Run `/export-my-data` first if you want a copy."
        ),
        color=discord.Color.red(),
    )
    # Named, because "everything" that quietly excludes two things is not
    # everything, and somebody should not discover that afterwards.
    embed.add_field(
        name="What stays",
        value=(
            "Server audit history stays for the server owner, with your id removed. "
            "Shared AI conversations stay for the other people in them — only your "
            "own messages go."
        ),
        inline=False,
    )
    embed.add_field(name="Sessions", value=personal_data.SESSION_CAVEAT, inline=False)
    return embed


class _ConfirmDeleteView(discord.ui.View):
    """Two buttons, owned by the person who ran the command."""

    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # The message is ephemeral, so nobody else should be able to reach it --
        # but a destructive confirmation is the wrong place to rely on that.
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("That is not yours to confirm.", ephemeral=True)
        return False

    @discord.ui.button(label="Delete everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)

        try:
            removed = await interaction.client.loop.run_in_executor(
                None, lambda: personal_data.delete(user_id)
            )
        except Exception:
            log.exception("Could not delete stored data for %s", user_id)
            await interaction.followup.send(
                "I could not complete that — nothing has been deleted. Try again shortly.",
                ephemeral=True,
            )
            return

        # Best-effort, and after the durable deletion: a counter that expires
        # within 48 hours anyway must not be able to fail the request.
        personal_data.clear_usage_counters(user_id, redis_url=REDIS_URL)

        # The in-process AI buffer is separate from the stored rows, and would
        # otherwise keep answering with context that has just been erased.
        try:
            from zephyr.services.gemini import forget_user_buffers

            forget_user_buffers(user_id)
        except Exception:
            log.warning("Could not clear in-memory AI buffers for %s", user_id, exc_info=True)

        for child in self.children:
            child.disabled = True
        counted = "\n".join(
            f"• {key.replace('_', ' ')}: {value}" for key, value in removed.items() if value
        )
        await interaction.followup.send(
            "Done. Everything erasable has been deleted."
            + (f"\n\n{counted}" if counted else "\n\nThere was nothing stored about you."),
            ephemeral=True,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="Cancelled — nothing was deleted.", embed=None, view=None
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrivacyCog(bot))
