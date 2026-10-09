"""--max-bitrate: the offer's kind-0 bitrate tiers are clamped to the cap
(the Mac takes its encoder ceiling from them), keeping the list's shape."""
import argparse
import plistlib
import zlib

import pytest

from isharescreen import cli
from isharescreen.proxy.protocol import offers


def _read_varint(b, p):
    v = s = 0
    while True:
        c = b[p]; p += 1
        v |= (c & 0x7F) << s; s += 7
        if not c & 0x80:
            return v, p


def _f9_tiers(blob):
    """(kind, bps, buf) of every top-level field-9 entry in a MediaBlob."""
    out, p = [], 0
    while p < len(blob):
        tag, p = _read_varint(blob, p)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            _, p = _read_varint(blob, p)
        elif wt == 2:
            n, p = _read_varint(blob, p)
            if fn == 9:
                sub, q, vals = blob[p:p + n], 0, {}
                while q < len(sub):
                    t, q = _read_varint(sub, q)
                    vals[t >> 3], q = _read_varint(sub, q)
                out.append((vals.get(1), vals.get(2), vals.get(3)))
            p += n
        else:
            raise AssertionError(f"unexpected wire type {wt}")
    return out


def _blob(plist_bytes):
    return zlib.decompress(plistlib.loads(plist_bytes)["avcMediaStreamNegotiatorMediaBlob"])


def test_no_cap_keeps_apples_tiers():
    assert offers._f9_entries(None) == offers._APPLE_AUDIO_F9
    assert offers._f9_entries(0) == offers._APPLE_AUDIO_F9


@pytest.mark.parametrize("cap", [8_000_000, 15_000_000, 40_000_000])
def test_cap_clamps_kind0_and_keeps_shape(cap):
    video, audio = offers.create_offers(max_bitrate_kbps=cap // 1000)
    for plist in (video, audio):
        tiers = _f9_tiers(_blob(plist))
        apple = offers._AUDIO_F9_TIERS
        assert len(tiers) == len(apple)
        for (k, v, b), (ak, av, ab) in zip(tiers, apple):
            assert k == ak and b == ab
            assert v == (min(av, cap) if ak == 0 else av)
        assert max(v for k, v, _ in tiers if k == 0) == cap


def test_offers_without_cap_are_unchanged_in_tiers():
    video, _ = offers.create_offers()
    assert [(k, v) for k, v, _ in _f9_tiers(_blob(video))] == \
        [(k, v) for k, v, _ in offers._AUDIO_F9_TIERS]


@pytest.mark.parametrize("arg,expected", [(None, None), (0, None), (8, 8000), (2.5, 2500)])
def test_cli_mbit_to_kbps(arg, expected):
    assert cli._max_bitrate_kbps(arg) == expected


@pytest.mark.parametrize("bad", [0.5, 150])
def test_cli_rejects_out_of_range(bad):
    with pytest.raises(SystemExit):
        cli._max_bitrate_kbps(bad)


def test_cli_parses_option():
    args = cli._make_parser().parse_args(["--host", "h", "--max-bitrate", "12"])
    assert args.max_bitrate == 12.0
