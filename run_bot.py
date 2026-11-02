"""Entry point for the Zephyr Discord bot.

    python run_bot.py
"""

import sys
import atexit
import traceback

from zephyr import config

# Fail early with a clear message if any required secret is missing.
config.validate_bot_config()

from zephyr.client import bot

def log_uncaught_exception(exctype, value, tb):
    print("".join(traceback.format_exception(exctype, value, tb)), file=sys.stderr, flush=True)

sys.excepthook = log_uncaught_exception
atexit.register(lambda: print("[Process] Bot process exiting / shutting down...", file=sys.stderr, flush=True))


def main():
    try:
        bot.run(config.TOKEN)
    except Exception as e:
        print(f"[FATAL] Bot encountered an exception: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.stderr.flush()
        raise
    except BaseException as e:
        print(f"[FATAL] Bot received base exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    main()
