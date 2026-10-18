"""libav's log messages, read without running Python inside `av_log`.

iss reacts to some libav log lines ("Could not find ref", "reference picture
missing", D3D11VA setup errors). PyAV's own log callback delivers them by
taking the GIL inside `av_log`, and libav sometimes logs while holding one of
its locks, e.g. a hardware frame pool's mutex in `av_buffer_pool_get`. If the
main thread holds the GIL and waits for that same mutex (dropping a frame:
`av_frame_unref`), neither can go on: the whole process freezes, and SIGINT
can't interrupt it (seen on 2026-10-15/17 on Linux with CUDA).

Instead, libav keeps its own C logger (`av_log_default_callback`, which never
touches the GIL), and glibc's `stderr` stream, which that logger writes to, is
pointed at a non-blocking pipe. Python's `sys.stderr` and faulthandler write to
fd 2 directly and are unaffected. The pipe is drained:

- right after each `decode()` call, on the decoding thread (`decoding()`), so
  the handler still runs before that call's frames are published, as it did
  inside the callback; but outside `av_log` and with no libav lock held;
- by a reader thread, for lines logged outside a decode (codec open, flush).

A full pipe drops lines instead of blocking libav (the write end is
non-blocking). Lines that aren't libav's ("[name @ 0x...] ...") are passed
through to fd 2.

Linux/glibc only (it replaces the C `stderr` FILE*); `install()` returns False
elsewhere and the caller keeps its old behaviour.
"""
from __future__ import annotations

import contextlib
import ctypes
import fcntl
import logging
import os
import re
import sys
import threading
from typing import Callable, Iterator, Optional

log = logging.getLogger(__name__)

# libav's default logger prefixes context messages with "[name @ 0xADDR] ".
_PREFIX = re.compile(r"^\[([^\]@]+?) @ 0x[0-9a-fA-F]+\] ?")
_F_SETPIPE_SZ = 1031
_PIPE_BYTES = 1 << 20
_READER_POLL_S = 0.05

Sink = Callable[[str, str], None]   # (component, message)


class _Capture:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buf = b""
        self._r: Optional[int] = None
        self._file: Optional[int] = None        # FILE* on the pipe
        self._old_stderr: Optional[int] = None  # glibc's original FILE*
        self._stderr_var: Optional[ctypes.c_void_p] = None
        self._sink: Optional[Sink] = None
        self._busy = 0
        self._stop = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self.dropped_reads = 0

    @property
    def installed(self) -> bool:
        return self._r is not None

    def install(self, sink: Sink, level: int) -> bool:
        if self.installed:
            self._sink = sink
            return True
        if not sys.platform.startswith("linux"):
            return False
        try:
            import av.logging as avlog
            libc = ctypes.CDLL(None, use_errno=True)
            libc.fdopen.restype = ctypes.c_void_p
            libc.fdopen.argtypes = [ctypes.c_int, ctypes.c_char_p]
            libc.setvbuf.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                     ctypes.c_int, ctypes.c_size_t]
            stderr_var = ctypes.c_void_p.in_dll(libc, "stderr")
        except (ImportError, OSError, ValueError) as e:
            log.debug("libav log capture unavailable: %s", e)
            return False
        r, w = os.pipe()
        for fd in (r, w):
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        try:
            fcntl.fcntl(w, _F_SETPIPE_SZ, _PIPE_BYTES)
        except OSError:
            pass  # the default 64 KB still works, it just drops sooner
        fp = libc.fdopen(w, b"w")
        if not fp:
            os.close(r)
            os.close(w)
            return False
        libc.setvbuf(fp, None, 2, 0)  # _IONBF: each message is written at once
        self._sink = sink
        self._r = r
        self._file = fp
        self._stderr_var = stderr_var
        self._old_stderr = stderr_var.value
        stderr_var.value = fp
        # libav's C logger instead of PyAV's GIL-taking one.
        avlog.restore_default_callback()
        avlog.set_libav_level(level)
        self._reader = threading.Thread(target=self._read_loop,
                                        name="iss-libav-log", daemon=True)
        self._reader.start()
        return True

    def uninstall(self) -> None:
        """Point C stderr back at the terminal (process exit)."""
        if not self.installed:
            return
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=1.0)
        if self._stderr_var is not None and self._old_stderr is not None:
            self._stderr_var.value = self._old_stderr
        self.drain()
        libc = ctypes.CDLL(None)
        libc.fclose.argtypes = [ctypes.c_void_p]
        libc.fclose(self._file)
        os.close(self._r)
        self._r = self._file = None

    @contextlib.contextmanager
    def decoding(self) -> Iterator[None]:
        """Wrap a `decode()` call: its log lines are handled on this thread
        as soon as it returns (the reader thread leaves them alone)."""
        if not self.installed:
            yield
            return
        with self._lock:
            self._busy += 1
        try:
            yield
        finally:
            # Drain before leaving busy, or the reader could take the lines.
            try:
                self.drain()
            finally:
                with self._lock:
                    self._busy -= 1

    def drain(self, *, unless_busy: bool = False) -> int:
        """Handle every complete line in the pipe. Any thread; returns the
        number of libav lines handled. `unless_busy` (the reader thread):
        leave the pipe to a `decoding()` call in progress. Checked under the
        lock, so a decode either starts after this read or is seen here."""
        if self._r is None:
            return 0
        with self._lock:
            if unless_busy and self._busy:
                return 0
            chunks = []
            while True:
                try:
                    chunk = os.read(self._r, 65536)
                except BlockingIOError:
                    break
                except OSError:
                    self.dropped_reads += 1
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            if not chunks:
                return 0
            data = self._buf + b"".join(chunks)
            *lines, self._buf = data.split(b"\n")
        n = 0
        sink = self._sink
        for raw in lines:
            text = raw.decode("utf-8", "replace").rstrip("\r")
            m = _PREFIX.match(text)
            if m is None:
                # Not libav's (another C library writing to stderr): pass on.
                try:
                    os.write(2, raw + b"\n")
                except OSError:
                    pass
                continue
            n += 1
            if sink is not None:
                try:
                    sink(m.group(1).strip(), text[m.end():].strip())
                except Exception:
                    log.debug("libav log sink raised", exc_info=True)
        return n

    def _read_loop(self) -> None:
        while not self._stop.wait(_READER_POLL_S):
            self.drain(unless_busy=True)


_capture = _Capture()


def install(sink: Sink, level: int) -> bool:
    """Route libav's log lines at `level` and above to `sink(component,
    message)` without Python inside `av_log`. False if unsupported here."""
    return _capture.install(sink, level)


def installed() -> bool:
    return _capture.installed


def decoding():
    """Context manager for a `decode()` call (see module docstring)."""
    return _capture.decoding()


def uninstall() -> None:
    _capture.uninstall()


__all__ = ["decoding", "install", "installed", "uninstall"]
