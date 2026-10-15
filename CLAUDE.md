# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

HydraFlasher: a customtkinter desktop GUI (`HydraFlasher.py`, single file) that drives `flashprog`/`flashrom` against a Raspberry Pi Pico running the modified pico-serprog firmware in `firmware/`. One Pico serves several SPI flash sockets via multiple chip-select lines.

`OLD_Ver/` and `serprog_fork/` are gitignored leftovers. Don't edit them; the live firmware source is `firmware/`.

**A user-visible change isn't finished until the user-guide wiki is updated and pushed.** See [User guide wiki](#user-guide-wiki-keep-it-in-sync) below.

## Commands

```sh
pip install -r requirements.txt     # customtkinter, pyserial, optional plyer and tkinterdnd2
python3 HydraFlasher.py             # Python 3.8+ with Tk; needs flashprog or flashrom on PATH

cd firmware && ./build.sh           # -> firmware/build/pico_serprog.uf2 (needs PICO_SDK_PATH or pico-sdk/ in/next to firmware/)
cd firmware && ./build.sh flash     # build + copy to a Pico in BOOTSEL; reboots a running HydraFlasher Pico into BOOTSEL via 0x21 first
```

```sh
tests/run_all.sh                    # GUI tests with a fake Pico and fake flashprog, on a hidden display
VISIBLE=1 tests/run_all.sh          # same, but the windows open on your desktop
tests/run_all.sh run_onebyone.py    # a single test
python3 tools/run_readme_shot.py . screenshot.png   # regenerate the README screenshot
python3 tools/draw_pinout.py firmware/pinout.png    # regenerate the pinout drawing
```

Each test (`tests/run_*.py`) starts the real GUI, swaps `Programmer` for a fake with scripted IDs and flashprog for a `tests/fakeflash*.py` script that logs its command lines, then prints `PASS`/`FAIL` lines. A test must point `CONFIG_FILE`, `DATA_DIR` and `FLASH_LOG` at its own folder, and seed `jedec_cache` for any chip it doesn't want auto-identified (otherwise the scan starts an extra probe job). `run_all.sh` re-runs itself on a hidden display (`xvfb-run`, else a headless `kwin_wayland --virtual --xwayland`), because test windows would take the user's focus; ad-hoc GUI scripts and screenshots must be wrapped the same way. It copies `tests/*.py` into a fresh temp dir per test (120 s timeout) and passes the repo path as the first argument; a test with zero `PASS` lines counts as failed. For a new test, copy the closest existing one: fakes, then steps chained with `app.after`, then `app.destroy()`. A few tests take screenshots with ImageMagick's `import`; those checks are skipped without it. Run tests after behaviour changes, and add one for new features. There are no linters. CI (`.github/workflows/firmware.yml`) only builds the firmware, against pico-sdk 2.2.0 (its TinyUSB 0.18.0 must match `firmware/tinyusb_patched/`; older SDKs fail to compile it).

## Architecture

**Two paths to the hardware, one serial port.** The GUI talks to the Pico in two ways:
- **flashprog subprocess** (`_build_command` / `_exec`) handles probe, read, erase, write and verify. The socket is chosen with the serprog `cs=<n>` parameter.
- **Direct serprog** (`Programmer` class, pyserial) covers what flashprog can't do: the live socket scan every second (JEDEC ID plus Winbond unique ID), LED control and the on-device CRC32.

Both paths hold `self._port_lock`, so only one of them has the port open at any time. Any new direct-access code must go through the `_programmer()` context manager, never open the port on its own.

The port is auto-selected by `_score_port`, which prefers the HydraFlasher USB VID `0xCAFE` over the stock Pico VID `0x2E8A`, plus descriptor keywords.

**Chip-select numbering.** The firmware has 10 CS pins (CS0 to CS9 on GP5 to GP14, LEDs on GP16 to GP18; `NUM_CS_PINS` in `firmware/main.c`), and the broadcast that pulls every CS line low at once is the index after the last pin (`CS_BROADCAST = NUM_CS_PINS` = 10). Older firmware had 6 pins with the broadcast on 6, and no `0x23`. The full pinout is in `README.md`; `tools/draw_pinout.py` draws `firmware/pinout.png`. Inside the GUI the broadcast is always `CS_ALL = MAX_SLOTS` (10), above every socket number, so `cs < self.num_slots` means "a single socket". Only `_build_command` turns it into the firmware's index: `job["broadcast"]`, from `self._fw_cs`, which the first scan after connecting asks for (`0x23`; `LEGACY_CS_PINS` = 6 without it; unknown, e.g. under sudo, assumes the current firmware). Sockets at or above `_fw_cs` are never scanned or probed (`job["usable"]`; on old firmware `cs=6` *is* the broadcast), their cards say so, and `_start_job` refuses them. The GUI shows `self.num_slots` sockets: `"sockets"` in the config, 1 to `MAX_SLOTS` (10), default `DEFAULT_SLOTS` (4), changed live from the settings page (`_set_socket_count`: rebuilds the socket row in `_sockets_box`, `_build_cs_map`, `_bind_socket_keys`, resizes the per-socket lists; only while idle); socket 10 is Alt+0. With one socket `has_all` is false: no ALL card, target or Alt+A, so `self.cards` has no `CS_ALL` key.

**Broadcast gotcha.** During a broadcast every chip drives MISO at the same time, so reads over the shared bus are garbage. "All" operations (`_start_write_all` and friends) therefore:
1. erase and write over the broadcast, erasing first so all chips hold identical contents, because flashprog reads before it writes;
2. verify each socket individually, never over the broadcast.

All chips must be the same type (`_require_same_type`).

**Custom serprog commands** are defined in `firmware/serprog.h`:
- `0x20` SET_LED (mirrored as `S_CMD_SET_LED` in `HydraFlasher.py`)
- `0x21` BOOTSEL (mirrored as `S_CMD_BOOTSEL`; the firmware update and `firmware/build.sh flash` send it)
- `0x22` CRC32 (mirrored as `S_CMD_CRC32`)
- `0x23` Q_NUM_CS, the number of CS lines = the broadcast index (mirrored as `S_CMD_Q_NUM_CS`)
- `0x24` Q_CS_WIRED (firmware 1.2), 2-byte bitmap of CS lines with an external pull-up, i.e. wired to a socket: each pin is read as an input with the RP2040's internal pull-down, which a 10 kΩ pull-up to 3V3 overrides (`detect_wired_cs` in `main.c`). Not used by the GUI yet.

A new command touches three places: `serprog.h`, the hand-built `cmdmap` bitmap in `firmware/main.c` (commands ≥ 32 go in the second word), and the GUI constant. If it's missing from `cmdmap`, the GUI never sees it. The GUI detects whether the firmware supports them through `S_CMD_Q_CMDMAP` (`self._fw_caps`). The fast "on Pico" verify and the partial-write/blank-check path need CRC32 support and direct (non-sudo) port access (`_can_use_pico`). Without either, they fall back to plain flashprog.

**Firmware version.** `FW_VERSION` in `firmware/main.c` is reported through the standard `S_CMD_Q_PGMNAME` as `"HydraFlasher <version>"` (16 bytes at most); older firmware answers `pico-serprog`. The scan stores it as `self._fw_name` (`fw_version()` parses it). Pushing a tag `fw-v<version>` makes CI (`.github/workflows/firmware.yml`) check it against `FW_VERSION` and publish a GitHub Release with `hydraflasher-fw-<version>.uf2`; `latest_fw_release()` finds the newest one.

**Settings page.** The gear in the header (or Ctrl+,) swaps the whole page below the header for `self._settings_view` (built on first open): `_open_settings` forgets the main view, status line, log and footer with their `pack_info()`, `_close_settings` re-packs them through plain `tkinter.Pack` (customtkinter's `pack()` would scale the saved paddings a second time on HiDPI). Confirming a firmware install goes back to the main view first, so its steps (and a "hold BOOTSEL" request) are visible in the log. The page is a `CTkScrollableFrame`, which only scrolls on `<MouseWheel>`; `_settings_wheel` adds X11's `<Button-4>/<Button-5>`. Its bottom shows `APP_VERSION` (semantic versioning, separate from the firmware's `FW_VERSION`; bump it with a release) plus the git commit (`app_commit()`), and the "Vibed by Waffelo" credit linking to `REPO_URL`. `_refresh_settings()` fills it in and returns at once while it's closed, so call it wherever shown state changes. Operation shortcuts are ignored while it's open, and Esc goes back when no job runs. Settings apply immediately (no save button). The firmware update (`_start_firmware_update`) is a normal job started with `needs_port=False`: `read_uf2()` checks the file (UF2 blocks, RP2040 family, embedded version) first; then 0x21 (or a prompt to hold BOOTSEL), `find_rp2_drive()` (findmnt plus `udisksctl mount` on Linux, removable drive letters on Windows, `/Volumes` on macOS), the copy, `_wait_for_pico()` and `_read_fw_identity()`, which compares the version. A `.uf2` dropped anywhere goes this way, never to a flash chip.

**Socket names.** `socket_names` (config, by CS number, 16 characters at most) label the cards (name as title, CS number as tag) and every socket mention in the log and in messages: use `_sock(cs)` ("CS1 EC"), `_socks()` for lists and `_sock_col()` for aligned tables (`result_row` and `_slot_rows` take the padded label). Plain `CS<n>` stays where it's data, not text: the CSV flash log's `socket` column, the target menu labels (saved in the config), read file names.

**Live scan and LEDs settings.** `scan_var` turns the every-interval scan off; `_scan_tick` then only asks the firmware once (`_scan_worker(..., chips=False)`), and `_queue_continue` still scans between jobs. Interval and SPI speed come from `scan_interval_var` / `scan_speed_var` (`SCAN_INTERVALS`, `SCAN_SPEEDS`); until the firmware is identified the tick stays at 1 s. With `leds_var` off `_set_led` does nothing; `force=True` switches them off once.

**Partial writes.** The image is split into 64 KiB erase blocks (`block_ranges`). Blocks the image doesn't use are blank-checked on the Pico through CRC32. Only the used blocks plus any non-blank unused blocks get erased and written, via flashprog layout args. The GUI skips this and does a full write once more than 90% of the blocks are in use (`PARTIAL_MAX_USED`).

**Threading.** Tk runs on the main thread. Jobs run in a worker thread started by `_start_job`, which snapshots all Tk variables into `self._job` first so the worker never touches Tk. Workers return `(ok, msg)` and update the UI only through `self.after(0, ...)`. Raise `JobAborted` or `ProgrammerError` to stop a job early.

**Job queue.** There's only one bus, so jobs never run in parallel. A job started while another runs is queued with its own settings snapshot (`self._queue`, `_launch`). After each job, `_queue_continue` runs one socket scan, because the live scan is paused during jobs, and then starts the next job. Because of that, single-socket write/verify skip the "chip known / size" checks at click time when queueing (`_queue_defers_check`), and the worker checks them when the job starts (`_socket_error`). Production mode doesn't queue.

**One-by-one.** The `one_by_one_var` toggle is only shown while ALL is the target. With it on, write and erase on ALL don't broadcast; erase queues one `_start_erase` per socket. Normally it queues one `_start_write` per usable socket; a production batch instead runs one `_start_write_each` job, which loops over the sockets with the shared `_write_socket`. The chips may then differ: each socket is checked against the image on its own. `_one_by_one()` and `_target_label()` are the helpers to use.

**Chip definition per job.** `_start_job` stores `_pick_chip_definition(cs)` in the job, not the global override. It returns the override if it fits that socket's chip, else a definition picked earlier for that JEDEC ID (`_chip_choice`), else auto-detect if only one definition matches, else it asks. That way a queue of mixed chip types never sends one chip's `-c` to another. Within a job, `_build_command` asks `_socket_chip(cs)` for each socket, because jobs over several sockets (probe/read/verify all, `_start_write_each`) meet chips the job's definition doesn't fit, and flashprog with a wrong `-c` reports no chip at all. Only runs that actually auto-detected (`_chip_used`) teach the JEDEC cache. When the live scan sees a chip that isn't in `jedec_cache` yet, it resets any chip override and starts `_start_identify`, a probe without `-c` that goes ahead of queued jobs. A job whose `key` (default: its title; file operations include the full path) matches the running or a queued job is refused as a duplicate. Operations listed in `QUEUE_BLOCKED` (currently probe and the firmware update) are refused while a job runs or waits. Their buttons are greyed but stay clickable, so a click can log the reason. To block another operation, add it to `QUEUE_BLOCKED` and call `_queue_blocked("<name>")` at the top of its entry point. `_refresh_op_buttons` decides which buttons are greyed, for any reason. Besides the queue rules, write and erase are greyed while the target is ALL and `_broadcast_write_error()` returns a reason (chip types differ, unsupported chip, no chips). With one-by-one, only "no chips" counts. It runs from `_refresh_sockets`, `_refresh_selection` and on every busy change.

**Time estimates.** After each successful job, `_learn_time` averages its duration into `job_times` (persisted in the config) under the signatures from `_eta_keys`: operation, sockets, chip, image or chip size, SPI speed and the pico/partial/no-verify flags, plus a more specific key that also names the image file. `_launch` looks one up (`_estimate`) and `_tick` shows `~… left` and the queue total. Probes and identify jobs aren't estimated.

**Drag and drop.** Optional, through `tkinterdnd2` (`DND_AVAILABLE`, `_load_dnd`). `_register_drop_targets` registers the socket cards (write to that socket), the log panel (verify the selected target) and the UI root (a hint only); tkdnd picks the nearest registered parent of the widget under the pointer. `_handle_drop` then goes through the same `_write_allowed` / `_write_file` / `_verify_file` path as the buttons, after the tkdnd callback has returned, so the confirmation dialog doesn't block the file manager's drag.

**Chip identity.** `jedec_cache` (persisted in the config) maps a scanned JEDEC ID to flashprog's definition names. It only learns from probes run without `-c`, because a forced definition hides the other matches. It never learns flashprog's catch-all "unknown … SPI chip" definitions (`is_generic_chip`); those chips show as "unsupported" and are refused. A manufacturer byte of `00`/`FF` counts as an empty socket or a garbled read. The chip override is reset to Auto-detect when the scan sees a chip it doesn't fit.

**Unresponsive Pico.** A board on a HydraFlasher-looking port may not answer (older or other firmware, or hung: TinyUSB's example VID `0xCAFE` is shared). Such a board can take 5 s to open (setup requests time out) and, with unsent bytes, 30 s to close, all with `_port_lock` held. So: always close ports with `close_serial()` (drops unsent bytes first); a write timeout or `NoAnswer` marks the port via `_mark_unresponsive` (log note once, header "not responding", scans paused `UNRESPONSIVE_PAUSE_S`, jobs refused except the firmware update, which skips 0x21 and asks for BOOTSEL); any answer clears it. Nothing on the UI thread may open the port: `_set_led` hands the newest state to a background thread (`_led_worker`), except `wait=True` when quitting. A Pico in bootloader mode is noticed by `rp2_drive_present()` (cheap enough for the UI thread).

**Single instance.** `__main__` holds a lock file (`acquire_instance_lock`, in the data dir). Two instances would talk to the same Pico, and the per-process `_port_lock` can't protect against that.

**Log output.** Jobs log as a tree: `log_job` (`● title`), then `log_step` and `log_detail`, ending in a `● ✓`/`● ✕` result. Anything outside a job (events, refusals, choices) goes through `log_note(text, kind)`, never a bare `log_line`: it adds the `●`, a `⚠`/`✕` icon for warning/error, and a blank line after a finished job. Socket tables use `_slot_rows`, per-socket results use `result_row`, so the columns line up. Use only symbols the log font has (1 cell wide). A fallback glyph is wider and shifts the line in the GUI, though a pasted copy looks fine; `⎿` was one (+6 px), which is why the tree uses `╰`.

**Theme (light/dark).** Every `T[...]` colour is a `(light, dark)` pair, built from `THEME_LIGHT` and `THEME_DARK`. Pass pairs straight to customtkinter widgets, which switch with `ctk.set_appearance_mode`. `blend()` mixes pairs per mode. Plain Tk widgets (the socket light canvas, the logo label, log text tags and selection, the log menu) need `tk_color(pair)` for the current value, and `_apply_theme()` re-colours them on a switch; add any new plain-Tk widget there. The sun/moon switch in the header (icon plus "light mode"/"dark mode", the mode a click switches to) changes the mode, and the choice is saved as `theme` (default dark). Its icons are drawn, not font glyphs: `THEME_ICONS` holds smooth alpha masks that `tinted_icon()` colours at runtime. Nerd Font "Mono" squeezes icons into one cell, which made a font sun read as a dot.

**sudo.** On Linux, if the user can't open the port, flashprog runs under `sudo -S` with the password piped in through stdin. On-Pico features are then disabled, because the direct port isn't accessible.

**Persisted state:**
- Config: `~/.config/hydra_flasher.json` (Windows: `~/.hydra_flasher.json`), including `jedec_cache`, `job_times`, `sockets`, `socket_names`, `live_scan`, `scan_interval`, `scan_speed`, `leds` and `notifications`
- Downloaded firmware releases: `<data dir>/firmware/`
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
