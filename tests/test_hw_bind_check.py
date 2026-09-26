"""First-frame hwaccel binding check in HevcDecoder._publish_frame.

PyAV downloads hardware frames to system memory, so a real VAAPI/CUDA decode
returns `yuv444p` frames just like a software fallback does. The check must
judge binding by the codec context's pixel format instead."""
from __future__ import annotations

import types

import av
import numpy as np

from isharescreen.proxy.media.hevc import HevcDecoder


def _decoder(hw_name, codec_fmt):
    d = HevcDecoder.__new__(HevcDecoder)
    d._hw_name = hw_name
    d._hw_verified = False
    d._codec = (None if codec_fmt is None else
                types.SimpleNamespace(format=types.SimpleNamespace(name=codec_fmt)))
    d._pts_to_tile = {}
    d._pts_submit_t = {}
    return d


def _frame(fmt: str = "yuv444p") -> av.VideoFrame:
    return av.VideoFrame.from_ndarray(np.zeros((3, 2, 2), np.uint8), format=fmt)


def test_downloaded_frame_from_bound_hwaccel_keeps_label():
    d = _decoder("vaapi", "vaapi")
    d._publish_frame(_frame())
    assert d.hw_accel == "vaapi"
    assert d._hw_verified


def test_software_fallback_is_relabelled():
    # get_format fell back: context settled on the software format.
    d = _decoder("d3d11va", "yuv444p")
    d._publish_frame(_frame())
    assert d.hw_accel is None
    assert d._hw_verified


def test_check_waits_when_context_is_gone():
    # A concurrent restart nulled the context — can't tell, try next frame.
    d = _decoder("cuda", None)
    d._publish_frame(_frame())
    assert d.hw_accel == "cuda"
    assert not d._hw_verified

