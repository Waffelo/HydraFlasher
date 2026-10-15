"""Drag and drop (handled drops, hover, tkdnd registration if available) and the
"~… left" time estimate learned from earlier runs."""
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

H.CONFIG_FILE = os.path.join(here, "cfg_dropeta.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {"jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}},
    open(H.CONFIG_FILE, "w"),
)
idents = {0: ("EF4018", "A"), 1: ("EF4018", "B")}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
open(os.path.join(here, "chips.txt"), "w").write("0 1")
calls_log = os.path.join(here, "calls.log")
open(calls_log, "w").close()
img = os.path.join(here, "drop image.bin")  # a space: tkdnd wraps it in {braces}
open(img, "wb").write(b"\x00" * (16 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
asks, metas = [], []
app._ask = lambda title, msg, **k: (asks.append((title, msg)), True)[1]
app._alert = lambda *a, **k: None


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def calls():
    return [c for c in open(calls_log).read().split("\n") if c]


def log_text():
    app._flush_log_buffer()
    return app.log.get("1.0", "end")


def wait_idle(then):
    def poll():
        meta = app.status_meta.cget("text")
        if app.busy and meta not in metas:
            metas.append(meta)
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(300, then)
    app.after(100, poll)


def s1():
    card = app.cards[0]
    if app._dnd:
        check("tkdnd: cards, log and window registered",
              all(app.tk.call("bind", w._w, "<<Drop>>")
                  for w in (card, app._log_panel, app._ui_root)))
        check("tkdnd: welcome tip shown", "Drag an image onto a socket card" in log_text())

        # tkdnd resolves the widget under the pointer to its nearest registered
        # parent; run that lookup on the innermost widgets of a card, the log and
        # the settings panel
        def deepest(w):
            kids = w.winfo_children()
            return deepest(kids[0]) if kids else w

        def drop_window(w):
            types = app.tk.call("tkdnd::platform_specific_types", H.DND_FILES)
            found = app.tk.call(
                "::tkdnd::generic::FindWindowWithCommonTypes", w._w, types)
            return str(app.tk.splitlist(found)[0])

        check("tkdnd: label inside a card resolves to the card",
              drop_window(deepest(card._name_lbl)) == card._w)
        check("tkdnd: log text resolves to the log panel",
              drop_window(app.log._textbox) == app._log_panel._w)
        check("tkdnd: settings resolve to the window (hint)",
              drop_window(deepest(app.port_entry)) == app._ui_root._w)
    else:
        check("no tkdnd: no welcome tip", "Drag an image" not in log_text())
    # Hover: green border while a file is over the card, selection border after
    app._select_target(0)
    app._drop_hover(card, True)
    check("hover: card border green", card.cget("border_color") == H.T["green"])
    app._drop_hover(card, False)
    check("hover off: selected border back", card.cget("border_color") == H.T["accent"])
    app._drop_hover(app._log_panel, True)
    check("hover: log border green", app._log_panel.cget("border_color") == H.T["green"])
    app._drop_hover(app._log_panel, False)

    # Refused drops: no confirmation, no job
    app._handle_drop([here], 1)
    app._handle_drop([img, img], 1)
    check("folder / several files refused", "Drop a single image file." in log_text()
          and not asks and not app.busy)
    app._handle_drop([img], None)
    check("drop elsewhere: hint only", "on the log to verify" in log_text() and not asks)
    check("no estimate before the first run", app._estimate(
        {"title": "Write x", "cs": 1, "key": ("write", 1, img),
         "job": {"speed": app.spispeed_var.get()}}) is None)

    # A real drop on card CS1, data as tkdnd delivers it
    app._on_drop(app.cards[1], 1, "{%s}" % img)
    wait_idle(s2)


def s2():
    check("drop on CS1 selects it", app._selected_cs() == 1)
    check("drop asks the usual write confirmation",
          asks and asks[0][0] == "Write" and "drop image.bin" in asks[0][1])
    check("write ran on CS1", any("cs=1" in c and "-w" in c for c in calls()))
    check("first run: no estimate shown", metas and not any("left" in m for m in metas))
    check("duration learned", len(app.job_times) == 2
          and all(v > 0 for v in app.job_times.values()))
    check("job_times saved in config",
          json.load(open(H.CONFIG_FILE)).get("job_times") == app.job_times)
    metas.clear()
    # Same write again, plus one to CS0 (same chip/image: estimated from CS1's run)
    app._handle_drop([img], 1)
    app._handle_drop([img], 0)
    wait_idle(s3)


def s3():
    check("second run shows ~… left", any("left" in m for m in metas))
    check("queue shows the total", any("1 queued, ~" in m and "total" in m for m in metas))
    check("queued drop ran on CS0", any("cs=0" in c and "-w" in c for c in calls()))
    # Verify by dropping on the log: against the selected target (CS0 now)
    asks.clear()
    n = len(calls())
    app._handle_drop([img], "verify")
    wait_idle(lambda: s4(n))


def s4(n):
    new = calls()[n:]
    check("drop on the log verifies the target", any("cs=0" in c and "-v" in c for c in new))
    check("verify asks nothing", not asks)
    # Production mode: drops ignored
    app._prod = {"path": img, "name": "x", "batch": 0, "ok": 0, "fail": 0}
    app._handle_drop([img], 1)
    check("production: drop ignored", "drop is ignored" in log_text() and not asks)
    app._prod = None
    app.destroy()


app.after(2500, s1)
app.after(60000, app.destroy)
app.mainloop()
