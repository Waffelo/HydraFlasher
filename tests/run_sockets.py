"""Configurable socket count ("sockets" in the config): 6 sockets end to end
(scan, cards, shortcuts, single write, broadcast write + per-socket verify), 2
sockets (a saved target beyond the count falls back), invalid values (warning, 4).
Each case runs in its own process: the window is built once per start."""
import json
import os
import shutil
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))

if len(sys.argv) < 4:  # parent: run every case and pass its PASS/FAIL lines on
    for case in ("six", "two", "one", "bad_text", "bad_seven", "bad_zero"):
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), sys.argv[1], sys.argv[2], case],
            capture_output=True, text=True, timeout=100,
        ).stdout
        lines = [l for l in out.splitlines() if l.startswith(("PASS", "FAIL"))]
        for l in lines:
            print(f"{l.split(' ', 1)[0]} [{case}] {l.split(' ', 1)[1]}", flush=True)
        if not lines:
            print(f"FAIL [{case}] no output:\n{out[-800:]}", flush=True)
    sys.exit(0)

case = sys.argv[3]
sys.path.insert(0, sys.argv[1])
import serial.tools.list_ports


class FakePort:
    device = "/dev/null"
    description = "HydraFlasher"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


serial.tools.list_ports.comports = lambda *a, **k: [FakePort()]
import HydraFlasher as H

work = os.path.join(here, case)
os.makedirs(work, exist_ok=True)
H.CONFIG_FILE = os.path.join(work, "cfg.json")
H.DATA_DIR = work
H.FLASH_LOG = os.path.join(work, "flash_log.csv")
sockets = {"six": 6, "two": 2, "one": 1, "bad_text": "6", "bad_seven": 7, "bad_zero": 0}[case]
json.dump(
    {
        "sockets": sockets,
        # last target from a 6-socket setup (ALL for the single-socket case)
        "cs": "All chips (CS6)" if case == "one" else "6 (CS5)",
        "fast_verify": False,
        "partial_write": False,
        "jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}},
    },
    open(H.CONFIG_FILE, "w"),
)
idents = {cs: ("EF4018", f"U{cs}") for cs in range(6)}  # a chip on every CS line
scanned = set()


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass

    def identify(self, cs):
        scanned.add(cs)
        return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
# fakeflash.py reads chips.txt and logs to calls.log next to itself
fake = os.path.join(work, "fakeflash.py")
shutil.copy(os.path.join(here, "fakeflash.py"), fake)  # keeps it executable
open(os.path.join(work, "chips.txt"), "w").write("0 1 2 3 4 5 6")
calls_log = os.path.join(work, "calls.log")
open(calls_log, "w").close()
img = os.path.join(work, "img.bin")
open(img, "wb").write(b"\x00" * (16 << 20))

app = H.HydraUI()
app.flashrom_path = fake
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
app._ask_open_file = lambda *a, **k: img
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def calls():
    return [c for c in open(calls_log).read().split("\n") if c]


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


def common(n):
    targets = list(range(n)) + ([H.CS_ALL] if n > 1 else [])  # one socket: no ALL
    check(f"{n} socket cards" + (" + ALL" if n > 1 else ", no ALL"),
          sorted(app.cards) == targets)
    check("targets in the socket menu", list(app.cs_map.values()) == targets)
    check("Alt+A only with ALL", bool(app.bind("<Alt-Key-a>")) == (n > 1))
    check("per-socket state sized to the count",
          len(app.slots) == len(app._socket_result) == len(app._socket_ident) == n)
    check("live scan reads only these sockets", scanned == set(range(n)))
    check("config keeps the count", json.load(open(H.CONFIG_FILE))["sockets"] == n)
    check(f"Alt+{n} bound, Alt+{n + 1} not",
          bool(app.bind(f"<Alt-Key-{n}>")) and not app.bind(f"<Alt-Key-{n + 1}>"))


def s_six():
    common(6)
    check("saved target CS5 kept", app._selected_cs() == 5)
    check("CS4 and CS5 identified", all(app.slots[c] and app.slots[c]["detected"] for c in (4, 5)))
    check("ALL card counts 6 chips", "6 identical" in app.cards[H.CS_ALL]._texts[1])
    app.focus_force()
    app.update()
    app.event_generate("<Alt-Key-5>")
    app.update()
    check("Alt+5 selects CS4", app._selected_cs() == 4)
    app._write_file(img)
    wait_idle(s_six_all)


def s_six_all():
    check("write ran on CS4", any("cs=4" in c and "-w" in c for c in calls()))
    check("CS4 write succeeded", results and results[-1][0])
    open(calls_log, "w").close()
    app._select_target(H.CS_ALL)
    app._write_file(img)
    wait_idle(s_six_done)


def s_six_done():
    c = calls()
    check("broadcast erase + write over CS6",
          any("cs=6" in x and "-E" in x for x in c) and any("cs=6" in x and "-w" in x for x in c))
    verified = sorted({int(x.split("cs=")[1].split()[0]) for x in c if "-v" in x})
    check("each of the 6 sockets verified on its own", verified == list(range(6)))
    check("broadcast write succeeded", results and results[-1][0]
          and "6 chip(s)" in results[-1][1])
    app.destroy()


def s_two():
    common(2)
    check("saved target CS5 falls back to CS0", app._selected_cs() == 0)
    check("ALL counts only 2 chips", "2 identical" in app.cards[H.CS_ALL]._texts[1])
    check("no warning for a valid count", 'Config "sockets"' not in log_text())
    app.destroy()


def s_one():
    common(1)
    check("saved ALL target falls back to CS0", app._selected_cs() == 0)
    def texts(w):
        out = [w.cget("text")] if isinstance(w, H.ctk.CTkLabel) else []
        return out + [t for c in w.winfo_children() for t in texts(c)]
    labels = texts(app)
    check("header says 1×", any("1× SOIC-16" in t for t in labels))
    check("shortcut hint says only alt+1",
          any("(alt+1)" in t for t in labels) and not any("alt+a" in t for t in labels))
    check("tips don't mention ALL or other sockets",
          "with ALL" not in log_text() and "other sockets" not in log_text()
          and "3. Write – blank check" in log_text())
    app._write_file(img)
    wait_idle(s_one_done)


def s_one_done():
    check("write on the only socket", any("cs=0" in c and "-w" in c for c in calls()))
    app.destroy()


def s_bad():
    common(4)
    check("warning logged", f'Config "sockets": {json.dumps(sockets)} is not a number'
          in log_text())
    app.destroy()


app.after(3000, {"six": s_six, "two": s_two, "one": s_one}.get(case, s_bad))
app.after(80000, app.destroy)
app.mainloop()
