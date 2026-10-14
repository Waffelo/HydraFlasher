# Getting started

1. Plug in the Pico. The top right corner shows `● /dev/ttyACM0` in green once it's found, or `○ no programmer` in red if it isn't.
2. Insert a chip. Its socket card switches from "empty" to the chip name within a second.
3. Click the socket card to make it the target. The card is outlined in orange.
4. **Back up first:** press **read** and save the dump somewhere safe.
5. Press **write**, pick the image and confirm.

## The window, top to bottom

### Sockets

One card per socket (CS0 to CS3), plus **ALL** for broadcast. Click a card, or press Alt+1 to Alt+4 or Alt+A, to choose the target for the buttons below. The light and the text show the socket's state:

| Card shows | Meaning |
| --- | --- |
| `not probed` | Nothing known yet (programmer not scanned) |
| `empty · no chip detected` | No chip in the socket |
| chip name and size | Chip identified and ready |
| `… · probe to identify` (yellow) | A chip HydraFlasher hasn't identified yet. It's probed automatically, see [Chip Detection](Chip-Detection). |
| `N defs match` (yellow) | Several flashprog chip definitions fit this chip. You're asked which one when you start a job. |
| `unsupported chip` (red) | flashprog has no definition for this chip, so it can't be used |
| `working…` (pulsing) | A job is using this socket right now |
| `queued #n` | A job for this socket is waiting in the [queue](Job-Queue) |
| `✓ verified` / `✕ failed` | Result of the last write or verify |

The **ALL** card shows how many identical chips are inserted, or `chip types differ` if a broadcast isn't possible.

While a job runs or waits, a red line under the cards reads **"DO NOT remove or add chips while tasks run."**

### Settings

| Setting | What it does |
| --- | --- |
| **port** / ↻ | Serial port of the Pico. Picked automatically; ↻ lists all serial ports in the log and picks the best match again. |
| **speed** | SPI clock, 4M to 48M. 16M is a safe default; lower it if reads fail on long wires. |
| **chip** / × | Chip override: force one flashprog chip definition (`-c`). × resets it to Auto-detect. See [Chip Detection](Chip-Detection#chip-override). |
| **force -f** | Passes `-f` to flashprog (forces operations it would normally refuse). Only for experts. |
| **progress** | Shows flashprog's progress and the progress bar |
| **skip verify -n** | Don't verify after writing. Faster, but you won't know if the write worked. |
| **verify on Pico** | Verify with a CRC32 on the Pico instead of reading the chip back over USB (much faster) |
| **used blocks only** | Only erase and write the parts of the chip the image uses (see [Operations](Operations#write)) |
| **production mode** | See [Production Mode](Production-Mode) |
| **auto-start when all sockets refilled** | Production mode: start the next batch automatically |
| **recent files ▸** | Write or verify a recently used image again |
| **flash log ▸** | Opens the CSV log of every write and verify |

### Buttons

**probe · read · verify · erase · write** act on the selected target. **■ stop** (or Esc) stops the running job. Each operation is explained in [Operations](Operations).

### Status line and log

The status line shows the running job, its step, elapsed time, the number of queued jobs and a progress bar. The log below lists every job with its steps, the exact flashprog command and the result. Ctrl+L clears the log.
