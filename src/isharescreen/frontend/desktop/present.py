"""When the desktop frontend presents: once per frame, or once per tile.

Apple splits each frame into horizontal tiles (4 at HiDPI), each its own HEVC
stream, and stamps every tile of one frame with the same RTP timestamp. The
tiles decode independently and reach the render loop a few milliseconds
apart. Presenting after each one ("tile" policy) can settle into ~119
presents/s with one tile each: FIFO then blocks on vblank between the tiles of
one frame, skips 4–6 % of pictures and adds 13–23 ms to the p95 latency
(measured on the replay harness, 2026-10-17).

The "frame" policy (the default) waits after a fresh upload until every tile
handed a picture with the newest timestamp has uploaded it, or until a short
deadline after the wait began, whichever comes first, then presents once.
Tiles whose decoder can't tell their timestamp (`TileFrame.rtp_ts is None`)
present right away, as with the "tile" policy.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Collection, Optional

log = logging.getLogger(__name__)

PRESENT_POLICIES = ("frame", "tile")
# How long a frame waits for its missing tiles. A tile that is late by more
# (lost, or held back by a recovering decoder) is shown with the next frame.
FRAME_DEADLINE_S = 0.012
# A timestamp this far behind the newest one (2 s at the 90 kHz video clock)
# belongs to a new stream, not to a late tile.
_TS_JUMP = 2 * 90_000
# Upper bound on one wait for a fresh tile while coalescing, so the deadline
# is checked often.
_POLL_S = 0.002


def _ts_newer(a: int, b: int) -> bool:
    """RTP timestamp `a` is after `b` (32-bit serial-number arithmetic)."""
    return a != b and ((a - b) & 0xFFFFFFFF) < 0x80000000


def make_coalescer(policy: str = "frame") -> Optional["FrameCoalescer"]:
    """The coalescer for `policy` (None for "tile"). `ISS_PRESENT_COALESCE_MS`
    overrides it for experiments: a deadline in ms, 0 = the "tile" policy."""
    deadline_s = FRAME_DEADLINE_S
    env = os.environ.get("ISS_PRESENT_COALESCE_MS", "").strip()
    if env:
        try:
            ms = float(env)
        except ValueError:
            log.warning("ISS_PRESENT_COALESCE_MS=%r is not a number; ignored", env)
        else:
            policy = "frame" if ms > 0 else "tile"
            deadline_s = ms / 1000.0
    if policy not in PRESENT_POLICIES:
        log.warning("present policy %r unknown; using 'frame'", policy)
        policy = "frame"
    log.info("present policy: %s%s", policy,
             f" (deadline {deadline_s * 1000:g} ms)" if policy == "frame" else "")
    return FrameCoalescer(deadline_s) if policy == "frame" else None


class FrameCoalescer:
    """Tracks the RTP timestamp each tile last uploaded, and waits until the
    newest frame is complete before the render loop presents it."""

    def __init__(self, deadline_s: float = FRAME_DEADLINE_S, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.deadline_s = deadline_s
        self._clock = clock
        self._uploaded: dict[int, int] = {}
        self._newest: Optional[int] = None

    def note_upload(self, tile_idx: int, rtp_ts: Optional[int]) -> None:
        """Record that `tile_idx` just uploaded a picture stamped `rtp_ts`."""
        if rtp_ts is None:
            return
        newest = self._newest
        if newest is not None and ((newest - rtp_ts) & 0xFFFFFFFF) > _TS_JUMP:
            # Far behind the newest frame: a new stream (SSRC adoption, host
            # restart), not a late tile. Start over from it.
            self._uploaded.clear()
            newest = None
        self._uploaded[tile_idx] = rtp_ts
        if newest is None or _ts_newer(rtp_ts, newest):
            newest = rtp_ts
        self._newest = newest

    def frame_complete(
        self,
        fed_tiles: Callable[[int], Collection[int]],
        frame_sent: Optional[Callable[[int], bool]] = None,
    ) -> bool:
        """Every tile fed the newest frame has uploaded it, and (with
        `frame_sent`) the host is done sending that frame, so no tile is
        still in flight. True when the newest timestamp is unknown, so such
        tiles present right away."""
        ts = self._newest
        if ts is None:
            return True
        if not all(self._uploaded.get(t) == ts for t in fed_tiles(ts)):
            return False
        return frame_sent is None or frame_sent(ts)

    def wait_for_frame(
        self,
        fed_tiles: Callable[[int], Collection[int]],
        wait_fresh: Callable[[float], object],
        drain: Callable[[], object],
        frame_sent: Optional[Callable[[int], bool]] = None,
    ) -> None:
        """After a fresh upload: keep draining new tiles until the newest
        frame is complete or the deadline passes. `drain` uploads fresh tiles
        and calls `note_upload` for each; `wait_fresh(timeout)` blocks until a
        decoder publishes a tile, or the timeout elapses."""
        t_end = self._clock() + self.deadline_s
        while not self.frame_complete(fed_tiles, frame_sent):
            remaining = t_end - self._clock()
            if remaining <= 0:
                return
            wait_fresh(min(remaining, _POLL_S))
            drain()

    def reset(self) -> None:
        """Forget all timestamps (decoder restart, canvas change)."""
        self._uploaded.clear()
        self._newest = None


__all__ = ["FRAME_DEADLINE_S", "PRESENT_POLICIES", "FrameCoalescer", "make_coalescer"]
