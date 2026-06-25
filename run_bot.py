"""Entry point for the Zephyr Discord bot.

    python run_bot.py
"""

from zephyr import config

# Fail early with a clear message if any required secret is missing.
config.validate_bot_config()

from zephyr.client import bot


def main():
    bot.run(config.TOKEN)


if __name__ == "__main__":
    main()
