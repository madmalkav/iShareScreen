"""Decode-overload resync (media/hevc.py).

When the decode worker falls behind, the queue used to fill and then drop
new slices one at a time, breaking the reference chain for good (permanent
gray). Now a backlog past `_QUEUE_RESYNC_AT` makes the worker discard it,
hold every tile until a fresh keyframe, and request one on all tiles."""
from __future__ import annotations

import queue
import threading

import pytest

from isharescreen.proxy.media import hevc
from isharescreen.proxy.media.hevc import HevcDecoder

_VPS = b"\x40\x01" + b"\x00" * 8     # NAL type 32: no RPS check in feed_nalu


class _Alive:
    def is_alive(self) -> bool:
        return True


def _decoder(num_tiles: int = 4) -> HevcDecoder:
    d = HevcDecoder(num_tiles)
    d._worker = _Alive()                                  # no real thread
    d._queue = queue.Queue(maxsize=hevc._QUEUE_MAX)
    return d


def test_backlog_past_high_water_arms_resync():
    d = _decoder()
    for _ in range(hevc._QUEUE_RESYNC_AT - 1):
        d.feed_nalu(_VPS, 0)
    assert not d._resync_pending
    d.feed_nalu(_VPS, 0)
    assert d._resync_pending


def test_full_queue_arms_resync_and_counts_drop():
    d = _decoder()
    d._queue = queue.Queue(maxsize=2)
    for _ in range(3):
        d.feed_nalu(_VPS, 1)
    assert d._resync_pending
    assert d.decode_queue_drops == 1


def test_resync_drops_backlog_holds_all_tiles_and_requests_keyframes():
    d = _decoder()
    for ti in range(40):
        d._queue.put_nowait((_VPS, ti % 4))
    d._resync_pending = True

    assert d._overload_resync() is True

    assert d.decode_queue_depth == 0
    assert d.decode_queue_resyncs == 1
    assert not d._resync_pending
    assert d._gate.bad_tiles == {0, 1, 2, 3}         # FIR on every tile
    if hevc._PERTILE_RECOVERY:
        assert d._tiles_await_idr == {0, 1, 2, 3}
    else:
        assert d._dpb_has_idr is False


def test_resync_has_a_cooldown():
    d = _decoder()
    assert d._overload_resync() is True
    d._queue.put_nowait((_VPS, 0))
    assert d._overload_resync() is False             # keyframe not due yet
    assert d.decode_queue_depth == 1                 # backlog kept, decode on
    assert d.decode_queue_resyncs == 1


def test_worker_discards_backlog_then_keeps_decoding(monkeypatch):
    d = _decoder()
    decoded: list[int] = []
    monkeypatch.setattr(d, "_decode_one", lambda nalu, ti: decoded.append(ti))
    q = d._queue
    for ti in range(10):
        q.put_nowait((_VPS, ti))
    d._resync_pending = True

    t = threading.Thread(target=d._worker_loop, daemon=True)
    t.start()
    # Wait for the resync to swallow the backlog, then feed fresh work.
    for _ in range(200):
        if q.qsize() == 0 and d.decode_queue_resyncs:
            break
        threading.Event().wait(0.005)
    q.put_nowait((_VPS, 99))
    q.put_nowait(None)                                  # stop sentinel
    t.join(timeout=2)

    assert d.decode_queue_resyncs == 1
    assert decoded == [99]                 # backlog skipped, new slice decoded


@pytest.mark.parametrize("pertile", [True, False])
def test_try_recovery_still_marks_only_broken_tiles(monkeypatch, pertile):
    """The shared helpers must keep `_try_recovery`'s semantics."""
    monkeypatch.setattr(hevc, "_PERTILE_RECOVERY", pertile)
    d = _decoder()
    d._gate._states[2].bad_streak = 1
    d._queue.put_nowait((_VPS, 0))
    d._try_recovery()
    assert d.decode_queue_depth == 0
    if pertile:
        assert d._tiles_await_idr == {2}
    else:
        assert d._dpb_has_idr is False
