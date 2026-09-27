"""rendercanvas GLFW window with correct sizing on Wayland.

rendercanvas (2.7) converts a logical size to GLFW window coordinates as
`logical * content_scale * (window_size / framebuffer_size)`, a runtime probe
for "are window coordinates physical (Windows/X11) or logical (macOS)?". On
Wayland the probe runs too early: when the compositor reports the output
scale (e.g. 2.0 at 200%), rendercanvas re-applies the size while the hidden
window's framebuffer is still unscaled, reads the ratio as 1 and doubles the
window. A 1920x1080 request became a 3840x2160 window with a 7680x4320
surface — larger than the screen, and 4x the pixels to draw each frame.

GLFW window coordinates on Wayland are always logical, so set them directly.
Other platforms keep rendercanvas's behaviour.

`usable_screen_area` / `fit_window_to_screen` keep a new window, including its
title bar, inside the screen's usable area (minus panels, dock, menu bar).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import glfw

log = logging.getLogger(__name__)
from rendercanvas.glfw import RenderCanvas


def _on_wayland() -> bool:
    return (hasattr(glfw, "get_platform")
            and glfw.get_platform() == getattr(glfw, "PLATFORM_WAYLAND", -1))


class WaylandSafeCanvas(RenderCanvas):
    def _set_logical_size(self, new_logical_size):
        if self._window is not None and _on_wayland():
            glfw.set_window_size(
                self._window, int(new_logical_size[0]), int(new_logical_size[1]))
            self._screen_size_is_logical = True
            return
        super()._set_logical_size(new_logical_size)


_wayland_area_cache: list = []


def _wayland_usable_area(timeout: float = 0.5) -> Optional[tuple[int, int]]:
    """Usable client size of a maximized window, asked of the compositor.

    Wayland exposes neither the work area (GLFW reports the whole output, in
    physical pixels) nor server-side title-bar sizes. A throwaway window hinted
    maximized gets the usable client size (panels and title bar already
    subtracted) in its first configure. It never attaches a buffer, so the
    compositor never maps it and nothing appears on screen. Cached per process.
    """
    if _wayland_area_cache:
        return _wayland_area_cache[0]
    area = None
    win = None
    try:
        glfw.init()
        glfw.default_window_hints()
        glfw.window_hint(glfw.CLIENT_API, glfw.NO_API)
        glfw.window_hint(glfw.MAXIMIZED, glfw.TRUE)
        win = glfw.create_window(64, 64, "", None, None)
        deadline = time.monotonic() + timeout
        while win and time.monotonic() < deadline:
            glfw.wait_events_timeout(0.02)
            w, h = glfw.get_window_size(win)
            if glfw.get_window_attrib(win, glfw.MAXIMIZED) and (w, h) != (64, 64) and w > 0 and h > 0:
                area = (w, h)
                break
    except Exception as e:
        log.debug("wayland usable-area probe failed: %s", e)
    finally:
        if win:
            glfw.destroy_window(win)
        glfw.default_window_hints()
    _wayland_area_cache.append(area)
    log.debug("wayland usable window area: %s", area)
    return area


def usable_screen_area(glfw_window=None) -> Optional[tuple[int, int]]:
    """Largest client size (window coordinates) whose window, decorations
    included, fits the primary monitor's usable area. None if unknown.

    Wayland: asks the compositor (see `_wayland_usable_area`). Elsewhere: the
    monitor work area minus the window's frame (title bar/borders), which GLFW
    reports on Windows, macOS and X11 once a window exists.
    """
    if _on_wayland():
        return _wayland_usable_area()
    try:
        mon = glfw.get_primary_monitor()
        if not mon:
            return None
        _x, _y, aw, ah = glfw.get_monitor_workarea(mon)
        left = top = right = bottom = 0
        if glfw_window is not None:
            left, top, right, bottom = glfw.get_window_frame_size(glfw_window)
        w, h = aw - left - right, ah - top - bottom
        return (w, h) if w > 0 and h > 0 else None
    except Exception as e:
        log.debug("usable screen area unavailable: %s", e)
        return None


def fit_size(size: tuple[int, int], area: Optional[tuple[int, int]]) -> tuple[int, int]:
    """Shrink `size` to fit `area`, keeping its aspect. Never enlarges."""
    w, h = size
    if not area or w <= 0 or h <= 0:
        return size
    f = min(area[0] / w, area[1] / h, 1.0)
    return (max(1, int(w * f)), max(1, int(h * f)))


def fit_window_to_screen(glfw_window) -> None:
    """Shrink an open window (keeping its aspect) so it and its title bar fit
    the usable screen area. No-op when it already fits or the area is unknown."""
    try:
        cur = glfw.get_window_size(glfw_window)
        new = fit_size(cur, usable_screen_area(glfw_window))
        if new != tuple(cur):
            glfw.set_window_size(glfw_window, *new)
            log.info("window %dx%d does not fit the usable screen area; "
                     "opened at %dx%d (video is scaled to fit)", cur[0], cur[1], *new)
    except Exception as e:
        log.debug("fit window to screen failed: %s", e)


__all__ = ["WaylandSafeCanvas", "fit_size", "fit_window_to_screen", "usable_screen_area"]
