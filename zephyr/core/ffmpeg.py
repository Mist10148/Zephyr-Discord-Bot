"""FFmpeg executable resolver.

Works on Windows, macOS, and Linux. Lookup order:
1. PyInstaller one-file or one-dir bundle
2. Explicit ``FFMPEG_PATH`` override from the environment
3. Bundled ``ffmpeg/`` folder next to the project root
4. ``ffmpeg`` on the system ``PATH``
"""

import logging
import os
import sys
import shutil

from zephyr.config import PROJECT_ROOT, FFMPEG_PATH_OVERRIDE


_log = logging.getLogger(__name__)


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

    # Explicit override from .env.  A relative path is read from the project
    # root, not whatever directory the bot happened to be started from.
    if FFMPEG_PATH_OVERRIDE:
        override = os.path.expanduser(FFMPEG_PATH_OVERRIDE)
        if not os.path.isabs(override):
            override = os.path.normpath(os.path.join(str(PROJECT_ROOT), override))
        # README says to drop the binaries in a folder "or point FFMPEG_PATH at
        # it", so a folder means the executable inside it.  exists() accepted
        # the folder itself, and every track then failed with no warning.
        if os.path.isdir(override):
            override = os.path.join(override, ffmpeg_exe_name)
        if os.path.isfile(override):
            return override
        _log.warning("FFMPEG_PATH=%s was not found; looking elsewhere.", FFMPEG_PATH_OVERRIDE)

    # Bundled ffmpeg/ folder at the project root
    bundled = os.path.join(str(PROJECT_ROOT), "ffmpeg", ffmpeg_exe_name)
    if os.path.exists(bundled):
        return bundled

    # System PATH fallback (works on all platforms)
    found = shutil.which(ffmpeg_exe_name)
    if found:
        return found
    # Said once at startup: otherwise the only sign was every track failing
    # in turn with "ffmpeg was not found" until the queue was empty.
    _log.warning("FFmpeg was not found (no FFMPEG_PATH, no ffmpeg/ folder, nothing on PATH); "
                 "music will not play.")
    return ffmpeg_exe_name


FFMPEG_PATH = get_ffmpeg_path()
