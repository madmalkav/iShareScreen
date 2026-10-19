"""FrameQualityGate.fast_retry: keyframe requests at the host's real pace.

While a tile waits for its keyframe the gate re-requests every
`_FAST_RE_ARM_S`; once a keyframe arrived and the tile only waits for a
clean frame it keeps the old 1 s pace; damage after the keyframe (past the
short grace) re-arms the fast pace; `on_keyframe_needed` fires when a tile
newly needs one. The default (non-fast) gate is unchanged.
"""
from __future__ import annotations

from isharescreen.proxy.media.quality_gate import FrameQualityGate


class _FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _gate(fast: bool = True) -> tuple[FrameQualityGate, _FakeClock, list[int]]:
    g = FrameQualityGate(4)
    clk = _FakeClock()
    g._time = clk
    calls: list[int] = []
    if fast:
        g.fast_retry()
        g.on_keyframe_needed = lambda: calls.append(1)
    return g, clk, calls


def test_first_request_goes_out_at_once_and_wakes_the_sender():
    g, clk, calls = _gate()
    g.mark_decode_error(2)
    assert calls == [1]
    assert g.next_fir_due() <= clk.t
    assert g.consume_fir_request() == {0}


def test_retries_at_fast_pace_until_a_keyframe_arrives():
    g, clk, _ = _gate()
    g.mark_decode_error(1)
    assert g.consume_fir_request() == {0}
    clk.advance(g._FAST_RE_ARM_S / 2)
    assert g.consume_fir_request() == set()
    clk.advance(g._FAST_RE_ARM_S / 2 + 0.001)
    assert g.consume_fir_request() == {0}


def test_after_the_keyframe_waits_at_the_old_pace():
    g, clk, _ = _gate()
    g.mark_decode_error(1)
    assert g.consume_fir_request() == {0}
    clk.advance(0.03)
    for ti in range(4):                     # the IDR fans out to every tile
        g.mark_idr_observed(ti)
    clk.advance(g._FAST_RE_ARM_S + 0.01)
    assert g.consume_fir_request() == set()
    assert g.next_fir_due() == g._fir_last_t[0] + g._RE_ARM_INTERVAL_S


def test_damage_after_the_keyframe_rearms_the_fast_pace():
    g, clk, calls = _gate()
    g.mark_decode_error(1)
    assert g.consume_fir_request() == {0}
    clk.advance(0.03)
    for ti in range(4):
        g.mark_idr_observed(ti)
    clk.advance(g._POST_IDR_GRACE_S + 0.01)
    g.mark_decode_error(1)                  # a new loss, past the grace
    assert len(calls) == 2
    assert g.consume_fir_request() == {0}


def test_damage_inside_the_grace_is_ignored():
    g, clk, calls = _gate()
    g.mark_idr_observed(0)
    clk.advance(g._POST_IDR_GRACE_S / 2)
    g.mark_decode_error(0)
    assert not g._keyframe_required and calls == []


def test_clean_frame_after_keyframe_clears():
    g, clk, _ = _gate()
    g.mark_decode_error(1)
    clk.advance(0.03)
    g.mark_idr_observed(1)
    clk.advance(0.02)
    g.mark_clean(1)
    assert not g._keyframe_required
    assert g.next_fir_due() is None


def test_default_gate_keeps_old_timings():
    g, clk, _ = _gate(fast=False)
    assert not g.fast and g.on_keyframe_needed is None
    assert g._POST_IDR_GRACE_S == 0.5
    g.mark_decode_error(1)
    assert g.consume_fir_request() == {0}
    clk.advance(0.2)
    assert g.consume_fir_request() == set()
    clk.advance(g._RE_ARM_INTERVAL_S)
    assert g.consume_fir_request() == {0}
