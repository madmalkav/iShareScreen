"""--cursor overlay|video: with 'video' the 0x1c offer leaves the
do-not-send-cursor bit (0x04) clear so the host draws the cursor into the
video; the browser frontend then hides its CSS cursor."""
from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from isharescreen.cli import _make_parser
from isharescreen.proxy.protocol.negotiation import build_0x1c


def _flags(monkeypatch, env: dict) -> int:
    for k in ("ISS_VIDEO_CURSOR", "ISS_LEGACY_CURSOR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    keys = SimpleNamespace(audio_key_v=bytes(46), audio_key_s=bytes(46),
                           video_key_v=bytes(46), video_key_s=bytes(46))
    msg = build_0x1c(b"a" * 10, b"v" * 10, keys)
    return struct.unpack_from(">I", msg, 6)[0]


def test_default_asks_host_not_to_send_cursor(monkeypatch):
    assert _flags(monkeypatch, {}) & 4


def test_video_cursor_lets_host_draw_it(monkeypatch):
    assert not _flags(monkeypatch, {"ISS_VIDEO_CURSOR": "1"}) & 4


def test_cli_option():
    p = _make_parser()
    assert p.parse_args([]).cursor == "overlay"
    assert p.parse_args(["--cursor", "video"]).cursor == "video"
    with pytest.raises(SystemExit):
        p.parse_args(["--cursor", "bogus"])


def test_cli_main_sets_env(monkeypatch):
    import isharescreen.cli as cli
    monkeypatch.delenv("ISS_VIDEO_CURSOR", raising=False)
    monkeypatch.setattr(cli.signal, "signal", lambda *a: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        cli.main(["--cursor", "video"])
    import os
    assert os.environ.get("ISS_VIDEO_CURSOR") == "1"


def test_browser_hides_css_cursor_in_video_mode(monkeypatch):
    wt = pytest.importorskip("isharescreen.frontend.wt.server")
    sent = []
    srv = SimpleNamespace(_loop=object(), _broadcast_frame=sent.append)
    img = SimpleNamespace(width=28, height=40, hotspot_x=5, hotspot_y=5, rgba=b"\xff" * (28 * 40 * 4))
    monkeypatch.delenv("ISS_VIDEO_CURSOR", raising=False)
    wt.WebTransportBridge._on_cursor(srv, img)
    monkeypatch.setenv("ISS_VIDEO_CURSOR", "1")
    wt.WebTransportBridge._on_cursor(srv, img)
    w_h = [struct.unpack_from(">HH", e, 14) for e in sent]
    assert w_h == [(28, 40), (0, 0)]
