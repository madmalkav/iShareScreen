"""Session.get_frame counts the frames actually handed to the frontend per
tile (the displayed rate, reported as `shown_rates` in the profile line), the
same way for every decoder."""
from __future__ import annotations

import types

from isharescreen.proxy.session import Session


def _session(frames):
    s = Session.__new__(Session)
    s._observed_tile_count = 2
    s._shown_counts = []
    s._decoder = types.SimpleNamespace(get_frame=lambda ti: frames[ti].pop(0))
    return s


def test_counts_only_returned_frames():
    s = _session({0: ["f", None, "f"], 1: [None, None]})
    for ti, n in ((0, 3), (1, 2)):
        for _ in range(n):
            s.get_frame(ti)
    assert s._shown_counts == [2, 0]


def test_no_decoder_counts_nothing():
    s = Session.__new__(Session)
    s._decoder = None
    s._shown_counts = []
    assert s.get_frame(0) is None
    assert s._shown_counts == []
