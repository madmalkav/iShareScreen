"""RCTL rate-control reports: packet layout and the receiver-delay estimate
the host's rate controller moves on."""
from __future__ import annotations

import struct

from isharescreen.proxy.protocol.rctl import VIDEO_CLOCK_HZ, RctlState, build_rctl


def test_packet_layout():
    pkt = build_rctl(0x11223344, echo_ts=0xABCDEF12, hold_ms=7, clock_s=2.5,
                     delay_s=0.010, received=5000, bwe_kbps=60000, bursty_loss=2)
    assert len(pkt) == 32
    b0, pt, length, ssrc = struct.unpack(">BBHI", pkt[:8])
    assert (b0, pt, length, ssrc) == (0x80, 204, 7, 0x11223344)
    assert pkt[8:12] == b"RCTL"
    f = struct.unpack(">BBHHIHHHHH", pkt[12:])
    assert f[:3] == (0x85, 0, 4)
    assert f[3] == (0xABCDEF12 >> 8) & 0xFFFF          # echo
    assert f[4] == 0
    assert f[5] == 7                                    # hold time, ms
    assert f[6] == int(2.5 * 1024)                      # viewer clock, 1/1024 s
    assert f[7] == int(0.010 * 8192)                    # delay, s * 8192
    assert f[8] == (2 << 12) | (5000 % 4096)            # loss | count
    assert f[9] == 60000                                # BWE, kbit/s


def test_fields_are_clamped():
    pkt = build_rctl(1, echo_ts=0, hold_ms=10**6, clock_s=0, delay_s=100.0,
                     received=0, bwe_kbps=10**6)
    f = struct.unpack(">BBHHIHHHHH", pkt[12:])
    assert f[5] == 0xFFFF and f[7] == 0xFFFF and f[9] == 0xFFFF


def _feed(st, n, *, fps=60, extra_lag_per_frame=0.0, ssrc=1, t0=100.0):
    ts, t = 0, t0
    for i in range(n):
        st.on_packet(ssrc, 0, ts, t + i * extra_lag_per_frame)
        ts += VIDEO_CLOCK_HZ // fps
        t += 1 / fps
    return t


def test_steady_stream_reports_no_delay():
    st = RctlState()
    _feed(st, 600)
    assert st.delay_s == 0.0
    assert st.received == 600 and st.last_ts is not None


def test_growing_queue_reports_delay():
    st = RctlState()
    _feed(st, 300)
    _feed(st, 120, extra_lag_per_frame=0.002, t0=105.0)   # each picture 2 ms later
    assert st.delay_s > 0.05


def test_other_tiles_count_but_do_not_sample_delay():
    st = RctlState()
    st.on_packet(1, 0, 0, 10.0)
    st.on_packet(2, 1, 0, 10.0)
    st.on_packet(2, 1, 400, 99.0)          # a very late tile-1 packet
    assert st.received == 3 and st.delay_s == 0.0
    assert st.last_ts == 400 and st.last_arrival == 99.0


def test_new_stream_starts_over():
    st = RctlState()
    _feed(st, 100, ssrc=1)
    st.on_packet(9, 0, 123, 500.0)         # tile 0 on a new SSRC
    assert st.ssrc == 9 and st.received == 1 and st.delay_s == 0.0
