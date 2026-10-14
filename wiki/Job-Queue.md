# Job queue

All sockets share one SPI bus, so only one chip can be talked to at a time. Instead of making you wait, HydraFlasher **queues** anything you start while a job is running. Queued jobs run one after another, automatically.

## How to use it

1. Start a job, for example **write** on CS0.
2. While it runs, select another socket (click it, or Alt+2) and press **write**, **verify**, **read** or **erase**. Confirm as usual.
3. The job is queued: its card shows `queued #1`, and the status line shows `1 queued`.
4. When CS0 is done, the next job starts by itself.

Each queued job keeps the settings it had when you started it (image, speed, chip definition, verify options). Changing settings afterwards doesn't affect jobs that are already waiting.

## Rules while tasks run

- **Don't remove or add chips.** A red warning under the sockets reminds you: *"DO NOT remove or add chips while tasks run."* The sockets share the bus, and plugging chips in or out during a transfer can disturb the chip that's being flashed.
- **No probing.** The probe button is greyed. The sockets are re-scanned automatically between jobs, so a probe isn't needed.
- **No duplicates.** Starting exactly the same job again (same operation, socket and file) while it runs or waits is ignored with "Already running or queued".
- **Production mode doesn't queue.** It runs its own batches, see [Production Mode](Production-Mode).

## Between jobs

After each job HydraFlasher scans the sockets once before starting the next one. That way a queued job always works with what's really in its socket:

- A queued **write** or **verify** checks when its turn comes that the socket holds a chip, and that the image size matches. If not, that job fails ("No chip on CS2 – nothing written") and the queue goes on.
- If the scan finds an unknown chip, a short *identify* probe runs first, ahead of the queue (see [Chip Detection](Chip-Detection#unknown-chips)).

## Chip definitions

If a chip matches several flashprog definitions, you're asked which one to use **when you start or queue the job**, never later. A queued job therefore never stops halfway through the queue to wait for you. Your choice is remembered (see [Chip Detection](Chip-Detection#chip-definitions)).

## Stopping

Esc or **■ stop** while jobs are queued asks what to do:

- **Stop the current job only.** The queue goes on with the next job.
- **Stop and clear the queue.** Stops the current job; nothing else runs.
- **Keep running.**

Quitting the app while jobs are queued asks for confirmation; the queued jobs won't run.
