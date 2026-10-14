"""Probe is refused (with a message) while a job runs or waits, and works again after."""
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

H.CONFIG_FILE = os.path.join(here, "cfg3.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump({"jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}},
          open(H.CONFIG_FILE, "w"))  # known chip: no auto-identify probe


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return ("EF4018", "U") if cs in (0, 1) else (None, None)


H.Programmer = FakeProgrammer
open(os.path.join(here, "chips.txt"), "w").write("0 1")
open(os.path.join(here, "calls.log"), "w").close()
img16 = os.path.join(here, "img16.bin")
open(img16, "wb").write(b"\x00" * (16 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
logged = []
orig_log = app.log_line
app.log_line = lambda text, tag="normal": (logged.append(text), orig_log(text, tag))[1]
btn = app._op_button_by_name["probe"]
idle_color = btn.cget("text_color")
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def go():
    check("probe normal when idle", btn.cget("text_color") == idle_color)
    app._select_target(0)
    app._write_file(img16)
    app._select_target(1)
    app._write_file(img16)  # something waiting in the queue too
    check("probe greyed while running", btn.cget("text_color") == H.T["faint"])
    n = len(app._queue)
    app._select_target(1)
    app.probe()  # button / Ctrl+P path
    check("probe not queued", len(app._queue) == n)
    check("message shown", any("Probing is not available during queue run." in t for t in logged))
    app.after(200, wait)


def wait():
    if app.busy or app._queue:
        if not app.busy:  # gap between queued jobs: still blocked
            check("greyed between jobs", btn.cget("text_color") == H.T["faint"])
        app.after(100, wait)
        return
    check("probe restored after queue", btn.cget("text_color") == idle_color)
    app._select_target(1)
    app.probe()
    check("probe runs when idle", app.busy)
    app.after(200, done)


def done():
    if app.busy:
        app.after(200, done)
        return
    calls = open(os.path.join(here, "calls.log")).read().split("\n")
    check("flashprog: 2 writes then 1 probe", [c.split(" ", 2)[-1] for c in calls if c] == ["-p -w --progress"] * 2 + ["-p --progress"])
    for name, ok in checks:
        print("PASS" if ok else "FAIL", name)
    app.destroy()


app.after(2500, go)
app.after(40000, app.destroy)
app.mainloop()
