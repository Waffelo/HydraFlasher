#!/usr/bin/env python3
"""Stand-in for flashprog: chip presence comes from chips.txt, operations just sleep."""
import os
import re
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
cs = int(re.search(r"cs=(\d+)", " ".join(args)).group(1))
chips = open(os.path.join(here, "chips.txt")).read().split()
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(f"{time.monotonic():.2f} cs={cs} {' '.join(a for a in args if a.startswith('-'))}\n")
if str(cs) not in chips:
    print("No EEPROM/flash device found.")
    sys.exit(1)
print('Found Winbond flash chip "W25Q128.V" (16384 kB, SPI) on serprog.')
if "-w" in args:
    for p in (25, 50, 75, 100):
        time.sleep(0.6)
        print(f"Writing... {p}%", flush=True)
    print("Erase/write done.")
else:
    time.sleep(0.5)
sys.exit(0)
