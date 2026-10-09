"""--refresh-rate: the virtual display's mode table advertises the chosen
refresh (the Mac sends at most one picture per refresh)."""
import struct

import pytest

from isharescreen import cli
from isharescreen.proxy.protocol.rfb import build_virtual_display


def _refreshes(msg):
    out, i = [], 0
    for hz in (30.0, 60.0, 90.0, 120.0):
        out.append((hz, msg.count(struct.pack(">d", hz))))
    return {hz: n for hz, n in out if n}


def test_default_is_60_in_every_mode():
    msg = build_virtual_display(width=1920, height=1080)
    assert _refreshes(msg) == {60.0: 5}


def test_30hz_in_every_mode():
    msg = build_virtual_display(width=1920, height=1080, refresh_hz=30)
    assert _refreshes(msg) == {30.0: 5}
    assert len(msg) == len(build_virtual_display(width=1920, height=1080))


@pytest.mark.parametrize("argv,hz", [([], 60), (["--refresh-rate", "30"], 30)])
def test_cli_option(argv, hz):
    args = cli._make_parser().parse_args(["--host", "h"] + argv)
    assert args.refresh_rate == hz


def test_cli_rejects_other_rates():
    with pytest.raises(SystemExit):
        cli._make_parser().parse_args(["--host", "h", "--refresh-rate", "120"])
