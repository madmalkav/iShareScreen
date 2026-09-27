"""Startup hold (media/hevc.py `_STARTUP_HOLD_S`): right after the decoder is
(re)built, a tile the gate still wants a keyframe for is not shown until it
has been shown once — instead of 2-4 s of invented-reference gray. Mid-session
behaviour (show the artifact, FIR in the background) is unchanged."""
from __future__ import annotations

import av
import numpy as np
import pytest

from isharescreen.proxy.media import hevc
from isharescreen.proxy.media.hevc import HevcDecoder


def _frame() -> av.VideoFrame:
    return av.VideoFrame.from_ndarray(np.full((3, 2, 2), 128, np.uint8), format="yuv444p")


@pytest.fixture
def dec(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(hevc, "_PERTILE_RECOVERY", False)
    import time as _time
    monkeypatch.setattr(_time, "monotonic", lambda: clock["t"])
    d = HevcDecoder(4)
    d._dpb_has_idr = True
    d._codec_built_t = clock["t"]            # as _install_codec would
    d._tiles_shown = set()
    d.clock = clock

    def publish(ti):
        slot = d._tiles[ti]
        slot.raw_frame = _frame()
        slot.good_count += 1
    d.publish = publish
    return d


def test_flagged_tile_held_right_after_build(dec):
    dec._gate.mark_decode_error(2)
    dec.publish(2)
    assert dec.get_frame(2) is None


def test_healthy_tile_shown_right_after_build(dec):
    dec.publish(0)
    assert dec.get_frame(0) is not None


def test_hold_ends_after_cap(dec):
    dec._gate.mark_decode_error(1)
    dec.clock["t"] += hevc._STARTUP_HOLD_S + 0.1
    dec.publish(1)
    assert dec.get_frame(1) is not None


def test_mid_session_policy_unchanged_once_shown(dec):
    dec.publish(3)
    assert dec.get_frame(3) is not None      # shown once since the build
    dec._gate.mark_decode_error(3)
    dec.publish(3)
    assert dec.get_frame(3) is not None      # later gray still shown (+FIR)


def test_rebuild_rearms_the_hold(dec, monkeypatch):
    dec.publish(0)
    assert dec.get_frame(0) is not None
    monkeypatch.setattr(dec, "_codec", None)
    dec._install_codec(object(), hw_name=None)   # e.g. the SSRC-switch restart
    dec._gate.mark_decode_error(0)
    dec.publish(0)
    assert dec.get_frame(0) is None


def test_ssrc_adoption_rearms_the_hold_without_rebuild(dec):
    dec.publish(0)
    assert dec.get_frame(0) is not None        # shown after the build
    dec.rearm_startup_hold()                   # host switched SSRC group
    dec._gate.mark_decode_error(0)
    dec.publish(0)
    assert dec.get_frame(0) is None            # held (keeps last picture)
