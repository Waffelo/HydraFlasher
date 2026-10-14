# Troubleshooting

## Starting up

**"Hydra Flasher is already running"**
Another HydraFlasher window is open, possibly on another desktop or hidden behind other windows. Use that one, or close it. On Linux, `pgrep -af HydraFlasher` lists running instances.

**HydraFlasher asks to locate flashprog/flashrom**
Neither was found on your `PATH`. Install flashprog (recommended) or flashrom, or point HydraFlasher at the binary once; the choice is remembered.

**`○ no programmer` stays red**
The Pico isn't detected. Check the cable (some cables are charge-only), and check that the Pico runs the HydraFlasher firmware (see [Installation](Installation#the-firmware)). Press ↻ to list all serial ports.

**"Sudo required" every time**
Your user can't open the serial port. Add yourself to `dialout` (or `uucp` on Arch) and log in again. See [Installation](Installation#serial-port-access-linux).

## Errors during a job

**`Programmer error: command 0x22: NAK`** (or another command number, or "no answer")
The Pico's answer wasn't what HydraFlasher expected. Almost always, another program is using the same serial port at the same time: a second HydraFlasher (prevented in current versions), a serial terminal, or `flashprog` started by hand. Close the other program, unplug and replug the Pico, and try again.

**"Multiple flash chip definitions match"**
Several flashprog definitions fit the chip. Pick the one printed on the chip in the dialog. See [Chip Detection](Chip-Detection#chip-definitions).

**Card shows `unsupported chip`**
flashprog has no definition for this chip. Reseat the chip first: a badly seated chip can return a wrong ID. See [Chip Detection](Chip-Detection#unsupported-chips).

**"Probe required – No chip known on CS*n*"**
HydraFlasher doesn't know the chip in that socket yet. Insert one, wait a second for the scan, or press **probe**. Under sudo the live scan is off, so probe first.

**"Size mismatch"**
The image isn't the same size as the chip. Truncating or padding writes a copy, never your original. If you're not sure the image belongs to this chip, cancel.

**"The chips in the sockets are not the same type!" / ALL card says `chip types differ`**
A broadcast (ALL) write or erase needs identical chips. Use single sockets (and the [queue](Job-Queue)) for mixed chips.

**A queued job failed with "No chip on CS*n* – nothing written."** or **"Image is … but CS*n* is … – nothing written."**
When the job's turn came, the socket was empty or held a chip of a different size. Insert the right chip and start the job again.

**Verify fails (`✕ MISMATCH`)**
1. Write the chip again.
2. Lower the **speed** (for example 8M). Long wires and adapters limit the SPI clock.
3. Reseat the chip and check the socket's contacts.
4. Make sure you verify against the image you wrote (a `_padded` or `_truncated` copy, if one was made).

**A write was stopped or failed halfway**
The chip is partially programmed. Write it again.

## Messages that are not errors

| Message | Meaning |
| --- | --- |
| `Probing is not available during queue run.` | Probing waits until the queue is done; the sockets are scanned between jobs anyway |
| `Already running or queued: …` | That exact job is already running or waiting, so it wasn't added twice |
| `Chip override … doesn't fit (…) – reset to Auto-detect` | A chip was inserted that the override doesn't fit, so the override was cleared |
| `Identify unknown chip on CS*n*` | An automatic probe that learns a new chip type |
| `Wait for the running/queued jobs first.` | Production mode can't be turned on while jobs run or wait |

## Still stuck?

The log shows every flashprog command HydraFlasher ran (lines starting with `$`). Running that command yourself in a terminal often gives more detail. Close HydraFlasher first, so the two don't share the port.
