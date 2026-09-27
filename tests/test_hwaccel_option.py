"""--hwaccel / ISS_HWACCEL: 'auto' keeps each platform's order, a device name
pins the libav decoders to that API, 'software' disables hardware decode."""
from __future__ import annotations

import pytest

from isharescreen import cli
from isharescreen.proxy.media import avc, hevc, hwcaps


@pytest.fixture(autouse=True)
def _linux(monkeypatch):
    monkeypatch.setattr(hevc.sys, "platform", "linux")
    monkeypatch.setattr(avc.sys, "platform", "linux")
    monkeypatch.setattr(hevc, "_loaded_va_driver", lambda *a: "iHD")
    monkeypatch.delenv("ISS_HWACCEL", raising=False)
    monkeypatch.delenv("ISS_PREFER_CUDA", raising=False)


def test_default_is_auto():
    assert hwcaps.hwaccel_choice() == "auto"
    assert hevc._platform_hwaccels() == ("vaapi", "cuda")
    assert avc._h264_hwaccels() == ("vaapi",)


@pytest.mark.parametrize("device", ["cuda", "vaapi"])
def test_device_pins_both_decoders(monkeypatch, device):
    monkeypatch.setenv("ISS_HWACCEL", device)
    assert hevc._platform_hwaccels() == (device,)
    assert avc._h264_hwaccels() == (device,)


def test_software_skips_the_hevc444_probe(monkeypatch):
    monkeypatch.setenv("ISS_HWACCEL", "software")
    monkeypatch.setattr(hwcaps, "_method_cache", {})
    monkeypatch.setattr(hwcaps, "_probe_one", lambda h: pytest.fail("probed"))
    assert hwcaps.hevc444_decode_method() is None


def test_device_probes_only_that_api(monkeypatch):
    monkeypatch.setenv("ISS_HWACCEL", "cuda")
    monkeypatch.setattr(hwcaps, "_method_cache", {})
    probed = []
    monkeypatch.setattr(hwcaps, "_probe_one", lambda h: probed.append(h) or True)
    assert hwcaps.hevc444_decode_method() == "libav"
    assert probed == ["cuda"]


def test_cli_accepts_the_choices():
    for choice in hwcaps.HWACCEL_CHOICES:
        assert cli._make_parser().parse_args(["--hwaccel", choice]).hwaccel == choice
    with pytest.raises(SystemExit):
        cli._make_parser().parse_args(["--hwaccel", "bogus"])


# ── connect form ─────────────────────────────────────────────────────────

def _launch_cmd(monkeypatch, **values):
    from isharescreen.gui import connect as C
    seen = {}

    class _Proc:
        pid = 1
        stdin = type("S", (), {"write": lambda *a: None, "flush": lambda *a: None,
                               "close": lambda *a: None})()
        stdout = iter(())

    def _popen(cmd, **_):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(C.subprocess, "Popen", _popen)
    monkeypatch.setattr(C, "_kill_current", lambda: None)
    monkeypatch.setattr(C, "_free_bridge_port", lambda *_: None)
    monkeypatch.setattr(C, "_emit", lambda *a: None)
    monkeypatch.setattr(C.threading, "Thread", lambda **k: type("T", (), {"start": lambda s: None})())
    monkeypatch.delenv("ISS_VIDEO_CODEC", raising=False)
    C._launch(dict(host="h", user="u", password="p", frontend="desktop", **values))
    return seen["cmd"]


def test_form_passes_hwaccel_and_leaves_decoder_to_the_session(monkeypatch):
    cmd = _launch_cmd(monkeypatch, hwaccel="software")
    i = cmd.index("--hwaccel")
    assert cmd[i + 1] == "software"
    assert "--decoder" not in cmd


def test_form_auto_adds_nothing(monkeypatch):
    from isharescreen.gui import connect as C
    monkeypatch.setattr("isharescreen.proxy.media.registry.resolve_codec", lambda c: "hevc")
    monkeypatch.setattr("isharescreen.proxy.media.registry.select", lambda c: None)
    assert "--hwaccel" not in _launch_cmd(monkeypatch, hwaccel="auto")


def test_form_lists_only_built_apis(monkeypatch):
    from isharescreen.gui import connect as C
    monkeypatch.setattr(C.sys, "platform", "linux")
    monkeypatch.setattr(hwcaps, "hwdevices_available", lambda: ("cuda", "drm"))
    html = C._hwaccel_options_html()
    assert 'value="auto"' in html and 'value="software"' in html
    assert 'value="cuda"' in html and 'value="vaapi"' not in html
