"""Socket names (cards, log, aligned tables, CSV keeps CS numbers), live scan on/off
with interval and speed, status LEDs on/off."""
import csv
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

H.CONFIG_FILE = os.path.join(here, "cfg_prefs.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {
        "jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}},
        "socket_names": ["", "EC"],
        "live_scan": False,
        "scan_interval": "2 s",
        "scan_speed": "4M",
        "leds": False,
    },
    open(H.CONFIG_FILE, "w"),
)
calls = {"identify": 0, "cmdmap": 0, "speeds": []}
led_writes = []


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self):
        calls["cmdmap"] += 1
        return set()
    def set_speed(self, hz): calls["speeds"].append(hz)
    def pins(self, on): pass

    def identify(self, cs):
        calls["identify"] += 1
        return ("EF4018", f"U{cs}") if cs < 2 else (None, None)


class FakeSerial:  # what _set_led opens
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def write(self, data): led_writes.append(data[1])
    def read(self, n): return b"\x06"


H.Programmer = FakeProgrammer
H.serial.Serial = FakeSerial
open(os.path.join(here, "chips.txt"), "w").write("0 1")
img = os.path.join(here, "img.bin")
open(img, "wb").write(b"\x00" * (16 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash.py")
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def log_text():
    app._flush_log_buffer()
    return app.log.get("1.0", "end")


def wait_idle(then):
    def poll():
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(300, then)
    app.after(100, poll)


def s1():
    c0, c1 = app.cards[0], app.cards[1]
    check("named card: name as title, CS number as tag",
          c1._title_lbl.cget("text") == "EC" and c1._tag_lbl.cget("text") == "CS1")
    check("unnamed card unchanged",
          c0._title_lbl.cget("text") == "CS0" and c0._tag_lbl.cget("text") == "#1")
    check("scan off: firmware asked once, no chips read",
          calls["cmdmap"] == 1 and calls["identify"] == 0)
    check("scan off: socket header says so", "scan off" in app._sockets_label.cget("text"))
    check("LEDs off: no LED command at start", not led_writes)
    check("footer names the target", "target CS0" in app.footer_right.cget("text"))
    app._select_target(1)
    check("footer uses the name", "target CS1 EC" in app.footer_right.cget("text"))
    app._start_probe(1)
    wait_idle(s2)


def s2():
    t = log_text()
    check("job title uses the name", "Probe CS1 EC" in t)
    check("result uses the name", any("CS1 EC: W25Q128.V" in m for _, m in results))
    check("LEDs off: no LED command during a job", not led_writes)
    rows = app._slot_rows([dict(app.slots[1], cs=1), {"cs": 0, "detected": False}])
    check("socket table: labels padded to the same width",
          rows[0][0].startswith("CS1 EC  ●") and rows[1][0].startswith("CS0     ○"))
    check("result rows aligned", H.result_row(app._sock_col(0), "✓ match", 1.0).index("✓")
          == H.result_row(app._sock_col(1), "✓ match", 1.0).index("✓"))
    app._write_file(img)
    wait_idle(s3)


def s3():
    check("write title uses the name", "Write img.bin → CS1 EC" in log_text())
    rows = list(csv.DictReader(open(H.FLASH_LOG)))
    check("CSV flash log keeps the plain CS number", rows and rows[-1]["socket"] == "CS1")
    # Settings: rename, clean-up, remove
    app._open_settings()
    app.update()
    e = app._name_entries
    check("one name field per socket", sorted(e) == [0, 1, 2, 3]
          and e[1].get() == "EC")
    e[0].insert(0, "  boot   rom  chip  extra long  ")
    e[0]._entry.event_generate("<FocusOut>")  # CTkEntry binds on its inner entry
    app.update()
    check("name cleaned and cut to 16 characters",
          app.socket_names[0] == "boot rom chip ex" and e[0].get() == "boot rom chip ex")
    check("card renamed at once", app.cards[0]._title == "boot rom chip ex"
          and app.cards[0]._tag_lbl.cget("text") == "CS0")
    check("name saved", json.load(open(H.CONFIG_FILE))["socket_names"][0] == "boot rom chip ex")
    e[1].delete(0, "end")
    app._set_socket_name(1, "")
    check("name removed: card back to CS1 / #2",
          app.cards[1]._title == "CS1" and app.cards[1]._tag_lbl.cget("text") == "#2")
    app._on_sockets_choice("10")
    app.update()
    check("name fields follow the socket count", sorted(app._name_entries) == list(range(10)))
    app._close_settings()
    app.update()
    app.after(500, s4)


def s4():
    title = app.cards[0]._title_lbl.cget("text")
    check("long name shortened on a narrow card", title.endswith("…") and len(title) < 16)
    app._open_settings()
    app._on_sockets_choice("4")
    app._close_settings()
    # LEDs on: idle at once, then the job states
    app.leds_var.set(True)
    check("LEDs on: switched to idle", led_writes == [H.LED["IDLE"]])
    app._start_probe(0)
    wait_idle(s5)


def s5():
    check("LEDs on: running and result sent",
          H.LED["RUNNING"] in led_writes and H.LED["SUCCESS"] in led_writes)
    led_writes.clear()
    app.leds_var.set(False)
    check("LEDs off: switched off once", led_writes == [H.LED["IDLE"]])
    led_writes.clear()
    # Scan on: chips read at the chosen speed
    n = calls["identify"]
    app.scan_var.set(True)
    check("scan on: header says live", "· live ·" in app._sockets_label.cget("text"))
    app.after(2600, lambda: s6(n))


def s6(n):
    check("scan on: chips read again", calls["identify"] > n)
    check("scan speed from the settings (4M)", 4_000_000 in calls["speeds"])
    cfg = json.load(open(H.CONFIG_FILE))
    check("settings saved", cfg["live_scan"] is True and cfg["leds"] is False
          and cfg["scan_interval"] == "2 s" and cfg["scan_speed"] == "4M")
    check("no LED command while off", not led_writes)
    app.destroy()


app.after(2500, s1)
app.after(60000, app.destroy)
app.mainloop()
