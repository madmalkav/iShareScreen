"""Precise scrolling: Apple's 0x17 scroll-wheel message (58 bytes, point
deltas on both axes) is used when the Mac's ServerInit bitmap lists 0x17;
wheel buttons otherwise."""
import struct
import types

import pytest

from isharescreen.proxy import input as inp
from isharescreen.proxy.protocol import negotiation as neg
from isharescreen.proxy import session as sess


def test_layout_matches_apples():
    m = inp.build_scroll_wheel(40, 25, 100, 200)
    assert len(m) == 58
    t, flags, size, ver, kind = struct.unpack(">BBHHH", m[:8])
    assert (t, flags, size, ver, kind) == (0x17, 0, 54, 1, 11)
    lx, ly, lz = struct.unpack(">hhh", m[8:14])
    fx, fy, fz = struct.unpack(">iii", m[14:26])
    px, py, pz = struct.unpack(">iii", m[26:38])
    phase, momentum, count, fl, x, y = struct.unpack(">IIIIHH", m[38:58])
    assert (px, py, pz) == (40, 25, 0)
    assert (lx, ly) == (4, 2) and (fx, fy) == (4 * 65536, int(2.5 * 65536))
    assert (phase, momentum, count, fl, x, y) == (0, 0, 0, 2, 100, 200)


def test_bitmap_bits_msb_first():
    bm = bytes([0xBF, 0xF6, 0xE7, 0x2F, 0xEC]) + bytes(11)    # measured Mac
    assert neg.server_accepts(bm, 0x1D) and neg.server_accepts(bm, 0x1C)
    assert neg.server_accepts(bm, 0x12) and neg.server_accepts(bm, 0x21)
    assert neg.server_accepts(bm, 0x17)
    assert not neg.server_accepts(bm, 0x01)                     # bit 6 of 0xbf
    assert not neg.server_accepts(b"", 0x17)


class _Cap:
    def __init__(self, precise):
        self.sent = []
        self.c = inp.InputController(None, None, server_width=3840, server_height=2160,
                                     precise_scroll=precise)
        self.c._send = self.sent.append


def test_precise_path_sends_one_message_with_flipped_signs():
    cap = _Cap(True)
    cap.c.scroll_event(10, 20, -3, 50)          # left 3, down 50
    assert len(cap.sent) == 1 and cap.sent[0][0] == 0x17
    px, py = struct.unpack(">ii", cap.sent[0][26:34])
    assert (px, py) == (3, -50)                 # 0x17: left/up positive


def test_wheel_path_unchanged():
    cap = _Cap(False)
    cap.c.pointer_event = lambda b, x, y: cap.sent.append(("ptr", b))
    cap.c.scroll_event(10, 20, 5, -2)           # dx ignored, 2 ticks up
    assert [b for _, b in cap.sent] == [inp.BTN_SCROLL_UP, 0, inp.BTN_SCROLL_UP, 0]


@pytest.mark.parametrize("env,bitmap,expected", [
    (None, bytes([0, 0, 0x01]), True), ("0", bytes([0, 0, 0x01]), False), (None, b"", False)])
def test_session_choice(monkeypatch, env, bitmap, expected):
    if env is None:
        monkeypatch.delenv("ISS_PRECISE_SCROLL", raising=False)
    else:
        monkeypatch.setenv("ISS_PRECISE_SCROLL", env)
    assert sess._precise_scroll_ok(types.SimpleNamespace(server_bitmap=bitmap)) is expected
