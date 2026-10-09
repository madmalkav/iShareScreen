"""The Mac's media-stream messages (encoding 1010) and the re-offer they
trigger: one offer per message 1 (or per burst of them), and a fallback
re-offer on a geometry change only when no message 1 comes."""
import struct
import threading
import time

from isharescreen.proxy.protocol.mediamsg import iter_fbu_media_msgs, parse_body
from isharescreen.proxy.session import Session


def _msg1_body(audio=5900, video=5901, video2=0):
    body = bytearray(36)
    struct.pack_into(">HHI", body, 0, 1, 1, 0)
    struct.pack_into(">HI", body, 8, audio, 1)
    struct.pack_into(">HI", body, 14, video, 1)
    struct.pack_into(">HI", body, 20, video2, 0)
    return bytes(body)


def _rect(enc, payload):
    return struct.pack(">HHHHi", 0, 0, 0, 0, enc) + payload


def _sized(enc, body):
    return _rect(enc, struct.pack(">H", len(body)) + body)


def _fbu(*rects):
    return struct.pack(">BBH", 0, 0, len(rects)) + b"".join(rects)


def test_parse_message1_ports():
    m = parse_body(_msg1_body())
    assert m.kind == 1 and m.ports == (5900, 5901, 0)
    assert "5901" in m.describe()


def test_parse_message3_error():
    body = struct.pack(">HHIII", 3, 1, 0, 1, 1)
    m = parse_body(body)
    assert m.kind == 3 and m.error == (1, 1)


def test_iter_steps_over_cursor_and_layout_rects():
    cursor = _rect(1104, struct.pack(">II", 7, 5) + b"zzzzz")
    layout = _sized(0x451, b"\x00" * 20)
    msg = _fbu(cursor, layout, _sized(1010, _msg1_body()))
    found = list(iter_fbu_media_msgs(msg))
    assert [m.kind for m in found] == [1]


def test_iter_ignores_non_fbu_and_truncated():
    assert list(iter_fbu_media_msgs(b"\x14\x00\x00\x04")) == []
    msg = _fbu(_sized(1010, _msg1_body()))
    assert list(iter_fbu_media_msgs(msg[:-5])) == []


class _Fake:
    """Just what `_request_reoffer` touches."""
    _request_reoffer = Session._request_reoffer

    def __init__(self):
        self._reoffer_lock = threading.Lock()
        self._reoffer_timer = None
        self._stop_evt = threading.Event()
        self.offers = []

    def _schedule_post_layout_arm(self):
        self.offers.append(time.monotonic())


def test_burst_of_requests_gives_one_offer():
    f = _Fake()
    for _ in range(3):
        f._request_reoffer(0.05, "message 1")
        time.sleep(0.01)
    time.sleep(0.2)
    assert len(f.offers) == 1


def test_fallback_does_not_delay_a_pending_message1_offer():
    f = _Fake()
    t0 = time.monotonic()
    f._request_reoffer(0.05, "message 1")
    f._request_reoffer(1.0, "layout change", keep_pending=True)
    time.sleep(0.2)
    assert len(f.offers) == 1 and f.offers[0] - t0 < 0.5


def test_message1_replaces_a_pending_fallback():
    f = _Fake()
    t0 = time.monotonic()
    f._request_reoffer(1.0, "layout change", keep_pending=True)
    f._request_reoffer(0.05, "message 1")
    time.sleep(0.2)
    assert len(f.offers) == 1 and f.offers[0] - t0 < 0.5
    time.sleep(1.0)
    assert len(f.offers) == 1


def test_no_offer_after_stop():
    f = _Fake()
    f._request_reoffer(0.05, "message 1")
    f._stop_evt.set()
    time.sleep(0.15)
    assert f.offers == []


# ── waiting for the answer at connect ───────────────────────────────

import plistlib
import socket
import zlib

from isharescreen.proxy.protocol import negotiation as neg
from isharescreen.proxy.protocol.enc1103 import StreamCipher

_BLOB, _KEY = bytes(range(36)), bytes(range(16, 32))


def _varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def _answer_msg(w=3840, h=2160, tiles=4):
    sub = b"\x20" + _varint(w) + b"\x28" + _varint(h) + b"\x30" + _varint(tiles)
    blob = zlib.compress(b"\x2a" + _varint(len(sub)) + sub)
    body = struct.pack(">HHI", 2, 1, 0) + plistlib.dumps(
        {"avcMediaStreamNegotiatorMediaBlob": blob}, fmt=plistlib.FMT_BINARY)
    return _fbu(_sized(1010, body))


class _Sock:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def settimeout(self, t):
        pass

    def recv(self, n):
        if not self.chunks:
            raise socket.timeout()
        c = self.chunks.pop(0)
        if c is None:
            raise socket.timeout()
        return c


def _wire(*msgs):
    tx = StreamCipher(_BLOB, ecb_key=_KEY)
    return [tx.encrypt_message(m) for m in msgs]


def test_answer_after_layout_rects_is_found_and_others_kept():
    layout = _fbu(_sized(0x451, b"\x00" * 20))
    w = _wire(layout, _answer_msg())
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    left = []
    canvas = neg._await_video_answer(_Sock([w[0], None, w[1]]), rx, left, 1.0)
    assert canvas == (3840, 2160, 4)
    assert left == [layout]


def test_partial_record_after_answer_is_completed():
    after = _fbu(_sized(1107, b"x" * 30))
    w = _wire(_answer_msg(), after)
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    left = []
    first = w[0] + w[1][:10]
    canvas = neg._await_video_answer(_Sock([first, w[1][10:]]), rx, left, 1.0)
    assert canvas[0] == 3840
    assert left == [after]


def test_no_answer_times_out_with_zeros():
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    t0 = time.monotonic()
    assert neg._await_video_answer(_Sock([]), rx, [], 0.2) == (0, 0, 0)
    assert time.monotonic() - t0 < 1.0


def _answer_without_canvas():
    blob = zlib.compress(b"\x2a\x00")
    body = struct.pack(">HHI", 2, 1, 0) + plistlib.dumps(
        {"avcMediaStreamNegotiatorMediaBlob": blob}, fmt=plistlib.FMT_BINARY)
    return _fbu(_sized(1010, body))


def _layout(bw=3840, bh=2160):
    return _fbu(_sized(0x451, struct.pack(">HHHHH", 0, bw // 2, bh // 2, bw, bh) + b"\x00" * 10))


def test_answer_without_canvas_uses_the_layout_seen_earlier():
    w = _wire(_answer_without_canvas())
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    canvas = neg._await_video_answer(_Sock([w[0]]), rx, [], 1.0,
                                     layout_backing=(3840, 2160))
    assert canvas[:2] == (3840, 2160)


def test_answer_without_canvas_uses_a_layout_in_the_same_exchange():
    w = _wire(_layout(2560, 1440), _answer_without_canvas())
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    canvas = neg._await_video_answer(_Sock(w), rx, [], 1.0)
    assert canvas[:2] == (2560, 1440)


def test_answer_without_canvas_and_no_layout_times_out():
    w = _wire(_answer_without_canvas())
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    assert neg._await_video_answer(_Sock([w[0]]), rx, [], 0.2) == (0, 0, 0)
