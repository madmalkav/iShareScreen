"""Decoder lifecycle rules for fresh RTP SSRC generations."""
from __future__ import annotations

import collections
import time
import types

from isharescreen.proxy.session import (
    Session,
    _DYNAMIC_SSRC_PACKET_THRESHOLD,
    _SSRC_ADOPT_STALL_S,
)


class _Decoder:
    def __init__(self) -> None:
        self.restart_calls = 0

    def restart(self) -> None:
        self.restart_calls += 1


def _session(codec: str) -> Session:
    now = time.monotonic()
    session = Session.__new__(Session)
    session._video_codec = codec
    session._ssrc_to_tile = {0x1000: 0}
    session._ssrc_blacklist = set()
    session._ssrc_last_seen = {0x1000: now - 5.0, 0x2000: now}   # 0x2000 is sending
    session._video_decryptor = types.SimpleNamespace(
        ssrc_counts=collections.Counter({
            0x1000: 100,
            0x2000: _DYNAMIC_SSRC_PACKET_THRESHOLD,
        }),
    )
    session._last_publish_t = now - _SSRC_ADOPT_STALL_S - 0.1
    session._last_ssrc_adopt_ts = 0.0
    session._last_decoder_restart_t = now - 1.0
    session._needs_param_harvest = False
    session._dpb_error_window = collections.deque([now])
    session._decoder = _Decoder()
    session._observed_tile_count = 1
    session.fir_calls = 0
    session.request_fir = lambda tile=None: setattr(
        session, "fir_calls", session.fir_calls + 1,
    )
    return session


def test_avc_fresh_ssrc_always_resets_dpb_inside_restart_guard(monkeypatch):
    monkeypatch.setenv("ISS_TILES_PER_FRAME", "1")
    session = _session("avc")

    session._note_unknown_ssrc(0x2000)

    assert session._ssrc_to_tile == {0x2000: 0}
    assert session._decoder.restart_calls == 1
    assert session.fir_calls == 1


def test_hevc_fresh_ssrc_keeps_rapid_restart_guard(monkeypatch):
    monkeypatch.setenv("ISS_TILES_PER_FRAME", "1")
    session = _session("hevc")

    session._note_unknown_ssrc(0x2000)

    assert session._ssrc_to_tile == {0x2000: 0}
    assert session._decoder.restart_calls == 0
    assert session.fir_calls == 1


def _tiled_session(counts, last_seen):
    now = time.monotonic()
    session = _session("hevc")
    session._ssrc_to_tile = {0x10: 0, 0x11: 1, 0x12: 2, 0x13: 3}
    session._video_decryptor = types.SimpleNamespace(
        ssrc_counts=collections.Counter(counts))
    session._ssrc_last_seen = {s: now - age for s, age in last_seen.items()}
    return session


def test_adoption_skips_dead_groups_from_negotiation_requeries(monkeypatch):
    """Each connect-time 0x1c re-query briefly starts a stream with its own
    SSRCs; only the group still sending may be adopted, even if a dead one
    has lower SSRC numbers."""
    monkeypatch.setenv("ISS_TILES_PER_FRAME", "4")
    dead = {0x100 + i: 50 for i in range(4)}           # lower numbers, stopped
    live = {0x900 + i: 50 for i in range(4)}
    session = _tiled_session({**dead, **live},
                             {**{s: 3.0 for s in dead}, **{s: 0.01 for s in live}})
    session._note_unknown_ssrc(0x900)
    assert session._ssrc_to_tile == {0x900: 0, 0x901: 1, 0x902: 2, 0x903: 3}


def test_no_adoption_while_no_group_is_sending(monkeypatch):
    monkeypatch.setenv("ISS_TILES_PER_FRAME", "4")
    dead = {0x100 + i: 50 for i in range(4)}
    session = _tiled_session(dead, {s: 3.0 for s in dead})
    session._note_unknown_ssrc(0x100)
    assert session._ssrc_to_tile == {0x10: 0, 0x11: 1, 0x12: 2, 0x13: 3}


def test_dead_current_group_is_replaced_without_waiting(monkeypatch):
    """If the adopted group stopped sending and a live group exists, switch
    at once even though frames were published moments ago."""
    monkeypatch.setenv("ISS_TILES_PER_FRAME", "4")
    live = {0x900 + i: 50 for i in range(4)}
    session = _tiled_session(live, {**{s: 0.01 for s in live},
                                    **{s: 1.0 for s in (0x10, 0x11, 0x12, 0x13)}})
    session._last_publish_t = time.monotonic() - 0.2        # recently published
    session._note_unknown_ssrc(0x900)
    assert session._ssrc_to_tile == {0x900: 0, 0x901: 1, 0x902: 2, 0x903: 3}


def test_live_current_group_is_kept(monkeypatch):
    monkeypatch.setenv("ISS_TILES_PER_FRAME", "4")
    other = {0x900 + i: 50 for i in range(4)}
    session = _tiled_session(other, {**{s: 0.01 for s in other},
                                     **{s: 0.01 for s in (0x10, 0x11, 0x12, 0x13)}})
    session._last_publish_t = time.monotonic() - 0.2
    session._note_unknown_ssrc(0x900)
    assert session._ssrc_to_tile == {0x10: 0, 0x11: 1, 0x12: 2, 0x13: 3}
