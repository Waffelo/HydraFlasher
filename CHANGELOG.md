# App changelog

Each app release (`app-v<version>` tag) publishes the section of its version as the GitHub release notes, next to the Windows installer, the portable `.exe` and the Linux AppImage; CI refuses a tag without one. Keep the headings as `## <version> · <date>`. The Pico firmware has its own changelog: [firmware/CHANGELOG.md](firmware/CHANGELOG.md).

## Unreleased

### Fixed
- **Linux AppImage: the buttons that open a file did nothing.** The AppImage's bundled libraries leaked into every program it started, and KDE's file dialog (kdialog) couldn't start with them. Programs started from the app (file dialog, flashprog, notify-send…) now use the system's libraries, and if the desktop's file dialog still fails, HydraFlasher opens its own.
- **Notifications stayed on screen** until clicked (AppImage): they close after 6 seconds now.
- **Status LEDs under sudo** (when your user can't open the serial port) didn't work in the packaged app.

### Changed
- A successful probe (or the automatic identify of a new chip) no longer shows a notification; a failed one still does.
- Notifications only appear while the HydraFlasher window isn't focused, and show the hydra icon.
- File dialogs on Linux: KDE gets its own dialog (kdialog) even when zenity is installed, with the image filter, and above the HydraFlasher window. The window keeps drawing and scanning while a dialog is open.
- Settings show the git commit of packaged builds next to the version.
- The AppImage needs FUSE (`fuse3`): the 1.0.0 notes said `libfuse2`. Without it, start it with `--appimage-extract-and-run`.

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
