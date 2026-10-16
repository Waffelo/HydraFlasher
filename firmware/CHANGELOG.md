# Firmware changelog

Each release (`fw-v<version>` tag) publishes the section of its version as the GitHub release notes; CI refuses a tag without one. Keep the headings as `## <version> · <date>`.

To update a Pico running HydraFlasher firmware: in the app, **settings → firmware → latest**. A blank Pico, or one with other firmware: hold **BOOTSEL** while plugging it in, then the same (see the [user guide](https://github.com/Waffelo/HydraFlasher/wiki/Settings#updating-the-firmware)).

## Unreleased (1.3)

**Its own USB identity, done properly.** The Pico now uses Raspberry Pi's USB vendor ID with the product ID Raspberry Pi assigns to Pico SDK serial devices, instead of TinyUSB's example vendor ID `0xCAFE`, which isn't meant for distributed devices.

### Changed
- **USB ID `2E8A:000A`** (was `CAFE:4001`). The product name stays "HydraFlasher", which is how the app finds the Pico.
- **Serial number from the Pico's unique flash ID** (was `621621` on every board), so two Picos get different names in `/dev/serial/by-id/` and on Windows.

### Compatibility
- Needs an app from the same day or later to be selected automatically after the update (older apps still find it by its name, with a lower score).
- On Windows, the Pico gets a new COM port number once, as it's a new device to Windows.

## 1.2 · 2026-10-15

**Socket detection.** The Pico can now tell which of its chip-select lines lead to a socket, so the app sets its socket count by itself.

### New
- **`S_CMD_Q_CS_WIRED` (`0x24`)**: returns a bitmap of the CS lines that have a pull-up resistor. Each CS pin is briefly read as an input against the RP2040's internal pull-down (50–80 kΩ); a line with an external 10 kΩ pull-up to 3V3 reads high, an open one low. The pin is set high again before it drives, so no chip sees a select glitch.

### Hardware (optional, recommended)
- Add a **10 kΩ resistor from each socket's /CS line to 3V3** (Pico pin 36). Values from 4.7 kΩ to 10 kΩ work; 22 kΩ or more is too weak. Keep the resistors' common side on 3V3: tied only to each other they seem to work while the Pico runs, but nothing holds the chips deselected while it starts or resets.
- Boards without the resistors keep working: the app then uses the socket count set by hand.

### In the app
- **settings → sockets → auto** (the default) shows exactly the wired sockets; a socket between wired ones without a resistor shows as `not wired` and is skipped. Without pull-ups the app falls back to a manual count; **detect again** re-checks.

### Compatibility
- Works with older versions of the app (they don't ask for the wiring). Everything from 1.1 stays the same.

## 1.1 · 2026-10-15

**Ten sockets, firmware updates from the app.** First release published on GitHub.

### New
- **10 chip selects**: CS0–CS9 on GP5–GP14 (was 6: CS0–CS5 on GP5–GP10). The broadcast that selects every socket at once moved from index 6 to **10**.
- **`S_CMD_Q_NUM_CS` (`0x23`)**: reports the number of CS lines, which is also the broadcast index. The app uses it to work with both this and older firmware.
- **Version**: the standard program-name query (`S_CMD_Q_PGMNAME`) answers `HydraFlasher 1.1` (before: `pico-serprog`), so the app can show the version and offer updates.

### Changed — hardware
- **Status LEDs moved to GP16 / GP17 / GP18** (Pico pins 21 / 22 / 24), the other side of the board. **Move them when updating from older firmware**: GP11–GP13 are chip selects now, and LEDs left there just stay lit.
- Sockets CS0–CS5 keep their pins.

### Fixed
- Builds against pico-sdk 2.2.0, whose TinyUSB 0.18.0 the patched CDC driver (`tinyusb_patched/`) needs; CI couldn't build the firmware before.

## Before 1.1

Unversioned builds (programmer name `pico-serprog`): six chip selects (CS0–CS5 on GP5–GP10) with the broadcast on index 6, status LEDs on GP11 / GP12 / GP13, and the custom commands `0x20` (LED), `0x21` (reboot into the bootloader) and `0x22` (CRC32 of a flash range).
