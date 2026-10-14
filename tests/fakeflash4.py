#!/usr/bin/env python3
"""CS0: W25Q256 (two definitions), CS1: W25Q128.V, CS2: chip flashprog doesn't support."""
import os
import re
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
cs = int(re.search(r"cs=(\d+)", " ".join(args)).group(1))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(f"cs={cs} {' '.join(a for a in args if not a.startswith(('serprog', '/')))}\n")
if cs == 0:
    if "-c" in args:
        print(f'Found Winbond flash chip "{args[args.index("-c") + 1]}" (32768 kB, SPI) on serprog.')
    else:
        print('Found Winbond flash chip "W25Q256FV" (32768 kB, SPI) on serprog.')
        print('Found Winbond flash chip "W25Q256JV_Q" (32768 kB, SPI) on serprog.')
        print('Multiple flash chip definitions match the detected chip(s): "W25Q256FV", "W25Q256JV_Q"')
        sys.exit(1)
elif cs == 1:
    print('Found Winbond flash chip "W25Q128.V" (16384 kB, SPI) on serprog.')
elif cs == 2:
    print('Found Generic flash chip "unknown SPI chip (RDID)" (0 kB, SPI) on serprog.')
    sys.exit(1)
else:
    print("No EEPROM/flash device found.")
    sys.exit(1)
time.sleep(1.5 if ("-w" in args or "-v" in args) else 0.3)
sys.exit(0)
