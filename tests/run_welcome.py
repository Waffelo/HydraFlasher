"""Screenshot the welcome box and report pixel x of each row's right border."""
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[1])
import serial.tools.list_ports

serial.tools.list_ports.comports = lambda *a, **k: []
import HydraFlasher as H

H.CONFIG_FILE = os.path.join(here, "cfg8.json")
H.DATA_DIR = here
H.FLASH_LOG = os.path.join(here, "flash_log.csv")
app = H.HydraUI()
app._set_led = lambda *a: None
tb = app.log._textbox


def go():
    app.update()
    xs = []
    for line in range(1, 8):
        text = tb.get(f"{line}.0", f"{line}.end")
        col = max(text.rfind("│"), text.rfind("╮"), text.rfind("╯"))
        if col < 0:
            continue
        bbox = tb.bbox(f"{line}.{col}")
        xs.append(bbox[0] if bbox else None)
        print(f"row {line}: right border at x={xs[-1]}  {text!r}")
    print("PASS" if len(set(xs)) == 1 else "FAIL", "all right borders aligned")
    print("font:", app.fonts["log"].actual("family"))
    app._copy_log(all_text=True)
    box = app.clipboard_get().split("\n")[:7]
    print("\n".join(box))
    print("PASS" if not any("\t" in l for l in box) else "FAIL", "copied box has no tabs")
    print("PASS" if len({len(l) for l in box}) == 1 else "FAIL", "copied rows equally wide", {len(l) for l in box})
    if len(sys.argv) > 2:
        subprocess.run(["import", "-window", app.wm_frame(), sys.argv[2]])
    app.destroy()


app.after(1500, go)
app.after(15000, app.destroy)
app.mainloop()
