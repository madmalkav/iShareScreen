"""HEVC hardware frames kept on the GPU (media/hevc.py `_HW_FRAMES_ON_GPU`).

Only enabled where verified (CUDA; VAAPI on NVIDIA's driver); every other
setup must keep today's inline download."""
from __future__ import annotations

import pytest

from isharescreen.proxy.media import hevc
from isharescreen.proxy.media.hevc import HevcDecoder


def _maps(tmp_path, *libs):
    p = tmp_path / "maps"
    p.write_text("".join(
        f"7f00-7f01 r-xp 00000000 00:00 0    /usr/lib/{lib}\n" for lib in libs))
    return str(p)


@pytest.mark.parametrize("lib, name", [
    ("dri/nvidia_drv_video.so", "nvidia"),
    ("dri/iHD_drv_video.so", "iHD"),
    ("dri/radeonsi_drv_video.so", "radeonsi"),
])
def test_loaded_va_driver(tmp_path, lib, name):
    assert hevc._loaded_va_driver(_maps(tmp_path, "libc.so.6", lib)) == name


def test_no_va_driver_loaded(tmp_path):
    assert hevc._loaded_va_driver(_maps(tmp_path, "libc.so.6")) is None
    assert hevc._loaded_va_driver(str(tmp_path / "missing")) is None


@pytest.mark.parametrize("env, hw_type, driver, expected", [
    ("", "cuda", None, True),
    ("", "vaapi", "nvidia", True),
    ("", "vaapi", "iHD", False),          # Intel: not verified -> unchanged
    ("", "vaapi", "radeonsi", False),     # AMD: not verified -> unchanged
    ("", "vaapi", None, False),
    ("", "d3d11va", None, False),         # Windows: unchanged
    ("", "videotoolbox", None, False),    # macOS: unchanged
    ("1", "vaapi", "iHD", True),          # explicit opt-in
    ("0", "cuda", None, False),           # explicit opt-out
    ("0", "vaapi", "nvidia", False),
])
def test_keep_hw_frames_on_gpu(monkeypatch, env, hw_type, driver, expected):
    monkeypatch.setattr(hevc, "_HW_FRAMES_ON_GPU", env)
    monkeypatch.setattr(hevc, "_loaded_va_driver", lambda *a: driver)
    assert hevc._keep_hw_frames_on_gpu(hw_type) is expected


class _Ctx:
    def __init__(self, owned: bool) -> None:
        self.is_hwaccel = True
        self.owned = owned

    def open(self) -> None:
        pass


def _patch_contexts(monkeypatch, *, gpu_ok=True):
    import av
    made = []
    monkeypatch.setattr(av, "CodecContext", type("CC", (), {"create": staticmethod(
        lambda *a, **k: made.append(_Ctx(False)) or made[-1])}))
    monkeypatch.setattr(HevcDecoder, "_gpu_frames_context", staticmethod(
        lambda hw: (made.append(_Ctx(True)) or made[-1]) if gpu_ok else None))
    return made


def test_verified_setup_builds_one_gpu_frames_context(monkeypatch):
    made = _patch_contexts(monkeypatch)
    monkeypatch.setattr(hevc, "_keep_hw_frames_on_gpu", lambda hw: True)
    ctx = HevcDecoder(1)._try_hwaccel("cuda", b"")
    assert ctx.owned and len(made) == 1


def test_unverified_setup_keeps_inline_download(monkeypatch):
    made = _patch_contexts(monkeypatch)
    monkeypatch.setattr(hevc, "_keep_hw_frames_on_gpu", lambda hw: False)
    ctx = HevcDecoder(1)._try_hwaccel("vaapi", b"")
    assert not ctx.owned and len(made) == 1


def test_driver_loaded_by_first_context_switches_to_gpu_frames(monkeypatch):
    made = _patch_contexts(monkeypatch)
    answers = iter([False, True])              # unknown first, nvidia after
    monkeypatch.setattr(hevc, "_keep_hw_frames_on_gpu", lambda hw: next(answers))
    ctx = HevcDecoder(1)._try_hwaccel("vaapi", b"")
    assert ctx.owned and [c.owned for c in made] == [False, True]


def test_pyav_without_gpu_frames_falls_back_to_inline(monkeypatch):
    made = _patch_contexts(monkeypatch, gpu_ok=False)
    monkeypatch.setattr(hevc, "_keep_hw_frames_on_gpu", lambda hw: True)
    ctx = HevcDecoder(1)._try_hwaccel("cuda", b"")
    assert ctx is not None and not ctx.owned


def test_real_gpu_frames_decode_to_full_444_tiles():
    """End to end on machines where the verified path is available."""
    from isharescreen.proxy.media import hwcaps
    for hw_type in ("vaapi", "cuda"):
        if hwcaps._probe_one(hw_type) and hevc._keep_hw_frames_on_gpu(hw_type):
            break
    else:
        pytest.skip("no verified GPU-frames hwaccel here")
    import av
    ctx = HevcDecoder._gpu_frames_context(hw_type)
    assert ctx is not None
    frames = list(ctx.decode(av.Packet(hwcaps._HEVC444_SAMPLE))) + list(ctx.decode(None))
    assert frames[0].format.name == hw_type            # stayed on the GPU
    from isharescreen.proxy.media.decode_common import _av_frame_to_tile
    tile, _ = _av_frame_to_tile(frames[0], [None], set())
    sw_ctx = av.CodecContext.create("hevc", "r")
    sw = (list(sw_ctx.decode(av.Packet(hwcaps._HEVC444_SAMPLE))) + list(sw_ctx.decode(None)))[0]
    assert (tile.width, tile.chroma_width) == (sw.width, sw.width)   # full 4:4:4
    assert bytes(tile.y) == bytes(sw.planes[0])
