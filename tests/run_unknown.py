"""An unknown chip showing up resets a specific chip override to Auto-detect."""
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

H.CONFIG_FILE = os.path.join(here, "cfg4.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {"jedec_cache": {"EF4019": {"chips": ["W25Q256FV", "W25Q256JV_Q"], "size_kb": 32768}}},
    open(H.CONFIG_FILE, "w"),
)
idents = {0: ("EF4019", "U0")}  # CS0: known W25Q256


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
app = H.HydraUI()
app._set_led = lambda *a: None
identified = []
app._start_identify = lambda s: identified.append(list(s))
logged = []
orig_log = app.log_line
app.log_line = lambda text, tag="normal": (logged.append(text), orig_log(text, tag))[1]
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))


def s1():
    app.chip_override.set("W25Q256JV_Q")
    idents[1] = ("EF4019", "U1")  # another known W25Q256 inserted
    app.after(1500, s2)


def s2():
    check("known chip keeps override", app.chip_override.get() == "W25Q256JV_Q")
    check("CS1 known", app.slots[1]["chips"])
    idents[2] = ("C84019", "")  # GigaDevice, never identified by flashprog
    app.after(1500, s3)


def s3():
    check("unknown chip resets override", app.chip_override.get() == "Auto-detect")
    check("CS2 shown as unknown", app.slots[2]["name"] == "unknown C84019")
    check("identify requested for CS2", identified == [[2]])
    check("reset logged", any("unknown chip on CS2" in t and "W25Q256JV_Q" in t for t in logged))
    n = len(logged)
    idents[3] = ("C84018", "")  # another unknown, override already Auto
    app.after(1500, lambda: s4(n))


def s4(n):
    check("no reset message when already Auto", not any("reset to Auto-detect" in t for t in logged[n:]))
    for name, ok in checks:
        print("PASS" if ok else "FAIL", name)
    app.destroy()


app.after(2500, s1)
app.after(30000, app.destroy)
app.mainloop()
