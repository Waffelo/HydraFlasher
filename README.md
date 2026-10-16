<img src="logo-mark.png" alt="HydraFlasher logo" align="right" width="120">

# HydraFlasher
  [![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757?style=flat&logo=claude&logoColor=white)](https://claude.com/claude-code)
  ![License](https://img.shields.io/github/license/Waffelo/HydraFlasher)
  ![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)
  ![Raspberry Pi Pico](https://img.shields.io/badge/hardware-RP2040%20Pico-A22846?logo=raspberrypi&logoColor=white)
  [![Static Badge](https://img.shields.io/badge/Github-Wiki-blue?style=flat&logo=github&link=https%3A%2F%2Fgithub.com%2FWaffelo%2FHydraFlasher%2Fwiki)](https://github.com/Waffelo/HydraFlasher/wiki)

  
A desktop GUI for flashing SPI flash chips with [flashprog](https://flashprog.org/)
(or flashrom) through a Raspberry Pi Pico running a modified
[pico-serprog](https://github.com/opensensor/pico-serprog) firmware.

The firmware adds up to ten chip-select lines, so one Pico can serve several
sockets: HydraFlasher scans them live, flashes and verifies each chip, and logs
every run.


**Note that:** This is a full vibe coded project for my work. I do not prefer vibe coding, this is just a SNCA project so I can make my work easier and faster..

<p align="center">
  <img src="screenshot.png" alt="HydraFlasher main window" width="621">
</p>

**User guide:** see the [wiki](https://github.com/Waffelo/HydraFlasher/wiki): features, use cases and troubleshooting.

## Layout

| Path | What |
| --- | --- |
| `HydraFlasher.py` | The GUI (Python, customtkinter) |
| `firmware/` | Pico serprog firmware with multi-CS, status LEDs and on-device CRC32 |
| `packaging/` | Builds the Windows installer, portable `.exe` and Linux AppImage |

## Download

Get the newest **[release](https://github.com/Waffelo/HydraFlasher/releases/latest)**:

| System | File |
| --- | --- |
| Windows | `HydraFlasher-Setup-<version>.exe` (installer) or `HydraFlasher-<version>-portable.exe` (no install) |
| Linux | `HydraFlasher-<version>-x86_64.AppImage`: `chmod +x`, then run it. Needs FUSE (`sudo apt install fuse3` on Debian/Ubuntu), or start it with `--appimage-extract-and-run` |

You still need `flashprog` (or `flashrom`); HydraFlasher asks for it on the first
start if it can't find it. The Pico firmware has its own releases (`fw-v…`), which
the app installs from its settings page.

## Running from source

Needs Python 3.8+ with Tk, and `flashprog` or `flashrom` on the `PATH`.

```sh
pip install -r requirements.txt
python3 HydraFlasher.py
```

On Linux your user needs access to the Pico's serial port (usually the
`dialout` or `uucp` group).

## Firmware

Pinout:

| Signal | GPIO |
| --- | --- |
| SCK | GP2 |
| MOSI | GP3 |
| MISO | GP4 (pull-up) |
| CS0 to CS9 | GP5 to GP14 |
| LED busy / pass / fail | GP16 / GP17 / GP18 |

Build with the Pico SDK (`PICO_SDK_PATH` set, or a `pico-sdk` checkout in or
next to `firmware/`):

```sh
cd firmware
./build.sh          # builds build/pico_serprog.uf2
./build.sh flash    # builds, then copies it to a Pico in BOOTSEL mode
```

GitHub Actions also builds the `.uf2` on every push that touches `firmware/`. Pushing a tag
`fw-v<version>` (matching `FW_VERSION` in `firmware/main.c`) publishes it as a GitHub Release,
which the app's settings page can download and install on the Pico.

## License

GPL-3.0, see `LICENSE`. The firmware keeps its upstream licenses
(`firmware/COPYING`, `firmware/COPYING.CC-BY-SA`). The downloadable packages
include the licenses of the software bundled with the app in
`THIRD-PARTY-LICENSES.txt` (settings → version → **licenses**).
