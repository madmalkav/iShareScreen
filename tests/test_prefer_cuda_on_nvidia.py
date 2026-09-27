"""On NVIDIA's VA driver, try CUDA before VAAPI (same NVDEC engine, CUDA is
the direct route); every other setup keeps the platform order."""
from __future__ import annotations

import pytest

from isharescreen.proxy.media import hevc


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(hevc.sys, "platform", "linux")
    monkeypatch.delenv("ISS_PREFER_CUDA", raising=False)


@pytest.mark.parametrize("driver, expected", [
    ("nvidia", ("cuda", "vaapi")),
    ("iHD", ("vaapi", "cuda")),        # Intel (incl. hybrid laptops): unchanged
    ("radeonsi", ("vaapi", "cuda")),   # AMD: unchanged
    (None, ("vaapi", "cuda")),         # driver unknown: unchanged
])
def test_order_by_va_driver(monkeypatch, linux, driver, expected):
    monkeypatch.setattr(hevc, "_loaded_va_driver", lambda *a: driver)
    assert hevc._platform_hwaccels() == expected


def test_opt_out(monkeypatch, linux):
    monkeypatch.setattr(hevc, "_loaded_va_driver", lambda *a: "nvidia")
    monkeypatch.setenv("ISS_PREFER_CUDA", "0")
    assert hevc._platform_hwaccels() == ("vaapi", "cuda")


@pytest.mark.parametrize("platform, expected", [
    ("win32", ("d3d11va", "d3d12va")),
    ("darwin", ()),
])
def test_other_platforms_untouched(monkeypatch, platform, expected):
    monkeypatch.setattr(hevc.sys, "platform", platform)
    monkeypatch.setattr(hevc, "_loaded_va_driver", lambda *a: "nvidia")
    assert hevc._platform_hwaccels() == expected


def test_decoder_builds_cuda_first_on_nvidia(monkeypatch, linux):
    monkeypatch.setattr(hevc, "_loaded_va_driver", lambda *a: "nvidia")
    tried = []
    d = hevc.HevcDecoder(1)
    d._vps, d._sps, d._all_pps = b"v", b"s", {0: b"p"}
    monkeypatch.setattr(d, "_try_hwaccel", lambda hw, ed: tried.append(hw) or object())
    monkeypatch.setattr(d, "_install_codec", lambda c, hw_name: setattr(d, "_hw_name", hw_name))
    d._create_codec(force_software=False)
    assert tried == ["cuda"] and d.hw_accel == "cuda"
