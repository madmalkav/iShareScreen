"""Decode-pipeline hot paths: emulation-prevention stripping and zero-copy
tile planes. Both used to hold the GIL for milliseconds per 4K tile and
capped the HEVC 4:4:4 pipeline near 60 fps."""
from __future__ import annotations

import gc
import random

import av
import pytest

from isharescreen.proxy.media.bitstream import remove_emulation_prevention
from isharescreen.proxy.media.decode_common import _av_frame_to_tile
from isharescreen.proxy.media.hwcaps import _HEVC444_SAMPLE


def _reference_epb(data: bytes) -> bytes:
    """The original byte-wise implementation, kept as the oracle."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        if i + 2 < n and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 3:
            out += b"\x00\x00"
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


@pytest.mark.parametrize("data", [
    b"", b"\x03", b"\x00\x00", b"\x00\x00\x03", b"\x00\x00\x03\x00\x00\x03",
    b"\x00\x00\x00\x03", b"\x00\x00\x03\x03", b"\x00\x00\x03\x00\x03",
    b"\x01\x00\x00\x03\x01\x00\x00\x03",
])
def test_epb_edge_cases_match_reference(data):
    assert remove_emulation_prevention(data) == _reference_epb(data)


def test_epb_random_inputs_match_reference():
    rnd = random.Random(1)
    for _ in range(20000):
        data = bytes(rnd.choice((0, 0, 0, 3, 3, 1, 255))
                     for _ in range(rnd.randint(0, 40)))
        assert remove_emulation_prevention(data) == _reference_epb(data)


def test_epb_accepts_other_buffers():
    raw = b"\x25\x00\x00\x03\x01"
    for buf in (bytearray(raw), memoryview(raw)):
        assert remove_emulation_prevention(buf) == b"\x25\x00\x00\x01"


def _decoded_444_frame() -> av.VideoFrame:
    ctx = av.CodecContext.create("hevc", "r")
    frames = list(ctx.decode(av.Packet(_HEVC444_SAMPLE))) + list(ctx.decode(None))
    assert frames and frames[0].format.name == "yuv444p"
    return frames[0]


def test_tile_planes_are_zero_copy_and_outlive_the_frame():
    frame = _decoded_444_frame()
    expected = [bytes(p) for p in frame.planes]
    tile, _ = _av_frame_to_tile(frame, [None], set())
    assert all(isinstance(b, memoryview) for b in (tile.y, tile.u, tile.v))
    del frame
    gc.collect()
    # The views keep the decoded buffers alive after the frame is dropped.
    assert [bytes(tile.y), bytes(tile.u), bytes(tile.v)] == expected


def test_copy_true_returns_bytes():
    frame = _decoded_444_frame()
    tile, _ = _av_frame_to_tile(frame, [None], set(), copy=True)
    assert all(type(b) is bytes for b in (tile.y, tile.u, tile.v))
    assert tile.y == bytes(frame.planes[0])


def test_gpu_upload_accepts_zero_copy_tile():
    from isharescreen.frontend.desktop.gpu import Renderer

    class _Q:
        def __init__(self):
            self.sizes = []

        def write_texture(self, dst, data, layout, size):
            assert data.nbytes == layout["bytes_per_row"] * size[1]
            self.sizes.append(size)

    tile, _ = _av_frame_to_tile(_decoded_444_frame(), [None], set())
    r = Renderer.__new__(Renderer)
    r._device = type("D", (), {"queue": _Q()})()
    r._w, r._h = tile.width, tile.height
    r._content_w = r._content_h = 0
    r._dbg_tiles = {}
    r._mode = "planar"
    r._y_tex = r._u_tex = r._v_tex = object()
    r.upload_tile(0, tile, tile.height)
    assert r._device.queue.sizes == [(tile.width, tile.height, 1)] * 3
