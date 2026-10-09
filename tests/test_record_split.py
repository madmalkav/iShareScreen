"""Control-channel records: a message longer than one record's body goes out
as full records and then the rest, and the receiver gets it back by
concatenation."""
import os
import struct

import pytest

from isharescreen.proxy.protocol.clipboard import (
    ClipboardReassembler, build_clipboard_send, decompress_clipboard_payload,
    parse_clipboard_items, parse_clipboard_send_header, text_from_items,
)
from isharescreen.proxy.protocol.enc1103 import MAX_RECORD_BODY, StreamCipher

_BLOB = bytes(range(36))
_KEY = bytes(range(16, 32))


def _pair():
    return (StreamCipher(_BLOB, ecb_key=_KEY), StreamCipher(_BLOB, ecb_key=_KEY))


def _record_lengths(wire: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(wire):
        n = struct.unpack(">H", wire[pos:pos + 2])[0]
        out.append(n)
        pos += 2 + n
    assert pos == len(wire)
    return out


def test_max_body_fills_a_record_exactly():
    assert MAX_RECORD_BODY == 65_498
    assert (2 + MAX_RECORD_BODY + 20) % 16 == 0


@pytest.mark.parametrize("size,records", [
    (1, 1), (MAX_RECORD_BODY, 1), (MAX_RECORD_BODY + 1, 2),
    (2 * MAX_RECORD_BODY, 2), (2 * MAX_RECORD_BODY + 7, 3),
])
def test_split_and_reassemble(size, records):
    tx, rx = _pair()
    msg = os.urandom(size)
    wire = tx.encrypt_message(msg)
    lengths = _record_lengths(wire)
    assert len(lengths) == records
    assert all(n <= 65_520 for n in lengths)
    bodies, consumed = rx.decrypt_stream(wire)
    assert consumed == len(wire)
    assert [len(b) for b in bodies[:-1]] == [MAX_RECORD_BODY] * (records - 1)
    assert b"".join(bodies) == msg


def test_counters_stay_in_step_after_a_split_message():
    tx, rx = _pair()
    wire = tx.encrypt_message(os.urandom(150_000)) + tx.encrypt_message(b"\x04after")
    bodies, _ = rx.decrypt_stream(wire)
    assert bodies[-1] == b"\x04after"


def test_encrypt_and_send_writes_all_records_at_once():
    class Sock:
        def __init__(self):
            self.calls = []

        def sendall(self, data):
            self.calls.append(bytes(data))

    tx, rx = _pair()
    sock = Sock()
    msg = os.urandom(MAX_RECORD_BODY * 2 + 1)
    tx.encrypt_and_send(sock, msg)
    assert len(sock.calls) == 1
    bodies, _ = rx.decrypt_stream(sock.calls[0])
    assert b"".join(bodies) == msg


def test_large_clipboard_text_round_trips():
    # Incompressible enough that the deflated archive spans several records.
    text = os.urandom(150_000).hex()
    msg = build_clipboard_send(text)
    assert len(msg) > MAX_RECORD_BODY
    tx, rx = _pair()
    bodies, _ = rx.decrypt_stream(tx.encrypt_message(msg))
    assert len(bodies) > 1
    reasm = ClipboardReassembler()
    full = None
    for b in bodies:
        full = reasm.feed(b)
    assert full == msg
    hdr = parse_clipboard_send_header(full)
    items = parse_clipboard_items(decompress_clipboard_payload(full[16:16 + hdr[3]]))
    assert text_from_items(items) == text
