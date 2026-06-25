"""FFmpeg executable resolver.

Adapted from the original bot.py ``get_ffmpeg_path`` (lines 116-148). The local
lookup is anchored to the project root (where the bundled ``ffmpeg/`` folder
lives), and the ``FFMPEG_PATH`` override is read from config (which loads it
from .env).
"""

import os
import sys
import shutil

from zephyr.config import PROJECT_ROOT, FFMPEG_PATH_OVERRIDE


def get_ffmpeg_path():
    """Determine the reliable path to the FFmpeg executable."""
    ffmpeg_exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    # PyInstaller / one-file bundle
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass_path = os.path.join(sys._MEIPASS, "ffmpeg", ffmpeg_exe_name)
        if os.path.exists(meipass_path):
            return meipass_path

    # PyInstaller / one-dir bundle
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        onedir_path = os.path.join(exe_dir, "ffmpeg", ffmpeg_exe_name)
        if os.path.exists(onedir_path):
            return onedir_path

    # Explicit override from .env
    if FFMPEG_PATH_OVERRIDE and os.path.exists(FFMPEG_PATH_OVERRIDE):
        return FFMPEG_PATH_OVERRIDE

    # Bundled ffmpeg/ folder at the project root
    bundled = os.path.join(str(PROJECT_ROOT), "ffmpeg", ffmpeg_exe_name)
    if os.path.exists(bundled):
        return bundled

    # System PATH fallback
    return shutil.which(ffmpeg_exe_name) or ffmpeg_exe_name


FFMPEG_PATH = get_ffmpeg_path()
