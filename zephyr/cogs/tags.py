"""Tags: custom per-server responses.

**Tag content is member-written text posted by the bot, and that is the whole
security surface here.** Zephyr can mention `@everyone` in servers where the
members who write tags cannot, so a tag is a way to borrow the bot's
permissions: without suppression, `/tag-create ping @everyone` hands every
member a permanent mass-ping button. Every reply that renders tag content passes
`AllowedMentions.none()`.

Nothing templates the content -- there are no placeholders and no `str.format` --
so the greetings cog's attribute-traversal problem does not arise here. It is
worth saying rather than leaving implicit: the next person to add `{user}`
support to tags has to reach for `greetings.render`'s literal substitution and
not for a format string.

Slash-only, deliberately. A prefix trigger is now possible (14.1 made the prefix
per-guild), and it would mean a database lookup on every message that begins with
the prefix -- the cost the activity cog exists to avoid, paid for a convenience.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.core.logging import get_logger
from zephyr.db import tags as repo
from zephyr.db.tags import MAX_CONTENT_CHARS, MAX_NAME_CHARS, MAX_TAGS_PER_GUILD, TagError
from zephyr.utils.autocomplete import MAX_CHOICES, cached, truncate

log = get_logger(__name__)

# Shown by /tag-list. An embed description holds 4096 characters and a listing
# longer than this is a wall rather than a list.
LIST_LIMIT = 50

NO_MENTIONS = discord.AllowedMentions.none()


class TagsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _may_manage(self, interaction: discord.Interaction, tag: dict) -> bool:
        """Whether the caller may edit or delete ``tag``.

        The author, or anybody with Manage Messages. The author exemption is
        what makes tags usable without an administrator in the loop; the
        permission is what lets a server clean up after somebody who left.
        """
        if str(tag["created_by"]) == str(interaction.user.id):
            return True
        permissions = getattr(interaction.user, "guild_permissions", None)
        return bool(permissions and permissions.manage_messages)

    # ---- commands --------------------------------------------------------

    @app_commands.command(name="tag", description="Show a tag.")
    @app_commands.describe(name="Which tag")
    @app_commands.guild_only()
    async def tag(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        found = await asyncio.to_thread(repo.get, str(interaction.guild.id), name)
        if found is None:
            await interaction.followup.send(
                f"❌ There is no tag called `{name}` here. `/tag-list` shows them all.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            found["content"],
            # The point of this whole module's docstring. Zephyr can mention
            # @everyone where the tag's author cannot, so without this a tag is
            # a permanent mass-ping button for every member.
            allowed_mentions=NO_MENTIONS,
        )
        # After the reply, and contained: a counter is not worth failing a
        # successful lookup over.
        try:
            await asyncio.to_thread(repo.record_use, str(interaction.guild.id), found["name"])
        except Exception:
            log.warning("Could not count a use of tag %r", found["name"], exc_info=True)

    @app_commands.command(name="tag-create", description="Create a tag.")
    @app_commands.describe(
        name=f"1-{MAX_NAME_CHARS} characters: letters, numbers, dashes, underscores",
        content="What the tag says",
    )
    @app_commands.guild_only()
    async def tag_create(self, interaction: discord.Interaction, name: str, content: str):
        await interaction.response.defer(ephemeral=True)
        try:
            created = await asyncio.to_thread(
                repo.create,
                guild_id=str(interaction.guild.id),
                name=name,
                content=content,
                created_by=str(interaction.user.id),
            )
        except TagError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        total = await asyncio.to_thread(repo.count_for_guild, str(interaction.guild.id))
        await interaction.followup.send(
            f"✅ Created `{created['name']}` ({total} of {MAX_TAGS_PER_GUILD} tags used).\n\n"
            f"**Preview**\n{created['content'][:500]}",
            # A preview renders the content, so it needs the same suppression as
            # the tag itself -- otherwise creating the tag is the ping.
            allowed_mentions=NO_MENTIONS,
            ephemeral=True,
        )

    @app_commands.command(name="tag-edit", description="Change what a tag says.")
    @app_commands.describe(name="Which tag", content="The new content")
    @app_commands.guild_only()
    async def tag_edit(self, interaction: discord.Interaction, name: str, content: str):
        await interaction.response.defer(ephemeral=True)
        found = await asyncio.to_thread(repo.get, str(interaction.guild.id), name)
        if found is None:
            await interaction.followup.send(f"❌ There is no tag called `{name}` here.", ephemeral=True)
            return
        if not self._may_manage(interaction, found):
            await interaction.followup.send(
                "❌ That is not your tag. Only its author or somebody with "
                "**Manage Messages** can change it.",
                ephemeral=True,
            )
            return

        try:
            updated = await asyncio.to_thread(
                repo.edit, str(interaction.guild.id), found["name"], content
            )
        except TagError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ Updated `{found['name']}`.\n\n**Preview**\n{updated['content'][:500]}",
            allowed_mentions=NO_MENTIONS,
            ephemeral=True,
        )

    @app_commands.command(name="tag-delete", description="Delete a tag.")
    @app_commands.describe(name="Which tag")
    @app_commands.guild_only()
    async def tag_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        found = await asyncio.to_thread(repo.get, str(interaction.guild.id), name)
        if found is None:
            await interaction.followup.send(f"❌ There is no tag called `{name}` here.", ephemeral=True)
            return
        if not self._may_manage(interaction, found):
            await interaction.followup.send(
                "❌ That is not your tag. Only its author or somebody with "
                "**Manage Messages** can delete it.",
                ephemeral=True,
            )
            return

        await asyncio.to_thread(repo.remove, str(interaction.guild.id), found["name"])
        await interaction.followup.send(f"🗑️ Deleted `{found['name']}`.", ephemeral=True)

    @app_commands.command(name="tag-list", description="Every tag in this server.")
    @app_commands.guild_only()
    async def tag_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await asyncio.to_thread(
            repo.list_for_guild, str(interaction.guild.id), limit=LIST_LIMIT
        )
        total = await asyncio.to_thread(repo.count_for_guild, str(interaction.guild.id))
        if not rows:
            await interaction.followup.send(
                "This server has no tags yet. `/tag-create` makes one.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Tags",
            # Names only, in code spans: a listing that rendered content would
            # be both enormous and a way to make the bot repeat fifty tags at
            # once.
            description=" ".join(f"`{row['name']}`" for row in rows),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{total} of {MAX_TAGS_PER_GUILD} used"
            + (f" · showing the {LIST_LIMIT} most used" if total > LIST_LIMIT else "")
        )
        await interaction.followup.send(embed=embed, allowed_mentions=NO_MENTIONS, ephemeral=True)

    @app_commands.command(name="tag-info", description="Who made a tag, and how often it is used.")
    @app_commands.describe(name="Which tag")
    @app_commands.guild_only()
    async def tag_info(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        found = await asyncio.to_thread(repo.get, str(interaction.guild.id), name)
        if found is None:
            await interaction.followup.send(f"❌ There is no tag called `{name}` here.", ephemeral=True)
            return

        embed = discord.Embed(title=f"`{found['name']}`", color=discord.Color.blurple())
        embed.add_field(name="Created by", value=f"<@{found['created_by']}>", inline=True)
        embed.add_field(name="Uses", value=f"{found['uses']:,}", inline=True)
        embed.add_field(
            name="Length", value=f"{len(found['content'])}/{MAX_CONTENT_CHARS}", inline=True
        )
        created = found["created_at"]
        if hasattr(created, "timestamp"):
            embed.add_field(name="Created", value=f"<t:{int(created.timestamp())}:D>", inline=True)
        await interaction.followup.send(embed=embed, allowed_mentions=NO_MENTIONS, ephemeral=True)

    # ---- autocomplete ----------------------------------------------------
    #
    # Registered by calling the decorator in the class body rather than by
    # stacking @tag.autocomplete four times, because one callback serves four
    # commands and the decorator form would need four identical wrappers. The
    # registrations sit *below* the commands they reference: the class body runs
    # top to bottom, so a name used before it is defined is a NameError -- the
    # mistake this codebase has already made once, in the weather cog.
    #
    # /tag-create deliberately has none: a tag that does not exist yet has no
    # name to suggest, and suggesting the existing ones would invite somebody to
    # pick a name that is already taken.

    async def _name_autocomplete(self, interaction: discord.Interaction, current: str):
        """Suggest tag names, through the shared 30-second cache.

        Cached because Discord closes an autocomplete after three seconds and
        shows nothing at all if the callback is slower -- and a person typing
        "r-u-l-e-s" would otherwise be five database round trips.
        """
        if interaction.guild is None:
            return []
        term = str(current or "").strip().lower()
        rows = await cached(
            f"tags:{interaction.guild.id}",
            term,
            lambda: asyncio.to_thread(
                repo.list_for_guild,
                str(interaction.guild.id),
                prefix=term or None,
                limit=MAX_CHOICES,
            ),
            default=[],
        )
        return [
            app_commands.Choice(name=truncate(row["name"]), value=row["name"])
            for row in (rows or [])[:MAX_CHOICES]
        ]

    tag.autocomplete("name")(_name_autocomplete)
    tag_edit.autocomplete("name")(_name_autocomplete)
    tag_delete.autocomplete("name")(_name_autocomplete)
    tag_info.autocomplete("name")(_name_autocomplete)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        try:
            await asyncio.to_thread(repo.delete_for_guild, str(guild.id))
        except Exception:
            log.exception("Could not forget tags for guild %s", guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(TagsCog(bot))
