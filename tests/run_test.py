"""Drive the queue in the real GUI with a fake flashprog and a fake Pico (no hardware)."""
import json
import os
import sys
import time

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

H.CONFIG_FILE = os.path.join(here, "cfg.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump({"jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}},
          open(H.CONFIG_FILE, "w"))  # known chip: no auto-identify probe


def chips():
    return open(os.path.join(here, "chips.txt")).read().split()


class FakeProgrammer:
    def __init__(self, port):
        pass

    def close(self):
        pass

    def command_map(self):
        return set()  # no CRC32 -> plain flashprog paths

    def set_speed(self, hz):
        pass

    def pins(self, on):
        pass

    def identify(self, cs):
        return ("EF4018", f"UID{cs}") if str(cs) in chips() else (None, None)


H.Programmer = FakeProgrammer

open(os.path.join(here, "chips.txt"), "w").write("0")
open(os.path.join(here, "calls.log"), "w").close()
img16 = os.path.join(here, "img16.bin")
img1 = os.path.join(here, "img1.bin")
open(img16, "wb").write(b"\x00" * (16 << 20))
open(img1, "wb").write(b"\x00" * (1 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
alerts = []
app._alert = lambda *a, **k: alerts.append(a)
results = []
orig_report = app._report_job


def report(ok, msg):
    results.append((round(time.monotonic() - t0, 1), ok, msg))
    orig_report(ok, msg)


app._report_job = report
checks = []
t0 = time.monotonic()


def check(name, cond):
    checks.append((name, bool(cond)))


def step1():  # scan has seen CS0: write it
    check("CS0 detected by scan", app.slots[0] and app.slots[0]["detected"])
    app._select_target(0)
    app._write_file(img16)
    check("job running", app.busy)
    app.after(500, step2)


def step2():  # while CS0 is flashing: insert a chip in CS1 and queue jobs
    open(os.path.join(here, "chips.txt"), "w").write("0 1")
    check("CS1 still unknown during job", not (app.slots[1] and app.slots[1]["detected"]))
    app._select_target(1)
    app._write_file(img16)  # deferred check, chip inserted mid-job
    app._select_target(2)
    app._write_file(img1)  # empty socket -> must fail at its turn
    app._select_target(3)
    app._start_probe(3)
    check("3 queued", len(app._queue) == 3)
    check("CS1 card queued", app.cards[1]._state == "queued")
    check("no alerts while queueing", not alerts)
    app.after(200, step3)


def step3():
    if app.busy or app._queue:
        app.after(200, step3)
        return
    app.after(500, finish)


def finish():
    for r in results:
        print("result", r)
    print(open(os.path.join(here, "calls.log")).read().strip())
    check("4 jobs finished", len(results) == 4)
    check("CS0 write ok", results and results[0][1] and "CS0" in results[0][2])
    check("CS1 write ok", len(results) > 1 and results[1][1] and "CS1" in results[1][2])
    check(
        "CS2 refused (no chip)",
        len(results) > 2 and not results[2][1] and "No chip on CS2" in results[2][2],
    )
    check("CS3 probe failed", len(results) > 3 and not results[3][1])
    calls = open(os.path.join(here, "calls.log")).read()
    check("CS2 never sent to flashprog", "cs=2" not in calls)
    check("stop button disabled at end", str(app.stop_button.cget("state")) == "disabled")
    for name, ok in checks:
        print("PASS" if ok else "FAIL", name)
    app.destroy()


app.after(2500, step1)
app.after(40000, app.destroy)
app.mainloop()
