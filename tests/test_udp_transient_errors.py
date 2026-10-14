"""A UDP receive that reports an earlier send's ICMP unreachable (Windows:
ConnectionResetError / WSAECONNRESET) must not end the receive loops, and on
Windows the socket is told not to report it at all (SIO_UDP_CONNRESET)."""
import socket
import types

from isharescreen.proxy import session as sess
from isharescreen.proxy.session import Session


class _Sock:
    """recvfrom yields the scripted results; an exception instance is raised."""
    def __init__(self, script):
        self.script = list(script)

    def settimeout(self, t):
        pass

    def recvfrom(self, n):
        item = self.script.pop(0) if self.script else socket.timeout()
        if isinstance(item, BaseException):
            raise item
        return item, ("192.0.2.1", 5901)


def _rtp(marker=False):
    return bytes([0x80, 0xE4 if marker else 0x64]) + b"\x00" * 30


def test_burst_drain_survives_connection_reset():
    pkts = [ConnectionResetError(10054, "reset"), _rtp(), ConnectionRefusedError(), _rtp(), _rtp()]
    got = []
    Session._drain_socket_into(_Sock(pkts), got, max_seconds=0.5)
    assert len(got) == 3


def test_burst_drain_still_stops_on_other_errors():
    got = []
    Session._drain_socket_into(_Sock([_rtp(), OSError(9, "bad fd"), _rtp()]), got, max_seconds=0.5)
    assert len(got) == 1


def test_disable_connreset_calls_ioctl_when_available(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "SIO_UDP_CONNRESET", 0x9800000C, raising=False)
    fake = types.SimpleNamespace(ioctl=lambda code, val: calls.append((code, val)))
    sess._disable_udp_connreset(fake)
    assert calls == [(0x9800000C, False)]


def test_disable_connreset_noop_without_ioctl(monkeypatch):
    monkeypatch.delattr(socket, "SIO_UDP_CONNRESET", raising=False)
    sess._disable_udp_connreset(types.SimpleNamespace())       # no ioctl, no error


def test_bound_socket_works_on_this_platform():
    s = Session._bind_udp("127.0.0.1", 0)
    try:
        assert s.getsockname()[1] > 0
    finally:
        s.close()
