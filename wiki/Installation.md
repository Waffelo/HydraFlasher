# Installation

You need three things: the **app** (Python), **flashprog or flashrom**, and a **Raspberry Pi Pico running the HydraFlasher firmware**.

## The app

Requirements: Python 3.8 or newer with Tk, and `flashprog` or `flashrom`.

```sh
git clone https://github.com/Waffelo/HydraFlasher.git
cd HydraFlasher
pip install -r requirements.txt     # customtkinter, pyserial, plyer (optional: desktop notifications)
python3 HydraFlasher.py
```

On first launch HydraFlasher looks for `flashprog`, then `flashrom`, on your `PATH` and in the usual install folders. If it finds neither, it asks you to locate the binary once and remembers the choice.

Only one HydraFlasher window can run at a time. Starting a second one shows "Hydra Flasher is already running", because two windows would talk to the same Pico and garble each other's commands.

### Serial port access (Linux)

Your user needs read/write access to the Pico's serial port (usually `/dev/ttyACM0`). Add yourself to the right group, then log out and back in:

```sh
sudo usermod -aG dialout $USER     # Debian, Ubuntu, Fedora
sudo usermod -aG uucp $USER        # Arch
```

Without access, HydraFlasher asks for your sudo password and runs flashprog through `sudo`. That works, but the features that talk to the Pico directly are then turned off: the live socket scan (so chips only show up after a probe), verify on Pico and used blocks only. Fixing the group is strongly recommended.

## The firmware

The Pico runs a modified [pico-serprog](https://github.com/opensensor/pico-serprog) with six chip-select lines, status LEDs and an on-device CRC32. Building needs the [Pico SDK](https://github.com/raspberrypi/pico-sdk): either set `PICO_SDK_PATH`, or put a `pico-sdk` checkout in or next to `firmware/`.

```sh
cd firmware
./build.sh          # builds build/pico_serprog.uf2
./build.sh flash    # builds, then copies it to a Pico in BOOTSEL mode
```

`./build.sh flash` reboots a Pico that already runs HydraFlasher firmware into its bootloader by itself. For a new Pico, hold the **BOOTSEL** button while plugging it in, so it shows up as the `RPI-RP2` drive. Every push to GitHub that touches `firmware/` also builds the `.uf2` in GitHub Actions.

### Pinout

| Signal | Pico GPIO |
| --- | --- |
| SCK | GP2 |
| MOSI | GP3 |
| MISO | GP4 (pull-up) |
| CS0 to CS5 | GP5 to GP10 |
| LED busy / pass / fail | GP11 / GP12 / GP13 |

All sockets share SCK, MOSI and MISO; each socket has its own CS line. The app uses sockets CS0 to CS3. CS6 is a broadcast that selects every socket at once (see [Operations](Operations#all-sockets-broadcast)).

The firmware uses its own USB vendor ID (`0xCAFE`), so HydraFlasher finds and selects the right serial port automatically.
