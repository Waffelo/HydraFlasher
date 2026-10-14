"""Right-click menu must stay open after the button is released."""
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
    # a real right-click: press, then release at the same spot (the pointer is
    # then over the menu if it was posted on press)
    tb.event_generate("<ButtonPress-3>", x=40, y=40, rootx=x, rooty=y)
    app.update()
    for target in (menu, tb):
        try:
            target.event_generate("<ButtonRelease-3>", x=2, y=2, rootx=x, rooty=y)
        except Exception:
            pass
    app.update()
    app.after(300, check)


def check():
    app.update()
    mapped = menu.winfo_ismapped()
    print("PASS" if mapped else "FAIL", "menu still open after release")
    print("PASS" if not copied else "FAIL", "release didn't trigger a menu item", copied)
    try:
        menu.unpost()
    except Exception:
        pass
    app.destroy()


app.after(1500, go)
app.after(15000, app.destroy)
app.mainloop()
