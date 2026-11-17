"""The audio-effect filter chains.

Every chain is run through a real FFmpeg when one is available, because the bug
this guards against -- a filter name FFmpeg does not have -- is invisible to any
test that only inspects the option string.
"""

import os
import shlex
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from zephyr.cogs.music import REVERB_FILTER, VoiceState
from zephyr.core.ffmpeg import FFMPEG_PATH

EFFECT_FLAGS = (
    "_16d_enabled", "_reverb_enabled", "_slowed_enabled",
    "_slownrev_enabled", "_nightcore_enabled", "_vaporwave_enabled",
)


def _state(**flags):
    state = VoiceState(MagicMock(), 1, channel_id=2)
    for name, value in flags.items():
        setattr(state, name, value)
    return state


def _filter_chain(state) -> str | None:
    tokens = shlex.split(state.get_ffmpeg_options()["options"])
    if "-filter:a" not in tokens:
        return None
    return tokens[tokens.index("-filter:a") + 1]


class TestFilterChain:
    def test_no_effects_means_no_filter(self):
        assert _filter_chain(_state()) is None

    def test_reverb_uses_a_filter_ffmpeg_has(self):
        chain = _filter_chain(_state(_reverb_enabled=True))
        assert "reverb=" not in chain
        assert REVERB_FILTER in chain

    def test_slownrev_adds_reverb_once_even_with_reverb_on(self):
        chain = _filter_chain(_state(_reverb_enabled=True, _slownrev_enabled=True))
        assert chain.count("aecho=") == 1

    def test_nightcore_resamples_from_a_known_rate(self):
        """asetrate=48000 alone was a no-op on 48 kHz input: no pitch change at all."""
        chain = _filter_chain(_state(_nightcore_enabled=True))
        assert chain.startswith("aresample=48000,asetrate=60000,aresample=48000")

    def test_speed_effects_compose_into_one_rate(self):
        state = _state(_nightcore_enabled=True, _slowed_enabled=True)
        assert state.playback_rate() == pytest.approx(1.125)
        assert _filter_chain(state).count("asetrate=") == 1

    def test_rate_is_one_without_speed_effects(self):
        assert _state(_reverb_enabled=True, _16d_enabled=True).playback_rate() == 1.0


def _ffmpeg_available() -> bool:
    return bool(FFMPEG_PATH) and (os.path.exists(FFMPEG_PATH) or shutil.which(FFMPEG_PATH) is not None)


@pytest.mark.skipif(not _ffmpeg_available(), reason="FFmpeg is not installed")
@pytest.mark.parametrize("flag", EFFECT_FLAGS + ("pitch", "bass", "everything"))
def test_every_chain_runs_in_ffmpeg(flag):
    state = _state()
    if flag == "pitch":
        state._pitch = 1.2
    elif flag == "bass":
        state._bass_boost = 10
    elif flag == "everything":
        for name in EFFECT_FLAGS:
            setattr(state, name, True)
        state._pitch, state._bass_boost = 0.8, -5
    else:
        setattr(state, flag, True)

    chain = _filter_chain(state)
    result = subprocess.run(
        [FFMPEG_PATH, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1:sample_rate=44100",
         "-filter:a", chain, "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


class TestPositionClock:
    def test_elapsed_runs_at_the_rate_the_source_started_with(self, monkeypatch):
        state = _state(_nightcore_enabled=True)
        monkeypatch.setattr("zephyr.cogs.music.time.time", lambda: 1000.0)
        state._current_position, state._current_start_time = 30.0, 960.0
        state._current_rate = state.playback_rate()
        # 40 s of listening at 1.25x covers 50 s of the track.
        assert state.elapsed == pytest.approx(80.0)

    def test_toggling_an_effect_does_not_rewrite_time_already_played(self, monkeypatch):
        """restart_current reads elapsed *after* the toggle, to find where to resume."""
        state = _state()
        monkeypatch.setattr("zephyr.cogs.music.time.time", lambda: 1000.0)
        state._current_position, state._current_start_time = 0.0, 900.0
        state._current_rate = state.playback_rate()
        state._nightcore_enabled = True
        assert state.elapsed == pytest.approx(100.0)

    def test_snapshot_reports_the_rate(self):
        state = _state()
        state._current_rate = 0.8
        assert state.snapshot()["rate"] == 0.8
