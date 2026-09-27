"""The desktop viewer window must open inside the usable screen area
(frontend/desktop/canvas.py): `--advertise 1920x1080` on a 1080-high screen
opened a window whose title bar/bottom went off-screen."""
from __future__ import annotations

import os

import pytest

glfw = pytest.importorskip("glfw")

from isharescreen.frontend.desktop import canvas as canvas_mod  # noqa: E402
from isharescreen.frontend.desktop.canvas import fit_size  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    canvas_mod._wayland_area_cache.clear()
    yield
    canvas_mod._wayland_area_cache.clear()


def test_fit_size_shrinks_keeping_aspect():
    assert fit_size((1920, 1080), (2560, 1052)) == (1870, 1052)
    assert fit_size((1920, 1080), (1500, 2000)) == (1500, 843)


def test_fit_size_never_enlarges_or_guesses():
    assert fit_size((1600, 900), (2560, 1052)) == (1600, 900)
    assert fit_size((1920, 1080), None) == (1920, 1080)


def test_non_wayland_uses_workarea_minus_frame(monkeypatch):
    monkeypatch.setattr(canvas_mod, "_on_wayland", lambda: False)
    monkeypatch.setattr(glfw, "get_primary_monitor", lambda: object())
    monkeypatch.setattr(glfw, "get_monitor_workarea", lambda m: (0, 25, 1920, 1055))
    monkeypatch.setattr(glfw, "get_window_frame_size", lambda w: (1, 28, 1, 1))
    assert canvas_mod.usable_screen_area() == (1920, 1055)          # no window yet
    assert canvas_mod.usable_screen_area(object()) == (1918, 1026)  # frame known


def test_wayland_asks_the_compositor(monkeypatch):
    monkeypatch.setattr(canvas_mod, "_on_wayland", lambda: True)
    monkeypatch.setattr(canvas_mod, "_wayland_usable_area", lambda: (2560, 1052))
    monkeypatch.setattr(glfw, "get_monitor_workarea",
                        lambda m: pytest.fail("work area is physical px on Wayland"))
    assert canvas_mod.usable_screen_area(object()) == (2560, 1052)


def test_fit_window_to_screen(monkeypatch):
    calls = []
    monkeypatch.setattr(canvas_mod, "usable_screen_area", lambda w=None: (2560, 1052))
    monkeypatch.setattr(glfw, "get_window_size", lambda w: (1920, 1080))
    monkeypatch.setattr(glfw, "set_window_size", lambda w, a, b: calls.append((a, b)))
    canvas_mod.fit_window_to_screen(object())
    assert calls == [(1870, 1052)]


def test_fit_window_leaves_fitting_window_alone(monkeypatch):
    monkeypatch.setattr(canvas_mod, "usable_screen_area", lambda w=None: (2560, 1052))
    monkeypatch.setattr(glfw, "get_window_size", lambda w: (1600, 900))
    monkeypatch.setattr(glfw, "set_window_size", lambda *a: pytest.fail("resized"))
    canvas_mod.fit_window_to_screen(object())


@pytest.mark.skipif(not os.environ.get("WAYLAND_DISPLAY"),
                    reason="needs a Wayland session")
def test_real_wayland_usable_area_is_logical_and_within_output():
    try:
        glfw.init()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no GLFW: {e}")
    if not canvas_mod._on_wayland():
        pytest.skip("GLFW is not using the Wayland platform")
    area = canvas_mod._wayland_usable_area()
    assert area is not None
    mon = glfw.get_primary_monitor()
    vm = glfw.get_video_mode(mon)
    sx, sy = glfw.get_monitor_content_scale(mon)
    logical = (round(vm.size.width / sx), round(vm.size.height / sy))
    assert 0 < area[0] <= logical[0] and 0 < area[1] < logical[1]  # title bar at least
