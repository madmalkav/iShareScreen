"""RTP/RTCP demultiplexing on the muxed ports (RFC 5761 §4)."""
from isharescreen.proxy.session import _is_rtcp


def test_rtcp_packet_types_are_rtcp():
    for pt in (200, 201, 202, 203, 204, 205, 206, 207):
        assert _is_rtcp(bytes([0x80, pt, 0, 6]))
    assert _is_rtcp(bytes([0x81, 200, 0, 12]))       # SR with one report block


def test_media_is_not_rtcp():
    for pt in (100, 101):                            # video / audio payload types
        for marker in (0, 0x80):
            assert not _is_rtcp(bytes([0x80, marker | pt, 0, 1]))


def test_garbage_is_not_rtcp():
    assert not _is_rtcp(b"")
    assert not _is_rtcp(bytes([0x00, 200]))          # not RTP version 2
