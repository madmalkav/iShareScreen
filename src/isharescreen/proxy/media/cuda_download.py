"""Download CUDA-decoded tiles into pinned host memory.

With frames kept on the GPU (hevc.py `_HW_FRAMES_ON_GPU`), each tile the
renderer takes is downloaded in `get_frame()`. PyAV's path,
`VideoReformatter().reformat()` = `av_hwframe_transfer_data` into ordinary
(pageable) memory, makes the CUDA driver stage the copy through the CPU:
~1.3 ms of CPU per 3840x544 4:4:4 tile, with the GIL held. A `cuMemcpy2D`
into page-locked memory (`cuMemHostAlloc`) runs as DMA instead: ~0.04 ms of
CPU and ~40 % less wall time per tile, with the GIL released (ctypes), and
bit-identical output (measured on 34k frames, 2026-10-17).

The device pointers belong to FFmpeg's CUDA context, so FFmpeg must create
it on the device's primary context (`primary_ctx=1`), which this module then
retains. FFmpeg sets the primary context's flags when it creates the device
and fails if the context is already active, so `retain` must only happen
after the first decoder context exists (it does: the first download is
after the first decoded frame).

Each tile alternates between two buffers, so the TileFrame handed out last
stays intact while the next one is copied. Buffers are only freed in
`close()`; after a size change the old ones are kept until then, since a
TileFrame may still point into them.

No `cuCtxSynchronize`: the synchronous `cuMemcpy2D` on the legacy default
stream is ordered after FFmpeg's own copy into the frame (verified
bit-identical without it on 17k frames).

ISS_CUDA_DOWNLOAD=default turns it off (PyAV's download, as before).
"""
from __future__ import annotations

import ctypes
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_ENABLED = os.environ.get("ISS_CUDA_DOWNLOAD", "pinned").lower() != "default"
_CU_MEMORYTYPE_HOST = 1
_CU_MEMORYTYPE_DEVICE = 2


class _Memcpy2D(ctypes.Structure):
    _fields_ = [
        ("srcXInBytes", ctypes.c_size_t), ("srcY", ctypes.c_size_t),
        ("srcMemoryType", ctypes.c_int),
        ("srcHost", ctypes.c_void_p), ("srcDevice", ctypes.c_uint64),
        ("srcArray", ctypes.c_void_p), ("srcPitch", ctypes.c_size_t),
        ("dstXInBytes", ctypes.c_size_t), ("dstY", ctypes.c_size_t),
        ("dstMemoryType", ctypes.c_int),
        ("dstHost", ctypes.c_void_p), ("dstDevice", ctypes.c_uint64),
        ("dstArray", ctypes.c_void_p), ("dstPitch", ctypes.c_size_t),
        ("WidthInBytes", ctypes.c_size_t), ("Height", ctypes.c_size_t),
    ]


class _CudaError(RuntimeError):
    pass


class PinnedDownloader:
    """Per-decoder pinned download of yuv444p CUDA frames (see module doc).
    Call `download` from one thread (the renderer's `get_frame`)."""

    def __init__(self, num_tiles: int) -> None:
        self._num_tiles = num_tiles
        self._cu = None
        self._ctx = ctypes.c_void_p()
        self._dev = ctypes.c_int()
        self._retained = False
        self._failed = not _ENABLED
        self._logged = False
        # (tile, slot) -> (ptr, size); next slot per tile
        self._bufs: dict[tuple[int, int], tuple[int, int]] = {}
        self._next: list[int] = [0] * num_tiles
        self._retired: list[int] = []

    @property
    def active(self) -> bool:
        return not self._failed

    def _check(self, rc: int, what: str) -> None:
        if rc != 0:
            name = ctypes.c_char_p()
            self._cu.cuGetErrorName(rc, ctypes.byref(name))
            raise _CudaError(f"{what}: {(name.value or b'?').decode()} ({rc})")

    def _init(self) -> None:
        cu = ctypes.CDLL("libcuda.so.1")
        cu.cuMemcpy2D_v2.argtypes = [ctypes.POINTER(_Memcpy2D)]
        cu.cuMemHostAlloc.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.c_size_t, ctypes.c_uint]
        cu.cuMemFreeHost.argtypes = [ctypes.c_void_p]
        cu.cuCtxPushCurrent_v2.argtypes = [ctypes.c_void_p]
        cu.cuGetErrorName.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        self._cu = cu
        self._check(cu.cuInit(0), "cuInit")
        self._check(cu.cuDeviceGet(ctypes.byref(self._dev), 0), "cuDeviceGet")
        self._check(cu.cuDevicePrimaryCtxRetain(ctypes.byref(self._ctx), self._dev),
                    "cuDevicePrimaryCtxRetain")
        self._retained = True

    def _buffer(self, tile_idx: int, size: int) -> int:
        slot = self._next[tile_idx]
        self._next[tile_idx] = slot ^ 1
        key = (tile_idx, slot)
        have = self._bufs.get(key)
        if have is not None and have[1] == size:
            return have[0]
        if have is not None:
            self._retired.append(have[0])   # a TileFrame may still use it
        ptr = ctypes.c_void_p()
        self._check(self._cu.cuMemHostAlloc(ctypes.byref(ptr), size, 0), "cuMemHostAlloc")
        self._bufs[key] = (ptr.value, size)
        return ptr.value

    def download(self, frame, tile_idx: int):
        """The frame's Y, U, V planes as memoryviews into pinned memory (pitch
        = width), or None to use PyAV's download (unsupported frame, or CUDA
        failed: then off for good, logged once)."""
        if self._failed or not 0 <= tile_idx < self._num_tiles:
            return None
        sw = getattr(frame, "sw_format", None)
        if sw is None or sw.name != "yuv444p":
            return None
        w, h = frame.width, frame.height
        plane = w * h
        try:
            if self._cu is None:
                self._init()
            self._check(self._cu.cuCtxPushCurrent_v2(self._ctx), "cuCtxPushCurrent")
            try:
                base = self._buffer(tile_idx, 3 * plane)
                m = _Memcpy2D()
                m.srcMemoryType = _CU_MEMORYTYPE_DEVICE
                m.dstMemoryType = _CU_MEMORYTYPE_HOST
                m.dstPitch = m.WidthInBytes = w
                m.Height = h
                for i, pl in enumerate(frame.planes[:3]):
                    m.srcDevice = pl.buffer_ptr
                    m.srcPitch = pl.line_size
                    m.dstHost = base + i * plane
                    self._check(self._cu.cuMemcpy2D_v2(ctypes.byref(m)), "cuMemcpy2D")
            finally:
                self._cu.cuCtxPopCurrent_v2(ctypes.byref(ctypes.c_void_p()))
        except (_CudaError, OSError, AttributeError) as e:
            self._failed = True
            log.warning("pinned CUDA download unavailable (%s); using PyAV's "
                        "download", e)
            return None
        if not self._logged:
            self._logged = True
            log.info("CUDA frames: pinned download (cuMemcpy2D, %dx%d 4:4:4)", w, h)
        arr = (ctypes.c_uint8 * (3 * plane)).from_address(base)
        mv = memoryview(arr).cast("B")
        return mv[:plane], mv[plane:2 * plane], mv[2 * plane:], w

    def close(self) -> None:
        """Free every buffer. Only once no TileFrame from this decoder is in
        use (decoder close)."""
        cu = self._cu
        if cu is None:
            return
        for ptr in [p for p, _ in self._bufs.values()] + self._retired:
            cu.cuMemFreeHost(ptr)
        self._bufs.clear()
        self._retired.clear()
        if self._retained:
            cu.cuDevicePrimaryCtxRelease_v2(self._dev)
            self._retained = False


__all__ = ["PinnedDownloader"]
