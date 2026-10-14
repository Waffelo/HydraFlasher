#!/usr/bin/env python3
"""CS0-CS2: W25Q256 (two definitions), CS3: W25Q128.V. Like the real flashprog, a -c
for a definition the chip doesn't match means 'no chip found'."""
import os
import re
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
cs = int(re.search(r"cs=(\d+)", " ".join(args)).group(1))
forced = args[args.index("-c") + 1] if "-c" in args else None
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(f"cs={cs} {' '.join(a for a in args if not a.startswith(('serprog', '/')))}\n")
defs = {} if cs == 2 else {0: ["W25Q256FV", "W25Q256JV_Q"], 1: ["W25Q256FV", "W25Q256JV_Q"],
        2: ["W25Q256FV", "W25Q256JV_Q"], 3: ["W25Q128.V"]}.get(cs)
size = 16384 if cs == 3 else 32768
if not defs or (forced and forced not in defs):
    print("No EEPROM/flash device found.")
    sys.exit(1)
names = [forced] if forced else defs
for n in names:
    print(f'Found Winbond flash chip "{n}" ({size} kB, SPI) on serprog.')
if len(names) > 1:
    print('Multiple flash chip definitions match the detected chip(s): "W25Q256FV", "W25Q256JV_Q"')
    sys.exit(1)
if "-r" in args:
    open(args[args.index("-r") + 1], "wb").write(b"\xff" * 16)
time.sleep(0.2)
sys.exit(0)
