"""Linux startup warning when PyAV's FFmpeg lacks VAAPI (media/hwcaps.py)."""
from __future__ import annotations

import logging

import av.codec.hwaccel as hwaccel_mod

from isharescreen.proxy.media import hwcaps


def test_warns_with_fix_when_vaapi_missing(monkeypatch, caplog):
    monkeypatch.setattr(hwaccel_mod, "hwdevices_available",
                        lambda: ["cuda", "qsv"])
    with caplog.at_level(logging.WARNING, logger=hwcaps.log.name):
        hwcaps._warn_if_no_vaapi()
    assert "without VAAPI" in caplog.text
    assert "--no-binary av" in caplog.text


def test_silent_when_vaapi_present(monkeypatch, caplog):
    monkeypatch.setattr(hwaccel_mod, "hwdevices_available",
                        lambda: ["cuda", "vaapi"])
    with caplog.at_level(logging.WARNING, logger=hwcaps.log.name):
        hwcaps._warn_if_no_vaapi()
    assert caplog.text == ""
