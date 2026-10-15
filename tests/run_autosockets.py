"""Socket count from the detected wiring (S_CMD_Q_CS_WIRED): auto by default, live
changes, gaps ("not wired": not scanned, probed or usable), manual override, the
count waiting for a running job, the fallback count, old firmware, and starting
with the last detected count. Each case runs in its own process."""
import json
import os
import shutil
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))

if len(sys.argv) < 4:  # parent: run every case and pass its PASS/FAIL lines on
    for case in ("main", "oldfw", "cache"):
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), sys.argv[1], sys.argv[2], case],
            capture_output=True, text=True, timeout=110,
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
cfg = {"jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}}
cfg.update({"oldfw": {"sockets": 6}, "cache": {"sockets_detected": 7}}.get(case, {}))
json.dump(cfg, open(H.CONFIG_FILE, "w"))
state = {"wired": 0b1111 if case != "cache" else 0b1111111, "identified": []}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass

    def command_map(self):
        caps = {H.S_CMD_Q_NUM_CS, H.S_CMD_Q_PGMNAME}
        return caps if case == "oldfw" else caps | {H.S_CMD_Q_CS_WIRED}

    def num_cs(self): return 10
    def pgm_name(self): return "HydraFlasher 1.1" if case == "oldfw" else "HydraFlasher 1.2"
    def cs_wired(self): return state["wired"]
    def set_speed(self, hz): pass
    def pins(self, on): pass

    def identify(self, cs):
        state["identified"].append(cs)
        return ("EF4018", f"U{cs}")


H.Programmer = FakeProgrammer
fake = os.path.join(work, "fakeflash.py")
shutil.copy(os.path.join(here, "fakeflash.py"), fake)
open(os.path.join(work, "chips.txt"), "w").write(" ".join(str(c) for c in range(10)))
calls_log = os.path.join(work, "calls.log")
open(calls_log, "w").close()
first_count = []

app = H.HydraUI()
first_count.append(app.num_slots)
app.flashrom_path = fake
app._set_led = lambda *a, **k: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def log_text():
    app._flush_log_buffer()
    return app.log.get("1.0", "end")


def summary():
    app._open_settings()
    text = app._settings_widgets["sockets"].cget("text")
    app._close_settings()
    return text


def cfg_now():
    return json.load(open(H.CONFIG_FILE))


def wait_idle(then):
    def poll():
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(300, then)
    app.after(100, poll)


def calls():
    return [c for c in open(calls_log).read().split("\n") if c]


# ---------------------------------------------------------------- main
def m1():
    check("auto by default, 4 wired sockets detected", app.sockets_auto and app.num_slots == 4
          and sorted(app.cards) == [0, 1, 2, 3, H.CS_ALL])
    check("detection logged", "Sockets wired: CS0–CS3" in log_text())
    check("settings say detected", summary() == "detected CS0–CS3")
    check("detected count remembered", cfg_now()["sockets_detected"] == 4
          and cfg_now()["sockets_auto"] is True)
    state["wired"] = 0b111111
    app._detect_sockets()
    app.after(800, m2)


def m2():
    check("detect again: 6 sockets, live", app.num_slots == 6 and "Socket count: 6 (detected)" in log_text())
    state["wired"] = 0b1011  # CS2 not wired
    app._detect_sockets()
    app.after(800, m3)


def m3():
    check("gap: count up to the highest (CS3)", app.num_slots == 4)
    check("gap: CS2 card says not wired", app.cards[2]._texts == ("—", "not wired"))
    check("gap: ALL counts only the wired sockets",
          app.cards[H.CS_ALL]._texts[1] == "3 identical chips"
          and sorted(d["cs"] for d in app._detected_slots()) == [0, 1, 3])
    check("gap: settings name the gap", summary() == "detected CS0, CS1, CS3 (CS2 not wired)")
    state["identified"].clear()
    app.after(2500, m4)


def m4():
    check("gap: CS2 not scanned", state["identified"] and 2 not in state["identified"])
    check("gap: job on CS2 refused", app._start_probe(2) is None and not app.busy
          and "isn't wired (no pull-up detected)" in log_text())
    app._start_probe_all()
    wait_idle(m5)


def m5():
    probed = {int(c.split("cs=")[1].split()[0]) for c in calls()}
    check("gap: probe all skips CS2", probed == {0, 1, 3})
    # Manual overrides the detection
    app._on_sockets_choice("6")
    check("manual: 6 sockets", app.num_slots == 6 and not app.sockets_auto)
    check("manual: CS2 usable again", app.cards[2]._texts[1] != "not wired")
    check("manual: saved", cfg_now()["sockets"] == 6 and cfg_now()["sockets_auto"] is False)
    check("manual: settings say so", summary() == "manual · CS0–CS5")
    app._on_sockets_choice("auto")
    check("back to auto: detected count again", app.num_slots == 4 and app.sockets_auto)
    # A detection while a job runs waits for it
    open(calls_log, "w").close()
    app._start_probe(0)
    state["wired"] = 0b11111
    app._set_wired(0b11111)
    check("busy: count waits", app.num_slots == 4 and app._sockets_pending)
    wait_idle(m6)


def m6():
    check("busy: applied after the job", app.num_slots == 5 and not app._sockets_pending)
    # No pull-ups seen (on connect): auto switches to manual, once
    wired_logs = log_text().count("Sockets wired:")
    app._set_wired(0)
    check("no pull-ups: switched to manual, manual count (6)",
          not app.sockets_auto and app.num_slots == 6 and cfg_now()["sockets_auto"] is False)
    check("no pull-ups: one clear warning", log_text().count("No pull-up resistors found") == 1)
    check("settings explain", summary() == "manual (no pull-ups found) · CS0–CS5")
    # Flaky wiring: reconnects alternate pull-ups / none -- nothing flips any more
    for bits in (0b1111, 0, 0b1111, 0):
        app._reset_sockets()  # what a disconnect does
        app._set_wired(bits)  # what the next connect's detection does
    check("flaky reconnects: stays manual with the same count",
          not app.sockets_auto and app.num_slots == 6)
    check("flaky reconnects: no new warnings", log_text().count("No pull-up resistors found") == 1)
    check("flaky reconnects: wiring logged only when it changed",
          log_text().count("Sockets wired:") == wired_logs + 1)
    # detect again with pull-ups: back to auto
    state["wired"] = 0b1111
    app._detect_sockets()
    app.after(800, m7)


def m7():
    check("detect again with pull-ups: back to auto, 4 sockets",
          app.sockets_auto and app.num_slots == 4 and cfg_now()["sockets_auto"] is True)
    check("back to auto logged", "socket count back to auto" in log_text())
    # The same wiring after a reconnect isn't logged again
    n = log_text().count("Sockets wired:")
    app._reset_sockets()
    app._set_wired(0b1111)
    check("same wiring after a reconnect: not logged again", log_text().count("Sockets wired:") == n)
    # detect again without pull-ups while in auto: manual again
    state["wired"] = 0
    app._detect_sockets()
    app.after(800, m8)


def m8():
    check("detect again without pull-ups: manual", not app.sockets_auto and app.num_slots == 6)
    # The auto fallback count (no detection support): set while in auto
    app._on_sockets_choice("auto")
    app._wired = None
    app._open_settings()
    app._on_fallback_choice("3")
    app._close_settings()
    check("fallback count used without a detection", app.num_slots == 3 and cfg_now()["sockets"] == 3)
    app.destroy()


# ---------------------------------------------------------------- old firmware
def o1():
    check("old firmware: manual count used", app.num_slots == 6 and app.sockets_auto)
    check("old firmware: settings explain",
          summary() == "the firmware can't detect (needs 1.2) · using 6")
    app._detect_sockets()
    check("old firmware: detect again explains", "needs version 1.2" in log_text())
    app.destroy()


# ---------------------------------------------------------------- cache
def c1():
    check("starts with the last detected count (no jump)", first_count == [7])
    check("still 7 after detection", app.num_slots == 7)
    app.destroy()


app.after(3000, {"main": m1, "oldfw": o1, "cache": c1}[case])
app.after(90000, app.destroy)
app.mainloop()
