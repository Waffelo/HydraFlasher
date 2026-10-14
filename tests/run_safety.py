"""Override misfit reset, unsupported chips, cache hygiene, duplicate jobs, banner."""
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

H.CONFIG_FILE = os.path.join(here, "cfg6.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {
        "jedec_cache": {
            "EF4019": {"chips": ["W25Q256FV", "W25Q256JV_Q"], "size_kb": 32768},
            "EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384},
            "FFFE19": {"chips": ["W25Q256FV"], "size_kb": 32768},  # garbled read
            "C84019": {"chips": ["unknown SPI chip (RDID)"], "size_kb": 0},  # generic
        }
    },
    open(H.CONFIG_FILE, "w"),
)
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


# Real Programmer.identify with a fake SPI read
p = H.Programmer.__new__(H.Programmer)
for raw, want in [(b"\xff\xfe\x19", (None, None)), (b"\x00\x40\x19", (None, None)),
                  (b"\xc8\x40\x19", ("C84019", None))]:
    p.spi = lambda cs, tx, rlen, raw=raw: raw
    check(f"identify {raw.hex()} -> {want}", p.identify(0) == want)

idents = {}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
open(os.path.join(here, "calls.log"), "w").close()
img32 = os.path.join(here, "img32.bin")
open(img32, "wb").write(b"\x00" * (32 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash4.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
app._choose = lambda *a, **k: 0
alerts = []
app._alert = lambda title, msg, **k: alerts.append((title, msg))
logged = []
orig_log = app.log_line
app.log_line = lambda text, tag="normal": (logged.append(text), orig_log(text, tag))[1]
results = []
orig_rep = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig_rep(ok, msg))

check("cache cleaned on load", set(app.jedec_cache) == {"EF4019", "EF4018"})


def wait_idle(then):
    def poll():
        if app.busy or app._queue:
            app.after(150, poll)
        else:
            app.after(300, then)
    app.after(150, poll)


def s1():  # CS0 known W25Q256 present; override fits
    idents[0] = ("EF4019", "U0")
    app.chip_override.set("W25Q256JV_Q")
    app.after(1500, s2)


def s2():
    check("fitting chip keeps override", app.chip_override.get() == "W25Q256JV_Q")
    idents[1] = ("EF4018", "U1")  # known W25Q128: override doesn't fit
    app.after(1500, s3)


def s3():
    check("misfit chip resets override", app.chip_override.get() == "Auto-detect")
    check("misfit logged", any("CS1 holds W25Q128.V" in t for t in logged))
    idents[2] = ("C84019", "")  # unsupported chip -> identify job
    app.after(1200, lambda: wait_idle(s4))


def s4():
    check("identify reports unsupported", results and not results[-1][0] and "Unsupported chip on CS2" in results[-1][1])
    check("CS2 shown as unsupported", app.slots[2]["name"] == "unsupported chip" and app.cards[2]._state == "warn")
    check("generic name not learned", "C84019" not in app.jedec_cache)
    app._select_target(2)
    app._write_file(img32)
    check("write to unsupported refused", alerts and alerts[-1][0] == "Unsupported chip")
    check("nothing queued for unsupported", not app.busy and not app._queue)
    # probe with -c must not shrink the learned definitions
    app.chip_override.set("W25Q256FV")
    app._start_probe(0)
    wait_idle(s5)


def s5():
    check("-c probe keeps both definitions", app.jedec_cache["EF4019"]["chips"] == ["W25Q256FV", "W25Q256JV_Q"])
    app._select_target(0)
    app._write_file(img32)
    check("banner shown while running", app.busy_banner.cget("text") == "DO NOT remove or add chips while tasks run.")
    app._write_file(img32)  # same write again -> refused
    check("duplicate of running job refused", not app._queue)
    app._verify_file(img32)
    app._verify_file(img32)  # same verify again -> refused
    check("duplicate in queue refused", len(app._queue) == 1)
    check("duplicate message", any("Already running or queued" in t for t in logged))
    wait_idle(s6)


def s6():
    check("banner cleared when idle", app.busy_banner.cget("text") == "")
    for name, ok in checks:
        print("PASS" if ok else "FAIL", name)
    app.destroy()


app.after(2000, s1)
app.after(60000, app.destroy)
app.mainloop()
