"""One present per frame (frontend/desktop/present.py).

Every tile of one Apple frame carries the frame's RTP timestamp. The session
records which tiles were fed each timestamp, the decoders hand it out on the
TileFrame, and the desktop frontend waits until the newest frame's tiles are
all uploaded (or 12 ms) before it presents."""
from __future__ import annotations

import queue

import av
import numpy as np
import pytest

from isharescreen.frontend.desktop import present
from isharescreen.frontend.desktop.present import FrameCoalescer, make_coalescer
from isharescreen.proxy import session as sess
from isharescreen.proxy.media.hevc import HevcDecoder

_TS = 1_000_000


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class _Stream:
    """Stands in for the session + render loop: tiles become ready at given
    times; `drain` uploads the ready ones into the coalescer."""

    def __init__(self, coalescer: FrameCoalescer, clock: _Clock,
                 fed: dict[int, set[int]]) -> None:
        self.c = coalescer
        self.clock = clock
        self.fed = fed
        self.pending: list[tuple[float, int, int]] = []   # (ready_at, tile, ts)

    def fed_tiles(self, ts: int) -> set[int]:
        return self.fed.get(ts, set())

    def wait_fresh(self, timeout: float) -> None:
        nxt = min((r for r, _, _ in self.pending), default=None)
        if nxt is not None and nxt <= self.clock.t + timeout:
            self.clock.t = max(self.clock.t, nxt)
        else:
            self.clock.t += timeout

    def drain(self) -> bool:
        ready = [p for p in self.pending if p[0] <= self.clock.t]
        for p in ready:
            self.pending.remove(p)
            self.c.note_upload(p[1], p[2])
        return bool(ready)


def _setup(deadline_s: float = 0.012):
    clock = _Clock()
    c = FrameCoalescer(deadline_s, clock=clock)
    return c, clock, _Stream(c, clock, {_TS: {0, 1, 2, 3}})


def test_tiles_0_to_2_then_3_give_one_present():
    c, clock, s = _setup()
    t0 = clock.t
    s.pending = [(t0, 0, _TS), (t0, 1, _TS), (t0, 2, _TS), (t0 + 0.004, 3, _TS)]
    presents = 0
    assert s.drain()                      # the render loop's first drain: 0-2
    assert not c.frame_complete(s.fed_tiles)
    c.wait_for_frame(s.fed_tiles, s.wait_fresh, s.drain)
    presents += 1
    assert c.frame_complete(s.fed_tiles)
    assert clock.t == pytest.approx(t0 + 0.004)   # presented when tile 3 landed
    assert not s.pending and presents == 1


def test_tile_0_alone_presents_after_the_deadline():
    c, clock, s = _setup()
    t0 = clock.t
    s.pending = [(t0, 0, _TS)]
    s.drain()
    c.wait_for_frame(s.fed_tiles, s.wait_fresh, s.drain)
    assert clock.t == pytest.approx(t0 + 0.012)
    assert not c.frame_complete(s.fed_tiles)      # gave up waiting for 1-3


def test_only_fed_tiles_are_waited_for():
    # Apple skips tiles that didn't change; a frame of two tiles is complete
    # once those two are up.
    c, clock, s = _setup()
    s.fed = {_TS: {1, 2}}
    c.note_upload(1, _TS)
    assert not c.frame_complete(s.fed_tiles)
    c.note_upload(2, _TS)
    assert c.frame_complete(s.fed_tiles)


def test_a_late_tile_of_the_previous_frame_does_not_complete_the_new_one():
    c, _, s = _setup()
    s.fed[_TS + 1500] = {0, 1}
    c.note_upload(0, _TS + 1500)
    c.note_upload(1, _TS)                 # stale picture for tile 1
    assert not c.frame_complete(s.fed_tiles)
    c.note_upload(1, _TS + 1500)
    assert c.frame_complete(s.fed_tiles)


def test_unknown_timestamp_presents_at_once():
    c, clock, s = _setup()
    t0 = clock.t
    c.note_upload(0, None)               # a decoder that can't tell (VT, QSV)
    assert c.frame_complete(s.fed_tiles)
    c.wait_for_frame(s.fed_tiles, s.wait_fresh, s.drain)
    assert clock.t == t0


def test_timestamp_wraparound_still_orders_frames():
    c, _, s = _setup()
    old, new = 0xFFFFFF00, 0x00000500    # new is ~1500 ticks after old
    s.fed = {old: {0, 1}, new: {0, 1}}
    c.note_upload(0, old)
    c.note_upload(0, new)
    c.note_upload(1, old)
    assert not c.frame_complete(s.fed_tiles)       # still waiting for new
    c.note_upload(1, new)
    assert c.frame_complete(s.fed_tiles)


def test_a_new_stream_far_behind_restarts_tracking():
    c, _, s = _setup()
    c.note_upload(0, _TS)
    restart = _TS - 10 * 90_000           # new stream, timestamps 10 s back
    s.fed = {restart: {0}}
    c.note_upload(0, restart)
    assert c.frame_complete(s.fed_tiles)  # not stuck on the old newest


def test_policy_and_env_override(monkeypatch):
    monkeypatch.delenv("ISS_PRESENT_COALESCE_MS", raising=False)
    c = make_coalescer("frame")
    assert c is not None and c.deadline_s == present.FRAME_DEADLINE_S
    assert make_coalescer("tile") is None
    monkeypatch.setenv("ISS_PRESENT_COALESCE_MS", "8")
    c = make_coalescer("tile")
    assert c is not None and c.deadline_s == pytest.approx(0.008)
    monkeypatch.setenv("ISS_PRESENT_COALESCE_MS", "0")
    assert make_coalescer("frame") is None


def test_session_records_fed_tiles_per_timestamp():
    s = sess.Session.__new__(sess.Session)
    s._fed_by_ts = {}
    s._note_fed(_TS, 0)
    s._note_fed(_TS, 3)
    s._note_fed(_TS + 1500, 1)
    assert s.fed_tiles(_TS) == {0, 3}
    assert s.fed_tiles(_TS + 1500) == {1}
    assert s.fed_tiles(42) == frozenset()
    for i in range(sess._FED_BY_TS_MAX + 1):
        s._note_fed(_TS + 3000 * (i + 1), 0)
    assert len(s._fed_by_ts) == sess._FED_BY_TS_MAX
    assert s.fed_tiles(_TS) == frozenset()            # oldest forgotten


def test_hevc_decoder_carries_the_timestamp_to_the_tile_frame():
    d = HevcDecoder(4)
    d._worker = type("Alive", (), {"is_alive": lambda self: True})()
    d._queue = queue.Queue()
    vps = b"\x40\x01" + b"\x00" * 8
    d.feed_nalu(vps, 2, rtp_ts=_TS)
    assert d._queue.get_nowait() == (vps, 2, _TS)

    # The decode side: a pts fed with a timestamp publishes it on the slot.
    d._pts_to_tile[7] = 2
    d._pts_to_ts[7] = _TS
    frame = av.VideoFrame.from_ndarray(np.zeros((3, 16, 16), np.uint8),
                                       format="yuv444p")
    frame.pts = 7
    d._publish_frame(frame)
    assert 7 not in d._pts_to_ts
    d._dpb_has_idr = True
    tf = d.get_frame(2)
    assert tf is not None and tf.rtp_ts == _TS
