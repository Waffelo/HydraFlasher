"""Right-click menu opens on release (not press) and stays open."""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
import serial.tools.list_ports

serial.tools.list_ports.comports = lambda *a, **k: []
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg7.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
app = H.HydraUI()
app._set_led = lambda *a: None
tb = app.log._textbox
menu = app.log_menu
copied = []
orig_copy = app._copy_log
app._copy_log = lambda all_text=False: (copied.append(all_text), orig_copy(all_text))


def go():
    app.focus_force()
    app.update()
    x, y = tb.winfo_rootx() + 40, tb.winfo_rooty() + 40
    tb.event_generate("<ButtonPress-3>", x=40, y=40, rootx=x, rooty=y)
    app.update()
    print("PASS" if not menu.winfo_ismapped() else "FAIL", "press alone doesn't open the menu")
    tb.event_generate("<ButtonRelease-3>", x=40, y=40, rootx=x, rooty=y)
    app.update()
    app.after(300, lambda: check(x, y))


def check(x, y):
    app.update()
    print("PASS" if menu.winfo_ismapped() else "FAIL", "release opens the menu, still open 300 ms later")
    print("PASS" if (menu.winfo_rootx(), menu.winfo_rooty()) == (x + 1, y + 1) else "FAIL",
          "menu 1 px off the pointer", (menu.winfo_rootx(), menu.winfo_rooty()), (x, y))
    print("PASS" if not copied else "FAIL", "no menu item triggered")
    print("PASS" if menu.entrycget("Copy", "state") == "disabled" else "FAIL", "Copy greyed without a selection")
    menu.unpost()
    app.destroy()


app.after(1500, go)
app.after(15000, app.destroy)
app.mainloop()
