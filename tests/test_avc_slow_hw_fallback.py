"""Measured hardware fallback for H.264: a hardware decoder that is busy for
most of the wall-clock time (e.g. 4K copy-back through NVIDIA's VA driver) is
switched to software on the next intra frame; fast hardware, little motion, an
explicit --hwaccel or ISS_HW_SLOW_FALLBACK=0 keep hardware."""
from __future__ import annotations

import pytest

from isharescreen.proxy.media import avc
from isharescreen.proxy.media.avc import AvcDecoder


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(avc.time, "monotonic", c)
    return c


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ISS_HWACCEL", raising=False)
    monkeypatch.delenv("ISS_HW_SLOW_FALLBACK", raising=False)


def _run(decoder, clock, *, windows, fps, ms):
    """Feed `windows` measurement windows of `fps` pictures costing `ms` each."""
    per_window = int(fps * avc._SLOW_WINDOW_S)
    step = avc._SLOW_WINDOW_S / per_window
    for _ in range(windows * per_window + 1):
        decoder._account_decode_load(ms / 1000)
        clock.t += step


def _hw_decoder(name="vaapi"):
    d = AvcDecoder(1)
    d._hw_name = name
    return d


def test_slow_hardware_switches_to_software(clock):
    d = _hw_decoder()
    _run(d, clock, windows=avc._SLOW_WINDOWS, fps=45, ms=22)
    assert d._hw_failed
    assert d._reference_reset_pending      # rebuild (as software) on next intra frame
    assert d._gate.consume_fir_request()   # and a keyframe is requested now


def test_needs_consecutive_windows(clock):
    d = _hw_decoder()
    _run(d, clock, windows=avc._SLOW_WINDOWS - 1, fps=45, ms=22)
    assert not d._hw_failed


@pytest.mark.parametrize("fps, ms", [(60, 4), (60, 10)])   # fast hardware: 24% / 60% busy
def test_fast_hardware_is_kept(clock, fps, ms):
    d = _hw_decoder()
    _run(d, clock, windows=5, fps=fps, ms=ms)
    assert not d._hw_failed


def test_little_motion_is_ignored(clock):
    d = _hw_decoder()
    _run(d, clock, windows=5, fps=10, ms=100)      # busy, but only 10 fps
    assert not d._hw_failed


def test_software_decoder_is_not_measured(clock):
    d = _hw_decoder(None)
    _run(d, clock, windows=5, fps=45, ms=22)
    assert not d._hw_failed


@pytest.mark.parametrize("env, value", [("ISS_HWACCEL", "vaapi"), ("ISS_HW_SLOW_FALLBACK", "0")])
def test_explicit_choice_keeps_hardware(monkeypatch, clock, env, value):
    monkeypatch.setenv(env, value)
    d = _hw_decoder()
    _run(d, clock, windows=5, fps=45, ms=22)
    assert not d._hw_failed
