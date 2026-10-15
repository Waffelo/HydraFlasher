"""Settings page: open/close, firmware info and release, live socket count, flashprog
binary, notifications, resets, .uf2 checks and the firmware update (file, GitHub
release via a file:// URL, wrong version, .uf2 dropped on a card)."""
import json
import os
import shutil
import struct
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
import serial.tools.list_ports


class FakePort:
    device = "/dev/null"
    description = "HydraFlasher"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


ports = [FakePort()]
serial.tools.list_ports.comports = lambda *a, **k: list(ports)
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg_settings.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump(
    {
        "jedec_cache": {"EF4018": {"chips": ["W25Q128.V"], "size_kb": 16384}},
        "job_times": {"write|1|x|1|16M|": 5.0},
        "recent_files": ["/tmp/a.bin"],
    },
    open(H.CONFIG_FILE, "w"),
)


def make_uf2(path, version, family=H.UF2_FAMILY_RP2040, blocks=3):
    """A small valid UF2 whose payload holds "HydraFlasher <version>"."""
    payload = (b"\x00" * 100 + f"HydraFlasher {version}".encode() + b"\x00" * 200) if version else b"\x00" * 300
    data = b""
    for i in range(blocks):
        chunk = payload[i * 256 : (i + 1) * 256].ljust(256, b"\x00")
        head = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, 0x10000000 + i * 256,
                           256, i, blocks, family)
        data += head + chunk + b"\x00" * (476 - 256) + struct.pack("<I", 0x0AB16F30)
    open(path, "wb").write(data)


fw = {"name": "HydraFlasher 1.1", "next": "HydraFlasher 1.2"}
drive = os.path.join(here, "RPI-RP2")
state = {"boot": False, "bootsel_calls": 0}


class FakeProgrammer:
    def __init__(self, port):
        if state["boot"]:
            raise H.serial.SerialException("no such port")
    def close(self): pass
    def command_map(self): return {H.S_CMD_Q_NUM_CS, H.S_CMD_Q_PGMNAME, H.S_CMD_BOOTSEL}
    def num_cs(self): return 10
    def pgm_name(self): return fw["name"]
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return ("EF4018", f"U{cs}") if cs < 2 else (None, None)

    def bootsel(self):
        state["bootsel_calls"] += 1
        state["boot"] = True
        ports.clear()  # the serial port goes away, the drive shows up
        os.makedirs(drive, exist_ok=True)


def fake_find_drive(mount=True):
    return drive if state["boot"] else None


H.Programmer = FakeProgrammer
H.find_rp2_drive = fake_find_drive
H.latest_fw_release = lambda timeout=10: None  # replaced per step

app = H.HydraUI()
app._set_led = lambda *a: None
app.notify = lambda *a: None
asks, alerts = [], []
app._ask = lambda title, msg, **k: (asks.append((title, msg)), True)[1]
app._alert = lambda title, msg, **k: alerts.append((title, msg))
results = []
orig = app._report_job
app._report_job = lambda ok, msg: (results.append((ok, msg)), orig(ok, msg))
real_wait = app._wait_for_pico


def wait_for_pico(timeout=20):
    # The copy is done: the Pico restarts with the new firmware
    if state["boot"] and os.listdir(drive):
        state["boot"] = False
        fw["name"] = fw["next"]
        ports[:] = [FakePort()]
    return real_wait(timeout)


app._wait_for_pico = wait_for_pico


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
    # Open / close
    app._toggle_settings()
    app.update()
    check("settings page shown, main view hidden",
          app._settings_view.winfo_ismapped() and not app._top_view.winfo_ismapped())
    check("full page: status line, log and footer hidden",
          not any(w.winfo_ismapped() for w in (app._status_line, app._log_panel, app._footer)))
    body = app._settings_body
    check("settings fill the window", app._settings_view.winfo_height() > 500)
    # Mouse wheel (X11: buttons 4/5) scrolls; needs content taller than the window
    app.geometry("860x700")
    app.update()
    canvas = body._parent_canvas
    before = canvas.yview()[0]
    app._settings_widgets["binary"]._label.event_generate("<Button-5>", x=5, y=5)
    app.update()
    check("mouse wheel scrolls the settings", canvas.yview()[0] > before)
    app._settings_widgets["binary"]._label.event_generate("<Button-4>", x=5, y=5)
    app.update()
    check("and back up", canvas.yview()[0] == before)
    app.geometry("1000x900")
    app.update()
    # Version and credit
    app.after(800, lambda: None)
    opened = []
    H.webbrowser.open = lambda url: opened.append(url)
    for lbl in app._credit_links:
        lbl._label.event_generate("<Button-1>", x=2, y=2)
    check("credit links open the repository",
          opened == [H.REPO_URL, H.REPO_URL] and app._credit_links[0].cget("text") == "Vibed by Waffelo")
    called = []
    app._shortcut(lambda: called.append(1))
    check("operation shortcuts ignored on the settings page", not called)
    w = app._settings_widgets
    check("firmware info: version and CS lines",
          w["fw_pico"].cget("text") == "HydraFlasher 1.1 · 10 CS lines")
    check("version shown", w["version"].cget("text").startswith(f"HydraFlasher {H.APP_VERSION}"))
    app.event_generate("<Escape>")
    app.update()
    check("Esc goes back", not app._settings_open and app._top_view.winfo_ismapped())
    order = [x.winfo_y() for x in (app._top_view, app._status_line, app._log_panel, app._footer)]
    check("main view restored in order", all(x.winfo_ismapped() for x in
          (app._status_line, app._log_panel, app._footer)) and order == sorted(order))
    app._open_settings()

    # Release info
    app._release_checked({"version": "1.2", "tag": "fw-v1.2", "name": "x.uf2",
                          "url": "u", "size": 1, "date": "2026-10-20"})
    check("newer release: update available",
          "update available" in w["fw_latest"].cget("text"))
    app._release_checked({"error": "timed out"})
    check("failed check shown", "couldn't check: timed out" in w["fw_latest"].cget("text"))
    app._release_checked({})
    check("no release yet", "no firmware release" in w["fw_latest"].cget("text"))
    app._update_firmware_latest()
    check("install latest without a release refused", "No firmware release" in log_text())

    # Live socket count
    app._on_sockets_choice("6")
    app.update()
    check("6 sockets: cards and ALL rebuilt", sorted(app.cards) == [0, 1, 2, 3, 4, 5, H.CS_ALL])
    check("6 sockets: state sized", len(app.slots) == len(app._socket_ident) == 6)
    check("6 sockets: Alt+6 bound", bool(app.bind("<Alt-Key-6>")))
    check("6 sockets: header", "6× SOIC-16" in app._subtitle.cget("text"))
    check("6 sockets: saved", json.load(open(H.CONFIG_FILE))["sockets"] == 6)
    check("6 sockets: chips seen before are kept", app.slots[0] and app.slots[0]["detected"])
    app._select_target(5)
    app._on_sockets_choice("1")
    app.update()
    check("1 socket: no ALL card, target CS0",
          sorted(app.cards) == [0] and app._selected_cs() == 0 and not app.bind("<Alt-Key-a>"))
    app.busy = True
    app._on_sockets_choice("4")
    app.busy = False
    check("refused while busy", app.num_slots == 1 and app._sockets_var.get() == "1")
    app._on_sockets_choice("4")
    check("back to 4", app.num_slots == 4 and sorted(app.cards) == [0, 1, 2, 3, H.CS_ALL])
    app.after(1500, s2)  # one scan with the new count


def s2():
    check("scan after the change sees the chips", app.slots[1] and app.slots[1]["detected"])
    # flashprog binary
    fake = os.path.join(here, "fakeprog.py")
    open(fake, "w").write("#!/bin/sh\necho 'flashprog p9.9 on Linux'\n")
    os.chmod(fake, 0o755)
    asks.clear()
    app._set_binary(fake)
    check("binary set (answers like flashprog: no question)",
          app.flashrom_path == fake and not asks and app._binary_version == "flashprog p9.9")
    check("binary saved", json.load(open(H.CONFIG_FILE))["flashrom_path"] == fake)
    other = os.path.join(here, "other.sh")
    open(other, "w").write("#!/bin/sh\necho hello\n")
    os.chmod(other, 0o755)
    app._set_binary(other)
    check("unknown binary asks first", asks and "doesn't answer like" in asks[-1][1])
    app.flashrom_path = os.path.join(here, "fakeflash.py")

    # Notifications, resets
    app.notify_var.set(False)
    check("notifications off saved", json.load(open(H.CONFIG_FILE))["notifications"] is False)
    app._reset_estimates()
    app._clear_recent_files()
    app._forget_chips()
    cfg = json.load(open(H.CONFIG_FILE))
    check("resets saved", cfg["job_times"] == {} and cfg["recent_files"] == []
          and cfg["jedec_cache"] == {})
    check("counts on the page", app._settings_widgets["jedec"].cget("text").startswith("0 "))
    app.after(2500, s3)  # forgetting chips starts an identify probe


def s3():
    # .uf2 checks
    good, other, bad = (os.path.join(here, n) for n in ("good.uf2", "other.uf2", "bad.uf2"))
    make_uf2(good, "1.2")
    make_uf2(other, "1.2", family=0xE48BFF59)  # RP2350
    open(bad, "wb").write(b"x" * 700)
    check("uf2: version found", H.read_uf2(good)["version"] == "1.2")
    for path, why in ((other, "another chip"), (bad, "not a UF2")):
        try:
            H.read_uf2(path)
            check(f"uf2 refused: {why}", False)
        except ValueError as e:
            check(f"uf2 refused: {why}", why in str(e))
    alerts.clear()
    check("bad file never starts a job", app._start_firmware_update(path=bad) is False
          and alerts and not app.busy)

    # Update from a file
    asks.clear()
    app._start_firmware_update(path=good)
    check("asks before installing", asks and "Install firmware 1.2" in asks[-1][1]
          and "HydraFlasher 1.1" in asks[-1][1])
    app.update()
    check("installing goes back to the main view (log visible)",
          not app._settings_open and app._log_panel.winfo_ismapped())
    wait_idle(s4)


def s4():
    check("restart command sent", state["bootsel_calls"] == 1)
    check("file copied to the drive", os.path.isfile(os.path.join(drive, "hydraflasher.uf2")))
    check("update succeeded", results and results[-1][0]
          and "Firmware 1.2 installed · 10 CS lines" in results[-1][1])
    app._open_settings()
    check("page shows the new version",
          app._settings_widgets["fw_pico"].cget("text").startswith("HydraFlasher 1.2"))
    t = log_text()
    check("steps logged", all(s in t for s in ("Restart the Pico into its bootloader",
                                                 "Copy the firmware", "Check the new firmware")))
    # Latest release through a file:// URL (real download code)
    shutil.rmtree(drive)
    src = os.path.join(here, "hydraflasher-fw-1.3.uf2")
    make_uf2(src, "1.3")
    fw["next"] = "HydraFlasher 1.3"
    app._release_checked({"version": "1.3", "tag": "fw-v1.3", "name": "hydraflasher-fw-1.3.uf2",
                          "url": "file://" + src, "size": os.path.getsize(src),
                          "date": "2026-10-21"})
    check("latest button names the version", "1.3" in app._fw_latest_button.cget("text"))
    app._update_firmware_latest()
    wait_idle(s5)


def s5():
    check("release downloaded into the data folder",
          os.path.isfile(os.path.join(here, "firmware", "hydraflasher-fw-1.3.uf2")))
    check("release installed", results and results[-1][0] and "Firmware 1.3" in results[-1][1])
    # The Pico comes back with another version than the file: failure
    shutil.rmtree(drive)
    wrong = os.path.join(here, "wrong.uf2")
    make_uf2(wrong, "1.4")
    fw["next"] = "HydraFlasher 1.1"
    app._start_firmware_update(path=wrong)
    wait_idle(s6)


def s6():
    check("version mismatch fails", results and not results[-1][0]
          and "reports HydraFlasher 1.1, not 1.4" in results[-1][1])
    # A .uf2 dropped on a socket card updates the firmware, it's never written to a chip
    shutil.rmtree(drive)
    fw["next"] = "HydraFlasher 1.2"
    asks.clear()
    app._close_settings()
    app._handle_drop([os.path.join(here, "good.uf2")], 0)
    check("dropped .uf2 asks to install firmware",
          asks and asks[-1][0] == "Update firmware")
    wait_idle(s7)


def s7():
    check("dropped .uf2 installed, no write job",
          results and results[-1][0] and "Firmware 1.2" in results[-1][1]
          and "Write" not in log_text().split("Update firmware → 1.2")[-1])
    app.destroy()


app.after(2500, s1)
app.after(90000, app.destroy)
app.mainloop()
