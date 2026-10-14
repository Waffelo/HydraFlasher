#!/usr/bin/env python3
import re, sys
cs = int(re.search(r"cs=(\d+)", " ".join(sys.argv)).group(1))
name = "MX25L25635F/MX25L25645G" if cs == 3 else "W25Q256FV"
vendor = "Macronix" if cs == 3 else "Winbond"
print(f'Found {vendor} flash chip "{name}" (32768 kB, SPI) on serprog.')
