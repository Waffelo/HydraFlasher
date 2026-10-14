"""Unknown chip -> override reset -> automatic identify probe (idle and mid-queue)."""
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

H.CONFIG_FILE = os.path.join(here, "cfg5.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {"jedec_cache": {"EF4019": {"chips": ["W25Q256FV", "W25Q256JV_Q"], "size_kb": 32768}}},
    open(H.CONFIG_FILE, "w"),
)
JEDEC = {0: "EF4019", 1: "EF4019", 2: "C84019", 3: "C86019"}


def present():
    return open(os.path.join(here, "chips.txt")).read().split()


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return (JEDEC[cs], f"U{cs}") if str(cs) in present() else (None, None)


H.Programmer = FakeProgrammer
open(os.path.join(here, "chips.txt"), "w").write("0 1")
open(os.path.join(here, "calls.log"), "w").close()
img32 = os.path.join(here, "img32.bin")
open(img32, "wb").write(b"\x00" * (32 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash3.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
app._choose = lambda *a, **k: 1  # W25Q256JV_Q
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def calls():
    return [c for c in open(os.path.join(here, "calls.log")).read().split("\n") if c]


def a1():  # idle: override set, unknown chip inserted in CS2
    app.chip_override.set("W25Q256JV_Q")
    open(os.path.join(here, "chips.txt"), "w").write("0 1 2")
    app.after(200, a2)


def a2():
    if not results:
        app.after(200, a2)
        return
    check("A: override reset", app.chip_override.get() == "Auto-detect")
    check("A: CS2 probed without -c", calls() == ["cs=2 -p --progress"])
    check("A: CS2 identified", app.slots[2]["name"] == "GD25Q256")
    check("A: chip learned", app.jedec_cache.get("C84019", {}).get("chips") == ["GD25Q256"])
    check("A: identify ok", results[-1][0])
    open(os.path.join(here, "calls.log"), "w").close()
    del results[:]
    app.after(500, b1)


def b1():  # queue run: write CS0, insert unknown CS3 meanwhile, queue write CS1
    app._select_target(0)
    app._write_file(img32)  # picker -> W25Q256JV_Q
    app.after(400, b2)


def b2():
    open(os.path.join(here, "chips.txt"), "w").write("0 1 2 3")
    app._select_target(1)
    app._write_file(img32)
    app.after(200, b3)


def b3():
    if app.busy or app._queue:
        app.after(200, b3)
        return
    c = calls()
    print("\n".join(c))
    print(results)
    check("B: order write CS0, identify CS3, write CS1",
          [x.split()[0] + (" -w" if "-w" in x else " probe") for x in c]
          == ["cs=0 -w", "cs=3 probe", "cs=1 -w"])
    check("B: CS3 identify without -c", "-c" not in c[1] if len(c) > 1 else False)
    check("B: queued CS1 kept its -c", "-c W25Q256JV_Q" in c[2] if len(c) > 2 else False)
    check("B: all ok", len(results) == 3 and all(r[0] for r in results))
    for name, ok in checks:
        print("PASS" if ok else "FAIL", name)
    app.destroy()


app.after(2500, a1)
app.after(40000, app.destroy)
app.mainloop()
