# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

HydraFlasher: a customtkinter desktop GUI (`HydraFlasher.py`, single file) that drives `flashprog`/`flashrom` against a Raspberry Pi Pico running the modified pico-serprog firmware in `firmware/`. One Pico serves several SPI flash sockets via multiple chip-select lines.

`OLD_Ver/` and `serprog_fork/` are gitignored leftovers. Don't edit them; the live firmware source is `firmware/`.

**A user-visible change isn't finished until the user-guide wiki is updated and pushed.** See [User guide wiki](#user-guide-wiki-keep-it-in-sync) below.

## Commands

```sh
pip install -r requirements.txt     # customtkinter, pyserial, optional plyer
python3 HydraFlasher.py             # Python 3.8+ with Tk; needs flashprog or flashrom on PATH

cd firmware && ./build.sh           # -> firmware/build/pico_serprog.uf2 (needs PICO_SDK_PATH or pico-sdk/ in/next to firmware/)
cd firmware && ./build.sh flash     # build + copy to a Pico in BOOTSEL; reboots a running HydraFlasher Pico into BOOTSEL via 0x21 first
```

There are no tests or linters. CI (`.github/workflows/firmware.yml`) only builds the firmware, against pico-sdk 1.5.0.

## Architecture

**Two paths to the hardware, one serial port.** The GUI talks to the Pico in two ways:
- **flashprog subprocess** (`_build_command` / `_exec`) handles probe, read, erase, write and verify. The socket is chosen with the serprog `cs=<n>` parameter.
- **Direct serprog** (`Programmer` class, pyserial) covers what flashprog can't do: the live socket scan every second (JEDEC ID plus Winbond unique ID), LED control and the on-device CRC32.

Both paths hold `self._port_lock`, so only one of them has the port open at any time. Any new direct-access code must go through the `_programmer()` context manager, never open the port on its own.

The port is auto-selected by `_score_port`, which prefers the HydraFlasher USB VID `0xCAFE` over the stock Pico VID `0x2E8A`, plus descriptor keywords.

**Chip-select numbering.** The firmware has 6 CS pins (CS0 to CS5, on GP5 to GP10; `NUM_CS_PINS`/`CS_BROADCAST` in `firmware/main.c`). The full pinout is in `README.md`. `CS_ALL = 6` is a broadcast that pulls every CS line low at once. The GUI shows `NUM_SLOTS = 4` sockets.

**Broadcast gotcha.** During a CS6 broadcast every chip drives MISO at the same time, so reads over the shared bus are garbage. "All" operations (`_start_write_all` and friends) therefore:
1. erase and write over CS6, erasing first so all chips hold identical contents, because flashprog reads before it writes;
2. verify each socket individually, never over CS6.

All chips must be the same type (`_require_same_type`).

**Custom serprog commands** are defined in `firmware/serprog.h`:
- `0x20` SET_LED (mirrored as `S_CMD_SET_LED` in `HydraFlasher.py`)
- `0x21` BOOTSEL (no GUI constant; only `firmware/build.sh flash` sends it)
- `0x22` CRC32 (mirrored as `S_CMD_CRC32`)

A new command touches three places: `serprog.h`, the hand-built `cmdmap` bitmap in `firmware/main.c` (commands ≥ 32 go in the second word), and the GUI constant. If it's missing from `cmdmap`, the GUI never sees it. The GUI detects whether the firmware supports them through `S_CMD_Q_CMDMAP` (`self._fw_caps`). The fast "on Pico" verify and the partial-write/blank-check path need CRC32 support and direct (non-sudo) port access (`_can_use_pico`). Without either, they fall back to plain flashprog.

**Partial writes.** The image is split into 64 KiB erase blocks (`block_ranges`). Blocks the image doesn't use are blank-checked on the Pico through CRC32. Only the used blocks plus any non-blank unused blocks get erased and written, via flashprog layout args. The GUI skips this and does a full write once more than 90% of the blocks are in use.

**Threading.** Tk runs on the main thread. Jobs run in a worker thread started by `_start_job`, which snapshots all Tk variables into `self._job` first so the worker never touches Tk. Workers return `(ok, msg)` and update the UI only through `self.after(0, ...)`. Raise `JobAborted` or `ProgrammerError` to stop a job early.

**Job queue.** There's only one bus, so jobs never run in parallel. A job started while another runs is queued with its own settings snapshot (`self._queue`, `_launch`). After each job, `_queue_continue` runs one socket scan, because the live scan is paused during jobs, and then starts the next job. Because of that, single-socket write/verify skip the "chip known / size" checks at click time when queueing (`_queue_defers_check`), and the worker checks them when the job starts (`_socket_error`). Production mode doesn't queue. When the live scan sees a chip that isn't in `jedec_cache` yet, it resets any chip override and starts `_start_identify`, a probe without `-c` that goes ahead of queued jobs. A job whose `key` (default: its title; file operations include the full path) matches the running or a queued job is refused as a duplicate. Operations listed in `QUEUE_BLOCKED` (currently probe) are refused while a job runs or waits. Their buttons are greyed but stay clickable, so a click can log the reason. To block another operation, add it to `QUEUE_BLOCKED` and call `_queue_blocked("<name>")` at the top of its entry point.

**Chip identity.** `jedec_cache` (persisted in the config) maps a scanned JEDEC ID to flashprog's definition names. It only learns from probes run without `-c`, because a forced definition hides the other matches. It never learns flashprog's catch-all "unknown … SPI chip" definitions (`is_generic_chip`); those chips show as "unsupported" and are refused. A manufacturer byte of `00`/`FF` counts as an empty socket or a garbled read. The chip override is reset to Auto-detect when the scan sees a chip it doesn't fit.

**Single instance.** `__main__` holds a lock file (`acquire_instance_lock`, in the data dir). Two instances would talk to the same Pico, and the per-process `_port_lock` can't protect against that.

**sudo.** On Linux, if the user can't open the port, flashprog runs under `sudo -S` with the password piped in through stdin. On-Pico features are then disabled, because the direct port isn't accessible.

**Persisted state:**
- Config: `~/.config/hydra_flasher.json` (Windows: `~/.hydra_flasher.json`)
- CSV flash log: `~/.local/share/hydra-flasher/flash_log.csv` (Windows: `%APPDATA%\HydraFlasher`)

**Firmware TinyUSB patch.** `firmware/tinyusb_patched/cdc_device.c` is a copy of TinyUSB 0.18.0's CDC driver with one change: a separate IN buffer size. `CMakeLists.txt` filters the SDK's copy out of `tinyusb_device_base` and compiles this one instead.

## User guide wiki (keep it in sync!)

The user guide (features, use cases, troubleshooting) is the GitHub wiki: https://github.com/Waffelo/HydraFlasher/wiki. It's a separate repo (`https://github.com/Waffelo/HydraFlasher.wiki.git`, branch `master`) and isn't checked out in this repo. The user requires it to stay current: **every user-visible change must update the affected wiki pages, in the same task, without being asked.** User-visible means, for example, new or changed buttons, settings, messages, shortcuts, file locations or behaviour.

For every change:
1. Clone the wiki repo into a temporary folder (not into this repo).
2. Update every affected page:
   - the feature pages: Operations, Job Queue, Chip Detection, Production Mode;
   - `Getting-Started.md`: card states and the settings table;
   - `Troubleshooting.md`: the message tables;
   - `Reference.md`: shortcuts, files and saved settings;
   - `Use-Cases.md` and `Home.md`, if a feature is added or removed.
3. Keep links valid. Pages link to each other as `[text](Page-Name#anchor)`, and the anchors come from headings.
4. Commit with a message naming the change, push to `master`, and delete the temporary clone.
5. In the report, list the pages you changed. If nothing user-visible changed, say so.

Pushing uses the HTTPS credentials stored in git (SSH ports are blocked from Claude sessions). If the push fails, report it; don't skip the update silently.

## License

The GUI is GPL-3.0. The firmware keeps its upstream licenses (`firmware/COPYING`, `firmware/COPYING.CC-BY-SA`).
