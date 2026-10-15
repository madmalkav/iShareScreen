"""H.264 receive backlog guard: when packets wait > 0.5 s before processing,
drop the queue, re-root the decoder and ask for a keyframe; repeated drops
while RCTL is on stop the rate reports for the session."""
from __future__ import annotations

import queue
import threading
import types

import pytest

import isharescreen.proxy.session as S
from isharescreen.proxy.protocol.rctl import RctlState
from isharescreen.proxy.session import Session


@pytest.fixture
def clock(monkeypatch):
    t = {"now": 1000.0}
    monkeypatch.setattr(S.time, "monotonic", lambda: t["now"])
    monkeypatch.delenv("ISS_RCTL", raising=False)
    return t


def _session(delay, qsize=500):
    s = Session.__new__(Session)
    s._video_q = queue.Queue()
    for i in range(qsize):
        s._video_q.put(b"pkt%d" % i)
    s._pending_groups = {(1, 2): []}
    s._rctl = RctlState()
    s._rctl.delay_s = delay
    s._rctl_lock = threading.Lock()
    s._last_backlog_shed_t = 0.0
    s._backlog_sheds = 0
    s._congestion_delay_s = 0.0
    s._congestion_t = 0.0
    s._pps, s._pps_t0, s._pps_count = 0.0, 1000.0, 0
    s.broken = []
    s._decoder = types.SimpleNamespace(mark_reference_chain_broken=s.broken.append)
    s.firs = 0
    s.request_fir = lambda tile=None: setattr(s, "firs", s.firs + 1)
    return s


def test_small_delay_is_left_alone(clock):
    s = _session(0.2)
    s._maybe_shed_backlog()
    assert s._video_q.qsize() == 500 and s.firs == 0


def test_backlog_is_dropped_and_keyframe_requested(clock):
    s = _session(1.5)
    s._maybe_shed_backlog()
    assert s._video_q.qsize() == 0 and not s._pending_groups
    assert s.firs == 1 and s.broken and s._backlog_sheds == 1
    assert s._rctl.delay_s == 0.0                      # estimate restarts...
    assert s._held_congestion(clock["now"]) == pytest.approx(1.5)   # ...but the host still hears it


def test_cooldown(clock):
    s = _session(1.5)
    s._maybe_shed_backlog()
    s._rctl.delay_s = 1.5
    s._video_q.put(b"x")
    clock["now"] += 1.0                                # within the cooldown
    s._maybe_shed_backlog()
    assert s._backlog_sheds == 1


def test_held_congestion_fades(clock):
    s = _session(2.0)
    s._maybe_shed_backlog()
    clock["now"] += S._CONGESTION_HOLD_S / 2
    assert s._held_congestion(clock["now"]) == pytest.approx(1.0)
    clock["now"] += S._CONGESTION_HOLD_S
    assert s._held_congestion(clock["now"]) == 0.0


def test_delay_spike_without_a_real_queue_is_ignored(clock):
    s = _session(1.5, qsize=50)                     # delay reading, but no backlog
    s._maybe_shed_backlog()
    assert s._video_q.qsize() == 50 and s.firs == 0
