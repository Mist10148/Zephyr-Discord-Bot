"""One place that decides what a failed command says to the user.

Nothing handled slash-command errors. ``ZephyrBot`` registered no
``on_app_command_error`` and no ``tree.on_error``, and the only error hook in the
package was ``MusicCog.cog_command_error`` -- the *prefix* hook, which fires for
a ``commands.Context`` and therefore never for any of the 75 slash commands. An
unhandled exception inside one produced "The application did not respond", or a
silent failure, with nothing written anywhere.

``zephyr/cogs/chat.py`` said so out loud: ``/forget``'s permission check carried
a comment noting that "this cog has no ``cog_app_command_error``, so the
resulting CheckFailure would surface as an unhandled error instead of a readable
reply". It now surfaces as a readable reply.

The split below is the whole design. A *user-fault* error is something the person
can act on -- they are on cooldown, they lack a permission, they are not in a
voice channel -- and gets a plain sentence and no log noise. Everything else is
a bug: the user gets an apology carrying a short correlation id, and the log gets
the full traceback under the same id, so a report of "it said ZP-3F9A2C" is
enough to find the exact stack.
"""

import secrets

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.core.logging import get_logger

log = get_logger(__name__)

GENERIC = (
    "Something went wrong on my side. It has been logged — quote `{reference}` "
    "if you report it."
)


def new_reference() -> str:
    """A short, unambiguous id. Not a UUID: it has to be readable aloud."""
    return f"ZP-{secrets.token_hex(3).upper()}"


class Refused(app_commands.CheckFailure):
    """A check failure whose message is already written for the person.

    A bare ``CheckFailure`` carries no reason, so the branch below can only say
    "You cannot use that command here." That is fine for a library check and
    useless for one of ours: "the DJ role is required in this server" tells
    somebody what to do, and "you cannot use that command here" sends them to
    ask an administrator why the bot is broken.
    """


def user_facing_message(error: Exception) -> str | None:
    """The sentence to show, or ``None`` when this is a bug rather than a
    misuse.

    Ordered most specific first, because several of these subclass each other --
    ``MissingPermissions`` is a ``CheckFailure``, and matching the base first
    would lose the useful detail.
    """
    # Unwrap: the tree wraps a handler's own exception, and reading the wrapper
    # would report every failure as "command invoke error".
    if isinstance(error, (app_commands.CommandInvokeError, commands.CommandInvokeError)):
        error = error.original  # type: ignore[assignment]

    if isinstance(error, (app_commands.CommandOnCooldown, commands.CommandOnCooldown)):
        return f"That command is on cooldown — try again in {error.retry_after:.0f}s."

    if isinstance(error, (app_commands.MissingPermissions, commands.MissingPermissions)):
        missing = ", ".join(str(name).replace("_", " ") for name in error.missing_permissions)
        return f"You need the {missing} permission to do that."

    if isinstance(error, (app_commands.BotMissingPermissions, commands.BotMissingPermissions)):
        missing = ", ".join(str(name).replace("_", " ") for name in error.missing_permissions)
        return f"I need the {missing} permission to do that."

    if isinstance(error, app_commands.NoPrivateMessage) or isinstance(error, commands.NoPrivateMessage):
        return "That command only works inside a server."

    if isinstance(error, app_commands.TransformerError):
        return f"`{error.value}` is not a valid {error.type.name}."

    if isinstance(error, (commands.BadArgument, commands.UserInputError)):
        return f"I could not read that: {error}"

    if isinstance(error, commands.CommandNotFound):
        # Answering this would mean replying to every stray message that starts
        # with the prefix.
        return ""

    # Before the bare branch, because Refused *is* a CheckFailure and matching
    # the base first would throw away the sentence it carries.
    if isinstance(error, Refused):
        return f"❌ {error}"

    # Last, because the four above are all CheckFailures. A bare check that
    # returned False carries no reason, so this is as specific as it gets.
    if isinstance(error, (app_commands.CheckFailure, commands.CheckFailure)):
        return "You cannot use that command here."

    # Zephyr's own refusals, raised by the music cog and the weather provider.
    # They are written for users already.
    if type(error).__name__ in {"VoiceError", "YTDLError", "SubscriptionError", "PlaylistError"}:
        return f"❌ {error}"

    return None


async def report(interaction: discord.Interaction, error: Exception) -> None:
    """Answer ``interaction`` and, when this is a bug, log it."""
    message = user_facing_message(error)

    if message is None:
        reference = new_reference()
        # exc_info so the traceback is on the record rather than lost. `extra`
        # puts the ids in their own JSON fields, which is what makes "find the
        # stack for ZP-3F9A2C" a search rather than a grep.
        log.error(
            "Unhandled error in /%s",
            interaction.command.qualified_name if interaction.command else "unknown",
            exc_info=error,
            extra={
                "reference": reference,
                "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
                "user_id": str(interaction.user.id) if interaction.user else None,
            },
        )
        message = GENERIC.format(reference=reference)
    elif message == "":
        return

    await _respond(interaction, message)


async def _respond(interaction: discord.Interaction, message: str) -> None:
    """Reply however this interaction can still be replied to.

    ``is_done()`` is the load-bearing check. Most of these commands defer first,
    and calling ``response.send_message`` on a deferred interaction raises --
    which would make the error handler itself the thing that fails, turning a
    readable message back into "the application did not respond".

    Every failure here is swallowed: the interaction may have expired, and an
    exception raised out of an error handler has nowhere left to go.
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not deliver an error message for /%s",
                    interaction.command.qualified_name if interaction.command else "unknown")
