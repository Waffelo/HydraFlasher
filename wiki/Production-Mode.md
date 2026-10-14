# Production mode

For flashing the same image onto many chips: pick the image once, then every batch runs with one key press, or by itself when you refill the sockets.

## Starting

1. Select the target: **ALL** to flash all four sockets per batch (broadcast), or a single socket.
2. Tick **production mode** and pick the production image.
3. Confirm. From now on, batches run **without asking again**.

The **write** button becomes **▶ run batch ⏎**, and the footer shows the image, the batch number and the pass/fail counts.

## Running batches

1. Insert the chips.
2. Press **Enter** (or **▶ run batch**). The batch erases, writes and verifies the image with your current settings.
3. When it finishes, the log says `Batch #n: … · swap chips`. Swap the chips and repeat.

With **auto-start when all sockets refilled** on, you don't even press Enter: once every socket has been emptied and refilled, and the scan has seen the new chips twice, the next batch starts by itself. Auto-start needs all four sockets filled.

Rules in production mode:

- The image must be **exactly** the chip size. There's no pad/truncate prompt mid-production; prepare the image beforehand.
- With ALL, all chips must be the same type.
- Jobs aren't [queued](Job-Queue) in production mode, so one batch runs at a time.

Untick **production mode** to stop. The log then shows the totals (`n batch(es), ✓ ok ✕ failed`).

## Flash log

Every write and verify, in production and in normal use, is appended to a CSV file:

- Linux/macOS: `~/.local/share/hydra-flasher/flash_log.csv`
- Windows: `%APPDATA%\HydraFlasher\flash_log.csv`

**flash log ▸** opens it. One row per chip and operation:

| Column | Content |
| --- | --- |
| `time` | Date and time |
| `mode` | `manual` or `production` |
| `batch` | Production batch number |
| `socket` | `CS0` to `CS3` |
| `jedec` | JEDEC ID of the chip |
| `unique_id` | The chip's unique ID (Winbond chips), for tracing a single chip |
| `chip` | flashprog chip name |
| `operation` | `write` or `verify` |
| `image` | Image file |
| `image_crc32` | CRC32 of the image, to prove which image a chip got |
| `result` | `pass` or `fail` |
| `seconds` | How long it took |
