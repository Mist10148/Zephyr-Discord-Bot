"""Opus codec loader (required for Discord voice).

Tries platform-specific library names in this order:
1. PyInstaller one-file bundle
2. Bundled copy at the project root (Windows DLL)
3. System libraries (libopus.so.0 / libopus.so on Linux, libopus.dylib on macOS)
4. Generic "opus" name from PATH
"""

import os
import sys

import discord

from zephyr.config import PROJECT_ROOT


def _opus_lib_names():
    """Return the candidate library names for this OS."""
    if os.name == "nt":
        return ["libopus-0.x64.dll", "libopus-0.dll", "opus.dll"]
    if sys.platform == "darwin":
        return ["libopus.dylib", "opus"]
    # Linux and other Unix-like systems
    return ["libopus.so.0", "libopus.so", "opus"]


def load_opus():
    """Load the Opus codec library needed for Discord voice, then report status."""
    if discord.opus.is_loaded():
        print("[Startup] Opus already loaded.")
        return

    loaded = False
    candidates = []

    # PyInstaller / one-file bundle
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        for name in _opus_lib_names():
            path = os.path.join(sys._MEIPASS, name)
            if os.path.exists(path):
                candidates.append(("PyInstaller bundle", path))

    # Project-root copy (normal local run on Windows)
    for name in _opus_lib_names():
        path = os.path.join(str(PROJECT_ROOT), name)
        if os.path.exists(path):
            candidates.append(("project root", path))

    # System libraries / PATH
    for name in _opus_lib_names():
        candidates.append(("system", name))

    last_error = None
    for source, path in candidates:
        try:
            discord.opus.load_opus(path)
            loaded = True
            print(f"[Startup] Opus loaded from {source}: {path}")
            break
        except Exception as exc:
            last_error = exc

    if not loaded:
        print(
            "[Startup Warning] Could not load Opus. "
            "Discord voice will not work until libopus is installed or bundled."
        )
        if last_error:
            print(f"[Startup Warning] Last error: {last_error}")
