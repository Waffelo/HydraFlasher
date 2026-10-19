"""Light/dark mode: default dark, toggle re-colors plain Tk parts and is saved."""
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
start = sys.argv[2]  # "none" (no saved theme) or "light"
import serial.tools.list_ports


class FakePort:
    device = "/dev/null"
    description = "HydraFlasher"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


serial.tools.list_ports.comports = lambda *a, **k: [FakePort()]
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg11.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
cfg = {"jedec_cache": {"EF4019": {"chips": ["W25Q256FV"], "size_kb": 32768},
                       "EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}}
if start == "light":
    cfg["theme"] = "light"
json.dump(cfg, open(H.CONFIG_FILE, "w"))
idents = {0: ("EF4019", "A"), 1: ("EF4018", "B")}


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
tb = app.log._textbox


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def colors(mode):
    p = H.THEME_LIGHT if mode == "light" else H.THEME_DARK
    led = app.cards[2]._led  # empty socket
    fills = {led.itemcget(i, "fill") for i in led.find_all()}
    check(f"{mode}: appearance mode", H.ctk.get_appearance_mode().lower() == mode)
    check(f"{mode}: socket light canvas bg", led.cget("bg") == p["panel"])
    check(f"{mode}: empty-socket light color", p["led_empty"] in fills)
    check(f"{mode}: logo background", app._logo_lbl.cget("bg") == p["bg"])
    check(f"{mode}: log text colors", tb.tag_cget("success", "foreground") == p["green"]
          and tb.tag_cget("accent", "foreground") == p["accent"])
    check(f"{mode}: menu colors", str(app.log_menu.cget("bg")) == p["panel_hi"])
    other = "light" if mode == "dark" else "dark"
    check(f"{mode}: switch offers {other} mode", app.theme_button.cget("text") == f"{other} mode")


def s1():
    app.log_line("✓ example success", "success")
    app.update()
    mode = "light" if start == "light" else "dark"
    if start == "none":
        check("default is dark", app.theme == "dark")
    colors(mode)
    subprocess.run(["import", "-window", app.wm_frame(), os.path.join(here, f"theme_{mode}.png")])
    # click the sun/moon
    app.theme_button._label.event_generate("<Button-1>")
    app.update()
    new = "dark" if mode == "light" else "light"
    colors(new)
    check(f"theme '{new}' saved", json.load(open(H.CONFIG_FILE)).get("theme") == new)
    subprocess.run(["import", "-window", app.wm_frame(), os.path.join(here, f"theme_{new}_switched.png")])
    app.destroy()


app.after(2500, s1)
app.after(20000, app.destroy)
app.mainloop()
