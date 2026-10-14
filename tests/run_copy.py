"""Log copying: drag-select + Ctrl+C, Ctrl+A, right-click menu, stays read-only."""
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
tb = app.log._textbox  # the underlying tkinter.Text
checks = []


def check(name, cond):
    checks.append((name, bool(cond)))
    print("PASS" if cond else "FAIL", name, flush=True)


def clip():
    try:
        return app.clipboard_get()
    except Exception:
        return None


def go():
    app._clear_log()
    app.log_line("$ flashprog -p serprog:dev=/dev/ttyACM0,cs=0 -w fw.bin", "soft")
    app.log_line("Found Winbond flash chip \"W25Q256FV\" (32768 kB, SPI) on serprog.", "success")
    app.after(100, drag)


def drag():
    app.update()
    app.clipboard_clear()
    app.clipboard_append("<old>")
    app.focus_force()
    app.update()
    # mouse: press at line 1 col 2, drag to line 1 col 20, release (like a user)
    x1, y1, *_ = tb.bbox("1.2")
    x2, y2, *_ = tb.bbox("1.20")
    tb.event_generate("<ButtonPress-1>", x=x1 + 1, y=y1 + 3)
    tb.event_generate("<B1-Motion>", x=x2 + 1, y=y2 + 3)
    tb.event_generate("<ButtonRelease-1>", x=x2 + 1, y=y2 + 3)
    app.update()
    sel = tb.tag_ranges("sel")
    check("drag selects text", bool(sel))
    check("log has focus after click", app.focus_get() is tb)
    tb.event_generate("<Control-c>")
    app.update()
    print("selected:", repr(tb.get("sel.first", "sel.last")), "clipboard:", repr(clip()))
    check("Ctrl+C copies the selection", clip() == tb.get("sel.first", "sel.last") and clip().startswith("flashprog"))
    app.clipboard_clear()
    tb.event_generate("<Control-a>")
    app.update()
    tb.event_generate("<Control-c>")
    app.update()
    check("Ctrl+A then Ctrl+C copies everything", (clip() or "").startswith("$ flashprog") and "W25Q256FV" in (clip() or ""))
    tb.event_generate("<Key-x>")
    tb.event_generate("<BackSpace>")
    app.update()
    check("still read-only (typing does nothing)", "W25Q256FV" in tb.get("1.0", "end") and tb.get("1.0", "1.2") == "$ ")
    menu = getattr(app, "log_menu", None)
    check("right-click menu exists", menu is not None)
    if menu is not None:
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1) if menu.type(i) == "command"]
        check("menu has Copy / Copy all / Select all", labels == ["Copy", "Copy all", "Select all"])
        app.clipboard_clear()
        app._copy_log(all_text=True)
        check("Copy all copies the whole log", "W25Q256FV" in (clip() or "") and not (clip() or "").endswith("\n\n"))
    app.destroy()


app.after(1500, go)
app.after(20000, app.destroy)
app.mainloop()
