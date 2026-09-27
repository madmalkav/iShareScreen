# CLAUDE.md

Python client for macOS Screen Sharing **High Performance** mode: RFB 003.889 control over TCP, HEVC 4:4:4 (or H.264 4:2:0)
video + AAC-ELD audio over SRTP/UDP, rendered with wgpu. The protocol reference is `docs/apple_vnc_rfc.md` (media transport §10,
rate control §10.8.1, liveness §10.8.2; facts from the remotex notes are marked [REMOTEX]); the user-facing codec and decoder
guide is the README's "Codecs and decoders" section.

## Layout
- `proxy/session.py`: the session core. Handshake + start burst, UDP drain/process threads, RTCP (RR, FIR/PLI, NACK, LTR ack,
  RCTL), SSRC adoption, stall/liveness logic, the profile log line.
- `proxy/protocol/`:
  - `negotiation.py`: handshake, `0x1c` offer/answer, degenerate-answer re-query loop.
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
- **Rate control:** the host encoder follows AVConference's rate controller, bounded 20–60 Mbit/s by the screen-sharing profile
  (the offer's bitrate tiers don't change it). It only moves on the viewer's **RCTL** reports: an RTCP APP packet named
  `RCTL` with a 20-byte payload, sent alone (not compound), every 50 ms on the video leg. The one-way-delay field is what
  moves the target. With RCTL the target sits at ~58 Mbit/s; without it, it stays at ~20.8 (#20). TMMBR and the BWE field
  are ignored.
- **4 tiles:** Apple's default `tilesPerFrame=4` = 4 horizontal strips = 4 consecutive SSRCs, cross-tile references, DONL in
  every payload. `tilesPerFrame=1` has no DONL (depacketizer must handle both), makes the host send only ~57.5 fps, and saves no
  decode CPU. Keep 4.
- **Start:** the host's first `0x1c` answer often has a zero canvas. iss re-sends the offer every 0.2 s until one has a canvas;
  each re-send makes the host start and tear down a stream with new SSRCs. SSRC adoption must pick the group that is **still
  sending** (last-packet time per SSRC), not the lowest-numbered one (#21). Since #21 one "falling behind" resync appears just
  before `session ready`; it's harmless.
- **Resize:** after `0x1d`, re-arm with a 1×1 incremental FramebufferUpdateRequest, never full-screen (a full-size read racing
  a shrink can crash the host's ScreensharingAgent, per remotex) (#24).
- **Liveness:** no video + no host RTCP for 10 s → warning; 48 s → session ends (Apple's limit). Armed only after host RTCP has
  been seen (#25).
- **Refresh:** the host accepts any virtual-display refresh in the `0x1d` mode (30/60/90/120), but its **encoder frame rate stays
  60** (`vcMediaStreamFramerate = 60`). 30 Hz halves the pictures and decode cost; >60 gives nothing.
- **Curtain mode** (default) gives the virtual display at the requested size and locks the host's local screen while the session
  runs; the host stays locked after the session ends. `--no-curtain` shares the physical display (e.g. native 5120×2160).
  The host may switch the session to its physical display on a local login/idle (see open topics).
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

## Merged work (fork), for context
#1–3 honest HW probe/binding · #4 PyAV/VAAPI docs · #5 resize stale-tile crash · #6 overload resync · #7 pipeline 60→95 fps ·
#8 Wayland window size · #9 GPU-kept HW frames · #10 visible H.264 fallback · #11 CUDA first on NVIDIA · #12 window fits screen ·
#13 `--cursor video` · #14 `--hwaccel` + measured H.264 fallback + README guide · #15 macOS HW 4:4:4 probe · #16 `shown_rates` ·
#17 README Intel Mac H.264 · #18 clean Ctrl-C · #19 README bitrate note · #20 RCTL · #21 live SSRC adoption · #22 RTCP per leg ·
#23 RTCP demux · #24 1×1 resize request · #25 RTCP liveness.

Useful env switches: `ISS_HWACCEL`, `ISS_HW_SLOW_FALLBACK=0`, `ISS_PREFER_CUDA=0`, `ISS_HW_FRAMES_ON_GPU`, `ISS_RCTL=0`,
`ISS_RCTL_BWE_KBPS`, `ISS_VIDEO_RTCP_ON_CTRL=1` (old routing), `ISS_TILES_PER_FRAME`, `ISS_NALU_DUMP=<file>` (record HEVC for
offline replay), `ISS_DECODE_DELAY_MS` (simulate a slow decoder).

## Tried and dropped (don't retry without a new idea)
- **One `0x1c` offer at a time** (wait for the complete answer instead of re-sending): the stream is already running when the
  answer arrives, and the start burst then decodes mid-sequence. Flush + keyframe request + a 0.5 s burst cap still
  corrupted 3/6 starts. The re-send loop + #21 gives clean starts.
- **Connected UDP sockets:** on macOS `sendto()` with an address on a connected socket fails (EISCONN; ~12 call sites), and ICMP
  unreachables become receive errors; the gain is marginal.
- **Lossless framebuffer path** (zlib/CopyRect instead of video): works, but the host delivers only ~4–8 fps for a changing
  window.
- **Offer bitrate tiers / TMMBR** to raise the bitrate: no effect (RCTL is the lever).
- **ProRes:** AVConference has a ProRes codec type, but how to request it (payload/params) and whether the screen-sharing
  profile supports it are unknown. Apple's viewer offers only HEVC and H.264.

## Open topics
1. **Encoder frame rate above 60:** find what sets `encode frame rate 60`. First as a diagnostic, host-side
   `defaults write com.apple.VideoConference forceVideoStreamFramerate|forceEncodeFramerate` (undo:
   `defaults delete com.apple.VideoConference`); then look for a viewer-side offer field. At the 60 Mbit/s cap, 120 fps halves
   the bits per picture.
2. **`--refresh-rate 60|30` option:** a proven decode-cost lever (−44 % software CPU, same per-picture quality); not built.
   Could let weak/Intel viewers keep HEVC 4:4:4 at 30 fps instead of H.264.
3. **Host switching to its physical display** (on local login or long idle) during a curtain session. Branch
   `fix/return-to-virtual-display` (pushed, not merged) re-requests the virtual display after an unrequested switch; it needs
   live confirmation (a local login or long idle during a session) before merging. Branch `fix/hide-startup-gray` (pushed,
   not merged) holds tiles at decoder start until a keyframe; since #21 startups are mostly clean, so check whether it's still
   needed. The layout's session-state word ([REMOTEX] reading, rfc §8.4) may be a better trigger than inferring the switch.
4. **Software decode cost after RCTL:** at ~56 Mbit/s, 4K60 HEVC software decode needs ~6 cores. Weak CPUs rely on the
   delay feedback to make the host back off; not measured on a weak machine.
5. **Host rate-control inputs not sent:** RCTL's second byte (meaning unknown) and loss/BWE fields are sent as 0/60000.
   Loss reporting might matter on lossy links; untested.
6. **ProRes:** revisit only if a way to request it becomes known (see above).
7. **`AutoFrameBufferUpdate` (`0x09`) body:** static analysis reads a `selected_screen` word where [REMOTEX] measured a push
   interval in µs (rfc §8.11). iss arms nothing and polls 1×1 every tx tick, which keeps cursor shapes flowing; resolve before
   relying on `0x09`.
