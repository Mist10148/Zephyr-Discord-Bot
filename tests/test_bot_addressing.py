"""When a guild message counts as talking to the bot."""

from types import SimpleNamespace

import discord

from zephyr.client import ZephyrBot

ME = SimpleNamespace(id=1)
BOT_ROLE = SimpleNamespace(id=50)


def _message(*, mentions=(), role_mentions=(), resolved=None, everyone=False):
    guild = SimpleNamespace(me=SimpleNamespace(roles=[BOT_ROLE]))
    reference = SimpleNamespace(resolved=resolved) if resolved is not None else None
    return SimpleNamespace(mentions=list(mentions), role_mentions=list(role_mentions), reference=reference,
                           guild=guild, mention_everyone=everyone)


def _addresses(message):
    return ZephyrBot._addresses_bot(SimpleNamespace(user=ME), message)


def test_a_direct_mention_addresses_the_bot():
    assert _addresses(_message(mentions=[ME]))


def test_mentioning_the_bots_managed_role_addresses_the_bot():
    assert _addresses(_message(role_mentions=[BOT_ROLE]))


def test_everyone_and_here_pings_do_not():
    assert not _addresses(_message(everyone=True))


def test_a_reply_to_a_deleted_message_does_not_crash():
    deleted = discord.DeletedReferencedMessage.__new__(discord.DeletedReferencedMessage)
    assert not _addresses(_message(resolved=deleted))


def test_a_reply_to_the_bot_addresses_it():
    reply = discord.Message.__new__(discord.Message)
    reply.author = ME
    assert _addresses(_message(resolved=reply))
