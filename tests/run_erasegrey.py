"""Erase on ALL: greyed with mixed chips (broadcast); one-by-one erases each socket."""
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

H.CONFIG_FILE = os.path.join(here, "cfg21.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump({"jedec_cache": {
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
app._choose = lambda *a, **k: 1  # W25Q256JV_Q
alerts = []
app._alert = lambda title, msg, **k: alerts.append(title)
erase_btn, write_btn = app._op_button_by_name["erase"], app._op_button_by_name["write"]
normal_bg = erase_btn.cget("fg_color")


def greyed(b):
    return b.cget("text_color") == H.T["faint"]


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
    app._select_target(H.CS_ALL)
    app.update()
    check("ALL + mixed (broadcast): erase greyed", greyed(erase_btn))
    check("ALL + mixed (broadcast): write greyed", greyed(write_btn))
    app.erase()
    check("click shows 'Cannot use broadcast', nothing started", alerts == ["Cannot use broadcast"] and not app.busy)
    app._select_target(0)
    app.update()
    check("single socket: erase normal", not greyed(erase_btn))
    app._select_target(H.CS_ALL)
    app.one_by_one_var.set(True)
    app.update()
    check("ALL + one-by-one: erase not greyed", not greyed(erase_btn))
    check("ALL + one-by-one: write not greyed", not greyed(write_btn))
    open(calls_log, "w").close()
    app.erase()
    check("one erase job per socket (1 running + 3 queued)", app.busy and len(app._queue) == 3)
    wait_idle(s2)


def s2():
    c = calls()
    print("\n".join(c))
    check("each socket erased on its own, never over CS6",
          [x.split()[0] for x in c] == ["cs=0", "cs=1", "cs=2", "cs=3"] and all("-E" in x for x in c))
    check("W25Q256 sockets erased with -c W25Q256JV_Q", all("-c W25Q256JV_Q" in x for x in c[:3]))
    check("W25Q128 socket erased without the W25Q256 -c", "-c" not in c[3])
    app.destroy()


app.after(2500, s1)
app.after(40000, app.destroy)
app.mainloop()
