"""One-by-one (ALL), queued look, auto-start removed, per-chip definitions, production."""
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

H.CONFIG_FILE = os.path.join(here, "cfg10.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {"jedec_cache": {
        "EF4019": {"chips": ["W25Q256FV", "W25Q256JV_Q"], "size_kb": 32768},
        "EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}}},
    open(H.CONFIG_FILE, "w"),
)
idents = {0: ("EF4019", "A"), 1: ("EF4018", "B"), 2: ("EF4019", "C")}


class FakeProgrammer:
    def __init__(self, port): pass
    def close(self): pass
    def command_map(self): return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return idents.get(cs, (None, None))


H.Programmer = FakeProgrammer
calls_log = os.path.join(here, "calls.log")
open(calls_log, "w").close()
img32 = os.path.join(here, "img32.bin")
open(img32, "wb").write(b"\x00" * (32 << 20))

app = H.HydraUI()
app.flashrom_path = os.path.join(here, "fakeflash5.py")
app._set_led = lambda *a: None
app.notify = lambda *a: None
app._ask = lambda *a, **k: True
app._ask_open_file = lambda *a, **k: img32
prompts = []
app._choose = lambda title, msg, opts, **k: (prompts.append(opts), 1)[1]  # W25Q256JV_Q
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))
seen_queued = {}


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def calls():
    return [c for c in open(calls_log).read().split("\n") if c]


def wait_idle(then):
    def poll():
        for cs in range(4):
            c = app.cards[cs]
            if c._state == "queued":
                seen_queued[cs] = (c._texts[1], c._detail_lbl.cget("text_color"), c.STATES["queued"][0])
        if app.busy or app._queue:
            app.after(100, poll)
        else:
            app.after(300, then)
    app.after(100, poll)


def s1():
    check("auto-start option removed", not hasattr(app, "autostart_var"))
    app._select_target(0)
    app.update()
    check("one-by-one hidden for a single socket", not app._one_by_one_toggle.winfo_ismapped())
    app._select_target(H.CS_ALL)
    app.update()
    check("one-by-one shown for ALL", app._one_by_one_toggle.winfo_ismapped())
    check("ALL + mixed chips (broadcast): write greyed", app._broadcast_write_error())
    app.one_by_one_var.set(True)
    app.update()
    check("one-by-one saved in config", json.load(open(H.CONFIG_FILE)).get("one_by_one") is True)
    check("ALL card: 3 chips · in turn", app.cards[H.CS_ALL]._texts == ("one-by-one", "3 chips · in turn"))
    check("footer: target ALL (one-by-one)", "target ALL (one-by-one)" in app.footer_right.cget("text"))
    check("ALL + mixed chips (one-by-one): write not greyed", not app._broadcast_write_error())
    app.write()
    check("one job per socket (1 running + 2 queued)", app.busy and len(app._queue) == 2)
    check("definition asked once, at queue time", len(prompts) == 1)
    wait_idle(s2)


def s2():
    c = calls()
    print("\n".join(c))
    print(results)
    q = seen_queued.get(2)
    check("queued card: clock + grey text + grey light",
          q and q[0].startswith(app._clock + " queued #") and q[1] == H.T["dim"] and q[2] == H.T["dim"])
    check("order CS0, CS1, CS2", [x.split()[0] for x in c] == ["cs=0", "cs=2"] or [x.split()[0] for x in c] == ["cs=0", "cs=2"])
    check("W25Q256 sockets got -c W25Q256JV_Q", all("-c W25Q256JV_Q" in x for x in c if x.startswith(("cs=0", "cs=2"))))
    check("CS1 refused on size, never sent to flashprog", not any(x.startswith("cs=1") for x in c))
    check("results: CS0 ok, CS1 size fail, CS2 ok",
          [r[0] for r in results] == [True, False, True] and "CS1 is 16.0 MiB" in results[1][1])
    # production batch, one-by-one
    open(calls_log, "w").close()
    del results[:]
    app.production_var.set(True)
    app.after(500, s3)


def s3():
    check("production on", app._prod is not None)
    app._run_batch()
    check("batch runs as one job", app.busy and not app._queue)
    wait_idle(s4)


def s4():
    print(results)
    check("batch: 1/3 failed (CS1)", results and not results[0][0] and "1/3 socket(s) failed (CS1)" in results[0][1])
    check("production counts ✓2 ✕1", (app._prod["ok"], app._prod["fail"]) == (2, 1))
    app.destroy()


app.after(2500, s1)
app.after(60000, app.destroy)
app.mainloop()
