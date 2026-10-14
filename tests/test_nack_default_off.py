"""The Mac never retransmits, so iss sends no NACK by default and waits for a
missing H.264 packet only as long as a reorder takes; ISS_NACK=1 restores both."""
import importlib
import types
from collections import defaultdict

from isharescreen.proxy import session as sess


def test_defaults():
    assert sess._NACK_ENABLED is False
    assert sess._AVC_RTP_REPAIR_WAIT_S == 0.010


def test_drain_sends_nothing_and_clears_when_off():
    s = sess.Session.__new__(sess.Session)
    s._nack_pending = defaultdict(set, {0x1000: {11, 12}})
    s._video_rtcp_dest = lambda: (_ for _ in ()).throw(AssertionError("must not send"))
    s._drain_pending_nack()
    assert not s._nack_pending


def test_env_restores_nack_and_long_wait(monkeypatch):
    monkeypatch.setenv("ISS_NACK", "1")
    try:
        m = importlib.reload(sess)
        assert m._NACK_ENABLED is True and m._AVC_RTP_REPAIR_WAIT_S == 0.075
    finally:
        monkeypatch.delenv("ISS_NACK")
        importlib.reload(sess)
