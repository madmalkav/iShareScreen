"""ISS_LAT_TRACE: a picture's timestamps travel session → decoder → frontend
and become one per-stage sample; the default (off) path adds nothing."""
import time
import types
from collections import deque

from isharescreen.proxy import session as sess


def _session():
    s = sess.Session.__new__(sess.Session)
    s._lat_grp = {}
    s._lat_samples = []
    s._lat_last_log = 0.0
    s._decoder = types.SimpleNamespace(_lat_fifo={}, lat_last_out={})
    return s


def test_off_by_default():
    assert sess._LAT_TRACE is False


def test_push_then_consume_makes_one_sample():
    s = _session()
    t0 = time.monotonic()
    s._lat_grp[(0x10, 99)] = [t0 - 0.010, t0 - 0.006]
    s._lat_push((0x10, 99), 2)
    first, last, fed = s._decoder._lat_fifo[2][0]
    assert (first, last) == (t0 - 0.010, t0 - 0.006) and fed >= t0
    s._decoder.lat_last_out[2] = (first, last, fed, fed + 0.001, fed + 0.003)
    s._lat_note_consumed(2)
    arrive, assemble, queue_, decode, handoff, total = s._lat_samples[0]
    assert abs(arrive - 0.004) < 1e-9 and abs(decode - 0.002) < 1e-9
    assert total >= 0.010 and 2 not in s._decoder.lat_last_out


def test_consume_without_trace_is_a_noop():
    s = _session()
    s._lat_note_consumed(0)
    assert s._lat_samples == []
