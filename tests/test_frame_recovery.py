"""HEVC damage and recovery from the frames themselves (hevc.py
`_FRAME_RECOVERY`): a frame flagged `is_corrupt` marks its tile for a
keyframe; a keyframe followed by an unflagged frame is recovery, whatever
the keyframe's size."""
from __future__ import annotations

import types

from isharescreen.proxy.media import hevc
from isharescreen.proxy.media.hevc import HevcDecoder
from isharescreen.proxy.media.tiles import TileFrame


def _tile():
    b = bytes(16)
    return TileFrame(y=b, u=b, v=b, width=4, height=4, y_stride=4, uv_stride=4,
                     chroma_width=4, chroma_height=4)


def _decoder(monkeypatch) -> HevcDecoder:
    d = HevcDecoder(4)
    d._dpb_has_idr = True
    # The frame conversion isn't under test: hand back a tile and the old
    # "no error" reading (PyAV 19 frames have no decode_error_flags).
    monkeypatch.setattr(hevc, "_av_frame_to_tile", lambda *a, **k: (_tile(), False))
    return d


def _show(d, ti, corrupt):
    slot = d._tiles[ti]
    slot.raw_frame = types.SimpleNamespace(is_corrupt=corrupt, pts=0)
    slot.good_count += 1
    return d.get_frame(ti)


def test_flags_keep_output_corrupt():
    assert hevc._HEVC_CODEC_FLAGS & 0x8 and hevc._HEVC_CODEC_FLAGS & 0x80000


def test_corrupt_frame_marks_its_tile_and_is_still_shown(monkeypatch):
    d = _decoder(monkeypatch)
    d._gate._POST_IDR_GRACE_S = 0.0
    assert _show(d, 2, True) is not None          # still shown
    assert d._gate._keyframe_required == {2}
    assert d.corrupt_shown == [0, 0, 1, 0]


def test_keyframe_then_clean_frame_recovers_even_if_small(monkeypatch):
    d = _decoder(monkeypatch)
    g = d._gate
    g._POST_IDR_GRACE_S = 0.0
    g._RECOVERY_QUIET_S = 0.0
    _show(d, 0, True)
    assert 0 in g._keyframe_required
    # hevc.py passes suspicious=False to the gate under frame recovery.
    g.mark_idr_observed(0, suspicious=False)
    _show(d, 0, False)
    assert 0 not in g._keyframe_required


def test_flagged_frames_after_a_keyframe_keep_the_tile_marked(monkeypatch):
    d = _decoder(monkeypatch)
    g = d._gate
    g._POST_IDR_GRACE_S = 0.0
    g._RECOVERY_QUIET_S = 0.0
    _show(d, 0, True)
    g.mark_idr_observed(0, suspicious=False)
    _show(d, 0, True)                             # keyframe didn't repair it
    assert 0 in g._keyframe_required


def test_opt_out_reads_frames_as_before(monkeypatch):
    monkeypatch.setattr(hevc, "_FRAME_RECOVERY", False)
    d = _decoder(monkeypatch)
    d._gate._POST_IDR_GRACE_S = 0.0
    _show(d, 1, True)
    assert d._gate._keyframe_required == set()    # old: flag never read
    assert d.corrupt_shown == [0, 1, 0, 0]        # still counted
