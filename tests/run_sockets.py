"""Configurable socket count ("sockets" in the config) and the firmware's CS lines.
six: old firmware (6 CS, broadcast cs=6) end to end: scan, cards, shortcuts, single
write, broadcast write + per-socket verify. ten: current firmware (10 CS, asked via
S_CMD_Q_NUM_CS, broadcast cs=10), Alt+0. old8: 8 sockets on old firmware (CS6/CS7
refused, never scanned). two/one: fewer sockets, saved targets. bad_*: warning, 4.
Each case runs in its own process: the window is built once per start."""
import json
import os
import shutil
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))

if len(sys.argv) < 4:  # parent: run every case and pass its PASS/FAIL lines on
    for case in ("six", "ten", "old8", "two", "one", "bad_text", "bad_eleven", "bad_zero"):
        try:
            out = subprocess.run(
                [sys.executable, os.path.abspath(__file__), sys.argv[1], sys.argv[2], case],
                capture_output=True, text=True, timeout=100,
            ).stdout
        except subprocess.TimeoutExpired as e:  # a hang must fail, not skip the rest
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            print(f"FAIL [{case}] timed out after 100 s", flush=True)
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
sockets = {"six": 6, "ten": 10, "old8": 8, "two": 2, "one": 1,
           "bad_text": "6", "bad_eleven": 11, "bad_zero": 0}[case]
new_fw = case == "ten"  # firmware answering S_CMD_Q_NUM_CS with 10
fw_cs = 10 if new_fw else 6
json.dump(
    {
        "sockets": sockets,
        # last target from a 6-socket setup; ALL as older versions saved it
        "cs": "All chips (CS6)" if case in ("one", "two") else "6 (CS5)",
        "fast_verify": False,
        "partial_write": False,
        "jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}},
    },
    open(H.CONFIG_FILE, "w"),
)
idents = {cs: ("EF4018", f"U{cs}") for cs in range(10)}  # a chip on every CS line
scanned, naks = set(), []


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return {H.S_CMD_Q_NUM_CS} if new_fw else set()
    def num_cs(self): return 10
    def set_speed(self, hz): pass
    def pins(self, on): pass

    def identify(self, cs):
        if cs >= fw_cs:  # the firmware NAKs a CS line it doesn't have
            naks.append(cs)
            raise H.ProgrammerError("NAK")
        scanned.add(cs)
        return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
# fakeflash.py reads chips.txt and logs to calls.log next to itself
fake = os.path.join(work, "fakeflash.py")
shutil.copy(os.path.join(here, "fakeflash.py"), fake)  # keeps it executable
open(os.path.join(work, "chips.txt"), "w").write(" ".join(str(c) for c in range(11)))
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
    check("live scan reads only these sockets", scanned == set(range(min(n, fw_cs))))
    check("no socket the firmware lacks was asked", not naks)
    check("config keeps the count", json.load(open(H.CONFIG_FILE))["sockets"] == n)
    if n < 10:
        check(f"Alt+{n} bound, Alt+{n + 1} not",
              bool(app.bind(f"<Alt-Key-{n}>")) and not app.bind(f"<Alt-Key-{n + 1}>"))
    else:
        check("Alt+0 bound for socket 10", bool(app.bind("<Alt-Key-0>")))
    check("firmware CS lines detected", app._fw_cs == fw_cs)


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


def s_ten():
    common(10)
    check("CS9 identified", app.slots[9] and app.slots[9]["detected"])
    check("ALL card counts 10 chips", "10 identical" in app.cards[H.CS_ALL]._texts[1])
    app.focus_force()
    app.update()
    app.event_generate("<Alt-Key-0>")
    app.update()
    check("Alt+0 selects CS9", app._selected_cs() == 9)
    app._write_file(img)
    wait_idle(s_ten_all)


def s_ten_all():
    check("write ran on CS9", any("cs=9" in c and "-w" in c for c in calls()))
    open(calls_log, "w").close()
    app._select_target(H.CS_ALL)
    app._write_file(img)
    wait_idle(s_ten_done)


def s_ten_done():
    c = calls()
    check("broadcast erase + write over cs=10",
          any("cs=10" in x and "-E" in x for x in c) and any("cs=10" in x and "-w" in x for x in c))
    check("no cs=6 broadcast on the new firmware", not any("cs=6" in x and "-w" in x for x in c))
    verified = sorted({int(x.split("cs=")[1].split()[0]) for x in c if "-v" in x})
    check("each of the 10 sockets verified on its own", verified == list(range(10)))
    check("broadcast write succeeded", results and results[-1][0]
          and "10 chip(s)" in results[-1][1])
    app.destroy()


def s_old8():
    common(8)
    check("warning: firmware has fewer CS lines",
          "firmware has 6 CS lines" in log_text())
    check("CS6 and CS7 cards say so",
          all(app.cards[c]._texts[1] == "no CS line in firmware" for c in (6, 7)))
    n = len(app._queue)
    ok = app._start_write(img, 7)
    check("job on CS7 refused", ok is False and not app.busy and len(app._queue) == n
          and "CS7 has no CS line" in log_text())
    app._select_target(H.CS_ALL)
    app._write_file(img)
    wait_idle(s_old8_done)


def s_old8_done():
    c = calls()
    check("broadcast stays cs=6 on old firmware",
          any("cs=6" in x and "-w" in x for x in c) and not any("cs=10" in x for x in c))
    verified = sorted({int(x.split("cs=")[1].split()[0]) for x in c if "-v" in x})
    check("only the 6 real sockets verified", verified == list(range(6)))
    app.destroy()


def s_two():
    common(2)
    check("ALL saved by an older version still selected", app._selected_cs() == H.CS_ALL)
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


app.after(3000, {"six": s_six, "ten": s_ten, "old8": s_old8, "two": s_two,
                 "one": s_one}.get(case, s_bad))
app.after(80000, app.destroy)
app.mainloop()
