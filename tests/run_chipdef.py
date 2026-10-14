"""Chip definition is picked when a job is queued, never when a queued job starts."""
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
import serial.tools.list_ports


class FakePort:
    device = "/dev/null"
    description = "HydraFlasher"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


serial.tools.list_ports.comports = lambda *a, **k: [FakePort()]
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg2.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {
        "jedec_cache": {
            "EF4019": {"chips": ["W25Q256FV", "W25Q256JV_Q"], "size_kb": 32768},
            "EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384},
        }
    },
    open(H.CONFIG_FILE, "w"),
)


def present():
    return open(os.path.join(here, "chips.txt")).read().split()


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass

    def identify(self, cs):
        if str(cs) not in present():
            return None, None
        return ("EF4018" if cs == 2 else "EF4019"), f"U{cs}"


H.Programmer = FakeProgrammer
open(os.path.join(here, "chips.txt"), "w").write("1 2")
open(os.path.join(here, "calls.log"), "w").close()
img16, img32 = os.path.join(here, "img16.bin"), os.path.join(here, "img32.bin")
open(img16, "wb").write(b"\x00" * (16 << 20))
open(img32, "wb").write(b"\x00" * (32 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash2.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
prompts = []
app._choose = lambda title, msg, opts, **k: (prompts.append((app.busy, msg, opts)), 1)[1]
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))


def step1():
    app._select_target(2)
    app._write_file(img16)  # single definition -> no prompt
    app.after(400, step2)


def step2():
    open(os.path.join(here, "chips.txt"), "w").write("1 2 3")  # CS3 inserted mid-job
    app._select_target(3)
    app._write_file(img32)  # unknown yet -> no prompt possible
    n_before = len(prompts)
    app._select_target(1)
    app._write_file(img32)  # known W25Q256 -> prompt now, while queueing
    print("prompts while queueing CS1:", len(prompts) - n_before)
    app.after(200, wait)


def wait():
    if app.busy or app._queue:
        app.after(200, wait)
        return
    print("prompts:", prompts)
    print("results:", results)
    print(open(os.path.join(here, "calls.log")).read().strip())
    ok = (
        len(prompts) == 1
        and prompts[0][0]  # asked while the CS2 job was running (= at queue time)
        and len(results) == 3
        and all(r[0] for r in results)
    )
    print("PASS" if ok else "FAIL")
    app.destroy()


app.after(2500, step1)
app.after(40000, app.destroy)
app.mainloop()
