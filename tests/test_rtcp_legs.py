"""Each media leg gets its RTCP on its own port under its own SRTCP key:
video reports/requests on the video port, an audio-leg report on 5900."""
from __future__ import annotations

import types

from isharescreen.proxy.session import Session


class _Sock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def _session():
    s = Session.__new__(Session)
    s._config = types.SimpleNamespace(port=5900, udp_ctrl_port=0, udp_video_port=0)
    s._sock_ctrl, s._sock_video = _Sock(), _Sock()
    s._dest_host = "mac"
    return s


def test_video_rtcp_goes_to_the_video_port(monkeypatch):
    monkeypatch.delenv("ISS_VIDEO_RTCP_ON_CTRL", raising=False)
    s = _session()
    assert s._video_rtcp_dest() == (s._sock_video, 5901)


def test_old_routing_opt_out(monkeypatch):
    monkeypatch.setenv("ISS_VIDEO_RTCP_ON_CTRL", "1")
    s = _session()
    assert s._video_rtcp_dest() == (s._sock_ctrl, 5900)


def test_audio_rr_uses_audio_key_and_port():
    s = _session()
    s._our_audio_ssrc = 0xA0A0A0A0
    s._audio_remote_ssrc = 0x12345678
    s._audio_max_seq = 42
    s._server_sr_audio = {}
    s._audio_srtcp_enc = types.SimpleNamespace(protect=lambda p: b"AUDIO" + p)
    s._send_audio_rr()
    (data, addr), = s._sock_ctrl.sent
    assert addr == ("mac", 5900) and data.startswith(b"AUDIO")
    rr = data[5:]
    assert rr[1] == 201 and rr[0] & 0x1F == 1                  # RR with one block
    assert int.from_bytes(rr[4:8], "big") == 0xA0A0A0A0       # from our audio SSRC
    assert int.from_bytes(rr[8:12], "big") == 0x12345678      # about the host's audio
    assert not s._sock_video.sent
