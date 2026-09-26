"""`--codec auto` may fall back to H.264 4:2:0 when no HEVC 4:4:4 hardware
decoder is found — but never silently: the user is told why and how to get
full 4:4:4 quality."""
from __future__ import annotations

import logging
import sys
import threading
import types

import pytest

from isharescreen import cli
from isharescreen.gui import connect as C
from isharescreen.proxy.media import registry as R


@pytest.fixture
def desktop_stub(monkeypatch):
    """Run cli._run_frontend's desktop branch without opening a window."""
    fake = types.ModuleType("isharescreen.frontend.desktop.app")
    fake.run = lambda *a, **k: 0
    monkeypatch.setitem(sys.modules, "isharescreen.frontend.desktop.app", fake)
    monkeypatch.delenv("ISS_VIDEO_CODEC", raising=False)


def _args(codec="auto", frontend="desktop"):
    return types.SimpleNamespace(codec=codec, frontend=frontend, display=None,
                                 auto_quit_secs=0)


def test_desktop_auto_fallback_warns(monkeypatch, caplog, desktop_stub):
    monkeypatch.setattr(R, "can_decode", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="iss.cli"):
        cli._run_frontend(None, _args())
    assert R.AVC_FALLBACK_NOTICE in caplog.text
    assert "--codec hevc" in R.AVC_FALLBACK_NOTICE


def test_desktop_auto_with_hevc_hw_is_quiet(monkeypatch, caplog, desktop_stub):
    monkeypatch.setattr(R, "can_decode", lambda *a, **k: True)
    with caplog.at_level(logging.WARNING, logger="iss.cli"):
        cli._run_frontend(None, _args())
    assert R.AVC_FALLBACK_NOTICE not in caplog.text


def test_explicit_codec_is_quiet(monkeypatch, caplog, desktop_stub):
    monkeypatch.setattr(R, "can_decode", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="iss.cli"):
        cli._run_frontend(None, _args(codec="avc"))
    assert R.AVC_FALLBACK_NOTICE not in caplog.text


def _probe_done(monkeypatch):
    done = threading.Event(); done.set()
    monkeypatch.setattr(C, "_PROBE_DONE", done)


def test_gui_auto_label_names_the_fallback(monkeypatch):
    _probe_done(monkeypatch)
    monkeypatch.setattr(R, "can_decode", lambda *a, **k: False)
    html = C._decoder_options_html()
    assert "H.264 4:2:0" in html and "HEVC — Software (CPU)" in html


def test_gui_auto_label_plain_when_hevc_hw(monkeypatch):
    _probe_done(monkeypatch)
    monkeypatch.setattr(R, "can_decode", lambda *a, **k: True)
    assert "Auto (best available)" in C._decoder_options_html()
