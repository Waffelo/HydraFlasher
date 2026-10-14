"""Stage the README screenshot: 4 chips, one-by-one write running, 3 queued.

python3 tools/run_readme_shot.py . screenshot.png   (needs ImageMagick's import)
Fakes the Pico and flashprog; config, log and image live in a temp folder."""
import json
import os
import subprocess
import sys
import tempfile

work = tempfile.mkdtemp()
sys.path.insert(0, sys.argv[1])
shot = sys.argv[2]
import serial.tools.list_ports


class FakePort:
    device = "/dev/ttyACM0"
    description = "HydraFlasher"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


serial.tools.list_ports.comports = lambda *a, **k: [FakePort()]
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(work, "config.json")
H.DATA_DIR = work
H.FLASH_LOG = os.path.join(work, "flash_log.csv")
json.dump({"jedec_cache": {"EF4019": {"chips": ["W25Q256FV"], "size_kb": 32768}}},
          open(H.CONFIG_FILE, "w"))
idents = {0: ("EF4019", "DB652C30C7284433"), 1: ("EF4019", "DB653C756318262A"),
          2: ("EF4019", "DB6598144F1F2826"), 3: ("EF4019", "DB658C21C31C2530")}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
os.makedirs(os.path.join(work, "bin"))
fake = os.path.join(work, "bin", "flashprog")  # named so the log shows "flashprog"
open(fake, "w").write('''#!/usr/bin/env python3
import sys, time
print('Found Winbond flash chip "W25Q256FV" (32768 kB, SPI) on serprog.', flush=True)
if "-w" in sys.argv:
    for p in range(0, 101, 4):
        print(f"Writing... {p}%", flush=True)
        time.sleep(0.25)
''')
os.chmod(fake, 0o755)
img = os.path.join(work, "firmware_v1.4.bin")
open(img, "wb").write(bytes(32 << 20))

app = H.HydraUI()
app.flashrom_path = fake
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
app._ask_open_file = lambda *a, **k: img
app.geometry("1000x900")


def wait_idle(then):
    def poll():
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(300, then)
    app.after(100, poll)


def s1():
    app._clear_log()
    app._select_target(H.CS_ALL)
    app.probe()
    wait_idle(s2)


def s2():
    app.one_by_one_var.set(True)
    app.write()
    app.after(2600, s3)  # ~40 % into CS0's write


def s3():
    app.update()
    subprocess.run(["import", "-window", app.wm_frame(), shot])
    app._queue.clear()
    app._abort_requested = True
    if app._proc:
        app._proc.terminate()
    app.after(800, app.destroy)


app.after(3000, s1)
app.after(40000, app.destroy)
app.mainloop()
