"""RCTL rate-control reports — the feedback the host's video rate controller
works from.

The Mac's encoder follows a rate controller that only moves on the viewer's
reports: without them it stays at its 20 Mbit/s floor for the whole session
(the ceiling is the offer's bitrate entries, capped at 60 Mbit/s), which is
what made fast scrolling blur at 4K. Apple's viewer sends an RTCP APP packet
named ``RCTL`` every 50 ms; with a low one-way delay in it the controller
ramps to near the cap within seconds, and a growing delay walks it back down.

Layout and behaviour follow the protocol notes of the remotex project
(github.com/andrewtheguy/remotex, docs/apple-vnc-889.md, "Rate control"),
confirmed here against the host's own rate-controller log. The APP packet
must be sent alone in its datagram (the host rejects it inside a compound
packet), as SRTCP, on the video port. Payload, 20 bytes, big-endian:

    +0  u8   0x85
    +1  u8   0 (a millisecond figure / 20 of unknown meaning; 0 is accepted)
    +2  u16  4
    +4  u16  echo: RTP timestamp of the last received video packet >> 8
    +6  u32  0
    +10 u16  milliseconds since that packet arrived
    +12 u16  the viewer's clock, 1/1024 s
    +14 u16  one-way relative delay, seconds * 8192, at most 0xffff
    +16 u16  bursty loss (top 4 bits) | video packets received mod 4096
    +18 u16  bandwidth estimate, kbit/s

The host takes the round-trip time from the echo and the hold time, and moves
its target on the delay; loss and the bandwidth estimate don't move it.
"""
from __future__ import annotations

import struct
from typing import Optional

RCTL_NAME = b"RCTL"
RCTL_INTERVAL_S = 0.050
VIDEO_CLOCK_HZ = 24_000          # the video leg's RTP timestamp rate
_RESTART_S = 30.0                # a lag this far from either average restarts


def build_rctl(sender_ssrc: int, *, echo_ts: int, hold_ms: int, clock_s: float,
               delay_s: float, received: int, bwe_kbps: int,
               bursty_loss: int = 0) -> bytes:
    """One standalone RTCP APP ``RCTL`` packet (32 bytes before SRTCP)."""
    payload = struct.pack(
        ">BBHHIHHHHH",
        0x85, 0, 4,
        (echo_ts >> 8) & 0xFFFF,
        0,
        max(0, min(int(hold_ms), 0xFFFF)),
        int(clock_s * 1024) & 0xFFFF,
        max(0, min(int(delay_s * 8192), 0xFFFF)),
        ((bursty_loss & 0xF) << 12) | (received & 0x0FFF),
        max(0, min(int(bwe_kbps), 0xFFFF)),
    )
    return (struct.pack(">BBHI", 0x80, 204, (12 + len(payload)) // 4 - 1,
                        sender_ssrc & 0xFFFFFFFF)
            + RCTL_NAME + payload)


class RctlState:
    """What an RCTL report needs, fed from the video receive path.

    Delay: one sample per picture, from its first packet. The lag is the
    packet's arrival less its RTP timestamp at 24 kHz, both counted from the
    stream's first picture (no shared clock with the host). A short average
    (0.9/0.1) follows the lag and a long one (0.9999/0.0001) settles on its
    floor; the reported delay is short - long, and when that goes negative the
    long average takes the short one's value and the delay is 0. It measures a
    queue building up before our socket reads the packet.

    Not thread-safe by itself; the session serialises access with a lock.
    """

    def __init__(self) -> None:
        self.reset(None)

    def reset(self, ssrc: Optional[int]) -> None:
        self.ssrc = ssrc
        self.received = 0
        self.last_ts: Optional[int] = None
        self.last_arrival = 0.0
        self._base: Optional[tuple[float, int]] = None   # (arrival, rtp ts)
        self._last_picture_ts: Optional[int] = None
        self._short: Optional[float] = None
        self._long: Optional[float] = None
        self.delay_s = 0.0

    def on_packet(self, ssrc: int, tile_idx: int, ts: int, arrival: float) -> None:
        """A video RTP packet of the displayed stream (tile `tile_idx` of the
        current SSRC group) was read at `arrival` (monotonic seconds). Every
        tile's packets are counted and echoed; the delay is sampled from tile
        0's pictures, and a new tile-0 SSRC (stream switch / resize) starts
        the count and the delay over."""
        if tile_idx == 0 and ssrc != self.ssrc:
            self.reset(ssrc)
        self.received += 1
        self.last_ts = ts
        self.last_arrival = arrival
        if tile_idx != 0 or ts == self._last_picture_ts:
            return                                   # not a picture's first packet
        self._last_picture_ts = ts
        if self._base is None:
            self._base = (arrival, ts)
        a0, t0 = self._base
        lag = (arrival - a0) - ((ts - t0) & 0xFFFFFFFF) / VIDEO_CLOCK_HZ
        if (self._short is None
                or abs(lag - self._short) > _RESTART_S
                or abs(lag - self._long) > _RESTART_S):
            self._short = self._long = lag
        else:
            self._short = 0.9 * self._short + 0.1 * lag
            self._long = 0.9999 * self._long + 0.0001 * lag
        d = self._short - self._long
        if d < 0:
            self._long = self._short
            d = 0.0
        self.delay_s = d


__all__ = ["RCTL_INTERVAL_S", "RctlState", "build_rctl"]
