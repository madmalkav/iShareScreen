"""A dynamic resize re-arms the TCP side with a 1x1 incremental update
request, never a full-screen one (a full-size read racing a display shrink
can crash the host's ScreensharingAgent)."""
from __future__ import annotations

import struct
import types

from isharescreen.proxy.session import Session


def test_resize_sends_display_config_then_1x1_request():
    sent = []
    s = Session.__new__(Session)
    s._negotiation = types.SimpleNamespace(
        sock=object(),
        cipher=types.SimpleNamespace(encrypt_and_send=lambda sock, msg: sent.append(msg)))
    s._config = types.SimpleNamespace(refresh_hz=60)
    s.fir_calls = 0
    s.request_fir = lambda tile=None: setattr(s, "fir_calls", s.fir_calls + 1)

    s.send_dynamic_resolution(1280, 720, hidpi_scale=2.0)

    assert sent[0][0] == 0x1d                                   # SetDisplayConfiguration
    fbu = [m for m in sent if m[0] == 0x03]
    assert len(fbu) == 1
    _, incremental, x, y, w, h = struct.unpack(">BBHHHH", fbu[0])
    assert (incremental, x, y, w, h) == (1, 0, 0, 1, 1)
    assert s.fir_calls == 1


def test_resize_keeps_the_configured_refresh_rate():
    sent = []
    s = Session.__new__(Session)
    s._negotiation = types.SimpleNamespace(
        sock=object(),
        cipher=types.SimpleNamespace(encrypt_and_send=lambda sock, msg: sent.append(msg)))
    s._config = types.SimpleNamespace(refresh_hz=30)
    s.request_fir = lambda tile=None: None
    s.send_dynamic_resolution(1280, 720, hidpi_scale=2.0)
    assert struct.pack(">d", 30.0) in sent[0]
    assert struct.pack(">d", 60.0) not in sent[0]
