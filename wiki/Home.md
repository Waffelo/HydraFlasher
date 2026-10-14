# HydraFlasher user guide

HydraFlasher is a desktop app for reading, erasing, writing and verifying SPI flash chips. It drives [flashprog](https://flashprog.org/) (or flashrom) with a Raspberry Pi Pico running the HydraFlasher firmware. One Pico serves **four sockets** at once, so you can work through several chips without re-plugging the programmer.

<p align="center">
  <img src="https://raw.githubusercontent.com/Waffelo/HydraFlasher/main/screenshot.png" alt="HydraFlasher main window" width="560">
</p>

## What it does

- **Live socket detection.** Every socket is scanned once a second, so inserting or removing a chip shows up immediately, with its type and unique ID.
- **All the usual operations** (probe, read, verify, erase, write) on one socket or on all of them.
- **Broadcast writes.** Identical chips are erased and written all at once, then verified one by one.
- **Job queue.** Start jobs on other sockets while one is running; they run in turn.
- **Fast checks on the Pico.** Verifies and blank checks run as a CRC32 on the Pico itself, instead of reading the whole chip back over USB.
- **Partial writes.** Only the 64 KiB blocks your image actually uses (plus any that aren't blank) are erased and written.
- **Production mode.** Flash batch after batch with one key press, or automatically when all sockets are refilled.
- **Traceability.** Every write and verify is logged to a CSV file with the chip's unique ID and the image CRC.
- **Safeguards.** Some examples:
  - unsupported chips are refused;
  - a chip override that doesn't fit the inserted chip is reset;
  - duplicate jobs are ignored;
  - only one instance can run at a time.

## Pages

| Page | What's in it |
| --- | --- |
| [Installation](Installation) | Requirements, serial port access, building and flashing the firmware, pinout |
| [Getting Started](Getting-Started) | First launch and a tour of the window |
| [Operations](Operations) | Probe, read, verify, erase, write, and what each one does step by step |
| [Job Queue](Job-Queue) | Working on several sockets while a job runs |
| [Chip Detection](Chip-Detection) | Live scan, chip definitions, chip override, unknown and unsupported chips |
| [Production Mode](Production-Mode) | Batch flashing and the flash log |
| [Use Cases](Use-Cases) | Step-by-step recipes for common jobs |
| [Troubleshooting](Troubleshooting) | Error messages and what to do about them |
| [Reference](Reference) | Keyboard shortcuts, settings, files, LEDs, pinout |
