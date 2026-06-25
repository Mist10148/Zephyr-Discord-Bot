"""Opus codec loader (required for Discord voice on Windows).

Adapted from the original bot.py ``_load_opus`` (lines 77-110); the only change
is that the local lookup is anchored to the project root (where the bundled
``libopus-0.x64.dll`` lives) instead of the script directory.
"""

import os
import sys

import discord

from zephyr.config import PROJECT_ROOT


def load_opus():
    """Load the Opus codec library needed for Discord voice, then report status."""
    if not discord.opus.is_loaded():
        opus_lib = "libopus-0.x64.dll" if os.name == "nt" else "opus"

        # PyInstaller / one-file bundle
        loaded = False
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            meipass_path = os.path.join(sys._MEIPASS, opus_lib)
            if os.path.exists(meipass_path):
                discord.opus.load_opus(meipass_path)
                loaded = True

        # Project-root copy (normal run)
        if not loaded:
            local_path = os.path.join(str(PROJECT_ROOT), opus_lib)
            if os.path.exists(local_path):
                discord.opus.load_opus(local_path)
                loaded = True

        # System PATH fallback
        if not loaded:
            try:
                discord.opus.load_opus(opus_lib)
            except Exception as exc:
                print(f"[Startup Warning] Could not load Opus: {exc}")

    print(f"[Startup] Opus loaded: {discord.opus.is_loaded()}")
