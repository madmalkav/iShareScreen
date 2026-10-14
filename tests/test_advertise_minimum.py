"""Virtual displays are never asked for below 800x600 points, the least
Apple's own viewer asks for: the CLI's --advertise, and the desktop and
browser viewers' resize paths (below it the Mac can send no picture)."""
import pytest

from isharescreen import cli
from isharescreen.frontend.desktop import app
from isharescreen.proxy.protocol.rfb import MIN_DISPLAY_H, MIN_DISPLAY_W


def test_minimum_is_apples():
    assert (MIN_DISPLAY_W, MIN_DISPLAY_H) == (800, 600)


@pytest.mark.parametrize("spec,expected", [
    ("320x90@1", (800, 600, 1.0)),
    ("640x480", (800, 600, 2.0)),
    ("1920x200", (1920, 600, 2.0)),
    ("800x600@1", (800, 600, 1.0)),
    ("1600x900@2", (1600, 900, 2.0)),
])
def test_advertise_minimum(spec, expected, capsys):
    a = cli._parse_advertise(spec)
    assert (a.width, a.height, a.hidpi_scale) == expected
    warned = "minimum" in capsys.readouterr().err
    assert warned == (expected[:2] != tuple(int(v) for v in spec.split("@")[0].split("x")))


@pytest.mark.parametrize("win,expected", [((400, 300), (800, 600)), ((1200, 700), (1200, 700))])
def test_desktop_resize_request_minimum(win, expected):
    w, h, _scale = app._resolve_hidpi_request("on", *win)
    assert (w, h) == expected
