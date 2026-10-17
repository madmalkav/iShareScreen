"""libav log lines without Python inside av_log (proxy/media/libav_log.py).

The old path (PyAV's log callback) took the GIL inside av_log; libav can log
while holding a frame-pool mutex the main thread waits for with the GIL held,
which froze the whole process. The capture keeps libav's C logger and reads
its lines from a pipe after each decode()."""
from __future__ import annotations

import ctypes
import logging
import sys
import time

import av
import av.logging
import pytest

from isharescreen.proxy.media import libav_log

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="the capture is Linux/glibc only")


@pytest.fixture
def capture():
    got: list[tuple[str, str]] = []
    c = libav_log._Capture()
    assert c.install(lambda comp, msg: got.append((comp, msg)), av.logging.WARNING)
    try:
        yield c, got
    finally:
        c.uninstall()


# Real libav messages end in a newline; av.logging.log() passes the text
# through as given.


def _wait_for(pred, timeout=2.0):
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_no_python_callback_inside_av_log(capture, caplog):
    """A libav message must reach the sink, not PyAV's Python logger (which
    would mean PyAV's GIL-taking callback is installed)."""
    c, got = capture
    caplog.set_level(logging.DEBUG, logger="libav")
    with c.decoding():
        av.logging.log(av.logging.ERROR, "hevc", "Could not find ref with POC 7\n")
    assert ("hevc", "Could not find ref with POC 7") in got
    assert not [r for r in caplog.records if r.name.startswith("libav")]
    assert av.logging.get_level() is None      # PyAV's own callback never set


def test_lines_of_a_decode_are_handled_before_it_returns(capture):
    c, got = capture
    with c.decoding():
        av.logging.log(av.logging.WARNING, "h264", "reference picture missing during reorder\n")
        assert got == []                      # nothing runs inside av_log
    assert got == [("h264", "reference picture missing during reorder")]


def test_a_real_decode_error_is_captured(capture):
    c, got = capture
    ctx = av.CodecContext.create("hevc", "r")
    with c.decoding():
        try:
            ctx.decode(av.Packet(b"\x00\x00\x00\x01\x42\x01" + b"\xff" * 64))
        except av.error.FFmpegError:
            pass
    assert got and all(comp == "hevc" for comp, _ in got)


def test_lines_outside_a_decode_reach_the_sink_from_the_reader(capture):
    c, got = capture
    av.logging.log(av.logging.ERROR, "hevc", "late line\n")
    assert _wait_for(lambda: ("hevc", "late line") in got)


def test_below_the_level_is_dropped_by_libav(capture):
    c, got = capture
    with c.decoding():
        av.logging.log(av.logging.INFO, "hevc", "chatty\n")
    assert got == []


def test_other_c_stderr_output_passes_through(capture, capfd):
    c, got = capture
    libc = ctypes.CDLL(None)
    libc.fputs.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    stderr = ctypes.c_void_p.in_dll(libc, "stderr")
    with c.decoding():
        libc.fputs(b"some other library\n", stderr)
    assert got == []
    assert "some other library" in capfd.readouterr().err


def test_python_stderr_is_untouched(capture, capfd):
    print("python side", file=sys.stderr)
    assert "python side" in capfd.readouterr().err
