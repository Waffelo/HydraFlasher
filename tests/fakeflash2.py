#!/usr/bin/env python3
"""flashprog stand-in: CS2 holds a W25Q128 (one definition), other sockets a W25Q256
that matches two definitions, so without -c it fails like the real flashprog."""
import os
import re
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
cs = int(re.search(r"cs=(\d+)", " ".join(args)).group(1))
chips = open(os.path.join(here, "chips.txt")).read().split()
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(f"cs={cs} {' '.join(a for a in args if not a.startswith('serprog'))}\n")
if str(cs) not in chips:
    print("No EEPROM/flash device found.")
    sys.exit(1)
if cs == 2:
    print('Found Winbond flash chip "W25Q128.V" (16384 kB, SPI) on serprog.')
elif "-c" in args:
    print(f'Found Winbond flash chip "{args[args.index("-c") + 1]}" (32768 kB, SPI) on serprog.')
else:
    print('Found Winbond flash chip "W25Q256FV" (32768 kB, SPI) on serprog.')
    print('Found Winbond flash chip "W25Q256JV_Q" (32768 kB, SPI) on serprog.')
    print('Multiple flash chip definitions match the detected chip(s): "W25Q256FV", "W25Q256JV_Q"')
    print("Please specify which chip definition to use with the -c <chipname> option.")
    sys.exit(1)
time.sleep(2.0 if "-w" in args else 0.3)
sys.exit(0)
