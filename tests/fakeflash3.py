#!/usr/bin/env python3
"""CS2/CS3: GD25Q256 (identified only without -c, like a wrong override does);
other sockets: W25Q256 matching two definitions."""
import os
import re
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
cs = int(re.search(r"cs=(\d+)", " ".join(args)).group(1))
chips = open(os.path.join(here, "chips.txt")).read().split()
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(f"cs={cs} {' '.join(a for a in args if not a.startswith(('serprog', '/')))}\n")
if str(cs) not in chips:
    print("No EEPROM/flash device found.")
    sys.exit(1)
if cs in (2, 3):
    if "-c" in args:
        print("No EEPROM/flash device found.")
        sys.exit(1)
    print('Found GigaDevice flash chip "GD25Q256" (32768 kB, SPI) on serprog.')
elif "-c" in args:
    print(f'Found Winbond flash chip "{args[args.index("-c") + 1]}" (32768 kB, SPI) on serprog.')
else:
    print('Found Winbond flash chip "W25Q256FV" (32768 kB, SPI) on serprog.')
    print('Found Winbond flash chip "W25Q256JV_Q" (32768 kB, SPI) on serprog.')
    print('Multiple flash chip definitions match the detected chip(s): "W25Q256FV", "W25Q256JV_Q"')
    sys.exit(1)
time.sleep(2.0 if "-w" in args else 0.3)
sys.exit(0)
