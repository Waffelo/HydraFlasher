# Operations

Every button acts on the **target** selected in the socket row: one socket (CS0 to CS3), or **ALL**. The exact flashprog command of each step is printed in the log.

## Probe

Asks flashprog which chip is in the socket and shows its name and size on the card. You rarely need it: the live scan already detects chips, and unknown chips are probed automatically (see [Chip Detection](Chip-Detection)).

- **One socket:** probes that socket.
- **ALL:** probes every socket one by one, and warns if the chips aren't all the same type (broadcast write and erase are then unavailable).

Probing isn't available while a job runs or is queued: the button is greyed and a click logs "Probing is not available during queue run."

## Read

Saves the chip's contents to a file.

- **One socket:** asks where to save; the suggested name is `<chip>_CS<n>.bin`.
- **ALL:** asks for a folder and saves one `<chip>_CS<n>.bin` per detected chip.

## Verify

Compares a chip with an image file and reports match or mismatch.

- With **verify on Pico** on (default), the Pico computes a CRC32 of the chip and compares it with the image's CRC32. Nothing is read back over USB, so this is many times faster.
- Otherwise flashprog reads the chip back and compares (`flashprog -v`).
- **ALL** verifies every detected chip one by one.

Results go to the [flash log](Production-Mode#flash-log) and show on the card as `✓ verified` or `✕ failed`.

## Erase

Erases the whole chip (all bytes become `0xFF`) after a confirmation. **ALL** erases every chip at once over the broadcast line.

## Write

Flashes an image to the chip. The steps for one socket:

1. **Blank check (on Pico)**, only with *used blocks only*. The image is split into 64 KiB blocks. Blocks that are entirely `0xFF` in the image are "unused"; the Pico checks with a CRC32 whether those blocks on the chip are already blank.
2. **Write.** flashprog erases and writes the used blocks plus any unused block that wasn't blank. If more than 90 % of the blocks are used, it simply writes the whole chip.
3. **Verify.** On the Pico (CRC32) if *verify on Pico* is on, otherwise flashprog's own verify. Skipped entirely with *skip verify -n*.

Before writing you confirm the image and the target. If the image size doesn't match the chip, HydraFlasher offers to fix it. Your original file is never changed:

- **image too big:** truncate it to the chip size and save a copy as `<name>_truncated.bin`;
- **image too small:** pad it with `0xFF` up to the chip size and save a copy as `<name>_padded.bin`.

## ALL sockets (broadcast)

With **ALL** selected, write and erase use chip-select **CS6**, which selects every socket at once. Identical chips are erased and written **in parallel**, as fast as a single chip.

There's a catch, and HydraFlasher handles it for you. During a broadcast every chip answers at the same time, so nothing can be *read* over CS6. A broadcast write therefore:

1. re-probes the sockets and checks they still hold the chips you confirmed;
2. erases all chips over CS6, so they all start with identical contents (flashprog reads the chip before writing);
3. writes all chips over CS6;
4. verifies **each chip on its own**, never over CS6.

All inserted chips must be the **same type**. Otherwise the ALL card shows `chip types differ` and broadcast write and erase are refused. Unsupported chips can't be used in a broadcast either.

## Stop

**■ stop** or Esc asks for confirmation, then stops the running job. Interrupting an erase or write leaves the chip partially programmed: write it again before using it. With jobs in the queue you can also clear the queue (see [Job Queue](Job-Queue#stopping)).
