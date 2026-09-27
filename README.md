# iShareScreen

Cross-platform Python client for Apple's macOS Screen Sharing **High
Performance** mode (HEVC RExt 4:4:4 over UDP/SRTP). Renders the host
Mac's screen in a native wgpu window with hardware decode where
available.

## Setup

### On the host Mac (the target you want to view)

1. Any modern macOS that supports the Screen Sharing app's High
   Performance mode (Apple Silicon, recent macOS).
2. *System Settings → General → Sharing → **Screen Sharing*** → toggle on.

### On the viewing machine

#### macOS

1. Install Python 3.10 or later (from [python.org](https://www.python.org/downloads/),
   `brew install python`, or `uv python install 3.13`).
2. Install iShareScreen:
   ```sh
   pip install git+https://github.com/renegadelink/iShareScreen.git
   ```

#### Windows

1. Install Python 3.10 or later from [python.org](https://www.python.org/downloads/)
   (the installer includes pip; check "Add python.exe to PATH" during install).
2. Open PowerShell or cmd and run:
   ```sh
   pip install git+https://github.com/renegadelink/iShareScreen.git
   ```

3. (Optional, only if you want audio) install **libfdk-aac** — Apple's
   PT=101 audio uses AAC-ELD-SBR, which Windows Media Foundation can't
   decode. The cleanest source is [MSYS2](https://www.msys2.org):
   ```sh
   pacman -Sy --noconfirm mingw-w64-x86_64-fdk-aac
   ```
   This drops `libfdk-aac-2.dll` at `C:\msys64\mingw64\bin\`, which iss
   searches automatically. If you already have [scoop](https://scoop.sh),
   `scoop install msys2` followed by the same `pacman` command also
   works — iss looks under `%USERPROFILE%\scoop\apps\msys2\current\mingw64\bin`
   too. Without libfdk-aac, video works as normal and audio is silently
   skipped.

#### Linux (Debian / Ubuntu)

1. Install Python and the system libraries that the GPU + window stack
   need (Vulkan loader, OpenGL, X11 / Wayland surfaces, PortAudio,
   AAC-ELD-SBR audio decoder):
   ```sh
   sudo apt install python3 python3-pip python3-venv \
       libvulkan1 libgl1 libegl1 \
       libxrandr2 libxinerama1 libxcursor1 libxi6 \
       libportaudio2 libfdk-aac2 \
       xclip
   ```
   `libfdk-aac2` is only used for audio; if you skip it, video still
   works and audio is silently disabled.

   `xclip` enables bidirectional clipboard sync with the macOS host. On
   Wayland desktops use `wl-clipboard` instead. If neither is installed
   iss logs a one-line warning at startup and runs without clipboard
   sync — everything else still works.
2. (Optional, for hardware HEVC decode on Intel GPUs) install vaapi:
   ```sh
   sudo apt install vainfo intel-media-va-driver-non-free
   ```
   For AMD, swap the driver: `sudo apt install mesa-va-drivers`. NVIDIA
   GPUs reach VAAPI through `nvidia-vaapi-driver`.
3. Install iShareScreen (in a venv recommended):
   ```sh
   python3 -m venv ~/.venvs/iss
   ~/.venvs/iss/bin/pip install git+https://github.com/renegadelink/iShareScreen.git
   ~/.venvs/iss/bin/iss     # or symlink to ~/.local/bin
   ```

**Hardware decode needs PyAV built against your system FFmpeg.** iss
decodes through PyAV, and the prebuilt PyAV wheel from PyPI bundles its own
FFmpeg that is built *without* VAAPI — so even with the drivers above and a
system `ffmpeg -hwaccel vaapi` that works, iss decodes in software (it logs
a warning saying so at startup). To use VAAPI, install with PyAV compiled
from source instead:
```sh
sudo apt install pkg-config gcc python3-dev \
    libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev \
    libavfilter-dev libswscale-dev libswresample-dev
~/.venvs/iss/bin/pip install --no-binary av git+https://github.com/renegadelink/iShareScreen.git
```
(On an existing install: `~/.venvs/iss/bin/pip install --force-reinstall
--no-binary av av`.) Arch-based distros ship the FFmpeg headers in the
`ffmpeg` package itself. To check, this should list `vaapi`:
```sh
~/.venvs/iss/bin/python -c "from av.codec.hwaccel import hwdevices_available as h; print(h())"
```

For Fedora / Arch / openSUSE, translate the apt package names with your
distro's package manager (most are named the same or very close).

### Firewall

iss connects to the host Mac over **TCP 5900** (control) plus two UDP
flows: **5900** (audio + RTCP) and **5901** (video). It sends from both
UDP ports during connect. If a firewall still blocks the stream, allow
UDP 5900–5901 inbound.

## Usage

```sh
iss
```

Opens the terminal UI: a connect form for host / username / password
(masked) and resolution, then a live session view with per-tile fps and
loss, throughput, UDP queue health, and a log tail. The actual screen
streams in a separate window. Last-session values pre-fill the form on
the next launch; passwords are never persisted.

Any flag accepted by `iss --headless` also pre-fills the form, so
launchers can do:

```sh
iss --host mac.local -u me --advertise 1920x1080 --no-curtain
```

Useful keys in the live session: **f** force IDR refresh (fixes the
rare gray patch the auto-recovery doesn't catch), **r** reconnect,
**d** disconnect, **Ctrl-B** save a bug-report snapshot, **q** quit.

For CI / scripted use:

```sh
echo "$PASSWORD" | iss --headless --host mac.local -u me --password-stdin --auto-quit-secs 30
```

## Codecs and decoders

The Mac can send the screen in one of two codecs, and iss can decode each one
in several ways. The defaults pick the best combination for your machine;
these options exist for when they don't, or for comparing.

### Codec (`--codec`)

| codec | picture | cost to decode |
|---|---|---|
| **HEVC 4:4:4** (`--codec hevc`) | Full colour resolution: sharpest text, including coloured text. What Apple's own viewer uses. | Needs a GPU that decodes HEVC 4:4:4 (Apple silicon, NVIDIA RTX 20 and newer, Intel 11th gen and newer), or a fast CPU. |
| **H.264 4:2:0** (`--codec avc`) | Colour at half resolution: coloured text (red/orange especially) and thin coloured lines look softer. | Decoded in hardware by almost any GPU from the last decade, and cheap in software. |

`--codec auto` (the default) uses HEVC 4:4:4 when a hardware HEVC 4:4:4 decoder
passes a quick test at startup, and H.264 otherwise. It prints a warning when it
falls back to H.264, because the picture is lower quality. The browser frontend
always uses H.264 (browsers decode it themselves).

### Hardware decode API (`--hwaccel`)

| value | meaning |
|---|---|
| `auto` (default) | The usual API for your system: Linux VAAPI then CUDA (CUDA first when VAAPI runs on NVIDIA's driver), Windows D3D11VA/D3D12VA, macOS VideoToolbox. A hardware H.264 decoder that can't keep up is switched to software automatically (see below). |
| `software` | CPU decode only. With `--codec auto` this picks H.264, which is the fast codec in software. |
| `vaapi`, `cuda`, `d3d11va`, `d3d12va`, `dxva2`, `videotoolbox` | Use only this API; if it can't open, iss decodes in software and logs why. The automatic software switch is off. |

The connect form has the same choice as **Hardware decode**, listing only the APIs
your iss install supports. `iss --list-decoders` shows the decoders and the
hardware APIs available on this machine.

### Decoder (`--decoder`)

Normally `auto`. Pinning one is mostly for debugging:

| decoder | codec | where | notes |
|---|---|---|---|
| `vt-hevc444` | HEVC 4:4:4 | macOS | Native VideoToolbox; the macOS default. |
| `libav-hevc444` | HEVC 4:4:4 | all | FFmpeg with the `--hwaccel` API; the Linux/Windows default. |
| `qsv-hevc444` | HEVC 4:4:4 | Windows, Linux | Intel Quick Sync; used where the generic path lacks 4:4:4. |
| `libav-hevc444-sw` | HEVC 4:4:4 | all | Software; slow at high resolutions. |
| `libav-avc420` | H.264 4:2:0 | all | FFmpeg with the `--hwaccel` API, software if none. |

### Which combination to use

| your machine | recommended | notes |
|---|---|---|
| Apple silicon Mac | defaults (HEVC, VideoToolbox) | |
| Intel Mac | defaults (H.264) | Most Intel Macs can't decode HEVC 4:4:4 in hardware, so auto picks H.264 (it asks VideoToolbox for a hardware 4:4:4 decoder at startup). On a 2015 MacBook Pro, HEVC ran at ~28 fps with gray patches, while H.264 ran at ~60 fps (in software there, as VideoToolbox H.264 failed to start at that size). |
| Linux/Windows, NVIDIA RTX 20 or newer | defaults (HEVC, CUDA/VAAPI or D3D11VA) | Tested on an RTX 2080: 4K and 5120×2160 at ~60–70 fps with a fraction of a CPU core. |
| NVIDIA GTX 10 or older | defaults (H.264) | These can't decode HEVC 4:4:4. At 4K, H.264 through NVIDIA's VA driver is slower than software (each frame is copied back from the GPU), so iss switches to software when it measures that. |
| Intel 11th gen or newer | defaults | Should get HEVC 4:4:4 in hardware via VAAPI or Quick Sync (untested here). |
| AMD | defaults (H.264) | AMD GPUs don't decode HEVC 4:4:4 as far as we know, so auto picks H.264 in hardware (untested here). |
| older Intel, or no GPU | defaults (H.264), or `--hwaccel software` | |

### Automatic switch to software (H.264)

In `--hwaccel auto` mode, iss times each hardware H.264 decode. If the decoder is
busy more than 75% of the time for three 2-second windows in a row while the picture
is moving, it switches the session to software on the next keyframe and logs
the numbers. Example: an RTX 2080 decoding 4K H.264 through VAAPI was busy 93%
of the time (22 ms per frame) and displayed ~14 fps; after the switch, software
showed a steady 51 fps (all the Mac sent). Set `ISS_HW_SLOW_FALLBACK=0`, or pick
an API with `--hwaccel`, to keep hardware.

### Troubleshooting

- **Choppy picture or gray patches:** the decoder can't keep up. Try
  `--codec avc`, a smaller `--advertise`, or a different `--hwaccel`.
- **Text is blurry for a moment after scrolling, then sharpens:** that's the
  Mac's video encoder, not iss. It sends roughly 20 Mbit/s at most whatever
  the resolution, so a smaller `--advertise` (e.g. `1600x900 --hidpi on`) gives
  it more bits per pixel and cleaner scrolling, at the cost of a smaller desktop.
- **Coloured text looks soft:** you're on H.264 (4:2:0). Use HEVC if your
  hardware allows it.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

This is an independent reverse-engineering of a publicly-documented
network protocol. No Apple source code, headers, or symbols are
included.
