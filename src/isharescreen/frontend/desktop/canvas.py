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
"""
from __future__ import annotations

import glfw
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


__all__ = ["WaylandSafeCanvas"]
