# Reference

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| Ctrl+P | Probe |
| Ctrl+R | Read |
| Ctrl+V | Verify |
| Ctrl+E | Erase |
| Ctrl+W | Write |
| Enter | Run batch (production mode) |
| Esc | Stop |
| Alt+1 to Alt+4 | Select socket CS0 to CS3 |
| Alt+A | Select ALL (broadcast) |
| Ctrl+L | Clear the log |
| Ctrl+Q | Quit |

Shortcuts are ignored while you're typing in a text field (for example the port box), so Ctrl+V still pastes there. Click anywhere outside the field to use them again.

## Files

| What | Linux / macOS | Windows |
| --- | --- | --- |
| Settings | `~/.config/hydra_flasher.json` | `~/.hydra_flasher.json` |
| Flash log | `~/.local/share/hydra-flasher/flash_log.csv` | `%APPDATA%\HydraFlasher\flash_log.csv` |
| Instance lock | `~/.local/share/hydra-flasher/hydra_flasher.lock` | `%APPDATA%\HydraFlasher\hydra_flasher.lock` |

The settings file remembers:

- the port, the speed and the selected socket;
- the toggles: force, progress, skip verify, verify on Pico, used blocks only and auto-start;
- the flashprog path, the last folder and the recent files;
- the chip names learned from probes.

The chip override and production mode are **not** remembered; they start as Auto-detect and off.

## Status LEDs on the Pico

| LED (GPIO) | Meaning |
| --- | --- |
| Orange (GP11) | A job is running |
| Green (GP12) | The last job succeeded |
| Red (GP13) | The last job failed |
| All off | Idle |

## Pinout

| Signal | Pico GPIO |
| --- | --- |
| SCK | GP2 |
| MOSI | GP3 |
| MISO | GP4 (pull-up) |
| CS0 to CS5 | GP5 to GP10 (the app uses CS0 to CS3) |
| CS6 | Not a pin: broadcast, selects all CS lines at once |
| LEDs | GP11 / GP12 / GP13 |

## Under the hood

- Probe, read, erase, write and flashprog verify run the `flashprog` command; the socket is selected with the serprog option `cs=<n>`.
- The live scan, verify on Pico, the blank check and the LEDs talk to the Pico directly over the same serial port, using custom serprog commands (`0x20` LED, `0x21` reboot to bootloader, `0x22` CRC32). HydraFlasher makes sure only one of the two uses the port at any moment.
- If the firmware doesn't support the CRC32 command, or the port needs sudo, verify on Pico and used blocks only quietly fall back to plain flashprog.
