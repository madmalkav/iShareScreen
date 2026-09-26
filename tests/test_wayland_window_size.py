"""Desktop window sizing on Wayland (frontend/desktop/canvas.py).

rendercanvas doubled the window on Wayland at 200% scaling (1920x1080 ->
3840x2160 window, 7680x4320 surface) because it re-applied the size when the
output scale arrived, while the framebuffer was still unscaled."""
from __future__ import annotations

import os
import time

import pytest

glfw = pytest.importorskip("glfw")
from rendercanvas.glfw import RenderCanvas  # noqa: E402

from isharescreen.frontend.desktop import canvas as canvas_mod  # noqa: E402
from isharescreen.frontend.desktop.canvas import WaylandSafeCanvas  # noqa: E402


def _bare() -> WaylandSafeCanvas:
    c = WaylandSafeCanvas.__new__(WaylandSafeCanvas)
    c._window = object()
    c._screen_size_is_logical = False
    return c


def test_wayland_sets_logical_size_directly(monkeypatch):
    calls = []
    monkeypatch.setattr(canvas_mod, "_on_wayland", lambda: True)
    monkeypatch.setattr(canvas_mod.glfw, "set_window_size",
                        lambda win, w, h: calls.append((w, h)))
    c = _bare()
    c._set_logical_size((1920.0, 1080.0))
    assert calls == [(1920, 1080)]
    assert c._screen_size_is_logical


def test_other_platforms_keep_rendercanvas_behaviour(monkeypatch):
    calls = []
    monkeypatch.setattr(canvas_mod, "_on_wayland", lambda: False)
    monkeypatch.setattr(RenderCanvas, "_set_logical_size",
                        lambda self, size: calls.append(size))
    _bare()._set_logical_size((1920.0, 1080.0))
    assert calls == [(1920.0, 1080.0)]


@pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"),
                    reason="needs a Wayland session")
def test_real_wayland_window_is_requested_size():
    try:
        c = WaylandSafeCanvas(title="iss size test", size=(800, 600))
    except Exception as e:  # pragma: no cover - no usable compositor
        pytest.skip(f"cannot open a window: {e}")
    try:
        if not canvas_mod._on_wayland():
            pytest.skip("GLFW is not using the Wayland platform")
        for _ in range(20):          # let the compositor report its scale
            glfw.poll_events()
            time.sleep(0.03)
        ratio = c.get_pixel_ratio()
        assert tuple(c.get_logical_size()) == (800, 600)
        assert glfw.get_window_size(c._window) == (800, 600)
        assert glfw.get_framebuffer_size(c._window) == (round(800 * ratio),
                                                        round(600 * ratio))
    finally:
        c.close()
