# Chip detection

## Live scan

While no job is running, HydraFlasher reads the **JEDEC ID** of every socket once a second, plus the 64-bit **unique ID** on Winbond chips. That's how cards update the moment you insert or remove a chip, and how the [flash log](Production-Mode#flash-log) knows exactly which chip was flashed.

The scan needs direct access to the serial port. Under sudo (see [Installation](Installation#serial-port-access-linux)) it's off, and chips only show up after a probe.

## Chip names

The JEDEC ID alone doesn't say which flashprog chip definition to use. HydraFlasher learns the name the first time flashprog probes a chip type, and remembers it in its config file. From then on, inserting that chip type shows its name right away.

It only learns from probes **without** a chip override. With an override, flashprog reports just the forced name, which would hide that other definitions also fit.

## Unknown chips

A chip whose type HydraFlasher hasn't learned yet shows as `unknown <JEDEC ID>` with "probe to identify". You don't need to do anything:

1. If a chip override is set, it's reset to Auto-detect, since an override would stop flashprog from recognising the new chip.
2. An **identify** probe runs automatically on that socket. If jobs are queued, it runs first, in the next pause between jobs.
3. The card then shows the real chip name, and the name is remembered.

## Unsupported chips

If flashprog only matches one of its catch-all definitions (`unknown SPI chip (RDID)` and similar), it has no real definition for the chip. The card then shows **`unsupported chip`** in red. Read, erase and write are refused with an explanation, and the chip is never added to the remembered names.

What to do: check that the chip is seated correctly (a badly seated chip can return a garbled ID), then check whether a newer flashprog supports it.

## Chip definitions

Some chips match several flashprog definitions, for example a Winbond W25Q256 matches both `W25Q256FV` and `W25Q256JV_Q`. The card then shows `2 defs match`.

When you start a job on such a chip, HydraFlasher asks which definition to use. **Pick the one printed on the chip.** The choice becomes the chip override, so later jobs don't ask again. If a chip's definitions weren't known yet when you queued its job, the job uses your choice if it fits.

## Chip override

The **chip** box in the settings forces one definition (flashprog's `-c`). It's set automatically when you pick a definition; × resets it to **Auto-detect**.

An override only fits one chip type. Whenever a chip is inserted that the override doesn't fit, HydraFlasher resets it to Auto-detect and says so in the log, for example:

> → Chip override W25Q256JV_Q doesn't fit (CS1 holds W25Q128.V) – reset to Auto-detect

Jobs that are already queued keep the definition they were started with.
