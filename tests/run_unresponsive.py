"""A Pico that doesn't answer (other or hung firmware): marked once, scans paused,
jobs refused, LEDs skipped, the UI never waits on the port, closing doesn't wait for
unsent bytes, the firmware update asks for BOOTSEL at once; a Pico in bootloader
mode (RPI-RP2) is pointed out."""
import json
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
import serial.tools.list_ports


class FakePort:
    device = "/dev/null"
    description = "TinyUSB Device - TinyUSB CDC"
    manufacturer = product = hwid = ""
    vid = 0xCAFE


serial.tools.list_ports.comports = lambda *a, **k: [FakePort()]
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg_unresp.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
json.dump({}, open(H.CONFIG_FILE, "w"))
H.UNRESPONSIVE_PAUSE_S = 3
H.latest_fw_release = lambda timeout=10: {}
state = {"answer": False, "scans": 0, "bootsel": 0}


class FakeProgrammer:
    def __init__(self, port):
        state["scans"] += 1
    def close(self): pass
    def command_map(self):
        if not state["answer"]:
            raise H.serial.SerialTimeoutException("Write timeout")
        return set()
    def set_speed(self, hz): pass
    def pins(self, on): pass
    def identify(self, cs): return (None, None)
    def bootsel(self): state["bootsel"] += 1


led_opens = []


class SlowSerial:  # what _set_led opens: slow, like the hung Pico
    def __init__(self, *a, **k):
        led_opens.append(time.monotonic())
        time.sleep(1.5)
    def write(self, data): pass
    def read(self, n): return b""
    def reset_output_buffer(self): pass
    def close(self): pass


H.Programmer = FakeProgrammer
H.serial.Serial = SlowSerial
rp2 = {"present": False}
H.rp2_drive_present = lambda: rp2["present"]
H.find_rp2_drive = lambda mount=True: None

app = H.HydraUI()
app.notify = lambda *a: None
asks = []
app._ask = lambda title, msg, **k: (asks.append(title), True)[1]
app._alert = lambda *a, **k: None


def check(name, cond):
    print("PASS" if cond else "FAIL", name, flush=True)


def log_text():
    app._flush_log_buffer()
    return app.log.get("1.0", "end")


def s1():
    t = log_text()
    check("unresponsive Pico reported once", t.count("doesn't answer (") == 1
          and "hold BOOTSEL" in t.replace("Hold", "hold"))
    check("header says not responding",
          "not responding" in app.conn_label.cget("text"))
    n = state["scans"]
    app.after(1800, lambda: s2(n))


def s2(n):
    check("scans paused while it doesn't answer", state["scans"] == n)
    check("job refused with a hint", app._start_probe(0) is None and not app.busy
          and "doesn't answer – Probe CS0 not started" in log_text())
    led_opens.clear()
    app.leds_var.set(True)
    app._set_led(H.LED["RUNNING"])
    check("LED update skipped for an unresponsive Pico", not led_opens)
    app._open_settings()
    check("settings explain it",
          app._settings_widgets["fw_pico"].cget("text").startswith("not responding"))
    app._close_settings()
    # The firmware update doesn't try the restart command; it asks for BOOTSEL
    good = os.path.join(here, "x.uf2")
    import struct
    data = b""
    for i in range(2):
        data += struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x2000, i * 256, 256, i, 2,
                            H.UF2_FAMILY_RP2040) + (b"HydraFlasher 1.1".ljust(256, b"\0")
                            if i == 0 else b"\0" * 256) + b"\0" * 220 + struct.pack("<I", 0x0AB16F30)
    open(good, "wb").write(data)
    app._start_firmware_update(path=good)
    app.after(1500, s3)


def s3():
    check("no restart command to an unresponsive Pico", state["bootsel"] == 0)
    check("status line asks for BOOTSEL", "Hold BOOTSEL" in app._status)
    app._abort_requested = True  # stop waiting for the drive
    app.after(1500, s4)


def s4():
    # The Pico answers again after the pause: cleared
    state["answer"] = True
    app._unresponsive = ("/dev/null", 0)  # pause over
    app.after(2500, s5)


def s5():
    check("answers again: cleared", app._unresponsive is None
          and "not responding" not in app.conn_label.cget("text")
          and "answers again" in log_text())
    # The UI never waits for a slow port: _set_led returns at once
    app.after(2000, s5b)  # let LED updates from the aborted job finish


def s5b():
    led_opens.clear()
    t0 = time.monotonic()
    app._set_led(H.LED["IDLE"])
    app._set_led(H.LED["RUNNING"])
    app._set_led(H.LED["SUCCESS"])
    check("LED update returns at once (sent from a thread)", time.monotonic() - t0 < 0.2)
    app.after(4000, s6)


def s6():
    check("only the newest waiting LED state is sent (2 of 3)", len(led_opens) == 2)
    # Closing a port drops unsent bytes first (no 30 s drain wait)
    calls = []

    class Ser:
        def reset_output_buffer(self): calls.append("reset")
        def close(self): calls.append("close")
    H.close_serial(Ser())
    check("close_serial: unsent bytes dropped before close", calls == ["reset", "close"])
    # A Pico in bootloader mode is pointed out
    rp2["present"] = True
    app.after(2500, s7)


def s7():
    check("bootloader Pico noticed", "bootloader mode (RPI-RP2)" in log_text())
    app._open_settings()
    check("settings offer to install",
          "bootloader mode" in app._settings_widgets["fw_pico"].cget("text"))
    app.destroy()


app.after(3000, s1)
app.after(60000, app.destroy)
app.mainloop()
