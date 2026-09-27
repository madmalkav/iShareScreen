"""macOS: --codec auto offers HEVC 4:4:4 only when VideoToolbox has a HARDWARE
4:4:4 decoder (Apple silicon). Intel Macs without one get H.264; if the check
can't run, the previous assumption (hardware present) is kept."""
from __future__ import annotations

import pytest

from isharescreen.proxy.media import hwcaps, vtdecode


@pytest.fixture(autouse=True)
def _mac(monkeypatch):
    monkeypatch.setattr(hwcaps.sys, "platform", "darwin")
    monkeypatch.setattr(hwcaps, "_method_cache", {})
    monkeypatch.delenv("ISS_HEVC444", raising=False)
    monkeypatch.delenv("ISS_HWACCEL", raising=False)


@pytest.mark.parametrize("hw, expected", [
    (True, "libav"),     # Apple silicon: native VideoToolbox HEVC 4:4:4
    (False, None),       # Intel Mac without 4:4:4 hardware → H.264
    (None, "libav"),     # check failed → keep the old behaviour
])
def test_method_follows_videotoolbox_hardware(monkeypatch, hw, expected):
    monkeypatch.setattr(vtdecode, "hevc444_hw_supported", lambda: hw)
    assert hwcaps.hevc444_decode_method() == expected


def test_override_still_forces_hevc(monkeypatch):
    monkeypatch.setattr(vtdecode, "hevc444_hw_supported", lambda: False)
    monkeypatch.setenv("ISS_HEVC444", "1")
    assert hwcaps.hevc444_decode_method() == "libav"


def test_check_is_none_without_videotoolbox(monkeypatch):
    monkeypatch.setattr(vtdecode, "_VT_OK", False)
    monkeypatch.setattr(vtdecode, "_hw444_cache", {})
    assert vtdecode.hevc444_hw_supported() is None


def test_auto_codec_on_intel_mac_is_avc(monkeypatch):
    """The registry must not count VideoToolbox as HEVC 4:4:4 hardware when
    the hardware check says no (it would decode in software)."""
    from isharescreen.proxy.media import registry
    monkeypatch.setattr(registry.sys, "platform", "darwin")
    monkeypatch.setattr(vtdecode, "hevc444_hw_supported", lambda: False)
    monkeypatch.setattr(registry, "_vt_available", lambda: True)
    assert registry.resolve_codec("auto") == "avc"
    # still selectable when HEVC is forced
    assert registry.select("hevc").name == "vt-hevc444"


def test_auto_codec_on_apple_silicon_is_hevc(monkeypatch):
    from isharescreen.proxy.media import registry
    monkeypatch.setattr(registry.sys, "platform", "darwin")
    monkeypatch.setattr(vtdecode, "hevc444_hw_supported", lambda: True)
    monkeypatch.setattr(registry, "_vt_available", lambda: True)
    assert registry.resolve_codec("auto") == "hevc"
