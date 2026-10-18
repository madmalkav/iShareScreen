"""libfdk-aac is configured without SBR (aac_eld.FDK_AUDIO_SPECIFIC_CONFIG):
Apple's units carry no SBR data, so the plain config decodes the same
samples with 32 samples less decoder delay."""
from __future__ import annotations

import importlib

import pytest

from isharescreen.proxy.media import aac_eld


def _bits(b: bytes) -> str:
    return "".join(f"{x:08b}" for x in b)


def test_fdk_config_is_apples_without_sbr():
    apple, fdk = _bits(aac_eld.AUDIO_SPECIFIC_CONFIG), _bits(aac_eld.FDK_AUDIO_SPECIFIC_CONFIG)
    # AOT escape + 39, 48 kHz, stereo, then ELDSpecificConfig's
    # frameLengthFlag (480) and three resilience flags: the same 23 bits.
    assert fdk[:23] == apple[:23] == "11111000111001100101000"
    assert apple[23] == "1"            # ldSbrPresentFlag in Apple's config
    assert fdk[23] == "0"              # ... cleared for libfdk
    assert aac_eld.FDK_AUDIO_SPECIFIC_CONFIG == bytes.fromhex("f8e65000")


def test_opt_out_restores_apples_config(monkeypatch):
    monkeypatch.setenv("ISS_AAC_SBR_CONFIG", "1")
    m = importlib.reload(aac_eld)
    try:
        assert m.FDK_AUDIO_SPECIFIC_CONFIG == m.AUDIO_SPECIFIC_CONFIG
    finally:
        monkeypatch.delenv("ISS_AAC_SBR_CONFIG")
        importlib.reload(aac_eld)


def test_silence_unit_decodes_to_one_10ms_frame():
    try:
        dec = aac_eld._LibFdkBackend()
    except (OSError, RuntimeError) as e:
        pytest.skip(f"libfdk-aac not available: {e}")
    try:
        out = None
        for _ in range(3):                 # a few units to get past priming
            out = dec.decode(bytes(4))     # Apple's silence unit
        assert out is not None and out.shape == (480, 2)
        assert abs(out).max() == 0.0
        assert dec._asc_keep[:] == list(aac_eld.FDK_AUDIO_SPECIFIC_CONFIG)
    finally:
        dec.close()
