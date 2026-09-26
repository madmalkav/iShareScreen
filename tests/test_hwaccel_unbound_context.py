"""A context PyAV built without the requested hwaccel must not be installed
as a hardware decoder (PyAV silently returns a software context when its
FFmpeg lacks the hwaccel, e.g. VAAPI in the PyPI wheel)."""
from __future__ import annotations

import av
import pytest

from isharescreen.proxy.media.avc import AvcDecoder
from isharescreen.proxy.media.hevc import HevcDecoder


class _Ctx:
    def __init__(self, is_hwaccel: bool) -> None:
        self.is_hwaccel = is_hwaccel
        self.opened = False

    def open(self) -> None:
        self.opened = True


@pytest.fixture
def ctx_factory(monkeypatch):
    made: list[_Ctx] = []

    def install(is_hwaccel: bool) -> list[_Ctx]:
        def create(*_a, **_k):
            made.append(_Ctx(is_hwaccel))
            return made[-1]
        # PyAV's types are immutable; swap the module attribute instead.
        monkeypatch.setattr(
            av, "CodecContext",
            type("CodecContext", (), {"create": staticmethod(create)}),
        )
        return made
    return install


def test_hevc_rejects_context_without_hwaccel(ctx_factory):
    ctx_factory(is_hwaccel=False)
    assert HevcDecoder(1)._try_hwaccel("vaapi", b"") is None


def test_hevc_keeps_context_with_hwaccel(ctx_factory):
    made = ctx_factory(is_hwaccel=True)
    ctx = HevcDecoder(1)._try_hwaccel("vaapi", b"")
    assert ctx is made[0] and ctx.opened


def test_avc_rejects_context_without_hwaccel(ctx_factory):
    ctx_factory(is_hwaccel=False)
    assert AvcDecoder(1)._try_hwaccel_locked("vaapi", b"") is None


def test_avc_keeps_context_with_hwaccel(ctx_factory):
    made = ctx_factory(is_hwaccel=True)
    ctx = AvcDecoder(1)._try_hwaccel_locked("vaapi", b"")
    assert ctx is made[0] and ctx.opened
