# Use cases

## Back up a chip before touching it

1. Insert the chip and click its socket card.
2. **read** → save as, for example, `laptop_bios_backup.bin`.
3. **verify** against that file. A match proves the backup is good.

Keep the backup somewhere other than the folder you flash from.

## Flash one chip

1. Click the socket, press **write**, pick the image.
2. If the size doesn't match, accept the pad or truncate offer, but only if you know the image is meant for this chip.
3. Confirm. Blank check, write and verify run automatically; the card shows `✓ verified` at the end.

## Flash four identical chips at once

1. Insert four chips of the **same type**. The ALL card should say `4 identical chips`.
2. Select **ALL** (Alt+A), press **write**, pick the image.
3. All four are erased and written in parallel, then verified one by one.

This takes about as long as one chip plus the verifies.

## Flash different chips, or different images, one after another

The broadcast only works for identical chips and one image. For anything else use the [queue](Job-Queue):

1. Insert all chips first.
2. Select CS0, **write** image A.
3. While it runs: select CS1, **write** image B; select CS2, **verify** against image C; and so on.
4. Walk away. The jobs run in turn; check the log or the cards when you're back.

Don't swap chips while the red warning is shown.

## Produce many identical boards

Use [Production Mode](Production-Mode) with **ALL** and auto-start:

1. Select ALL, tick **production mode**, pick the image, tick **auto-start when all sockets refilled**.
2. Fill the four sockets. The batch starts by itself.
3. After `swap chips`, take the four chips out and put four new ones in. The next batch starts once all four are seated.

The [flash log](Production-Mode#flash-log) records every chip's unique ID, so you can trace each part later.

## Check a pile of already-flashed chips

1. Insert up to four chips, select **ALL**, press **verify**, pick the expected image.
2. Each chip is checked on its own and gets `✓ verified` or `✕ failed`.

## Recover a chip after a failed or interrupted write

A stopped or failed write leaves the chip partially programmed. Just **write** it again; erase isn't needed first. If it fails again, lower the **speed** (for example to 8M), reseat the chip and retry. See [Troubleshooting](Troubleshooting).
