# CLAUDE.md

Python client for macOS Screen Sharing **High Performance** mode: RFB 003.889 control over TCP, HEVC 4:4:4 (or H.264 4:2:0)
video + AAC-ELD audio over SRTP/UDP, rendered with wgpu. The protocol reference is `docs/apple_vnc_rfc.md` (media transport §10,
rate control §10.8.1, liveness §10.8.2; facts from the remotex notes are marked [REMOTEX]); the user-facing codec and decoder
guide is the README's "Codecs and decoders" section.

## Layout
- `proxy/session.py`: the session core. Handshake + start burst, UDP drain/process threads, RTCP (RR, FIR/PLI, NACK, LTR ack,
  RCTL), SSRC adoption, stall/liveness logic, the profile log line.
- `proxy/protocol/`:
  - `negotiation.py`: handshake, one `0x1c` offer + answer wait (re-send loop only as fallback).
  - `mediamsg.py`: the Mac's media-stream messages (encoding 1010: message 1 ports, 2 answer, 3 error).
  - `offers.py`: AVConference offer protobuf.
  - `rfb.py`: `0x1d` virtual display.
  - `burst.py`: start burst → param sets + per-tile NALUs.
  - `srtp.py`, `rtcp.py`, `rctl.py`: SRTP/SRTCP, RTCP packets, rate-control reports.
- `proxy/media/`:
  - decoders: `hevc.py` (libav, hwaccel), `avc.py` (H.264), `vtdecode.py` (native VideoToolbox, macOS), `qsvhevc.py`;
  - `registry.py`: decoder ladder + `resolve_codec`;
  - `hwcaps.py`: HEVC 4:4:4 hardware probe + `--hwaccel` choice;
  - `nalu.py`: RTP depacketization.
- `frontend/desktop/`: wgpu/GLFW viewer (`app.py`, `canvas.py`, `gpu.py`). `frontend/wt/`: browser frontend (H.264 only).
  `gui/connect.py`: connect form.

## Build / test
- `pip install -e .[test]` then `pytest`. It must stay green on Linux and macOS (the suite runs on both).
- Linux hardware decode needs PyAV built against system FFmpeg (`--no-binary av`); the PyPI wheel has no VAAPI.
- Intel Macs: install with `--only-binary :all:`. The newest `cryptography` has no x86_64 macOS wheel and would need Xcode tools.

## Rules for changes
- A change must not degrade any platform (Linux VAAPI/CUDA, Windows D3D11VA/QSV, macOS VideoToolbox, software-only). Gate new
  paths on detected capability, keep the old path as fallback, add an env opt-out, and say in the PR what was tested and what
  was only reasoned about.
- Judge picture changes by the log, not only by eye: **"Could not find ref" errors more than 1 s after `session ready`**
  reliably predicted visible artifacts. A few dozen *before* `session ready` are the normal start. Also watch the start-burst
  size (`initial-burst packets`, normally ~50–300; thousands means a backlog decoded mid-sequence), `gray-out`, `falling
  behind`/resync, and `profile:` rates. `profile:` has `rates` (decoded), `clean_rates`, and `shown_rates` (handed to the
  frontend). Note that `shown` drops when the window is hidden.
- Host-side truth comes from the Mac's unified log: `/usr/bin/log show --predicate 'process == "avconferenced"'` (and
  `process CONTAINS[c] "screenshar"`). Use the full path over SSH, since zsh's `log` builtin shadows it. The rate controller
  prints `targetBitrate=… bitrateCap=… RTT=…` every few seconds.
- The external project remotex (github.com/andrewtheguy/remotex, `docs/apple-vnc-889.md`) documents much of the same protocol
  from its own measurements. It declares **no license**: use it for facts only, never copy code. Don't disassemble Apple
  binaries.

## Established facts (all verified live; host M4 / macOS 27.2)
- **Legs:** audio + its RTCP on UDP 5900, video + its RTCP on 5901, each with its **own** SRTP/SRTCP keys (viewer→server `*_v`,
  server→viewer `*_s`). RTCP must go to the leg it belongs to under that leg's key (#22).
- **RTP/RTCP demux:** RTCP is `b[1]` in 192–223 (RFC 5761). Masking with `0x7F` (an RTP check) drops every RTCP packet (#23).
  The host sends an SR on each leg ~1/s, even on a still screen.
- **Rate control:** the host encoder follows AVConference's rate controller, between a 20 Mbit/s floor and a ceiling of the
  offer's bitrate tiers capped at 60 (offering more doesn't raise it). An offer capped **below** 20 makes the encoder run at
  that cap: `--max-bitrate` (verified at 8/15/40: the host's `bitrateCap` follows). The tiers must be **clamped** (every
  kind-0 f9 entry lowered to the cap); dropping entries above the cap leaves `vcMediaStreamTXMaxBitrate = 0` and no video. It only moves on the viewer's **RCTL** reports: an RTCP APP packet named
  `RCTL` with a 20-byte payload, sent alone (not compound), every 50 ms on the video leg. The one-way-delay field is what
  moves the target. With RCTL the target sits at ~58 Mbit/s; without it, it stays at ~20.8 (#20). TMMBR and the BWE field
  are ignored.
- **4 tiles:** Apple's default `tilesPerFrame=4` = 4 horizontal strips = 4 consecutive SSRCs, cross-tile references, DONL in
  every payload. `tilesPerFrame=1` has no DONL (iss's depacketizers handle only 4-tile DONL streams), makes the host send only
  ~57.5 fps, and is **~24 ms slower end to end** (Mac render → on screen p50 53 → 78 ms, p95 59 → 85; 4K, live, 3 rounds,
  2026-10-18): the host encodes a 1-tile frame whole at 60/s (4 strips at 240/s, each sent as done, all 4 within ~8 ms) (+12 ms),
  and iss decodes and uploads the whole picture after its last packet instead of overlapping strips (+10 ms). Keep 4.
- **Start:** like Apple's viewer, iss's first (plaintext) `SetEncodings` leaves out 1010 and the second (encrypted, sent
  right after the cipher starts) names it; the Mac sends one message 1 per `SetEncodings` naming 1010, ~0.4–1.7 s later (its
  own delay), and answers an offer only after it. iss waits for message 1, sends **one** offer and reads until message 2
  (~0.05 s). Some answers carry no canvas; the canvas then comes from the layout's backing size. Re-sending (old behaviour,
  `ISS_OFFER_RESEND=1`) starts a new stream per offer. The start burst stops once the socket backlog is drained, on a
  picture boundary. The HW-decode probe runs in the background during the wait for message 1. Connect ≈ 3.2 s (was ~6.6;
  `ISS_FAST_CONNECT=0` = old timing). SSRC adoption must still pick the group that is **still sending** (#21).
- **Display changes:** every display change, including a wake from display sleep (same geometry), stops both legs; the
  Mac then sends a message 1 and restarts the stream only for a new offer. iss re-offers once per message 1 (0.3 s
  debounce), with a 1 s fallback on a geometry change that brings no message 1 (`ISS_REOFFER_ON_MSG1=0` = old trigger).
- **Resize:** after `0x1d`, re-arm with a 1×1 incremental FramebufferUpdateRequest, never full-screen (a full-size read racing
  a shrink can crash the host's ScreensharingAgent, per remotex) (#24).
- **Liveness:** no video + no host RTCP for 10 s → warning; 48 s → session ends (Apple's limit). Armed only after host RTCP has
  been seen (#25).
- **Keyframe requests and loss:** a FIR (PT 206/4), a PLI, and iss's RFC 2032-style PT 192 (target SSRC only) each bring an
  IDR alone, in 9–28 ms, with the same IDR size. iss sends all three in one compound, so the host logs `Request key frame
  too soon, discard` for the two extra (harmless noise). The quality gate re-requests every 1 s until recovery, which covers
  the 1 s least gap on 1-tile streams. The host has **retransmission off** (`vcMediaStreamIsRTXEnabled = 0`): iss's NACKs
  are ignored and loss is repaired only by keyframes (5 % random loss ≈ half the shown frames, 42 gray-outs in 45 s). The
  host derives loss from RCTL's running packet count (`Uplink PLR` matches), and loss doesn't move its target.
- **Refresh:** the host accepts any virtual-display refresh in the `0x1d` mode (30/60/90/120), but its **encoder frame rate stays
  60** (`vcMediaStreamFramerate = 60`). 30 Hz halves the pictures and decode cost (`--refresh-rate 30`: software
  HEVC 4K 319 % → 183 % of a core; kept across resizes); >60 gives nothing.
- **`AutoFrameBufferUpdate` (`0x09`):** its `u32` is a push interval in µs, `0xffffffff` = pushes off ([REMOTEX], rfc §8.11).
  iss arms nothing and polls 1×1 incremental every tx tick, which keeps cursor shapes flowing.
- **Curtain mode** (default) gives the virtual display at the requested size and locks the host's local screen while the session
  runs; the host stays locked after the session ends. `--no-curtain` shares the physical display (e.g. native 5120×2160).
  The host may switch the session to its physical display on a local login/idle (see open topics).
- **Audio** (fixed by the host): AAC-ELD, 48 kHz stereo, 480 samples = 10 ms per packet, ~320 kbit/s while sound plays
  (4-byte units in silence). The offer's audio field 4 is a **bitmask of accepted payload types** (bit `0x1000` = PT 101), not
  a bitrate: `4096` streams sound, `16384` is refused, `--no-audio` sends `1000`. Apple's decoder config signals SBR, but the
  stream carries none (rfc §10.3, §10.4), so libfdk-aac (Linux/Windows) is configured without it (`f8e65000`): same samples,
  0.67 ms sooner. Verified on Linux; **Windows not tested yet**. macOS (AudioToolbox) keeps Apple's config.
- **Decoders:**
  - NVIDIA: CUDA is preferred over VAAPI when the VA driver is NVIDIA's (#11). HW frames stay on the GPU and are downloaded
    lazily (#9).
  - H.264 through NVIDIA's VA driver/CUDA at 4K is slower than software (per-frame copy-back + GIL). The **measured** fallback
    switches a hardware H.264 decoder that stays >75 % busy to software (#14).
  - macOS: `--codec auto` picks HEVC only if VideoToolbox *requires-hardware* opens a 4:4:4 session (Apple silicon yes, Intel
    no) (#15).
  - Older Intel Macs can't hardware-decode the host's H.264: it declares 15 reference frames, and e.g. Broadwell's decoder
    allows ≤10.
- **Quality:** the post-scroll blur was the ~20 Mbit/s cap; RCTL mostly fixes it. What remains at 58–60 Mbit/s is the encoder's
  limit (smaller `--advertise` = more bits per pixel). H.264 4:2:0 softens coloured text.
- **Resolution** (live, 4 tiles, CUDA, RTX 2080, 2026-10-18; all held 60 fps): 1x `3840x2160` performs as `1920x1080@2`
  (same pixels). 1x `3440x1440` is ~5.5 ms faster end to end, and 1x `1920x1080` ~11 ms faster with about half the CPU (S2
  iss CPU 44 → 26 % of a core; decode 2.0 → 0.9 ms per tile). On a HiDPI desktop (KDE 2x) the window is sized in points, so
  1x pictures below 4K are upscaled (×1.46, ×1.95) and look soft. Bits per pixel weren't tested (the scenes ran far under
  the 60 Mbit/s cap).

## Merged work (fork), for context
#1–3 honest HW probe/binding · #4 PyAV/VAAPI docs · #5 resize stale-tile crash · #6 overload resync · #7 pipeline 60→95 fps ·
#8 Wayland window size · #9 GPU-kept HW frames · #10 visible H.264 fallback · #11 CUDA first on NVIDIA · #12 window fits screen ·
#13 `--cursor video` · #14 `--hwaccel` + measured H.264 fallback + README guide · #15 macOS HW 4:4:4 probe · #16 `shown_rates` ·
#17 README Intel Mac H.264 · #18 clean Ctrl-C · #19 README bitrate note · #20 RCTL · #21 live SSRC adoption · #22 RTCP per leg ·
#23 RTCP demux · #24 1×1 resize request · #25 RTCP liveness · #26/#28/#34/#38/#42/#43 docs · #29 control-message record split ·
#30 one offer per message 1 + re-offer on wake · #31 `--max-bitrate` · #32 `--refresh-rate` · #33 800×600-point minimum ·
#35 Windows UDP CONNRESET · #36 no NACKs/retransmit wait · #37 return to virtual display (layout flags) · #39 `ISS_LAT_TRACE` ·
#40 faster connect · #41 precise scroll `0x17`. Closed unmerged: #27 (AVC backlog guard for slow viewers; `--max-bitrate` covers it,
  branch `fix/avc-backlog-guard` kept).

Useful env switches: `ISS_HWACCEL`, `ISS_HW_SLOW_FALLBACK=0`, `ISS_PREFER_CUDA=0`, `ISS_HW_FRAMES_ON_GPU`, `ISS_RCTL=0`,
`ISS_RCTL_BWE_KBPS`, `ISS_VIDEO_RTCP_ON_CTRL=1` (old routing), `ISS_OFFER_RESEND=1` + `ISS_REOFFER_ON_MSG1=0` (old offer
flow), `ISS_TILES_PER_FRAME`, `ISS_NALU_DUMP=<file>` (record HEVC for offline replay), `ISS_DECODE_DELAY_MS` (simulate a slow
decoder), `ISS_PRESENT_COALESCE_MS=<ms>` (per-frame present deadline; 0 = present per tile, the old behaviour),
`ISS_CUDA_DOWNLOAD=default` (PyAV's download of CUDA frames instead of the pinned copy),
`ISS_FRAME_RECOVERY=0` (HEVC damage/recovery from the old IDR-size rule instead of `is_corrupt` frames),
`ISS_HIDE_DAMAGED=0` (show damaged HEVC frames instead of keeping the tile's last good picture).
`ISS_AAC_SBR_CONFIG=1` (configure libfdk-aac with Apple's SBR AudioSpecificConfig, the old behaviour).

## Tried and dropped (don't retry without a new idea)
- **Encoder frame rate above 60:** a 120/144 Hz virtual display and the `com.apple.VideoConference` keys
  `forceVideoStreamFramerate`/`forceEncodeFramerate` (not even read) leave the encoder at 60. ScreensharingAgent decides it:
  "encoder support 60FPS", "both viewer and server support 60 FPS on stream 1" — a 30/60 capability, nothing above.
- **Bitrate above 60 Mbit/s:** offer tiers and `forceVideoStreamTxMaxBitrate` change nothing; `forceVideoStreamTxMinBitrate`
  forces the encoder (min 90/max 100 → ~89 Mbit/s, QP ~20 vs ~25; min 100/max 150 → ~145, QP 12; the RTX 2080's NVDEC and a
  Ryzen 5 3600 in software both top out ~55–57 fps there). The Mac then starts at full rate, iss loses ~150 packets in its
  first second, and still areas stay corrupt (no re-encode). The user judged 90–100 Mbit/s not better. Dropped.
- **H.264 keep-decoding after a loss** (skip the lost picture, go on): heavy smearing and colour shifts. Dropped.
- **Connected UDP sockets:** on macOS `sendto()` with an address on a connected socket fails (EISCONN; ~12 call sites), and ICMP
  unreachables become receive errors; the gain is marginal.
- **Lossless framebuffer path** (zlib/CopyRect instead of video): works, but the host delivers only ~4–8 fps for a changing
  window.
- **Offer bitrate tiers / TMMBR** to *raise* the bitrate: no effect (RCTL is the lever). Tiers can still *lower* the cap (see
  Rate control above).
- **ProRes:** AVConference has a ProRes codec type, but how to request it (payload/params) and whether the screen-sharing
  profile supports it are unknown. Apple's viewer offers only HEVC and H.264.

## Open topics
1. **Encoder frame rate above 60:** settled — not possible (see Tried and dropped).
2. **Host switching to its physical display** on a login at the Mac during a curtain session: #37 re-requests the
   virtual display when the layout header flags (+0x0e) lose bit 0 (virtual), retrying every 15 s (3 tries per switch) while
   they stay physical, and offers once bit 0 is back ([REMOTEX] rfc §8.4). iss runs High Performance, which sends `0x5` (Standard
   sends `0x4`, bit 0 clear); `0x5` was verified on the M4. Still needs a live check with a real login at the Mac. Per the notes,
   some logins make the agent **close the connection** instead (not handled; it isn't a #37 failure), and unlocking or waking
   changes nothing. Once the flags word is known, the older display-id guess is off, so a switch that keeps bit 0 set (e.g. the
   old "long idle" report, never reproduced) would go unnoticed; watch for it.
3. **Software decode cost after RCTL:** at ~56 Mbit/s, 4K60 HEVC software decode needs ~6 cores. Weak CPUs rely on the
   delay feedback to make the host back off; not measured on a weak machine.
4. **RCTL's second byte:** meaning unknown, sent as 0 (accepted). The loss bits and BWE add nothing (see facts above).
5. **ProRes:** revisit only if a way to request it becomes known (see above).
6. **From the remotex notes (rfc §8.11, §10.3, §10.7, §10.8), not built yet:**
   - 1-tile (H.264) streams: LTR acks (payload = RTP timestamp) + PSFB FMT 2 refresh requests instead of IDRs. 4 tiles have no LTR.
   - Two virtual displays.
7. **Free-threaded Python 3.14t:** iss runs headless on it with the GIL off (every dependency has a 3.14t wheel except
   `aioquic`, so no browser frontend). On the RTX 2080 box it saved ~4 % CPU and gained no fps: iss's Python threads total
   ~45–60 % of a core (CUDA or software decode), so the GIL isn't a bottleneck there. Measure on a slow CPU before supporting it.
8. **H.264 loss repair by refresh pictures** (prototype `~/claude/iss-tools/avc-refresh-prototype-2026-10-14.patch`, not in the
   repo): the Mac accepts LTR for H.264 (answer `ltrpEnabled = 1` when the 123 bank carries `LTR;`), takes APP name 5 acks with
   the RTP timestamp and PSFB FMT 2 requests, and marks refresh pictures with RTP extension profile `0x9321` (bit `0x0020`,
   as HEVC's `0x9331`). Pictures decode clean, but on 1-tile streams recovery pictures come at most once a second, so it gains
   little over keyframes; the real lever on lossy links is fewer packets per picture (`--max-bitrate`). Low priority.
9. **Display-side latency** (render, vsync): iss's own pipeline adds ~3 ms p50 / ~9 ms p95 (`ISS_LAT_TRACE=1`); the
   desktop window's part is unmeasured (needs a desktop session).
