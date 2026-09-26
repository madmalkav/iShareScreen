"""HEVC 4:4:4 hardware probe (media/hwcaps.py) must not report a software
decode as hardware, and its sample must be decodable by NVDEC."""
from __future__ import annotations

import av
import pytest

from isharescreen.proxy.media import hwcaps


class _FakeCtx:
    def __init__(self, *, is_hwaccel: bool) -> None:
        self.is_hwaccel = is_hwaccel

    def decode(self, packet):
        # A frame always comes out — as it does when PyAV silently builds a
        # software context for an hwaccel its FFmpeg lacks.
        frame = type("F", (), {"format": type("Fmt", (), {"name": "yuv444p"})})
        return [] if packet is None else [frame]


@pytest.fixture
def fake_ctx(monkeypatch):
    def install(*, is_hwaccel: bool) -> None:
        # PyAV's types are immutable; swap the module attribute instead.
        fake = type("CodecContext", (), {
            "create": staticmethod(
                lambda *_a, **_k: _FakeCtx(is_hwaccel=is_hwaccel)),
        })
        monkeypatch.setattr(av, "CodecContext", fake)
    return install


def test_probe_rejects_context_without_hwaccel(fake_ctx):
    fake_ctx(is_hwaccel=False)
    assert hwcaps._probe_one("vaapi") is False


def test_probe_accepts_hwaccel_context_that_decodes(fake_ctx):
    fake_ctx(is_hwaccel=True)
    assert hwcaps._probe_one("vaapi") is True


def test_sample_is_444_and_wide_enough_for_nvdec():
    ctx = av.CodecContext.create("hevc", "r")
    frames = list(ctx.decode(av.Packet(hwcaps._HEVC444_SAMPLE)))
    frames += list(ctx.decode(None))
    assert len(frames) == 1
    assert frames[0].format.name == "yuv444p"
    # NVDEC's minimum HEVC width is 144.
    assert frames[0].width >= 144 and frames[0].height >= 144


def test_hwdevices_available_returns_tuple():
    assert isinstance(hwcaps.hwdevices_available(), tuple)
