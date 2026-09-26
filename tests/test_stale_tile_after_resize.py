"""A frame decoded before a dynamic resize shrank the canvas must not crash
the desktop renderer (wgpu: "Copy of X 0..3840 would end up overrunning the
bounds of the Destination texture of X size 2560" → fatal exit)."""
from __future__ import annotations

import pytest

from isharescreen.frontend.desktop.app import _is_stale_tile
from isharescreen.frontend.desktop.gpu import Renderer
from isharescreen.proxy.media.tiles import TileFrame


def _tile(w: int, h: int) -> TileFrame:
    return TileFrame(y=bytes(w * h), u=bytes(w * h), v=bytes(w * h),
                     width=w, height=h, y_stride=w, uv_stride=w,
                     chroma_width=w, chroma_height=h)


class _Queue:
    def __init__(self) -> None:
        self.writes: list[tuple] = []

    def write_texture(self, dst, data, layout, size) -> None:
        self.writes.append(size)


def _renderer(canvas_w: int, canvas_h: int) -> Renderer:
    r = Renderer.__new__(Renderer)
    r._device = type("D", (), {"queue": _Queue()})()
    r._w, r._h = canvas_w, canvas_h
    r._content_w = r._content_h = 0
    r._dbg_tiles = {}
    r._mode = "planar"
    r._y_tex = r._u_tex = r._v_tex = object()
    return r


def test_is_stale_tile():
    assert _is_stale_tile(_tile(3840, 8), 2560)
    assert not _is_stale_tile(_tile(2560, 8), 2560)
    # Host fell back below the advertised canvas: legitimate, letterboxed.
    assert not _is_stale_tile(_tile(1920, 8), 2560)


def test_upload_skips_tile_wider_than_canvas():
    r = _renderer(2560, 2104)
    r.upload_tile(0, _tile(3840, 394), 526)
    assert r._device.queue.writes == []
    assert r._content_w == 0          # didn't grow the content extent


def test_upload_writes_fitting_tile():
    r = _renderer(2560, 2104)
    r.upload_tile(0, _tile(2560, 528), 528)
    assert r._device.queue.writes == [(2560, 528, 1)] * 3   # Y, U, V


def test_real_wgpu_stale_tile_does_not_raise():
    """The exact crash from the field log, on a real device when present."""
    wgpu = pytest.importorskip("wgpu")
    try:
        adapter = wgpu.gpu.request_adapter_sync(power_preference="low-power")
        device = adapter.request_device_sync()
    except Exception as e:  # pragma: no cover - no GPU in CI
        pytest.skip(f"no wgpu device: {e}")
    if adapter is None:  # pragma: no cover
        pytest.skip("no wgpu adapter")
    r = Renderer(device, "bgra8unorm", 2560, 2104)
    r.upload_tile(0, _tile(3840, 394), 526)   # used to raise GPUValidationError
    r.upload_tile(0, _tile(2560, 528), 528)   # the new stream still uploads
