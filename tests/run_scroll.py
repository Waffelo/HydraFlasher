"""New log output follows only when the log was at the end."""
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


def go():
    for i in range(300):
        app.log_line(f"line {i}")
    app.after(200, s2)


def s2():
    app.update()
    print("PASS" if app._log_at_end() else "FAIL", "follows while at the end")
    tb.yview_moveto(0.0)
    app.update()
    for i in range(50):
        app.log_line(f"more {i}")
    app._log_progress("Writing... 40%")
    app.after(200, s3)


def s3():
    app.update()
    print("PASS" if tb.yview()[0] == 0.0 else "FAIL", "stays put when scrolled up", tb.yview())
    tb.yview_moveto(1.0)
    app.update()
    for i in range(50):
        app.log_line(f"again {i}")
    app.after(200, s4)


def s4():
    app.update()
    print("PASS" if app._log_at_end() else "FAIL", "follows again after scrolling down")
    app.destroy()


app.after(1500, go)
app.after(20000, app.destroy)
app.mainloop()
