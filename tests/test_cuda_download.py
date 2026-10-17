"""Pinned CUDA download (proxy/media/cuda_download.py): the fallbacks.

The real copy is checked against PyAV's download in
test_hw_frames_on_gpu.py::test_real_gpu_frames_decode_to_full_444_tiles
(CUDA machines only)."""
from __future__ import annotations

import types

from isharescreen.proxy.media import cuda_download
from isharescreen.proxy.media.cuda_download import PinnedDownloader


def _frame(sw="yuv444p"):
    return types.SimpleNamespace(
        sw_format=None if sw is None else types.SimpleNamespace(name=sw),
        width=16, height=8, planes=[])


def test_opt_out_uses_pyav(monkeypatch):
    monkeypatch.setattr(cuda_download, "_ENABLED", False)
    d = PinnedDownloader(4)
    assert not d.active
    assert d.download(_frame(), 0) is None


def test_only_444_frames_are_taken(monkeypatch):
    d = PinnedDownloader(4)
    monkeypatch.setattr(d, "_init", lambda: (_ for _ in ()).throw(AssertionError))
    assert d.download(_frame("nv12"), 0) is None
    assert d.download(_frame(None), 0) is None
    assert d.download(_frame(), 7) is None       # tile out of range
    assert d.active


def test_cuda_failure_falls_back_for_good(monkeypatch, caplog):
    d = PinnedDownloader(4)
    calls = []

    def boom():
        calls.append(1)
        raise OSError("libcuda.so.1: cannot open shared object file")
    monkeypatch.setattr(d, "_init", boom)
    assert d.download(_frame(), 0) is None
    assert not d.active
    assert d.download(_frame(), 0) is None
    assert calls == [1]                           # not retried
    assert sum("pinned CUDA download unavailable" in r.message
               for r in caplog.records) == 1


def test_close_without_cuda_is_a_no_op():
    PinnedDownloader(4).close()
