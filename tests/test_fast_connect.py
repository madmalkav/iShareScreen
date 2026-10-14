"""Faster connect: the enc1103 entry is used as soon as it is in, the drain
waits for the Mac's message 1 (keeping partial records), the opening
SetEncodings leaves 1010 out (one message 1, as Apple's viewer gets), and the
fixed waits are gone or short."""
import socket
import struct

from isharescreen.proxy.protocol import negotiation as neg
from isharescreen.proxy.protocol import rfb
from isharescreen.proxy.protocol.enc1103 import StreamCipher

_BLOB, _KEY = bytes(range(36)), bytes(range(16, 32))


def _rect(enc, payload):
    return struct.pack(">HHHHi", 0, 0, 0, 0, enc) + payload


def _fbu(*rects):
    return struct.pack(">BBH", 0, 0, len(rects)) + b"".join(rects)


def _msg1():
    body = bytearray(36)
    struct.pack_into(">HHI", body, 0, 1, 1, 0)
    struct.pack_into(">HI", body, 8, 5900, 1)
    struct.pack_into(">HI", body, 14, 5901, 1)
    return _fbu(_rect(1010, struct.pack(">H", 36) + bytes(body)))


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


def test_scan_finds_1103_and_reports_incomplete():
    burst = _fbu(_rect(1103, bytes(range(36))))
    cipher, pos, complete = neg._scan_for_enc1103(burst + b"tail", _KEY)
    assert cipher is not None and complete and burst[pos:] == b"" and pos == len(burst)
    c2, _, complete2 = neg._scan_for_enc1103(burst[:20], _KEY)
    assert c2 is None and not complete2


def test_read_until_enc1103_returns_the_encrypted_tail():
    burst = _fbu(_rect(1103, bytes(range(36))))
    cipher, tail, have_msg1 = neg._read_until_enc1103(_Sock([burst[:10], burst[10:] + b"ENC"]), _KEY)
    assert tail == b"ENC" and not have_msg1


def test_drain_waits_for_message1_and_keeps_partial_records():
    tx = StreamCipher(_BLOB, ecb_key=_KEY)
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    layout = _fbu(_rect(1107, struct.pack(">H", 4) + b"abcd"))
    w = tx.encrypt_message(layout) + tx.encrypt_message(_msg1())
    msgs = neg._drain_until_message1(_Sock([w[:7], None, w[7:30], w[30:]]), rx, max_wait=1.0)
    assert msgs == [layout, _msg1()]


def test_drain_gives_up_without_message1():
    rx = StreamCipher(_BLOB, ecb_key=_KEY)
    assert neg._drain_until_message1(_Sock([]), rx, max_wait=0.2) == []


def test_opening_encodings_leave_out_1010(monkeypatch):
    monkeypatch.delenv("ISS_FAST_CONNECT", raising=False)
    assert 1010 in rfb.HP_ENCODINGS_FULL
    assert 1010 not in neg._opening_encodings()
    assert set(neg._opening_encodings()) == set(rfb.HP_ENCODINGS_FULL) - {1010}
    monkeypatch.setenv("ISS_FAST_CONNECT", "0")
    assert neg._opening_encodings() == rfb.HP_ENCODINGS_FULL


def test_short_waits_by_default(monkeypatch):
    monkeypatch.delenv("ISS_SS_PROMPT_WAIT_S", raising=False)
    monkeypatch.delenv("ISS_WARMUP_DWELL_S", raising=False)
    waits = []

    class S:
        def settimeout(self, t):
            waits.append(t)

        def recv(self, n, flags=0):
            raise socket.timeout()
    assert neg._peek_ss_prompt(S()) is False
    assert waits[0] == 0.5
    slept = []
    monkeypatch.setattr(rfb.time, "sleep", lambda t: slept.append(t))
    monkeypatch.setattr(rfb.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    try:
        rfb.warmup_tcp("192.0.2.1", 5900)
    except OSError:
        pass
    assert slept == []
