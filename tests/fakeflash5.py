#!/usr/bin/env python3
"""CS0/CS2: W25Q256 (two definitions), CS1: W25Q128.V (one definition)."""
import os
import re
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
cs = int(re.search(r"cs=(\d+)", " ".join(args)).group(1))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(f"cs={cs} {' '.join(a for a in args if not a.startswith(('serprog', '/')))}\n")
if cs in (0, 2):
    if "-c" in args:
        print(f'Found Winbond flash chip "{args[args.index("-c") + 1]}" (32768 kB, SPI) on serprog.')
    else:
        print('Found Winbond flash chip "W25Q256FV" (32768 kB, SPI) on serprog.')
        print('Found Winbond flash chip "W25Q256JV_Q" (32768 kB, SPI) on serprog.')
        print('Multiple flash chip definitions match the detected chip(s): "W25Q256FV", "W25Q256JV_Q"')
        sys.exit(1)
elif cs == 1:
    print('Found Winbond flash chip "W25Q128.V" (16384 kB, SPI) on serprog.')
else:
    print("No EEPROM/flash device found.")
    sys.exit(1)
time.sleep(0.8 if "-w" in args else 0.2)
sys.exit(0)
