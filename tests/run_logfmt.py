"""The user's scenario: probe all (3x W25Q256 + 1 MX25L), then write on ALL."""
import json
import os
import re
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
shot = sys.argv[2]
import serial.tools.list_ports


class FakePort:
    device = "/dev/null"
    description = "HydraFlasher"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


serial.tools.list_ports.comports = lambda *a, **k: [FakePort()]
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg19.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump({"jedec_cache": {
    "EF4019": {"chips": ["W25Q256FV"], "size_kb": 32768},
    "C22019": {"chips": ["MX25L25635F/MX25L25645G"], "size_kb": 32768}}}, open(H.CONFIG_FILE, "w"))
idents = {0: ("EF4019", "A"), 1: ("EF4019", "B"), 2: ("EF4019", "C"), 3: ("C22019", "")}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
fake = os.path.join(here, "fakeflash8.py")
open(fake, "w").write('''#!/usr/bin/env python3
import re, sys
cs = int(re.search(r"cs=(\\d+)", " ".join(sys.argv)).group(1))
name = "MX25L25635F/MX25L25645G" if cs == 3 else "W25Q256FV"
vendor = "Macronix" if cs == 3 else "Winbond"
print(f'Found {vendor} flash chip "{name}" (32768 kB, SPI) on serprog.')
''')
os.chmod(fake, 0o755)
app = H.HydraUI()
app.flashrom_path = fake
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._alert = lambda *a, **k: None
app._clear_log()
tb = app.log._textbox


def wait_idle(then):
    def poll():
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(400, then)
    app.after(100, poll)


def s1():
    app._select_target(H.CS_ALL)
    app.probe()
    wait_idle(s2)


def s2():
    print("before write: busy", app.busy, "block", repr(app._log_block)); app.write()  # refused
    app.after(400, s3)


def s3():
    app.update()
    text = tb.get("1.0", "end-1c")
    print(text)
    cs_x, size_x = [], []
    for i, line in enumerate(text.split("\n"), 1):
        m = re.search(r"CS\d  ●", line)
        if m:
            cs_x.append(tb.bbox(f"{i}.{m.start()}")[0])
            k = line.index("MiB")
            size_x.append(tb.bbox(f"{i}.{k}")[0])
    print("CS column x:", cs_x)
    print("size column x:", size_x)
    print("PASS" if len(set(cs_x)) == 1 else "FAIL", "socket names line up")
    print("PASS" if len(set(size_x)) == 1 else "FAIL", "sizes line up")
    subprocess.run(["import", "-window", app.wm_frame(), shot])
    app.destroy()


app.after(2500, s1)
app.after(30000, app.destroy)
app.mainloop()
