"""Curtain mode: when the host switches the session to another display
without being asked (login/unlock at the Mac → its physical 5120x2160
display), the session asks for its virtual display again — rate-limited."""
from __future__ import annotations

import time
import types

import pytest

from isharescreen.proxy.protocol.rfb import DisplayRect
from isharescreen.proxy.session import Session


def _rect(did, w=3840, h=2160):
    return [DisplayRect(did, 0, 0, w, h)]


@pytest.fixture
def s(monkeypatch):
    monkeypatch.delenv("ISS_RETURN_TO_VIRTUAL", raising=False)
    sess = Session.__new__(Session)
    sess._config = types.SimpleNamespace(
        curtain=True, alt_session=False,
        advertise=types.SimpleNamespace(width=1920, height=1080, hidpi_scale=2.0))
    sess._current_display_id = None
    sess._last_display_request_t = time.monotonic() - 100   # long ago
    sess._last_display_request = None
    sess._return_to_virtual_pending = False
    sess._last_return_t = 0.0
    sess._return_tries = 0
    sent = []
    sess.send_dynamic_resolution = lambda *a: sent.append(a)
    sess.sent = sent
    return sess


def test_first_layout_is_accepted(s):
    s._note_display_layout(_rect(91))
    assert s._current_display_id == 91 and not s._return_to_virtual_pending


def test_unrequested_switch_triggers_return(s):
    s._note_display_layout(_rect(91))
    s._note_display_layout(_rect(1, 5120, 2160))       # host went physical
    assert s._return_to_virtual_pending
    s._maybe_return_to_virtual_display()
    assert s.sent == [(1920, 1080, 2.0)]                # the configured size


def test_switch_after_our_own_request_is_accepted(s):
    s._note_display_layout(_rect(91))
    s._last_display_request_t = time.monotonic()        # e.g. a window resize
    s._note_display_layout(_rect(92, 2560, 2104))
    assert not s._return_to_virtual_pending and s._current_display_id == 92


def test_same_display_resize_is_ignored(s):
    s._note_display_layout(_rect(91))
    s._note_display_layout(_rect(91, 3840, 1576))
    assert not s._return_to_virtual_pending


def test_uses_last_requested_size(s):
    s._last_display_request = (2560, 1052, 2.0)
    s._note_display_layout(_rect(91))
    s._note_display_layout(_rect(1))
    s._maybe_return_to_virtual_display()
    assert s.sent == [(2560, 1052, 2.0)]


def test_rate_limited_and_capped(s):
    s._note_display_layout(_rect(91))
    for i in range(6):
        s._return_to_virtual_pending = True
        s._last_return_t = 0.0 if i else 0.0            # cooldown elapsed
        s._maybe_return_to_virtual_display()
    assert len(s.sent) == 3                             # max tries
    s.sent.clear(); s._return_tries = 0
    s._return_to_virtual_pending = True
    s._last_return_t = time.monotonic()                 # just sent
    s._maybe_return_to_virtual_display()
    assert s.sent == [] and s._return_to_virtual_pending   # waits out cooldown


@pytest.mark.parametrize("cfg", [
    dict(curtain=False, alt_session=False),             # --no-curtain: physical is intended
    dict(curtain=True, alt_session=True),               # alt-session: leave alone
])
def test_not_active_outside_curtain_mode(s, cfg):
    s._config = types.SimpleNamespace(advertise=None, **cfg)
    s._note_display_layout(_rect(91))
    s._note_display_layout(_rect(1))
    assert not s._return_to_virtual_pending


def test_opt_out(s, monkeypatch):
    monkeypatch.setenv("ISS_RETURN_TO_VIRTUAL", "0")
    s._note_display_layout(_rect(91))
    s._note_display_layout(_rect(1))
    assert not s._return_to_virtual_pending


def test_multi_display_host_untouched(s):
    s._note_display_layout(_rect(91) + _rect(92))
    s._note_display_layout(_rect(1) + _rect(2))
    assert not s._return_to_virtual_pending
