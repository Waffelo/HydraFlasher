"""Probe/read/verify ALL with mixed chips and a chip override set for the majority."""
import json
import os
import sys
import tempfile

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

H.CONFIG_FILE = os.path.join(here, "cfg17.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump({"fast_verify": False, "jedec_cache": {
    "EF4019": {"chips": ["W25Q256FV", "W25Q256JV_Q"], "size_kb": 32768},
    "EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}}, open(H.CONFIG_FILE, "w"))
idents = {0: ("EF4019", "A"), 1: ("EF4019", "B"), 2: ("EF4019", "C"), 3: ("EF4018", "D")}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
calls_log = os.path.join(here, "calls.log")
app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash6.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))
outdir = tempfile.mkdtemp(dir=here)


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def calls():
    return [c for c in open(calls_log).read().split("\n") if c]


def wait_idle(then):
    def poll():
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(300, then)
    app.after(100, poll)


def s1():
    app.chip_override.set("W25Q256JV_Q")  # picked for the W25Q256 chips
    app._select_target(H.CS_ALL)
    open(calls_log, "w").close()
    app.probe()
    wait_idle(s2)


def s2():
    c = calls()
    print("probe all:", c)
    check("probe all: CS3 detected as W25Q128.V", app.slots[3] and app.slots[3]["name"] == "W25Q128.V")
    check("probe all: CS3 probed without the W25Q256 -c", not any(x.startswith("cs=3") and "-c" in x for x in c))
    check("probe all: CS0-2 keep -c W25Q256JV_Q", all("-c W25Q256JV_Q" in x for x in c if x[:4] in ("cs=0", "cs=1", "cs=2")))
    check("probe all: reports 4/4 populated", results and "4/4" in results[-1][1])
    open(calls_log, "w").close()
    app._start_read_all(outdir)
    wait_idle(s3)


def s3():
    print("read all:", results[-1])
    check("read all: all 4 dumps saved", results[-1][0] and len(os.listdir(outdir)) == 4)
    img = os.path.join(outdir, sorted(os.listdir(outdir))[0])
    open(calls_log, "w").close()
    app._start_verify_all(img)
    wait_idle(s4)


def s4():
    c = calls()
    check("verify all: CS3 verified without the W25Q256 -c", any(x.startswith("cs=3") and "-v" in x and "-c" not in x for x in c))
    app.destroy()


app.after(2500, s1)
app.after(60000, app.destroy)
app.mainloop()
