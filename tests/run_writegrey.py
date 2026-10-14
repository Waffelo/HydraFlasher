"""Write is greyed (and refuses before the file dialog) while ALL can't be broadcast to."""
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

H.CONFIG_FILE = os.path.join(here, "cfg9.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {"jedec_cache": {
        "EF4019": {"chips": ["W25Q256FV"], "size_kb": 32768},
        "EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}},
    open(H.CONFIG_FILE, "w"),
)
idents = {0: ("EF4019", "A"), 1: ("EF4018", "B")}  # different types


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
alerts, dialogs = [], []
app._alert = lambda title, msg, **k: alerts.append(title)
app._ask_open_file = lambda *a, **k: (dialogs.append(a), None)[1]
btn = app.write_button
normal_bg = btn.cget("fg_color")


def greyed():
    return btn.cget("fg_color") == H.T["panel_hi"] and btn.cget("text_color") == H.T["faint"]


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def s1():
    app._select_target(H.CS_ALL)
    app.update()
    check("ALL + mixed chips: write greyed", greyed())
    app.write()
    check("click shows 'Cannot use broadcast'", alerts == ["Cannot use broadcast"])
    check("no file dialog opened", not dialogs)
    app._select_target(0)
    app.update()
    check("single socket: write normal again", btn.cget("fg_color") == normal_bg and not greyed())
    app.write()
    check("single socket: write asks for a file", len(dialogs) == 1)
    app._select_target(H.CS_ALL)
    idents[1] = ("EF4019", "C")  # now identical
    app.after(1500, s2)


def s2():
    check("ALL + identical chips: write normal", btn.cget("fg_color") == normal_bg)
    idents.clear()  # all chips removed
    app.after(1500, s3)


def s3():
    check("ALL + no chips: write greyed", greyed())
    app.destroy()


app.after(2500, s1)
app.after(20000, app.destroy)
app.mainloop()
