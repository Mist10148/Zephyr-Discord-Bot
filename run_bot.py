"""Entry point for the Zephyr Discord bot.

    python run_bot.py
"""

from zephyr import config

# Fail early with a clear message if any required secret is missing.
config.validate_bot_config()

from zephyr.core.logging import configure_logging  # noqa: E402

# Before the client is imported, so a failure during cog loading is logged
# rather than printed into the void.
configure_logging(service="bot")

from zephyr.client import bot  # noqa: E402


def main():
    bot.run(config.TOKEN)


if __name__ == "__main__":
    main()
