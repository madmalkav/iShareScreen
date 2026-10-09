"""The Mac's media-stream messages: encoding 1010 (0x3f2) rectangles.

After a `SetEncodings` naming 1010, the Mac reports on the media stream with
framebuffer-update rectangles of encoding 1010, each a u16 size and a body
that starts with a common header (u16 type, u16 version, u32 flags):

- message 1: the UDP ports. Sent once per `SetEncodings` naming 1010 and
  once per display change, never in reply to an offer. Body: the header,
  then a u16 port + u32 flags for audio (+8/+10), video 1 (+14/+16) and
  video 2 (+20/+22); bit 0 of a leg's flags enables it.
- message 2: the answer to a `0x1c` offer (the AVConference answer blobs).
- message 3: an error (u32 type, u32 sub-code after the header).

Layouts as described by the remotex protocol notes (rfc §10.3).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator, Optional

ENC_MEDIA_STREAM = 1010
_ENC_CURSOR = 1104
_ENC_LAYOUT = 0x451


@dataclass(frozen=True)
class MediaStreamMsg:
    kind: int                      # 1 ports, 2 answer, 3 error
    body: bytes
    ports: Optional[tuple[int, int, int]] = None      # message 1: audio, video1, video2
    error: Optional[tuple[int, int]] = None           # message 3: type, sub-code

    def describe(self) -> str:
        if self.kind == 1 and self.ports:
            return "message 1 (ports audio=%d video=%d video2=%d)" % self.ports
        if self.kind == 3 and self.error:
            return "message 3 (error type=%d sub-code=%d)" % self.error
        return "message %d (%d bytes)" % (self.kind, len(self.body))


def parse_body(body: bytes) -> Optional[MediaStreamMsg]:
    """One 1010 rectangle's body (after its u16 size)."""
    if len(body) < 8:
        return None
    kind = struct.unpack(">H", body[0:2])[0]
    if kind == 1 and len(body) >= 24:
        ports = (struct.unpack(">H", body[8:10])[0],
                 struct.unpack(">H", body[14:16])[0],
                 struct.unpack(">H", body[20:22])[0])
        return MediaStreamMsg(1, bytes(body), ports=ports)
    if kind == 3 and len(body) >= 16:
        return MediaStreamMsg(3, bytes(body), error=struct.unpack(">II", body[8:16]))
    return MediaStreamMsg(kind, bytes(body))


def _iter_sized_rects(msg: bytes) -> Iterator[tuple[int, bytes]]:
    """(encoding, body) of each u16-size rectangle in one decrypted
    FramebufferUpdate, stepping over cursor rects; stops at anything it
    can't step over."""
    if len(msg) < 4 or msg[0] != 0x00:
        return
    n_rects = struct.unpack(">H", msg[2:4])[0]
    p = 4
    for _ in range(n_rects):
        if p + 12 > len(msg):
            return
        enc = struct.unpack(">i", msg[p + 8:p + 12])[0]
        p += 12
        if enc == _ENC_CURSOR:           # u32 cache id, u32 size, zlib bytes
            if p + 8 > len(msg):
                return
            p += 8 + struct.unpack(">I", msg[p + 4:p + 8])[0]
            continue
        if enc not in _SIZED_ENCODINGS or p + 2 > len(msg):
            return
        sz = struct.unpack(">H", msg[p:p + 2])[0]
        body = msg[p + 2:p + 2 + sz]
        p += 2 + sz
        if len(body) != sz:
            return
        yield enc, body


def iter_fbu_media_msgs(msg: bytes) -> Iterator[MediaStreamMsg]:
    """Media-stream messages in one decrypted FramebufferUpdate."""
    for enc, body in _iter_sized_rects(msg):
        if enc == ENC_MEDIA_STREAM:
            m = parse_body(body)
            if m is not None:
                yield m


def fbu_layout_backing(msg: bytes) -> Optional[tuple[int, int]]:
    """Backing (pixel) size from an AppleDisplayLayout (0x451) rectangle in
    one decrypted FramebufferUpdate, or None. Payload: u16 ?, u16 scaled w,
    u16 scaled h, u16 backing w, u16 backing h, ..."""
    for enc, body in _iter_sized_rects(msg):
        if enc == _ENC_LAYOUT and len(body) >= 10:
            bw, bh = struct.unpack(">HH", body[6:10])
            if bw and bh:
                return bw, bh
    return None


# Pseudo-encodings carried as a u16 size + body (session.py steps over the
# same set); 0x451 (layout) is too.
_SIZED_ENCODINGS = frozenset((1010, 1011, 1107, 1109, 1110, 0x3f3, 0x3ea,
                              0x451, 0x453, 0x455, 0x456))

__all__ = ["ENC_MEDIA_STREAM", "MediaStreamMsg", "fbu_layout_backing",
           "iter_fbu_media_msgs", "parse_body"]
