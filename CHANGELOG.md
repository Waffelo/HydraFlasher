# App changelog

Each app release (`app-v<version>` tag) publishes the section of its version as the GitHub release notes, next to the Windows installer, the portable `.exe` and the Linux AppImage; CI refuses a tag without one. Keep the headings as `## <version> · <date>`. The Pico firmware has its own changelog: [firmware/CHANGELOG.md](firmware/CHANGELOG.md).

## Unreleased

### Changed
- **One notification when the queue is done**, not one per job: a queue of four writes ends with "All done · 4 jobs finished", or "Done, 1 of 4 failed" naming the failed job. The taskbar highlight waits for the end too.
- **Settings show the copyright and license** under the credit line: © 2026 Jakub Heger · GPL-3.0-or-later · comes with no warranty. The README says the same, with a reminder to keep a backup before flashing.
- **The Windows `.exe` files carry version information** (version, description, copyright in their Properties), and so does the installer.
- **Smaller packages:** the drag-and-drop library (tkdnd) is bundled only for the package's own platform, not for all twelve.

### Fixed
- **`THIRD-PARTY-LICENSES.txt` was missing tkdnd's license** (the native drag-and-drop library inside tkinterdnd2), and on Linux listed two unrelated 32-bit system packages.

## 1.1.0 · 2026-10-16

**The Linux AppImage works properly, and the flash log becomes a page in the app.** The AppImage's file buttons and notifications are fixed, a finished job lights up the taskbar, the flash log has its own history page, and the app is ready for firmware 1.3.

### Downloads
- **Windows:** `HydraFlasher-Setup-1.1.0.exe` (installing it over 1.0.0 updates it) or `HydraFlasher-1.1.0-portable.exe`.
- **Linux:** `HydraFlasher-1.1.0-x86_64.AppImage`: `chmod +x`, then run it. It needs **FUSE** (`sudo apt install fuse3`; the 1.0.0 notes wrongly said `libfuse2`), or start it with `--appimage-extract-and-run`. Desktop notifications need `notify-send` (`libnotify-bin`).
- **Firmware 1.3** is released the same day: settings → firmware → **latest**.

### New
- **Flash history page** (**flash log ▸**): every write and verify as a table, newest first, with socket names and green/red results; filter all / failures / today; **export CSV…**, **open folder**, and **clear…**, which archives the old log as `flash_log-<date>.csv` instead of deleting it.
- **The taskbar entry lights up** when a job ends while you're in another window (Linux: until you switch back; Windows: it flashes), whether notifications are on or not.
- **Firmware 1.3's USB identity** (`2E8A:000A`) is recognized, also when the Pico restarts during a firmware update.
- **Licenses:** the packages include the license texts of everything bundled with HydraFlasher (Python, Tcl/Tk, customtkinter, pyserial, …); settings → version → **licenses** opens them.
- Settings show the git commit a package was built from next to the version.

### Changed
- **Notifications** only appear while the HydraFlasher window isn't focused, close after 6 seconds and show the hydra icon. A successful probe (or the automatic identify of a new chip) no longer notifies; a failed one still does. On Linux they go through `notify-send`.
- **Padding or truncating an image no longer leaves a `_padded`/`_truncated` file next to it:** the copy is temporary and deleted once the queue is done.
- **File dialogs on Linux:** KDE gets its own dialog (kdialog) even when zenity is installed, with the image filter, in front of the HydraFlasher window. The window keeps drawing and scanning while a dialog is open.
- Builds use pinned tools: appimagetool 1.9.1 with its checksum, GitHub Actions pinned to commits.

### Fixed
- **Linux AppImage: the buttons that open a file did nothing.** The AppImage's bundled libraries leaked into every program it started, and KDE's file dialog couldn't start with them. Programs started from the app (file dialog, flashprog, notify-send…) now use the system's libraries, and if the desktop's file dialog still fails, HydraFlasher opens its own.
- **Linux AppImage: notifications stayed on screen** until clicked.
- **Status LEDs under sudo** (when your user can't open the serial port) didn't work in the packages.
- **Firmware downloads are checked** against the SHA-256 checksum GitHub lists for the release file, and only files from this repository's releases are downloaded. A damaged download is refused instead of installed.
- **The settings file can't be left half-written** by a crash or a full disk. An unreadable one is kept as `hydra_flasher.json.broken` instead of being overwritten with defaults.
- A file name starting with `=`, `+`, `-` or `@` is written to the flash log with a leading `'`, so spreadsheets don't run it as a formula.

## 1.0.0 · 2026-10-15

**First packaged release.** HydraFlasher now comes as a Windows installer, a portable Windows `.exe` and a Linux AppImage, so Python isn't needed any more.

### Downloads
- **Windows:** `HydraFlasher-Setup-1.0.0.exe` installs it with a Start-menu entry and an uninstaller. `HydraFlasher-1.0.0-portable.exe` runs without installing (it unpacks itself at every start, which takes a few seconds). Windows may warn about an unknown publisher: **More info → Run anyway**.
- **Linux:** `HydraFlasher-1.0.0-x86_64.AppImage`: make it executable (`chmod +x`) and run it. Needs a distribution from 2022 or newer (glibc 2.35), and on some `libfuse2`. For the serial port, your user must be in the `dialout` group (`uucp` on Arch).
- **flashprog** (or flashrom) is still needed separately: HydraFlasher looks for it on the first start and asks for it if it's missing. See [Installation](https://github.com/Waffelo/HydraFlasher/wiki/Installation).

### Highlights
- **Up to 10 sockets**, counted automatically from pull-up resistors on the CS lines (firmware 1.2), or set by hand; sockets can be named.
- **Settings page** (⚙ or Ctrl+,): socket count and names, live scan on/off with interval and speed, status LEDs, flashprog binary, notifications, resetting learned data, app version.
- **Firmware updates from the app**: shows the Pico's firmware and the newest release, installs the latest release or any `.uf2` (also dropped on the window), mounts the Pico's bootloader drive on Linux.
- **Drag and drop**: an image on a socket card writes it, on the log verifies it.
- **Time left** for jobs that ran before, and for the whole queue.
- **Job queue, one-by-one and broadcast writes, production mode** with a CSV flash log, partial writes and verification on the Pico.
- **Light and dark mode**; the hydra as the window and app icon.

### Fixed
- The window no longer freezes on a Pico that doesn't answer (other or hung firmware): it's reported as `not responding`, and the firmware update asks for BOOTSEL.
- The settings page scrolls with the mouse wheel on Linux.
- Flaky or missing pull-up resistors no longer make the socket count switch back and forth at every reconnect.
