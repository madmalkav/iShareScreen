"""--advertise below 640x480 is raised to it (the Mac can't encode some small
heights with 4 tiles: no picture at all)."""
import pytest

from isharescreen import cli


@pytest.mark.parametrize("spec,expected", [
    ("320x90@1", (640, 480, 1.0)),
    ("1920x200", (1920, 480, 2.0)),
    ("640x480@1", (640, 480, 1.0)),
    ("1600x900@2", (1600, 900, 2.0)),
])
def test_advertise_minimum(spec, expected, capsys):
    a = cli._parse_advertise(spec)
    assert (a.width, a.height, a.hidpi_scale) == expected
    warned = "minimum" in capsys.readouterr().err
    assert warned == (spec in ("320x90@1", "1920x200"))
