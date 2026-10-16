import csv
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter
import tkinter.font
import urllib.request
import webbrowser
import zlib
import base64
from contextlib import contextmanager
from tkinter import filedialog, messagebox
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import customtkinter as ctk
import serial
import serial.tools.list_ports

# PyInstaller's bootloader points LD_LIBRARY_PATH at the bundled libraries (Linux), and
# every program started from the packaged app inherited it: kdialog didn't even start
# with the bundle's older liblzma, so the file picker did nothing. Programs started from
# here get the system's libraries again; this process keeps the bundled ones (the
# loader reads the variable only at startup).
if getattr(sys, "frozen", False) and "LD_LIBRARY_PATH" in os.environ:
    _system_libs = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if _system_libs is None:
        del os.environ["LD_LIBRARY_PATH"]
    else:
        os.environ["LD_LIBRARY_PATH"] = _system_libs

try:
    from plyer import notification
except ImportError:
    notification = None
# On Linux notify-send is called directly: plyer falls back to it when Python's dbus
# module is missing (as in the AppImage), but asks it for a notification that never
# closes ("-t 0")
NOTIFY_SEND = shutil.which("notify-send") if platform.system() == "Linux" else None
NOTIFICATIONS_AVAILABLE = bool(NOTIFY_SEND or notification)
NOTIFY_TIMEOUT_S = 6

try:
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

ctk.set_appearance_mode("dark")
if platform.system() == "Linux":
    # The default font-glyph corner drawing looks jagged with 1px borders on Linux
    ctk.DrawEngine.preferred_drawing_method = "polygon_shapes"

if platform.system() == "Windows":
    CONFIG_FILE = os.path.expanduser("~/.hydra_flasher.json")
    DATA_DIR = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), "HydraFlasher"
    )
else:
    CONFIG_FILE = os.path.expanduser("~/.config/hydra_flasher.json")
    DATA_DIR = os.path.expanduser("~/.local/share/hydra-flasher")
FLASH_LOG = os.path.join(DATA_DIR, "flash_log.csv")
FLASH_LOG_FIELDS = [
    "time",
    "mode",
    "batch",
    "socket",
    "jedec",
    "unique_id",
    "chip",
    "operation",
    "image",
    "image_crc32",
    "result",
    "seconds",
]

SERIAL_BAUD = 115200
LED = {"IDLE": 0, "RUNNING": 1, "SUCCESS": 2, "FAILED": 3}
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0
MAX_LOG_LINES = 5000

# serprog commands used directly (see firmware serprog.h)
S_ACK = 0x06
S_CMD_Q_CMDMAP = 0x02
S_CMD_Q_PGMNAME = 0x03
S_CMD_O_SPIOP = 0x13
S_CMD_S_SPI_FREQ = 0x14
S_CMD_S_PIN_STATE = 0x15
S_CMD_S_SPI_CS = 0x16
S_CMD_SET_LED = 0x20
S_CMD_BOOTSEL = 0x21
S_CMD_CRC32 = 0x22
S_CMD_Q_NUM_CS = 0x23  # number of CS pins; the broadcast index is the same number
S_CMD_Q_CS_WIRED = 0x24  # bitmap of CS lines with a pull-up (wired to a socket), fw 1.2

DEFAULT_SLOTS = 4
MAX_SLOTS = 10  # the firmware has 10 chip selects (CS0-CS9)
# Firmware without S_CMD_Q_NUM_CS: 6 chip selects, broadcast on index 6
LEGACY_CS_PINS = 6
# The broadcast target inside the GUI: above every socket number, so
# "cs < self.num_slots" means a single socket. flashprog gets the firmware's own
# broadcast index instead (job["broadcast"], see _build_command).
CS_ALL = MAX_SLOTS
CS_ALL_LABEL = "All chips (broadcast)"
CS_ALL_LABEL_OLD = "All chips (CS6)"  # saved by older versions
SPI_SPEEDS = ["4M", "8M", "12M", "16M", "20M", "24M", "30M", "36M", "40M", "48M"]
SCAN_INTERVAL_MS = 1000
SCAN_SPEED_HZ = 8_000_000
# Live scan choices on the settings page (label -> ms; SPI clock of the scan)
SCAN_INTERVALS = {"0.5 s": 500, "1 s": 1000, "2 s": 2000, "5 s": 5000}
SCAN_SPEEDS = ["1M", "2M", "4M", "8M", "12M"]
SOCKET_NAME_MAX = 16
# A Pico that doesn't answer (other or hung firmware) isn't polled for this long
UNRESPONSIVE_PAUSE_S = 30
ERASE_BLOCK = 64 * 1024  # granularity of partial writes
BLANK_CHECK_PIECE = 1024 * 1024  # granularity of the blank check
PARTIAL_MAX_USED = 0.9  # above this fraction of used blocks, just write everything
# Operations whose durations are remembered for the "~… left" estimate (see _eta_keys)
ETA_OPS = ("write", "read", "erase", "verify")
# Operations that only notify when they fail: a chip found is no news (see _job_op)
NOTIFY_FAILURE_ONLY = ("probe", "identify")
ETA_MAX_ENTRIES = 300
# Operations refused while a job runs or waits in the queue: operation -> reason
QUEUE_BLOCKED = {
    "probe": "Probing is not available during queue run.",
    "firmware": "The firmware can only be updated while no job runs or waits.",
}

# Must match the firmware's USB descriptors (usb_descriptors.c)
HYDRA_VID = 0xCAFE
PICO_VID = 0x2E8A

# The app's own version (semantic: minor for features, patch for fixes); the firmware
# has its own (FW_VERSION in firmware/main.c)
APP_VERSION = "1.0.0"

# Firmware updates: GitHub Releases tagged fw-v<version> carry the .uf2 (CI builds them)
GITHUB_REPO = "Waffelo/HydraFlasher"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
FW_TAG_PREFIX = "fw-v"
FW_NAME_PREFIX = "HydraFlasher "  # S_CMD_Q_PGMNAME of firmware 1.1+ ("pico-serprog" before)
FW_VERSION_RE = re.compile(rb"HydraFlasher (\d+\.\d+)")
UF2_MAGIC = (0x0A324655, 0x9E5D5157, 0x0AB16F30)  # start 0, start 1, end
UF2_FAMILY_RP2040 = 0xE48BFF56
RP2_LABEL = "RPI-RP2"  # the Pico bootloader's drive

FOUND_RE = re.compile(r'Found (.*?)\s*flash chip "([^"]+)" \((\d+) kB')
MULTI_MATCH = "Multiple flash chip definitions match"
NOISE_PATTERNS = [
    "flashprog is free software",
    "flashrom is free software",
    "Using clock_gettime",
    "serprog: Programmer name",
    "Warning: NAK to query operation buffer size",
    "get the source code at",
    "on Linux",
    "on Windows",
]

# ── Theme (Claude Code terminal look) ───────────────────────────────
THEME_DARK = {
    "bg": "#141413",
    "panel": "#1c1c1a",
    "panel_hi": "#262624",
    "border": "#34332f",
    "border_hi": "#57554e",
    "text": "#e8e6dc",
    "soft": "#b5b3a8",
    "dim": "#8b8981",
    "faint": "#55544f",
    "accent": "#d97757",
    "accent_hi": "#e8906f",
    "green": "#7ec27a",
    "red": "#e5655b",
    "yellow": "#e3b341",
    "led_empty": "#6e3b36",
}
# Text colours reach ~4.5:1 contrast on the panels (faint is meant to be dim)
THEME_LIGHT = {
    "bg": "#faf9f5",
    "panel": "#f2f0e8",
    "panel_hi": "#e8e5da",
    "border": "#d9d5c8",
    "border_hi": "#b9b4a5",
    "text": "#1f1e1b",
    "soft": "#4d4b45",
    "dim": "#6e6c64",
    "faint": "#a8a59b",
    "accent": "#b1532f",
    "accent_hi": "#a84f30",
    "green": "#2e7d32",
    "red": "#c62828",
    "yellow": "#8a5f00",
    "led_empty": "#e3b9b3",
}
# customtkinter takes (light, dark) pairs and switches with the appearance mode;
# plain Tk widgets need tk_color() and _apply_theme()
T = {k: (THEME_LIGHT[k], THEME_DARK[k]) for k in THEME_DARK}


def tk_color(c) -> str:
    """The current mode's value of a (light, dark) pair, for plain Tk widgets."""
    if isinstance(c, (tuple, list)):
        return c[0] if ctk.get_appearance_mode() == "Light" else c[1]
    return c
LOG_COLORS = {
    "success": T["green"],
    "error": T["red"],
    "info": T["soft"],
    "warning": T["yellow"],
    "normal": T["text"],
    "dim": T["dim"],
    "soft": T["soft"],
    "accent": T["accent"],
    "step": T["accent_hi"],
}
MONO_FONTS = [
    "JetBrains Mono",
    "JetBrainsMono Nerd Font Mono",
    "JetBrainsMono Nerd Font",
    "Fira Code",
    "Cascadia Mono",
    "Cascadia Code",
    "SF Mono",
    "Menlo",
    "Hack",
    "Consolas",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Noto Sans Mono",
    "Courier New",
]
SPINNER = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"]
BULLET = "⏺" if platform.system() == "Darwin" else "●"

# Header logo: the hydra mark from logo.svg (group g34, no wordmark/background),
# as PNGs at 1x and 2x so the GUI stays a single file. Regenerate with e.g.
#   inkscape logo.svg --export-id=g34 --export-id-only --export-height=20 \
#     --export-background-opacity=0 --export-filename=logo20.png
APP_ICON_PNG = {  # window/taskbar icon, from logo-mark.png (tools/make_icons.py)
    32: (
        "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"
        "rkJggg=="
    ),
    64: (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAWaElEQVR42u2bf5BcV3XnP+fe917/"
        "mJmenhnNjDTSWLKQLVuyI9nybzsIJ0sg2JgAGUwqENhNwtZWwm62liJsyCILUtmtJJUfJJsKLARC"
        "mTiRAhjYJbAY27KNZcmWLZAtW5Zk/RppRvO7Z7qn+/V79579o3tGki3J+mEDWfKqerqnq3/c8z3n"
        "fM/3nnMb/vX66b7kJ2ERmwYGbPfIyPxaRnt69LnNm3Uj+J9q76gimwYG7OvpqODHHH36+MBNuZrT"
        "twFLPdJqRKc87K3W5IciTxyFza4BhoqI6P9PKSAbQNbduS7bEoZ/21fI3j0dJ4Dg1FNN3Yz3PD5a"
        "je/7rW/vvH8SSpsGBuzd/7TZqf7LA0AAPdvzD777pv+eDezHqmkaoxIYIzYXGBQo190PXpwq/8lv"
        "Pfjs14DapoEBBlatUjZuVDn95/7kRYBuwMhG9OVAKAgDA0Y2b3YPvOumvy1kgn87HSdORAyqHiAb"
        "WqsK0/Xk/m3Hp/5s49a924EagAj4T2ww7N4trNqssvH8iPNHAsB33vczLW+594cVAB0YsKxapbAR"
        "2MDm3btlecdLBuClyaos8IXtkTVr4tQ5RGwz/72I0BpaU0lcfTZx3xmp1r6ybWR6y2d+cOjgT2wE"
        "PLR+fXD7li3pg+++8T8akf71q9/yu7Jx4xk9pCDfe+dNH2rJ2L+ZrafzAJyoCuqMEZsPLF6hXE8m"
        "Us8eaxltteHMUKX2+V/+1o5HblqyJHpicLD6I60CugHDRk6bk+qZ6W7LfOTh576z/v47r//qgenK"
        "7kW53OCC1iAVNUUx/nqDufFh1dUoS2YTx8uNb4S7WK9ouZ56QCJrO/Oh3KxAIQoYq5k24OmXRke7"
        "gONA5UcGwFzubRoYsAObNvt7BHlxZVkeYn2A1LeOVOrVyNrrF7UG13flQmLvESAKhIwNUVUSr4hX"
        "zkbzAoKIVUUVsCLE3oOAV63M8wqY842AC0oHbbxRH3jXLbfvr8fb3rN582zzk5TP7vAADw2sH/S+"
        "tl+RK8t151QIpfl99dT72DmPIiCCIPIqa1HQ0Io4r6UjM7VPj9fiZxa2ZH4eYRyopiIJ56gig5fZ"
        "ct7Xe8ACTvG/c3VLZvGD777hU1sHpw60Z4PZq3vb+1Ln70Djuw2yJHbeIFg5+fsEI4g5V/j1hKeS"
        "p45P/uZHH9tzP1AAvtZQjTvcZK129FwdOg/Ahg0boomJCens7JwH4p577gFIrLUqIogI2gzPuceb"
        "0hQbBLRGwZ620NyVKvffuLgjVlCjkm2LQmqpJ1F/ZgJWfDOixYiYs3lCVF0+DOxELXnmo4/teaC/"
        "kLkkdkH5+Ec+YmTjRtd8mTuvKvDhD3+44Jx7xBjT5r1XVZUoijh69Cg7d+6cFRHvvSdNU6IoYu5x"
        "GIZERmQy9e6q1mDB713W0ecVFSNWAKeqoE5UDHJqTmpD6yMCGWvIWEPiPdXUpYIEZ9kguJYosCPV"
        "5JG7vr79l5YUMl0z0/HYFJQuRBQFAMYYk6bpSmNM1ntPNptleHiYp59+mjRNAcjn83QWi4yOjpLN"
        "Zenq6GBkbIxS6mgPLWva8o2ka5CRzhMWEpwcjHOGh0bIBoJCfTZ1g2O1+o5ynH4tG5q39rfmf60p"
        "huwrc1PMbOq0NTBrf23Vksu+tHvweSC4UEV4MtLVJEmiMAx1cHBQtm3bhqpig4B6rUZfXx9LVlzG"
        "vm98g46+xaxeew2j3/0ONxdC3r+0SH8+lErq5YThr8xdVchaITTCcC3VHcer7JiqTY6Uq+996sDg"
        "kwAPvnPddNbwXiJjVU9lRAW8Ion33lpTeN/KxZ+5obf48R0jk0dXLsi1fGzLvsELBsB7b3K5nBkc"
        "HNQnn3xSAKy14D3eBlyXTpA/8Axv7Gkjl5RYsu8pNlxeZEVrhAemEz/Pbi833CvkrBCIsLcc893h"
        "Mk+Mz8poLdXAmt7WbObedVdf/Yf9PT1bPz48u/8GO7N9ccbcVnfel1NvZlOHKuQCQ082oD8Xmt4s"
        "9OYza7OB/T+rO1unHxke/yBQWtRKZqjM5LnygAB84AMfKLa3tx8cGhpq37ZtW4OMjGlEgDSMu2tR"
        "K//l8g4mEjAooooTIfZ6xhrqFCIjZK2wd6bO1wdLbB2fpZJ68qElYy0ICiIijXQAgkrqjKoiCF61"
        "EduqiAhW8K2hNVd15Lf+7oriI4r8h5l6su+t92//N5lMpjOO4wowfF4RsHbtWh5++GF27tzZMNra"
        "ebZ3Ci2B4aGxWa7tzHF1IUPVayM0VU9r+Bwk7ZFhpJbydwdLPDBcppx6WkNLZy48eVckqVdv8GpF"
        "IgVarJwEqSDzwqIBRarw1FQyeMumxz+26e3XfK5ooxCYzMYxMVTPOwW++MUvMjExgTGGIAhwziEi"
        "JzwrMJU4DlbqXFvMUPdnllm++aFGHf98pMSXD01xdLZOW2hpCSze+4ZymwdLaY8CU/HCdOo1Y4Ss"
        "NTLv+dNIFBFoC4keWr8+uP2bW/bNPV+CyQvmgDRNWbFiBatWreKBBx4gSRKsEVShmiofurSDd/S1"
        "MusUI2c2PlJP1YRsGoeHJ4RssZNVPSEqr4TMCMw6uHNByDVRwtaJWB4dq3CwktAaGKwx+DPIYufR"
        "27dsSdetI7zzTtzGjRfWPxSApUuXFp1zBwttbe3FYlGPDR0TAyQK1dTzG8uLvLOvjZnEI2cyXiEv"
        "ytGghYezfUyZDG2BIZvNYoJgPqVe/uVehDCt87bRXbT6hCkH3x6aYdOhSRKEfGBfbpkTERuG4f17"
        "9+59J00lelE9wWKxyNjYGGEuR7G7h8NDQ1QTR0dk+Y0VnfxCbwvTiT+r51sD4Zk0yyOmF1/3ZEyV"
        "RLJUKxVCkTO2hAxKbEOeNQXWxMcxJuC9/QWuLmT40z2jHKsmFDLh69YeDk4wttKTDVlRbGEyF3LF"
        "ghxvW9TKklxIOT2L8aq0Z0K+8OwRPvficQzaYOxm+rx1aTePHpugkjisCB49BQmRRvQ8Wcjz12+8"
        "AlAmE2VlIcMf/MxCPvXcCAfKMW1RiIq8TgBMTYENmB0f5Re7Ut65qousNdS9ntV4p0oxE/K5Zw/z"
        "2V2HX+bZhp13L1/AyraIP9rx0lkXsm8s5ki5yqXteeLUU049XZHl91f38PEfDjNaS8mFFsS8pgAY"
        "gCkga2D/rGPTcJVYG4bX9cyE57zSkQn5+xeO8dldhwmNwZ70Yt8EYDpx/MoVS/jjn72S2/u7WNae"
        "Z2FLlq5cREto56tNojBUiQmNoDT0x6xTejIB/3nlAqwIifPzeuA1T4E5tfaNYzNcU8xwbTFL1elp"
        "FY5rhv2DR8b49M4DDQOapS0bGC4t5Lmis5XlhTw9uQyTcZ1b+zq5bXEn5bojVY/3UHOOUpwyUo0Z"
        "qsT0t2WpuxMGWoFy6rm6Pcu/W97BX+0dpyBgjHntAQgEZlLP2xa2cl1HjsoZQt+r0hoGvFSq8HuP"
        "78Gr0pkNWdfTzs2LOljd1cbClgy5oNHRmk0cqSqVJG0aJQTGgBVaI0tvPsOVXa1YEaqpo97sFM1d"
        "c0r0zr4CE3XHlw9OUjSGiPC1A6AIjCl0ZyzvXtJGonracjfXgirFCf/t8T1c0dHKXct7uXFRkYX5"
        "DALEzlP3ShwnzVrfqABzXp3bG4DiFOqq8xpv7rWn0wsV53n/sg5yRvjSoRKJ1kPdsMG8Z/dGNm++"
        "WACKMDjqed8l7SzMBmfc2KAQBYZd4zN8cHU/t/d3EYihmjqm6+kpBttzzFMj596Nm3Weu5cWWdKS"
        "4b4j05Fs3HjRMyIRQNvbi+t6ug7+4dU97WGjesnZWlKBCNnAUE4cXvWMnjtpyNkof4BBMKZxfyIi"
        "zt0Op2hLYIi9L5fr6abHjo5/9o92HNi9Cuq7G5up86sCAlAqceeiFoqhIX2VxQiQqjJTT+c9faYN"
        "kWu2biNraI9CipmQbGBInDIVJ0zFCZUkPedurFcI8JJ4L6q0LWnL/frPX9Jz39L2zIID0NPsDZ5f"
        "Cnjgz9avYVVnlpprePNc9POZStGctzPWkAsCYuc5MlNl93iZ3RMzvFSaZbRap5w4nFc+uHoJv7py"
        "MTNJ+qpp0xIaSpJlsjJLbyikTkm8HhlL46rPkCMmuiAOeFOxyITEFzVlnAvlrDVkgoCjMzUeOTrM"
        "lsFxdk+Uid3pxWw1dbRGATNJimv2AE5XfazAfYNlfkCbHhw8Lms7sjs/cWV3R7me/GWlwmR7JtMS"
        "E89cEABTF0kkTpXQGApRwP6pCl/ZN8x3D49RalaCk8lxjvPnNttf2TvMivYWbu3rIBtY6s5Tc77Z"
        "B1B8sx+xZbTCF/aOkOe4z2Yju3U6fa47DDc8r+EoUC/F8RgXwAEXPRlyqrRFAVO1hM8/e5iv7htm"
        "NnXzNb/RC9RXEJ02/07FCf/1+y9weUcLty7q5LYlXawoZMGnJGKb5RF+OFUlMkIuCBAxdAW2bemX"
        "Htp/0gRo+oKl8AVPhASKmZDvH53k339vF/e+cJTZ1M0To2sarq/GJ8CLkxW+sPsIv//sCI/1XsWR"
        "qEjWNCqOV+iMLImf76DhVP2GDZiLseGiIsBIYz/w6WcO8OU9R1FteNzridneXEfX69mBBLDGgCpW"
        "hOFskRdbherOx/nVvjz5IOT2nla2jFQ4VEnotB4R0WYT5KI2BheE3lxf/749x7j3hYbxc+lwcitN"
        "9ezGv1xiu2a0hD4lQnlkrMondo+yZ6bO0paQT17Vy3sXt6JeSdM0eC3G+xcEgAjUnTJw+SI23XEt"
        "H73uDazraZ83ZC73e/MZVnW2nvfkVRuBTiEwDNccf/D8GLumaiwt5Mh2dVNXxaVpprl+/yMHYO7K"
        "WMPCfIZ3r1jEX7xpNf/z567i1r5OXHOD9HdvWcutfZ3zVeBCCDZvhbJT/nzPGH89AjOXXUN7Pg8i"
        "1WYr7MfDAXOpUPdKzSUIsLa7net62vnmgRGsCEsLOfLNXaFecJVp9ComHPzzi4fpPDpGXK8TRZG+"
        "CkkLAwMNcDZv9mcanV10GRQB2wzw2eaW945lPSReGa7E3NLXwWd2HZrf5uoFAp0xBkWZnpnBWos1"
        "jUOUjz7zTPCuxYtPCa/Rnh6VzZsdmze71zUCXlkZGusoJ2ljqoOyrJDnly/v497nBwmMkJ7EiqZZ"
        "Nc5WaQSoeSX1vjkZEoxXkoTkPQ0D3V/u2/eK93574KbO0Msv5QN5Yzkxn3zzVx97acmSJbnBl50d"
        "el1Ois4BYRAqScqvr+7nufEZnhkpYRs1fJ4wT40m4eSnKmmjwXpZa4bL2yL6cwEdkTWREXLWrO+/"
        "5qZ/8F4PCTIEVFW0xQi9qqwUz40ZKwvbopDR2Uod+E/10dFFwODJivG8ANDTnG0APSPDz2kAK/DH"
        "t13J/3hqHw8cHpvXC+9YsZDvHR6jnDiMCM57uro6sdbiVfnZBTlu72xnZSFDzhpUQVFpnBKRbmvk"
        "bnOS2pSTBFg19SReqaaObGB/5e1v6P2Lb+4/Pt0KhTKMnTMA8wcZUCxgRZk7sauAR3AiTRheCYYI"
        "pF4JjPDJm1eyrJDnc88e5rbFnXzixsvYOVJipt7YCHV3d3P99ddTjetcVx/jukvbiIKAiSSllDhO"
        "PUejKqhvVsyXLVo0E5iwlroD+6dqfzOb+NmubJgHjkkbhpkTdCQAD71jfdHb+KA10p6q6txI3isE"
        "BnLWkCKU1DLhDTWxqAhZ9bRpQpuPCb3DiSEVO5+7p4ueQmh4YKRKdz7D2pzy4GzAt9ICeTz9l1xC"
        "LpcjCAL2PbeL0oG9vHFRO7d0ZWkLDFWn56InfGBEUsexL+w6fMe9e4/ubTq6croJUnA2r7cGhlLq"
        "+L9DJXbMODovX01LVzcmCDDGYo2QwVNwMX3xFMtmx2hPq9ScUvd6ChBz96W6403dORKvTDnllhZF"
        "823sCjqI8ViFAwcOsu35F6kmjm3T4/TnAn5zeZEbOnKNfuVZI1Z9PgiC/ZXKP9279+jeK9rallSN"
        "GTlcKjk9lyqgTWWRDwwPj5S57/AUhysJRqAw8zT9ixfT2to671OP4BC8GLLOsCKOub3d0JcLqaSe"
        "tMkBJxNkJWlMn40I9dRx4/RBCsEkT1fg8OQUI6NjXJYP6Mxk6csGLM4F9GUD3HkUUeeYWtHZGZTT"
        "ireluPaPAwN24DR64JQUMEbavaoGIvL5AxN8c3CayBpygUWM4L2SOoeqniCdOdGvjUFnYgK6M5Y7"
        "FrbwC72tFEJLJXV45YxDFppT5cR76h5sEBCYxmHCoPmmum8cpHx1zaA+H1ozVUu3vu3r238RyDW3"
        "yrOne70F+OAVy7LeuN+xRrIW4c9fHJNvHZuhEBlEG2cYjWkoOu89QRAQRVGDrb0nCEOCMGycL/Ap"
        "sReemop5cryCeMcb2rLkraHe3OycDodUDFhLYO38WC3xDcPrXs952ycikjivrVHYf9fynsXd2ejZ"
        "Rdms/fTPXfXWD625tP6F5w6Pr4fgUDMSzKm62/ClQ1N8b7hMZ2RRhb7FS+ju7sa5Bn9ccsklFAoF"
        "0jRFRFi2bBm5XI4kSQjDkKWXLieXiWgRpWQzfNO1s/H5MbZOVAmAHI2+o2+SrM6HYqPm60n9AyMn"
        "bifGbXLWRGh+pK+6NO3JZ99/14qFW3973bIdxUx43wvj05cC+WdzuZ45PA3AQaZoCw2Pjlb42mCJ"
        "Qij4Zn29/PLLWbp0KfV6QztcddVV9Pb2EscxYRiyZs0a2tvbieOYlpYW1q5dSyabZbZWY2FXF7dc"
        "ew17Zx2f+uExPjMZ8ELQTrlao0UaPGPnGkZoOn9TdYqmXtXPgRSIkBWIfEJw+ja0V1UXGpG2KLRt"
        "YRjUnUeQUKFQTtL4yFQcAW1ONTylDOqGdxS/9tThA58rt7QXL1mu39/ycENsNBVdsVjkuuuu44kn"
        "nqBUKtHf388VV1zBo48+Sq1WY+XKlSxcuJDHHnsMgDVr1hAEAdu3bycMQ2664QamSlPs2PUcLW1t"
        "vP3m6+maGqKzdJwVxRYphELYnPdpc2o0dxLVe9VEVSbjRKckYrClG3d8kNs7QmoKRhtxE1ljM9ZQ"
        "TtxYLfWPjFfj7Ucq8d5SnAzX0OneMGLrwdLw/x4aqjbtrgA6XwXuPTxt9msql2aGT/llknOOarXK"
        "0NAQ9Xod5xzlcpmhoSGcczjnKJVK8wtO05Tx8fHGETsgSRKGR0aoVquE6vGzFZ47epzDx4778tSk"
        "ubrH3/MnK4sPD9fq1wdW+pKUBalqNmOZ2j4Zr3u+VLv2aDXRwUosLttCW2+GBZMzvLmzC4MSGiPZ"
        "wFCuJ4dGKvX/9dX9x7/x93sGjzRJ71WbpAKwZunSYsnY/aK+vZ6kGkWhzBkkInjv53PcWkuapjjn"
        "CJvElyQJ3nuiKJo3GiAMGwPMer0+fwALIKnXNbCWMBOlcUfflYe2bTl42sqw/LI7urz7Ot6lkTWB"
        "UcU7pxIE8tsrOqo3FzOm5vX4VK3+5b965tC9Dx0bHwLsukWL4qduuy1m0yYPwj33NOzceJqf7AjA"
        "wMCAnTx+fLnJZq2I6Fy+n7KYKJrngTlDL+Z/VRXvvbv11lsPrN69W+d+ODna06MAA8CfQvT9Umlp"
        "KonG9YY7oyhiqlo1M85VvtQHL+FG37P5iSrQ8ubeXh44fryi/JRdumGDeS2OTJgf0+3VUvS079sA"
        "ZsMGjP6E/Pz3X69/qdf/Aw/uHd3UaAfpAAAAAElFTkSuQmCC"
    ),
    128: (
        "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"
    ),
}

LOGO_PNG = {
    1: (
        "iVBORw0KGgoAAAANSUhEUgAAABYAAAAUCAYAAACJfM0wAAAEQklEQVR42o2Ua2xTZRjH/+85b3t6"
        "2a3bmrKtrpUJBoEQQKEMDS6EYfiCMgxfEPyCxmjkg4kElcgnozF4SRSDi5CYGBM3QvASCYZtEBcM"
        "hFW3TlcGc3R0tGtZLxs97bm8jx92ge6ino//8z+/83+e930eRkT4v09HS+NzYHRKN2kgoWrHD/UF"
        "26NRyi3kZf8F7moJeC+Ox5NHu4YLREQXng+8x4gdBgBDUDir6V8LU/zePhK91NafuEfTwEXBXU1N"
        "/OnOTqNz96aNBLTrQgwxMKcssZUMsD1gFSZR9Go8vf/t7vCvRKQDAG9tbd0CAMlkMp7NZrUZ91a3"
        "3v7D7s1RhdhjnJHXIknehQIkdfHlnu+vvPWi368CMGZ0HgwGu0ZGRtDX1wdXRQUYY/CRikZ/OZxc"
        "Wgs2ZUxpJvLcerbU0HpLrPKRGQDJ1s1vrK2vORYcDtMD5fNIJIJQKAQAOOCR4XNYYJfvVxrPGzh9"
        "O4OedAGQ5O3V1dVeMz1mmkSyk0s4sNp/88mHPNuOBSO3ANwrAkuSBBsDbFyGXZ6KSAR0TBAuTnKw"
        "Ug+8ZQw+C9ksen59R8EAGENaF7hUUv/TqbZTJ+e2iJdzhhUuG3bWlmCJIgMANEH4ZGAMfZkC6hwW"
        "jKpTrYvJEj57vA4bymrxYWgUJhgYY0REYi5YSmUnsMd7H2oS4Z3uAfwyMAxnLoODj1SixW3FRruJ"
        "elKRVAtYbgNeaXCBhAARsYUOlXtsHOUWeVa4fCeF7T433g0sh3Na39ngmffhOpcdLy914c9MqpxN"
        "xy4akK92PEF+u/yvQ3I3r+P2hIplFU44LMXeArGJs+HYUyf6/w4RkTmbeDFoqqDj7M0Yzg0nkFCn"
        "rvera/x4tsEDiTGoJuH9wTR2rfR/czp+eQhAUZ/5QtAfh+I4EYogp5tF+ud/DOPbcBRvNjdiGVTE"
        "JnLoddT0JJOYnNuKeWDNFLDKEmocCrKaAassITqZn30/ntdxUXegdzyF5qVLkNY0TgvshXlgqyyh"
        "2efGNp8bqbyO766Pom3wzryqumMZMMbQ9PAqfUY7v2+NM5zMGa//fEPjix0YA1Bps2Dvijr0351A"
        "umAglitA5hyVzMQL/gr47Bzu7PVD53dt3MvAFFmyr3ZD/gDAR+xCS4BmdkHEWo60ZqDU1PCoIuCU"
        "pn5iAkgawG8qR6amAXev92OT3UTApRSFid4rHN53LngcwCQHgO6UhpM3EhCIw+FwgDEGCwO2VCpo"
        "rrbCIUvwcGBnqYHIWAisAqi1K/OHglHJwfVLKz69NjTJPt62jo79NYaqqioQEYQQUBQFQgioqgp3"
        "WQkCTmBDCUOltfhqZg2BMi7NLjpD0C0CJZ45c2UH2xrYcDuRy+uSJBHnXOKcM13XSQghFGVqzlVV"
        "Na0Wi/zSKv9+d3LYMVrmWX0mPPJaWlLoiN82hkL+Ws+d9BdHrw4OTO9k+gff1vWdATJWAwAAAABJ"
        "RU5ErkJggg=="
    ),
    2: (
        "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"
    ),
}

# Light/dark switch icons: sun and moon drawn smooth at 1x/2x by the build-time
# script that also made the logo, kept as 8-bit alpha masks and coloured with
# tinted_icon() at runtime, so they follow the theme without Pillow.
THEME_ICONS = {  # (icon, scale): (size, base64 of zlib'd 8-bit alpha mask)
    ("sun", 1): (18, "eNplUDsOgzAMfXGQx8A5kbgJ3IEhEjsSF6BbK4FADB3pLSrUyE1IltLnwX6Jf8+A1gqKnw94pzUiCLm7gVQilfWRkcWHylYgjU5aznjos4xb6aAVnf5E+POl3mxZ1PNUF6U9KaDBq3isDKLYnhp5O/eWhgKb9lfPq3Mizq3c38ft92U4jv1SZcy1c0hI06c5TveL/22YVGT9wElFVEpYxCAqPdMVYfzkiSBcTClsO3sXLvYFvw1kgw=="),
    ("sun", 2): (36, "eNqNVLFKA0EQnZ1dUhgljaXYmoSAheA3pPIH7FJqLQS08EMMp7VKClGQXFybFIL24kdYmCLCJXnO3W3u9oyGGzj27u1jZm5m3hD5puloPj+WY4UxPQNPciybNrzgDIFLMu7L5A6VexwncJwcjs92dyv1zxQCFymHaavbdiRNbeBjIyEx2en0JsmZaeMDaKf5GzrHF+xaTNJ0B9zHONOaFfjc+VTb7/iGXRe/rJqjUVOxvK5bAd+3Fbt8ahYTvG0qlXrWpNTmm0C2liWd+B2jQxWHqAp1BEjjZ6TqKz4bEnr/pNc72ZckG594rRaKyVQ73CHaHSCxwS7RzmHtV72TIAdjzCKxGcYH5BUwI1V0a4Io9RNh0tKVjKLZGKM1J+10lJj0LABrHV+afCr2MENmM+zlE3L8eBUE1/06U3ce5Zxo3iWu96+DIHg4c9gLUwCfI93nFxRsJedoEA7D8PbvWLehXA5Oy+SsObb//j25NGVqWL4XJXpaYjZKzFiJWS0z80Xt3AN3y9rxNajpZjq1To6eBn0tG7oAwoWucy374jfSfQyJl3aCt1sKnMJu8feYVNr+uaM8jgzmKZki+gMu9IBT"),
    ("moon", 1): (18, "eNpVkC0OwlAQhHe3BY/Av4JGIxANGkmCqOcC3ICDEOAwpAYLghtgKjDQvvYNW/pHR22+7M/MEqk8pr6EaDrnjmoZXd5r8logcgTOHdCRE9JiIS3xaYMUrzG1a4SvhUUy6ggZC+fcjKUlSxSw2NOgR5xLAhoK6916SuHN/G/Ooei5C3yZrDwur2coET6PO7Zqp3SY5Q7OutK6NClQaBMOLFwnjVMgjSNqHqCdJgzNr6hVfYyrsF+gm1vf"),
    ("gear", 1): (18, "eNptUDEOhEAIZMHEN+hjTKxs/IN21/ikvc4nmGws9lo/YeIv9E4J3G5yjdmjAIYAwwAAiATV41EBIUK06MmpOvoBhG4qYGFeoJi6ANH0l272EDnspldvkNDrR1WYRUPikSgrV+FTg50sa5nFbVZPdW3rQrAB1qPfWeZIOgvvfqyH0M7amDw3TUhUh7Ryn3q/xvq++Rnvv7MXGaUXpipSpX++kXzsC/W3nXA="),
    ("gear", 2): (36, "eNqVVMGKwjAQnTRtT9496UX05tF7ERQEvXrzol/gBxQR/SWFzU0Q/0HZTxARirjaJrOTpNZdWDQ7BDqdeXmTl0kC8D/zoCrEx4cQVXL/SDOKcmijsTa5NvSLQSN8GGZfGY0huTwPP8yHjqiBH/hTTJHGlFyoiQ4lnpAoweMAGCwsZkHu4IhJVIA8iC54R4zL44OSiFIdxuUYKXSJ8nIe6yYoUSGesDByFQWTrl245+00CxJFpixCZUSGmmnnWQw0TlKa1JPHuFKeGnkxDn07zTCkacEmsW+2wOqaEbuZiT8+mZrluhjnQVg6G3aJ9/Vyub4bkMJzKQw4ZznTCDMD2TT1X3NjQBmObJZVWr1JvNfBDLchcN/nEG71HIn7eNJrVRiIQoq61iHQ8wKoX1UhUgAIdUtTS716qOCwssXT9KY0Bq0katOc5e3x2Vw3zohDV4xTrbdrfqn9M9fusIeuvXjbU6ez4XLGnM6qw5l3uTtOd9DpLju9CU5vi9Mb9dK+Ab+LbzI="),
    ("moon", 2): (36, "eNqVlLFKA0EQhmf31soQlKQIaFJZWNjYWYmlksZHECFJEUwRrGyinY/gY1ikEyFiaaFFioCQTlAwBISASW53nN3LJpdk9yQDVxz33T//zswOwKrBA8ETASbYfxoBPYWTy2PwKjEG6crTD77nuU+N/i11EbG37ZUJIHuPOB5iHYRXJdfGkVT4lfJl4jxDCOngnVcmgKZGMMRTJnzIuUEUql2PY8ZSH1Iapp8Btx0BZXJimN6Gh+HwrMKIGRbcuRhs/dJnHRIPTUccjotoZPTZr9xnF1CL7GidV8bdzLVlCDpyJoszIb44hyiWS0O3sMaSPBvbdRrY+FQtnF3XKMQbKpngjIKLpRpa6PFglmWHzfdi6gkfyntpxjb3a28XUcGmPY1BiJ+dzjdif33iyM5GjIpyj/BsWi47Y7FQSsohNmcVtbM6HyNsZzhfnPk4IQnJzU3K5O6EyhaADnqfXeidvYPhmEI77pZgad7MXW4NIp1Bq5K2fVjaCflitdGoFvOT18TdkrRmaEdRBHzl5QZ/7x1wRw=="),
}


def tinted_icon(master, name: str, scale: int, fg: str, bg: str) -> tkinter.PhotoImage:
    """A THEME_ICONS mask drawn in colour fg on bg (both "#rrggbb")."""
    size, data = THEME_ICONS[(name, scale)]
    alpha = zlib.decompress(base64.b64decode(data))
    shades: Dict[int, str] = {}
    rows = []
    for y in range(size):
        row = alpha[y * size : (y + 1) * size]
        rows.append(
            "{" + " ".join(shades.setdefault(a, blend(fg, bg, a / 255)) for a in row) + "}"
        )
    img = tkinter.PhotoImage(master=master, width=size, height=size)
    img.put(" ".join(rows))
    return img
_UNSCANNED = ("?", None)


def blend(c1, c2, t: float):
    """Mix colour c1 into c2 by factor t (0 → c2, 1 → c1). (light, dark) pairs are
    mixed per mode and give a pair."""
    if isinstance(c1, tuple) or isinstance(c2, tuple):
        p1 = c1 if isinstance(c1, tuple) else (c1, c1)
        p2 = c2 if isinstance(c2, tuple) else (c2, c2)
        return (blend(p1[0], p2[0], t), blend(p1[1], p2[1], t))
    a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(a, b))


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    return f"{sec}s" if sec < 60 else f"{sec // 60}m {sec % 60:02d}s"


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 ** 2):.1f} MiB"


def parse_speed(speed: str) -> int:
    mult = {"M": 1_000_000, "K": 1_000}.get(speed[-1].upper(), 1)
    return int(float(speed.rstrip("MmKk")) * mult)


Range = Tuple[int, int]  # [start, end)


def block_ranges(
    data: bytes, block: int = ERASE_BLOCK
) -> Tuple[List[Range], List[Range]]:
    """Split an image into (used, unused) ranges; unused = blocks that are all 0xFF."""
    ff = b"\xff" * block
    used: List[Range] = []
    unused: List[Range] = []
    for off in range(0, len(data), block):
        chunk = data[off : off + block]
        target = unused if chunk == ff[: len(chunk)] else used
        if target and target[-1][1] == off:
            target[-1] = (target[-1][0], off + len(chunk))
        else:
            target.append((off, off + len(chunk)))
    return used, unused


def is_generic_chip(name: str) -> bool:
    """flashprog's catch-all definitions for chips it doesn't support."""
    name = name.lower()
    return name.startswith("unknown ") and "spi chip" in name


def result_row(cs, status: str, secs: float, rest: str = "") -> str:
    """One aligned line of a per-socket result table: "CS0  ✓ match       1.2s  rest".
    cs: a socket number or a padded label (HydraUI._sock_col, for named sockets).
    The variable part (CRC, file name) goes last so it can't shift the columns."""
    label = f"CS{cs}" if isinstance(cs, int) else cs
    return f"{label}  {status:<11} {secs:5.1f}s" + (f"  {rest}" if rest else "")


def merge_ranges(ranges: Sequence[Range]) -> List[Range]:
    merged: List[Range] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def split_ranges(ranges: Sequence[Range], piece: int) -> List[Range]:
    out = []
    for start, end in ranges:
        for s in range(start, end, piece):
            out.append((s, min(s + piece, end)))
    return out


def ranges_size(ranges: Sequence[Range]) -> int:
    return sum(e - s for s, e in ranges)


def close_serial(ser):
    """Close a serial port without waiting for unsent bytes: a Pico that stopped
    reading would make close() wait for the kernel's drain timeout (30 s for USB
    serial), with the port lock held."""
    try:
        ser.reset_output_buffer()
    except Exception:
        pass
    try:
        ser.close()
    except Exception:
        pass


def read_uf2(path: str) -> dict:
    """Check that a .uf2 file is an image for the Pico (RP2040) and find the
    HydraFlasher version in it ("HydraFlasher x.y", the S_CMD_Q_PGMNAME answer).
    Returns {"blocks": n, "version": "x.y" or None}; ValueError says what's wrong."""
    with open(path, "rb") as f:
        data = f.read()
    if not data or len(data) % 512:
        raise ValueError("not a UF2 file (its size isn't a multiple of 512 bytes)")
    payload = bytearray()
    for off in range(0, len(data), 512):
        m0, m1, flags, _addr, size, _no, _count, family = struct.unpack_from(
            "<8I", data, off
        )
        (m_end,) = struct.unpack_from("<I", data, off + 508)
        if (m0, m1, m_end) != UF2_MAGIC or size > 476:
            raise ValueError("not a UF2 file (bad block header)")
        if flags & 0x2000 and family != UF2_FAMILY_RP2040:  # family ID present
            raise ValueError(
                f"made for another chip (UF2 family 0x{family:08x}, not the RP2040)"
            )
        payload += data[off + 32 : off + 32 + size]
    m = FW_VERSION_RE.search(bytes(payload))
    return {"blocks": len(data) // 512, "version": m.group(1).decode() if m else None}


def fw_version(name: Optional[str]) -> Optional[str]:
    """"1.1" from "HydraFlasher 1.1"; None for older firmware ("pico-serprog")."""
    if name and name.startswith(FW_NAME_PREFIX):
        return name[len(FW_NAME_PREFIX) :].strip() or None
    return None


def version_key(version: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in version.split("."))
    except ValueError:
        return (0,)


def latest_fw_release(timeout: float = 10) -> Optional[dict]:
    """The newest firmware release on GitHub that has a .uf2 attached, None if there
    is none. Raises OSError (urllib) or ValueError when GitHub can't be asked."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=30",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "HydraFlasher"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        releases = json.load(resp)
    best = None
    for r in releases:
        tag = r.get("tag_name") or ""
        if r.get("draft") or r.get("prerelease") or not tag.startswith(FW_TAG_PREFIX):
            continue
        asset = next(
            (a for a in r.get("assets") or [] if a.get("name", "").endswith(".uf2")),
            None,
        )
        if not asset:
            continue
        rel = {
            "version": tag[len(FW_TAG_PREFIX) :],
            "tag": tag,
            "name": asset["name"],
            "url": asset["browser_download_url"],
            "size": asset.get("size") or 0,
            "date": (r.get("published_at") or "")[:10],
        }
        if best is None or version_key(rel["version"]) > version_key(best["version"]):
            best = rel
    return best


def app_commit() -> str:
    """The git commit the app runs from ("15d0a93", "15d0a93 modified" with local
    changes), "" outside a git checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        head = subprocess.run(
            ["git", "-C", here, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, creationflags=CREATE_NO_WINDOW,
        )
        if head.returncode != 0:
            return ""
        changed = subprocess.run(
            ["git", "-C", here, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=3, creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return head.stdout.strip() + (" modified" if changed else "")


def desktop_notify(title: str, message: str) -> None:
    """A desktop notification that closes by itself after NOTIFY_TIMEOUT_S. Doesn't wait
    for the notification server: it's called on the UI thread when a job ends."""
    if NOTIFY_SEND:
        cmd = [NOTIFY_SEND, "-a", "Hydra Flasher", "-t", str(NOTIFY_TIMEOUT_S * 1000),
               title, message]

        def run():
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass

        threading.Thread(target=run, daemon=True).start()
    elif notification:  # Windows: plyer shows it from its own thread
        try:
            notification.notify(title=title, message=message, app_name="Hydra Flasher",
                                timeout=NOTIFY_TIMEOUT_S)
        except Exception:
            pass


def rp2_drive_present() -> bool:
    """Cheap check (UI thread) for a Pico in bootloader mode: the RPI-RP2 drive."""
    system = platform.system()
    if system == "Linux":
        return os.path.exists(f"/dev/disk/by-label/{RP2_LABEL}")
    if system == "Darwin":
        return os.path.isdir(f"/Volumes/{RP2_LABEL}")
    return find_rp2_drive(mount=False) is not None  # Windows: drive letters, fast


def find_rp2_drive(mount: bool = True) -> Optional[str]:
    """Where the Pico's bootloader drive (RPI-RP2) is mounted, None while there's
    none. On Linux it's mounted with udisksctl when the desktop doesn't do it."""
    system = platform.system()
    roots: List[str] = []
    if system == "Windows":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        drives = kernel32.GetLogicalDrives()
        for i in range(26):
            root = f"{chr(65 + i)}:\\"
            if drives >> i & 1 and kernel32.GetDriveTypeW(root) == 2:  # removable
                roots.append(root)
    elif system == "Darwin":
        roots.append(f"/Volumes/{RP2_LABEL}")
    else:
        try:
            out = subprocess.run(
                ["findmnt", "-rn", "-S", f"LABEL={RP2_LABEL}", "-o", "TARGET"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            roots += [line.replace("\\x20", " ") for line in out.splitlines() if line]
        except (OSError, subprocess.SubprocessError):
            pass
        dev = f"/dev/disk/by-label/{RP2_LABEL}"
        if not roots and mount and os.path.exists(dev):
            try:
                out = subprocess.run(
                    ["udisksctl", "mount", "--no-user-interaction", "-b",
                     os.path.realpath(dev)],
                    capture_output=True, text=True, timeout=15,
                ).stdout
                m = re.search(r" at (.+?)\.?$", out.strip())
                if m:
                    roots.append(m.group(1))
            except (OSError, subprocess.SubprocessError):
                pass
    for root in roots:
        try:
            with open(os.path.join(root, "INFO_UF2.TXT"), errors="replace") as f:
                if f"Board-ID: {RP2_LABEL}" in f.read():
                    return root
        except OSError:
            continue
    return None


class JobAborted(Exception):
    pass


class ProgrammerError(Exception):
    pass


class NoAnswer(ProgrammerError):
    """The Pico took a command but didn't answer at all (not just a NAK)."""


class Programmer:
    """Direct serprog access for what flashprog can't do: live socket scan,
    unique IDs and the firmware's on-device CRC32 (S_CMD_CRC32)."""

    def __init__(self, port: str):
        self.ser = serial.Serial(port, SERIAL_BAUD, timeout=1, write_timeout=1)

    def close(self):
        close_serial(self.ser)

    def _cmd(
        self, cmd: int, params: bytes = b"", nresp: int = 0, timeout: float = 1.0
    ) -> bytes:
        self.ser.timeout = timeout
        self.ser.write(bytes([cmd]) + params)
        ack = self.ser.read(1)
        if not ack:
            raise NoAnswer(f"command 0x{cmd:02x}: no answer")
        if ack != bytes([S_ACK]):
            raise ProgrammerError(f"command 0x{cmd:02x}: NAK")
        data = self.ser.read(nresp) if nresp else b""
        if len(data) != nresp:
            raise ProgrammerError(f"command 0x{cmd:02x}: short answer")
        return data

    def command_map(self) -> Set[int]:
        bits = int.from_bytes(self._cmd(S_CMD_Q_CMDMAP, nresp=32), "little")
        return {i for i in range(256) if bits >> i & 1}

    def num_cs(self) -> int:
        return self._cmd(S_CMD_Q_NUM_CS, nresp=1)[0]

    def cs_wired(self) -> int:
        """Bitmap of CS lines a socket board pulls up (bit n: CS n is wired)."""
        return int.from_bytes(self._cmd(S_CMD_Q_CS_WIRED, nresp=2), "little")

    def pgm_name(self) -> str:
        """"HydraFlasher <version>" from firmware 1.1 on, "pico-serprog" before."""
        name = self._cmd(S_CMD_Q_PGMNAME, nresp=16).split(b"\0")[0]
        return name.decode("ascii", "replace").strip()

    def bootsel(self):
        """Reboot into the USB bootloader (the RPI-RP2 drive)."""
        self._cmd(S_CMD_BOOTSEL)

    def set_speed(self, hz: int):
        self._cmd(S_CMD_S_SPI_FREQ, struct.pack("<I", hz), 4)

    def pins(self, enable: bool):
        self._cmd(S_CMD_S_PIN_STATE, bytes([int(enable)]))

    def spi(self, cs: int, tx: bytes, rlen: int) -> bytes:
        self._cmd(S_CMD_S_SPI_CS, bytes([cs]))
        return self._cmd(
            S_CMD_O_SPIOP,
            len(tx).to_bytes(3, "little") + rlen.to_bytes(3, "little") + tx,
            rlen,
        )

    def identify(self, cs: int) -> Tuple[Optional[str], Optional[str]]:
        """(JEDEC ID, unique ID) of the chip in a socket, (None, None) if empty."""
        jedec = self.spi(cs, b"\x9f", 3)
        # No manufacturer is 00 or FF: empty socket, or a garbled read (chip still
        # being seated, bus contention)
        if jedec[0] in (0x00, 0xFF):
            return None, None
        uid = None
        if jedec[0] == 0xEF:  # Winbond: 64-bit unique ID (4Bh)
            if jedec[2] >= 0x19:
                self.spi(cs, b"\xe9", 0)  # >16 MiB parts: back to 3-byte address mode
            uid = self.spi(cs, b"\x4b" + b"\x00" * 4, 8).hex().upper()
        return jedec.hex().upper(), uid

    def crc32(
        self, cs: int, addr: int, length: int, speed_hz: int, chip_size: int
    ) -> int:
        # Chips >16 MiB: always the 4-byte-address READ (13h), which works no matter
        # which address mode flashprog left the chip in
        addr_bytes = 4 if chip_size > (1 << 24) else 3
        timeout = length * 8 / speed_hz * 3 + 5
        data = self._cmd(
            S_CMD_CRC32, struct.pack("<BBII", cs, addr_bytes, addr, length), 4, timeout
        )
        return int.from_bytes(data, "little")


class TtyToggle(ctk.CTkLabel):
    """Terminal style checkbox: [x] label"""

    def __init__(self, master, text: str, variable: ctk.BooleanVar, font):
        super().__init__(master, text="", font=font, cursor="hand2")
        self._toggle_text = text
        self._toggle_var = variable
        self.bind(
            "<Button-1>", lambda e: self._toggle_var.set(not self._toggle_var.get())
        )
        variable.trace_add("write", lambda *a: self._render())
        self._render()

    def _render(self):
        on = self._toggle_var.get()
        self.configure(
            text=f"[{'x' if on else ' '}] {self._toggle_text}",
            text_color=T["accent"] if on else T["dim"],
        )


class SocketCard(ctk.CTkFrame):
    """One socket with a status light. Click to select it as target."""

    STATES = {  # state: (led colour, lit)
        "unknown": (T["faint"], False),
        "empty": (T["led_empty"], False),
        "ok": (T["green"], True),
        "warn": (T["yellow"], True),
        "pass": (T["green"], True),
        "fail": (T["red"], True),
        "busy": (T["accent"], True),
        "queued": (T["dim"], False),
    }

    def __init__(
        self, master, title: str, tag: str, fonts: dict, on_click: Callable[[], None]
    ):
        super().__init__(
            master,
            fg_color=T["panel"],
            border_color=T["border"],
            border_width=2,
            corner_radius=8,
            cursor="hand2",
        )
        self._state = "unknown"
        self._selected = False
        self._pulse = False
        self._pulse_id = None

        top = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
        top.pack(fill="x", padx=12, pady=(10, 2))
        self._led = tkinter.Canvas(
            top,
            width=18,
            height=18,
            bg=tk_color(T["panel"]),
            highlightthickness=0,
            cursor="hand2",
        )
        self._led.pack(side="left")
        self._title_lbl = ctk.CTkLabel(
            top, text=title, font=fonts["bold"], text_color=T["text"], cursor="hand2"
        )
        self._title_lbl.pack(side="left", padx=(8, 0))
        self._tag_lbl = ctk.CTkLabel(
            top, text=tag, font=fonts["small"], text_color=T["faint"], cursor="hand2"
        )
        self._tag_lbl.pack(side="right")
        self._name_lbl = ctk.CTkLabel(
            self,
            text="",
            font=fonts["normal"],
            text_color=T["text"],
            anchor="w",
            cursor="hand2",
            height=18,
        )
        self._name_lbl.pack(fill="x", padx=12)
        self._detail_lbl = ctk.CTkLabel(
            self,
            text="",
            font=fonts["small"],
            text_color=T["dim"],
            anchor="w",
            cursor="hand2",
            height=16,
        )
        self._detail_lbl.pack(fill="x", padx=12, pady=(0, 10))

        for w in (
            self,
            top,
            self._led,
            self._title_lbl,
            self._tag_lbl,
            self._name_lbl,
            self._detail_lbl,
        ):
            w.bind("<Button-1>", lambda e: on_click())
        self._fonts = fonts
        # Monospace: one character width per font, measured once. Measuring is a
        # slow round trip into Tk, and <Configure> fires many times per resize.
        self._char_w = tuple(
            max(fonts[f].measure("0"), 1) for f in ("normal", "small", "bold")
        )
        self._texts = ("", "")
        self._title, self._tag = title, tag
        self._max_chars = (20, 22, 12)
        self.bind("<Configure>", self._on_resize, add="+")
        self.set_state("unknown", "—", "not probed")

    def _on_resize(self, event):
        normal_w, small_w, bold_w = self._char_w
        inner = event.width - 28
        # The title shares its line with the light (26 px) and the tag
        title = (inner - 26 - (len(self._tag) + 1) * small_w) // bold_w
        chars = (max(inner // normal_w, 6), max(inner // small_w, 6), max(title, 3))
        if chars != self._max_chars:
            self._max_chars = chars
            self._apply_texts()

    def _apply_texts(self):
        name, detail = self._texts
        self._name_lbl.configure(text=trunc(name, self._max_chars[0]))
        self._detail_lbl.configure(text=trunc(detail, self._max_chars[1]))
        self._title_lbl.configure(text=trunc(self._title, self._max_chars[2]))

    def set_title(self, title: str, tag: str):
        self._title, self._tag = title, tag
        self._tag_lbl.configure(text=tag)
        self._apply_texts()

    def set_selected(self, selected: bool):
        self._selected = selected
        self.configure(border_color=T["accent"] if selected else T["border"])

    def set_drop_hover(self, hover: bool):
        """Highlight while a dragged file is over the card."""
        if hover:
            self.configure(border_color=T["green"])
        else:
            self.set_selected(self._selected)

    def set_state(
        self, state: str, name: str, detail: str, detail_color: Optional[str] = None
    ):
        self._state = state
        self._texts = (name, detail)
        self._name_lbl.configure(
            text_color=T["text"] if state != "unknown" else T["faint"]
        )
        self._detail_lbl.configure(text_color=detail_color or T["dim"])
        self._apply_texts()
        if state == "busy":
            if self._pulse_id is None:
                self._pulse_tick()
        else:
            if self._pulse_id is not None:
                self.after_cancel(self._pulse_id)
                self._pulse_id = None
            self._draw_led()

    def _pulse_tick(self):
        self._pulse = not self._pulse
        self._draw_led()
        self._pulse_id = self.after(450, self._pulse_tick)

    def _draw_led(self):
        """Also called when the theme changes: a Tk canvas doesn't follow it by itself."""
        color, lit = self.STATES[self._state]
        color, panel = tk_color(color), tk_color(T["panel"])
        self._led.configure(bg=panel)
        self._led.delete("all")
        if lit:
            halo = blend(
                color,
                panel,
                0.12 if (self._state == "busy" and self._pulse) else 0.32,
            )
            self._led.create_oval(1, 1, 17, 17, fill=halo, outline="")
        self._led.create_oval(5, 5, 13, 13, fill=color, outline="")


class HydraUI(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=T["bg"])
        self.title("Hydra Flasher")
        self._set_window_icon()
        self.geometry("1000x900")
        self.minsize(860, 700)

        self.platform = platform.system()
        self.is_windows = self.platform == "Windows"
        self.is_linux = self.platform == "Linux"

        families = set(tkinter.font.families(self))
        mono = next((f for f in MONO_FONTS if f in families), "TkFixedFont")
        self.fonts = {
            "small": ctk.CTkFont(family=mono, size=11),
            "normal": ctk.CTkFont(family=mono, size=12),
            "bold": ctk.CTkFont(family=mono, size=12, weight="bold"),
            "title": ctk.CTkFont(family=mono, size=16, weight="bold"),
            "log": ctk.CTkFont(family=mono, size=12),
        }
        # Clock for queued sockets: Nerd Fonts have one (U+F017); plain fonts get ◷
        # (drawn by a fallback font, fine outside aligned text)
        self._clock = "" if "nerd" in mono.lower() else "◷"

        # NOTE: not "self.config" – that would shadow Tk's config() method
        self.cfg = self.load_config()
        self.theme = "light" if self.cfg.get("theme") == "light" else "dark"
        ctk.set_appearance_mode(self.theme)
        self.last_dir = self.cfg.get("last_dir", os.path.expanduser("~"))
        self.recent_files: List[str] = self.cfg.get("recent_files", [])[:10]
        # JEDEC ID -> {"chips": [...], "size_kb": n}, learned from flashprog probes.
        # Drops entries older versions learned from garbled IDs or catch-all definitions.
        self.jedec_cache: Dict[str, dict] = {
            jedec: entry
            for jedec, entry in self.cfg.get("jedec_cache", {}).items()
            if re.fullmatch(r"[0-9A-F]{6}", jedec)
            and jedec[:2] not in ("00", "FF")
            and entry.get("chips")
            and not any(is_generic_chip(c) for c in entry["chips"])
        }
        # Job signature -> typical duration in seconds (see _eta_keys)
        self.job_times: Dict[str, float] = {
            k: float(v)
            for k, v in self.cfg.get("job_times", {}).items()
            if isinstance(v, (int, float)) and v > 0
        }
        self._eta: Optional[float] = None  # estimate for the running job
        self._entry: dict = {}  # queue entry of the running job

        # Number of sockets on the board ("sockets" in the config, read at start only:
        # the cards are built once). Written back so the key shows up in the file.
        sockets = self.cfg.get("sockets", DEFAULT_SLOTS)
        self._sockets_warning = ""
        if isinstance(sockets, bool) or not isinstance(sockets, int) or not (
            1 <= sockets <= MAX_SLOTS
        ):
            self._sockets_warning = (
                f'Config "sockets": {json.dumps(sockets)} is not a number from 1 to '
                f"{MAX_SLOTS} – using {DEFAULT_SLOTS}."
            )
            sockets = DEFAULT_SLOTS
        # "sockets" is the manual count; with sockets_auto the count comes from the
        # firmware's wiring detection (S_CMD_Q_CS_WIRED), the manual one otherwise.
        # The last detected count is used at start, so the cards don't jump.
        self.manual_sockets: int = sockets
        self.sockets_auto: bool = self.cfg.get("sockets_auto", True) is not False
        detected = self.cfg.get("sockets_detected")
        self.sockets_detected: Optional[int] = (
            detected
            if isinstance(detected, int) and not isinstance(detected, bool)
            and 1 <= detected <= MAX_SLOTS
            else None
        )
        if self.sockets_auto and self.sockets_detected:
            sockets = self.sockets_detected
        self.num_slots: int = sockets
        # One socket: no ALL card/target, a broadcast would only reach that chip anyway
        self.has_all = sockets > 1
        # Wired CS lines reported by the firmware (bitmap), None: not known or the
        # firmware can't tell; _sockets_pending: apply the count once idle
        self._wired: Optional[int] = None
        self._sockets_pending = False
        # The last detection result logged (kept across reconnects, so a replugged
        # board doesn't log the same wiring again), and whether auto switched itself
        # to manual because no pull-ups were found
        self._wired_logged: Optional[int] = None
        self._auto_fell_back = False

        self._build_cs_map()

        cs_display = self.cfg.get("cs", "1 (CS0)")
        if cs_display == CS_ALL_LABEL_OLD:
            cs_display = CS_ALL_LABEL
        spispeed = self.cfg.get("spispeed", "16M")

        self.sudo_password: Optional[str] = None
        self.available_chips = ["Auto-detect"]
        # Per socket (index = CS number)
        self.slots: List[Optional[dict]] = [None] * self.num_slots
        self._socket_result: List[Optional[str]] = [
            None
        ] * self.num_slots  # "pass" / "fail" / None
        self._socket_ident: List[tuple] = [
            _UNSCANNED
        ] * self.num_slots  # (jedec, uid) from live scan
        self._busy_sockets: set = set()

        # Live scan / direct programmer access
        self._port_lock = threading.Lock()
        self._fw_caps: Optional[Set[int]] = None
        # CS lines of the connected firmware (None: not asked yet, e.g. under sudo)
        self._fw_cs: Optional[int] = None
        self._fw_name: Optional[str] = None  # its S_CMD_Q_PGMNAME answer
        # A port whose Pico doesn't answer: (port, monotonic time to try again)
        self._unresponsive: Optional[Tuple[str, float]] = None
        self._rp2_present = False  # a Pico in bootloader mode (RPI-RP2) is plugged in
        # LED updates run in a thread: opening the port can take seconds
        self._led_mutex = threading.Lock()
        self._led_want: Optional[Tuple[str, int]] = None
        self._led_running = False
        # Settings page; the newest firmware release on GitHub (see _check_release)
        self._settings_open = False
        self._settings_view = None
        self._release: Optional[dict] = None
        self._release_checking = False
        self._binary_version = ""
        self._scan_running = False
        self._log_lock = threading.Lock()

        # Production mode
        self._prod: Optional[dict] = None
        # JEDEC ID -> chip definition picked for it this session (see _pick_chip_definition)
        self._chip_choice: Dict[str, str] = {}

        # Job state
        self.busy = False
        self._job: dict = {}
        self._job_output: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._abort_requested = False
        # Jobs started while another one runs wait here (one bus, so one job at a time)
        self._queue: List[dict] = []
        self._queue_gen = 0  # invalidates stale "start the next job" callbacks
        self._queue_wait_scan: Optional[int] = None
        self._retrying = False
        self._job_key: Optional[tuple] = None  # key of the running job
        self._progress_line_active = False
        self._in_step = False
        self._detail_first = False
        self._log_block = ""  # "job" or "note": what the log last showed (log_note)
        self._status = "Ready"
        self._sub = ""
        self._pct: Optional[float] = None
        self._job_started = 0.0
        self._spin_i = 0
        self._port_connected: Optional[bool] = None

        self.flashrom_path = self._resolve_flashrom_binary()
        if not self.flashrom_path:
            self._exit_no_binary()

        self.force_var = ctk.BooleanVar(value=self.cfg.get("force", False))
        self.progress_var = ctk.BooleanVar(value=self.cfg.get("progress", True))
        self.noverify_var = ctk.BooleanVar(value=self.cfg.get("noverify", False))
        self.fast_verify_var = ctk.BooleanVar(value=self.cfg.get("fast_verify", True))
        self.partial_var = ctk.BooleanVar(value=self.cfg.get("partial_write", True))
        self.production_var = ctk.BooleanVar(value=False)
        self.one_by_one_var = ctk.BooleanVar(value=self.cfg.get("one_by_one", False))
        self.notify_var = ctk.BooleanVar(value=self.cfg.get("notifications", True))
        # Live scan and LEDs (settings page)
        self.scan_var = ctk.BooleanVar(value=self.cfg.get("live_scan", True))
        interval = self.cfg.get("scan_interval", "1 s")
        self.scan_interval_var = ctk.StringVar(
            value=interval if interval in SCAN_INTERVALS else "1 s"
        )
        speed = self.cfg.get("scan_speed", "8M")
        self.scan_speed_var = ctk.StringVar(value=speed if speed in SCAN_SPEEDS else "8M")
        self.leds_var = ctk.BooleanVar(value=self.cfg.get("leds", True))
        # Socket names by CS number ("" = none), shown on the cards and in the log
        names = self.cfg.get("socket_names", [])
        names = names if isinstance(names, list) else []
        self.socket_names: List[str] = [
            self._clean_socket_name(names[i] if i < len(names) else "")
            for i in range(MAX_SLOTS)
        ]
        self.spispeed_var = ctk.StringVar(
            value=spispeed if spispeed in SPI_SPEEDS else "16M"
        )
        self.cs_display_var = ctk.StringVar(
            value=cs_display if cs_display in self.cs_map else "1 (CS0)"
        )
        self.chip_override = ctk.StringVar(value="Auto-detect")

        self._log_buffer: List[Tuple[str, str]] = []
        self._log_after_id = None

        self._dnd = self._load_dnd()
        self._create_ui()
        self._register_drop_targets()

        self._print_welcome_message()
        if self._sockets_warning:
            self.log_note(self._sockets_warning, "warning")
        self._auto_select_port(prefer=self.cfg.get("port", ""))
        self._set_led(LED["IDLE"])
        self.save_config()
        self._refresh_sockets()
        self._set_idle_status("Ready", T["dim"])
        self.after(1500, self._watch_ports)
        self.after(1000, self._scan_tick)

        shortcuts = {
            "<Control-p>": self.probe,
            "<Control-r>": self.read,
            "<Control-e>": self.erase,
            "<Control-w>": self.write,
            "<Control-v>": self.verify,
            "<Return>": self._run_batch,
        }
        for key, fn in shortcuts.items():
            self.bind(key, lambda e, fn=fn: self._shortcut(fn))
        self._bind_socket_keys()
        self.bind("<Control-l>", lambda e: self._clear_log())
        self.bind("<Control-q>", lambda e: self._on_closing())
        self.bind("<Control-comma>", lambda e: self._toggle_settings())
        # Esc stops jobs; with none running it leaves the settings page
        self.bind(
            "<Escape>",
            lambda e: self._close_settings()
            if self._settings_open and not (self.busy or self._queue)
            else self.stop(),
        )
        # Clicking anywhere outside a text field releases focus so shortcuts work again
        self.bind("<Button-1>", self._drop_focus, add="+")

    def _set_window_icon(self):
        """The hydra (logo-mark.png) as the window and taskbar icon."""
        try:
            self._icons = [
                tkinter.PhotoImage(master=self, data=APP_ICON_PNG[size])
                for size in sorted(APP_ICON_PNG, reverse=True)
            ]
            self.iconphoto(True, *self._icons)
            # customtkinter sets its own icon on Windows 200 ms after start unless
            # iconbitmap() was called; this keeps ours
            self._iconbitmap_method_called = True
        except tkinter.TclError:
            pass

    def _build_cs_map(self):
        """Target menu labels -> socket (or CS_ALL) for the current socket count."""
        self.cs_map = {f"{i + 1} (CS{i})": i for i in range(self.num_slots)}
        if self.has_all:
            self.cs_map[CS_ALL_LABEL] = CS_ALL
        self.cs_labels = {v: k for k, v in self.cs_map.items()}

    @staticmethod
    def _clean_socket_name(name) -> str:
        """A socket name as stored: one line, single spaces, at most 16 characters."""
        return " ".join(str(name or "").split())[:SOCKET_NAME_MAX]

    def _socket_unavailable(self, cs: int) -> str:
        """Why socket cs can't be used, "" if it can (also from worker threads)."""
        if self._fw_cs is not None and cs >= self._fw_cs:
            return "no CS line in firmware"
        if self.sockets_auto and self._wired and not self._wired >> cs & 1:
            return "not wired"
        return ""

    def _usable_sockets(self) -> List[int]:
        return [cs for cs in range(self.num_slots) if not self._socket_unavailable(cs)]

    def _auto_count(self) -> Optional[int]:
        """Socket count from the detected wiring: up to the highest wired CS line."""
        return self._wired.bit_length() if self._wired else None

    @staticmethod
    def _wired_text(bits: int) -> str:
        """"CS0–CS3", or "CS0, CS1, CS3 (CS2 not wired)" with gaps."""
        wired = [cs for cs in range(bits.bit_length()) if bits >> cs & 1]
        missing = [cs for cs in range(bits.bit_length()) if not bits >> cs & 1]
        if not missing:
            return "CS0" if len(wired) == 1 else f"CS0–CS{wired[-1]}"
        gap = ", ".join(f"CS{cs}" for cs in missing)
        return ", ".join(f"CS{cs}" for cs in wired) + f" ({gap} not wired)"

    def _sock(self, cs: int) -> str:
        """How a socket is named in the log and in messages: "CS1 EC", or "CS1"."""
        name = self.socket_names[cs] if 0 <= cs < len(self.socket_names) else ""
        return f"CS{cs} {name}" if name else f"CS{cs}"

    def _socks(self, sockets) -> str:
        return ", ".join(self._sock(cs) for cs in sockets)

    def _sock_col(self, cs: int) -> str:
        """_sock() padded to the longest socket label, for aligned log tables."""
        width = max(len(self._sock(c)) for c in range(self.num_slots))
        return f"{self._sock(cs):<{width}}"

    def _card_title(self, cs: int) -> Tuple[str, str]:
        """(title, tag) of a socket card: the name with its CS number as the tag,
        or the CS number with the socket's position."""
        name = self.socket_names[cs]
        return (name, f"CS{cs}") if name else (f"CS{cs}", f"#{cs + 1}")

    def _bind_socket_keys(self):
        """Alt+1…9 (Alt+0 for socket 10) and Alt+A, for the current socket count."""
        for key in [str(k) for k in range(10)] + ["a"]:
            self.unbind(f"<Alt-Key-{key}>")
        for i in range(self.num_slots):
            # Number row: socket 10 is Alt+0
            key = (i + 1) % 10
            self.bind(f"<Alt-Key-{key}>", lambda e, cs=i: self._select_target(cs))
        if self.has_all:
            self.bind("<Alt-Key-a>", lambda e: self._select_target(CS_ALL))

    # ====================== Config & Binary ======================
    def load_config(self) -> dict:
        if not os.path.isfile(CONFIG_FILE):
            return {}
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self):
        if not hasattr(self, "port_entry") or self.flashrom_path is None:
            return
        self.cfg.update(
            {
                "port": self.port_entry.get().strip(),
                "flashrom_path": self.flashrom_path,
                "force": self.force_var.get(),
                "progress": self.progress_var.get(),
                "noverify": self.noverify_var.get(),
                "fast_verify": self.fast_verify_var.get(),
                "partial_write": self.partial_var.get(),
                "one_by_one": self.one_by_one_var.get(),
                "notifications": self.notify_var.get(),
                "live_scan": self.scan_var.get(),
                "scan_interval": self.scan_interval_var.get(),
                "scan_speed": self.scan_speed_var.get(),
                "leds": self.leds_var.get(),
                "socket_names": self.socket_names,
                "theme": self.theme,
                "spispeed": self.spispeed_var.get(),
                "cs": self.cs_display_var.get(),
                "last_dir": self.last_dir,
                "recent_files": self.recent_files,
                "jedec_cache": self.jedec_cache,
                "job_times": self.job_times,
                "sockets": self.manual_sockets,
                "sockets_auto": self.sockets_auto,
                "sockets_detected": self.sockets_detected,
            }
        )
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=4)
        except Exception as e:
            print(f"Config save failed: {e}", file=sys.stderr)

    def _resolve_flashrom_binary(self) -> Optional[str]:
        saved = self.cfg.get("flashrom_path")
        if saved and os.path.isfile(saved):
            return saved
        found = self._auto_find_flashrom()
        if found:
            return found
        return self._ask_user_for_binary()

    def _auto_find_flashrom(self) -> Optional[str]:
        for name in ["flashprog", "flashrom"]:
            if p := shutil.which(name):
                return p
        if not self.is_windows:
            candidates = [
                "/usr/bin/flashprog",
                "/usr/bin/flashrom",
                "/usr/local/bin/flashprog",
                "/usr/local/bin/flashrom",
                os.path.expanduser("~/bin/flashprog"),
                os.path.expanduser("~/bin/flashrom"),
            ]
        else:
            candidates = [
                os.path.expanduser("~/Downloads/flashrom.exe"),
                os.path.expanduser("~/Downloads/flashprog.exe"),
                r"C:\Program Files\flashrom\flashrom.exe",
                r"C:\flashrom\flashrom.exe",
                os.path.join(os.getcwd(), "flashrom.exe"),
                os.path.join(os.getcwd(), "flashprog.exe"),
            ]
        for c in candidates:
            if os.path.isfile(c):
                return os.path.abspath(c)
        return None

    def _ask_user_for_binary(self) -> Optional[str]:
        title = "Locate flashrom / flashprog binary (one-time)"
        filetypes = [
            ("Executable", "*.exe" if self.is_windows else "*"),
            ("All files", "*.*"),
        ]
        path = self._ask_open_file(title, filetypes)
        return path if path and os.path.isfile(path) else None

    def _exit_no_binary(self):
        messagebox.showerror(
            "Error", "No valid flashrom/flashprog binary found or selected.\nExiting."
        )
        self.destroy()
        sys.exit(1)

    # ====================== UI ======================
    def _panel(self, parent, **kw) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=T["panel"],
            border_color=T["border"],
            border_width=1,
            corner_radius=8,
            **kw,
        )

    def _section(self, parent, text: str) -> ctk.CTkLabel:
        label = ctk.CTkLabel(
            parent, text=text, font=self.fonts["small"], text_color=T["dim"], anchor="w"
        )
        label.pack(fill="x", padx=4, pady=(12, 4))
        return label

    def _button(
        self,
        parent,
        text: str,
        command,
        color: Optional[str] = None,
        filled: bool = False,
        width: int = 110,
    ) -> ctk.CTkButton:
        color = color or T["text"]
        if filled:
            return ctk.CTkButton(
                parent,
                text=text,
                command=command,
                width=width,
                height=32,
                corner_radius=6,
                font=self.fonts["bold"],
                fg_color=color,
                hover_color=blend(color, "#ffffff", 0.85),
                text_color=T["bg"],
                text_color_disabled=blend(T["bg"], color, 0.5),
            )
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=32,
            corner_radius=6,
            font=self.fonts["normal"],
            fg_color="transparent",
            border_width=1,
            border_color=blend(color, T["panel"], 0.45),
            hover_color=T["panel_hi"],
            text_color=color,
            text_color_disabled=T["faint"],
        )

    def _combo(self, parent, values, variable, width) -> ctk.CTkComboBox:
        return ctk.CTkComboBox(
            parent,
            values=values,
            variable=variable,
            width=width,
            height=30,
            state="readonly",
            font=self.fonts["normal"],
            dropdown_font=self.fonts["normal"],
            fg_color=T["bg"],
            border_color=T["border"],
            border_width=1,
            button_color=T["border"],
            button_hover_color=T["border_hi"],
            text_color=T["text"],
            dropdown_fg_color=T["panel"],
            dropdown_hover_color=T["panel_hi"],
            dropdown_text_color=T["text"],
            corner_radius=6,
        )

    def _create_ui(self):
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=20, pady=(16, 10))
        self._ui_root = root

        self._create_header(root)
        # Swapped with the settings page; status line, log and footer stay below both
        self._top_view = ctk.CTkFrame(root, fg_color="transparent")
        self._top_view.pack(fill="x")
        self._sockets_box = ctk.CTkFrame(self._top_view, fg_color="transparent")
        self._sockets_box.pack(fill="x")
        self._create_sockets(self._sockets_box)
        self._create_settings(self._top_view)
        self._create_actions(self._top_view)
        self._create_status_line(root)
        self._create_log_area(root)
        self._create_footer(root)
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _create_header(self, parent):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x")
        # A plain Tk label: CTkLabel wants a PIL-backed CTkImage, PhotoImage
        # reads PNG (with alpha) on its own. Pick the 2x asset on HiDPI.
        scale = 2 if ctk.ScalingTracker.get_window_scaling(self) >= 1.5 else 1
        try:
            self._logo_img = tkinter.PhotoImage(master=self, data=LOGO_PNG[scale])
        except tkinter.TclError:
            self._logo_img = None
        self._logo_lbl = None
        if self._logo_img:
            self._logo_lbl = tkinter.Label(
                head,
                image=self._logo_img,
                bg=tk_color(T["bg"]),
                bd=0,
                highlightthickness=0,
            )
            self._logo_lbl.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            head,
            text="Hydra Flasher" if self._logo_img else "✻ Hydra Flasher",
            font=self.fonts["title"],
            text_color=T["accent"],
        ).pack(side="left")
        self._subtitle = ctk.CTkLabel(
            head,
            text=f"  {self.num_slots}× SOIC-16 · serprog",
            font=self.fonts["normal"],
            text_color=T["dim"],
        )
        self._subtitle.pack(side="left", pady=(3, 0))
        self.conn_label = ctk.CTkLabel(
            head, text="", font=self.fonts["normal"], text_color=T["dim"]
        )
        self.conn_label.pack(side="right")
        # Light/dark switch, left of the port status: icon + the mode a click switches
        # to. The icons are drawn (THEME_ICONS), not font glyphs: Nerd Font "Mono"
        # squeezes icons into one cell, which made the sun read as a dot.
        switch = ctk.CTkFrame(head, fg_color="transparent", cursor="hand2")
        switch.pack(side="right", padx=(0, 16))
        self._theme_icon = tkinter.Label(switch, bd=0, highlightthickness=0, cursor="hand2")
        self._theme_icon.pack(side="left")
        self.theme_button = ctk.CTkLabel(
            switch, text="", font=self.fonts["normal"], cursor="hand2"
        )
        self.theme_button.pack(side="left", padx=(6, 0))
        self._theme_hover = False
        for w in (switch, self._theme_icon, self.theme_button):
            w.bind("<Button-1>", lambda e: self._toggle_theme())
            w.bind("<Enter>", lambda e: self._render_theme_switch(True))
            w.bind("<Leave>", lambda e: self._render_theme_switch(False))
        self._render_theme_switch()
        # Settings, left of the theme switch: drawn gear + "settings" (Ctrl+,)
        gear = ctk.CTkFrame(head, fg_color="transparent", cursor="hand2")
        gear.pack(side="right", padx=(0, 18))
        self._gear_icon = tkinter.Label(gear, bd=0, highlightthickness=0, cursor="hand2")
        self._gear_icon.pack(side="left")
        self._gear_label = ctk.CTkLabel(
            gear, text="settings", font=self.fonts["normal"], cursor="hand2"
        )
        self._gear_label.pack(side="left", padx=(6, 0))
        self._gear_hover = False
        for w in (gear, self._gear_icon, self._gear_label):
            w.bind("<Button-1>", lambda e: self._toggle_settings())
            w.bind("<Enter>", lambda e: self._render_gear(True))
            w.bind("<Leave>", lambda e: self._render_gear(False))
        self._render_gear()

    def _render_gear(self, hover: Optional[bool] = None):
        """Accent while hovered or while the settings page is open."""
        if hover is not None:
            self._gear_hover = hover
        color = T["accent"] if (self._gear_hover or self._settings_open) else T["dim"]
        scale = 2 if ctk.ScalingTracker.get_window_scaling(self) >= 1.5 else 1
        bg = tk_color(T["bg"])
        self._gear_img = tinted_icon(self, "gear", scale, tk_color(color), bg)
        self._gear_icon.configure(image=self._gear_img, bg=bg)
        self._gear_label.configure(text_color=color)

    def _render_theme_switch(self, hover: Optional[bool] = None):
        """Sun + "light mode" in dark mode, moon + "dark mode" in light mode."""
        if hover is not None:
            self._theme_hover = hover
        color = T["accent"] if self._theme_hover else T["dim"]
        icon, label = ("sun", "light mode") if self.theme == "dark" else ("moon", "dark mode")
        scale = 2 if ctk.ScalingTracker.get_window_scaling(self) >= 1.5 else 1
        bg = tk_color(T["bg"])
        self._theme_img = tinted_icon(self, icon, scale, tk_color(color), bg)
        self._theme_icon.configure(image=self._theme_img, bg=bg)
        self.theme_button.configure(text=label, text_color=color)

    def _create_sockets(self, parent):
        self._sockets_label = self._section(parent, self._sockets_header())
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        self.cards = {}
        all_card = [CS_ALL] if self.has_all else []
        for i, cs in enumerate(list(range(self.num_slots)) + all_card):
            if cs == CS_ALL:
                card = SocketCard(
                    row, "ALL", "all", self.fonts, lambda: self._select_target(CS_ALL)
                )
            else:
                card = SocketCard(
                    row,
                    *self._card_title(cs),
                    self.fonts,
                    lambda cs=cs: self._select_target(cs),
                )
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            row.grid_columnconfigure(i, weight=1, uniform="sock")
            self.cards[cs] = card
        # Filled in while jobs run; its space stays reserved so the layout doesn't jump
        self.busy_banner = ctk.CTkLabel(
            parent, text="", font=self.fonts["bold"], text_color=T["red"], height=18
        )
        self.busy_banner.pack(fill="x", pady=(8, 0))
        if not getattr(self, "_cs_traced", False):
            self.cs_display_var.trace_add("write", lambda *a: self._refresh_selection())
            self._cs_traced = True
        self._refresh_selection()

    def _sockets_header(self) -> str:
        drop = " · drop an image to write" if self._dnd else ""
        n = self.num_slots
        keys = "alt+1" if n == 1 else f"alt+1…{min(n, 9)}" + (", alt+0" if n == 10 else "")
        scan = "live" if self.scan_var.get() else "scan off"
        return (
            f"sockets  · {scan} · click to select target{drop}  "
            f"({keys}{', alt+a' if self.has_all else ''})"
        )

    def _create_settings(self, parent):
        self._section(parent, "flashing options")
        panel = self._panel(parent)
        panel.pack(fill="x")

        row1 = ctk.CTkFrame(panel, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=(12, 6))

        def label(p, text):
            ctk.CTkLabel(
                p, text=text, font=self.fonts["normal"], text_color=T["dim"]
            ).pack(side="left", padx=(0, 8))

        label(row1, "port")
        self.port_entry = ctk.CTkEntry(
            row1,
            width=190,
            height=30,
            font=self.fonts["normal"],
            corner_radius=6,
            fg_color=T["bg"],
            border_color=T["border"],
            border_width=1,
            text_color=T["text"],
            placeholder_text="/dev/ttyACM0 or COM4",
        )
        self.port_entry.pack(side="left")
        self._button(row1, "↻", self._refresh_ports, width=34).pack(
            side="left", padx=(6, 22)
        )
        label(row1, "speed")
        self._combo(row1, SPI_SPEEDS, self.spispeed_var, 90).pack(
            side="left", padx=(0, 22)
        )
        label(row1, "chip")
        self.chip_combo = self._combo(
            row1, self.available_chips, self.chip_override, 240
        )
        self.chip_combo.pack(side="left", fill="x", expand=True)
        self._button(row1, "×", self._clear_chip_override, width=34).pack(
            side="left", padx=(6, 0)
        )

        row2 = ctk.CTkFrame(panel, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(2, 2))
        for text, var in [
            ("force -f", self.force_var),
            ("progress", self.progress_var),
            ("skip verify -n", self.noverify_var),
            ("verify on Pico", self.fast_verify_var),
            ("used blocks only", self.partial_var),
        ]:
            TtyToggle(row2, text, var, self.fonts["normal"]).pack(
                side="left", padx=(0, 18)
            )

        row3 = ctk.CTkFrame(panel, fg_color="transparent")
        row3.pack(fill="x", padx=14, pady=(2, 10))
        self._production_toggle = TtyToggle(
            row3, "production mode", self.production_var, self.fonts["normal"]
        )
        self._production_toggle.pack(side="left", padx=(0, 18))
        # Only shown while ALL is the target (see _refresh_selection)
        self._one_by_one_toggle = TtyToggle(
            row3,
            "one-by-one",
            self.one_by_one_var,
            self.fonts["normal"],
        )
        self.one_by_one_var.trace_add("write", lambda *a: self._on_one_by_one())
        self._refresh_selection()  # show the one-by-one toggle if ALL is the target
        self._button(
            row3, "flash log ▸", self._open_flash_log, color=T["soft"], width=120
        ).pack(side="right")
        self._button(
            row3, "recent files ▸", self._show_recent_menu, color=T["soft"], width=130
        ).pack(side="right", padx=(0, 8))
        self.production_var.trace_add(
            "write", lambda *a: self.after_idle(self._toggle_production)
        )

    def _create_actions(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(14, 0))
        buttons = [
            ("probe", self.probe, None, False),
            ("read", self.read, None, False),
            ("verify", self.verify, T["green"], False),
            ("erase", self.erase, T["red"], False),
            ("write", self.write, T["accent"], True),
        ]
        self._op_buttons = []
        self._op_button_by_name: Dict[str, ctk.CTkButton] = {}
        for text, cmd, color, filled in buttons:
            btn = self._button(row, text, cmd, color=color, filled=filled)
            btn.pack(side="left", padx=(0, 8), expand=True, fill="x")
            self._op_buttons.append(btn)
            self._op_button_by_name[text] = btn
        # Original look, restored once a blocked operation is available again
        self._op_button_style = {
            name: {
                k: btn.cget(k)
                for k in ("fg_color", "text_color", "border_color", "hover_color")
            }
            for name, btn in self._op_button_by_name.items()
        }
        self.write_button = self._op_buttons[-1]
        self.stop_button = self._button(
            row, "■ stop", self.stop, color=T["red"], width=90
        )
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="left", expand=True, fill="x")

    def _create_status_line(self, parent):
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(fill="x", pady=(14, 6))
        self._status_line = line
        self.spin_label = ctk.CTkLabel(
            line, text="●", width=18, font=self.fonts["bold"], text_color=T["dim"]
        )
        self.spin_label.pack(side="left")
        self.status_label = ctk.CTkLabel(
            line, text="", font=self.fonts["normal"], text_color=T["text"]
        )
        self.status_label.pack(side="left", padx=(6, 0))
        self.status_meta = ctk.CTkLabel(
            line, text="", font=self.fonts["normal"], text_color=T["dim"]
        )
        self.status_meta.pack(side="left", padx=(8, 0))
        self.bar_pct = ctk.CTkLabel(
            line, text="", font=self.fonts["normal"], text_color=T["soft"], width=44
        )
        self.bar_pct.pack(side="right")
        self.bar_empty = ctk.CTkLabel(
            line, text="", font=self.fonts["normal"], text_color=T["faint"]
        )
        self.bar_empty.pack(side="right")
        self.bar_fill = ctk.CTkLabel(
            line, text="", font=self.fonts["normal"], text_color=T["accent"]
        )
        self.bar_fill.pack(side="right")

    def _create_log_area(self, parent):
        panel = self._panel(parent)
        panel.pack(fill="both", expand=True)
        self._log_panel = panel
        self.log = ctk.CTkTextbox(
            panel,
            font=self.fonts["log"],
            wrap="word",
            fg_color=T["panel"],
            text_color=T["text"],
            border_width=0,
            corner_radius=8,
            scrollbar_button_color=T["border"],
            scrollbar_button_hover_color=T["border_hi"],
            activate_scrollbars=True,
        )
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        # Wrapped lines hang under the text instead of starting at the left edge
        hang = self.fonts["log"].measure("0" * 9)
        for tag in LOG_COLORS:
            self.log.tag_config(tag, lmargin2=hang)
        self.log.configure(state="disabled")
        # customtkinter's scrollbar ends every redraw with update_idletasks(), which
        # forces all pending redraws of the window at once, several times per resize
        # step. Without it the scrollbar is redrawn moments later with the rest.
        for sb in ("_y_scrollbar", "_x_scrollbar"):
            canvas = getattr(getattr(self.log, sb, None), "_canvas", None)
            if canvas is not None:
                canvas.update_idletasks = lambda: None
        self._make_log_copyable()
        self._apply_log_colors()

    def _make_log_copyable(self):
        """The log is read-only, but text in it can be selected and copied like in a
        terminal: drag + Ctrl+C (or Ctrl+Shift+C), Ctrl+A, or the right-click menu.
        Keys are bound explicitly: Tk's own Ctrl+A is 'line start' on X11."""
        tb = self.log._textbox
        # A disabled Text doesn't take focus on click by itself; without it Ctrl+C
        # would never reach the log
        tb.bind("<Button-1>", lambda e: tb.focus_set(), add="+")
        copy = lambda e: (self._copy_log(), "break")[1]
        for seq in ("<Control-c>", "<Control-C>", "<Control-Insert>"):
            tb.bind(seq, copy)
        tb.bind("<Control-a>", lambda e: (self._select_all_log(), "break")[1])
        self.log_menu = tkinter.Menu(
            self, tearoff=0, bd=0, font=self.fonts["normal"]
        )  # colours: _apply_log_colors
        self.log_menu.add_command(label="Copy", command=self._copy_log)
        self.log_menu.add_command(
            label="Copy all", command=lambda: self._copy_log(all_text=True)
        )
        self.log_menu.add_separator()
        self.log_menu.add_command(label="Select all", command=self._select_all_log)
        # Opened on button *release*: opened on press, the release lands on the
        # menu, which takes it as a click on the item under the pointer and closes
        buttons = (
            ("<ButtonRelease-2>", "<Control-ButtonRelease-1>")
            if self.platform == "Darwin"
            else ("<ButtonRelease-3>",)
        )
        for seq in buttons:
            tb.bind(seq, self._show_log_menu)

    def _apply_log_colors(self):
        """Colours of the log's plain Tk parts: text tags, selection, right-click menu.
        Tk doesn't follow customtkinter's appearance mode, so _apply_theme reruns this."""
        for tag, color in LOG_COLORS.items():
            self.log.tag_config(tag, foreground=tk_color(color))
        sel = tk_color(blend(T["accent"], T["panel"], 0.35))
        # Keep the selection visible while the log isn't focused (e.g. menu open)
        self.log._textbox.configure(selectbackground=sel, inactiveselectbackground=sel)
        self.log_menu.configure(
            bg=tk_color(T["panel_hi"]),
            fg=tk_color(T["text"]),
            activebackground=tk_color(T["accent"]),
            activeforeground=tk_color(T["bg"]),
            disabledforeground=tk_color(T["faint"]),
        )

    def _toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        ctk.set_appearance_mode(self.theme)
        self._apply_theme()
        self.save_config()

    def _apply_theme(self):
        """Re-colour the plain Tk widgets; customtkinter widgets switch by themselves."""
        self._render_theme_switch()
        self._render_gear()
        if self._logo_lbl:
            self._logo_lbl.configure(bg=tk_color(T["bg"]))
        self._apply_log_colors()
        for card in self.cards.values():
            card._draw_led()

    def _show_log_menu(self, event):
        has_sel = bool(self.log._textbox.tag_ranges("sel"))
        self.log_menu.entryconfigure("Copy", state="normal" if has_sel else "disabled")
        # 1 px off the pointer, so the pointer isn't already over the first item
        self.log_menu.tk_popup(event.x_root + 1, event.y_root + 1)
        if self.is_windows:
            # Only there does tk_popup wait until the menu closes; on X11 releasing
            # the grab right away makes the menu vanish
            self.log_menu.grab_release()
        return "break"

    def _copy_log(self, all_text: bool = False):
        """Copy the selection (or, with all_text, the whole log) to the clipboard."""
        tb = self.log._textbox
        if all_text:
            text = tb.get("1.0", "end-1c").rstrip("\n")
        elif tb.tag_ranges("sel"):
            text = tb.get("sel.first", "sel.last")
        else:
            return
        # The welcome box aligns its right border with a tab; paste it as spaces
        col = getattr(self, "_box_column", 0)
        text = "\n".join(
            line.replace("\t", " " * max(col - line.index("\t"), 1), 1)
            if "\t" in line
            else line
            for line in text.split("\n")
        )
        self.clipboard_clear()
        self.clipboard_append(text)

    def _select_all_log(self):
        tb = self.log._textbox
        tb.tag_add("sel", "1.0", "end-1c")
        tb.focus_set()

    def _log_at_end(self) -> bool:
        """Whether the log shows its newest output. Checked before adding text: new
        output only scrolls the log if it was at the end, so scrolling up (e.g. to
        select text) isn't undone by every new line."""
        return self.log._textbox.yview()[1] >= 0.999

    def _create_footer(self, parent):
        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.pack(fill="x", pady=(6, 0))
        self._footer = foot
        self.footer_left = ctk.CTkLabel(
            foot, text="", font=self.fonts["small"], text_color=T["faint"]
        )
        self.footer_left.pack(side="left")
        self.footer_right = ctk.CTkLabel(
            foot, text="", font=self.fonts["small"], text_color=T["faint"]
        )
        self.footer_right.pack(side="right")
        self.spispeed_var.trace_add("write", lambda *a: self._update_footer())
        self.chip_override.trace_add("write", lambda *a: self._update_footer())
        self._update_footer()

    def _update_footer(self):
        self.footer_left.configure(
            text="⏎ run batch · esc stop · ^L clear"
            if self._prod
            else "^P probe · ^R read · ^V verify · ^E erase · ^W write · esc stop · ^L clear"
        )
        if self._prod:
            p = self._prod
            self.footer_right.configure(
                text=f"production · {trunc(p['name'], 28)} · batch {p['batch']} · ✓ {p['ok']} ✕ {p['fail']}",
                text_color=T["accent"],
            )
            return
        chip = self.chip_override.get()
        self.footer_right.configure(
            text=f"target {self._target_label(self._selected_cs(), short=True)}"
            f" · {self.spispeed_var.get()}"
            + ("" if chip == "Auto-detect" else f" · -c {chip}"),
            text_color=T["faint"],
        )

    def _one_by_one(self, cs: Optional[int] = None) -> bool:
        """ALL is (or cs is) the target and writes go to each socket in turn."""
        cs = self._selected_cs() if cs is None else cs
        return cs == CS_ALL and self.one_by_one_var.get()

    def _target_label(self, cs: int, short: bool = False) -> str:
        if cs != CS_ALL:
            return f"{self._sock(cs)}"
        mode = "one-by-one" if self._one_by_one(cs) else "broadcast"
        return f"ALL ({mode})" if short else f"ALL sockets ({mode})"

    def _on_one_by_one(self):
        self._refresh_sockets()
        self._update_footer()
        self.save_config()

    # ====================== Sockets ======================
    def _select_target(self, cs: int):
        self.cs_display_var.set(self.cs_labels[cs])

    def _refresh_selection(self):
        sel = self._selected_cs()
        for cs, card in self.cards.items():
            card.set_selected(cs == sel)
        if hasattr(self, "footer_right"):
            self._update_footer()
        if hasattr(self, "_one_by_one_toggle"):
            if sel == CS_ALL:
                self._one_by_one_toggle.pack(
                    side="left", padx=(0, 18), after=self._production_toggle
                )
            else:
                self._one_by_one_toggle.pack_forget()
            self._refresh_sockets()  # the ALL card text depends on one-by-one
        self._refresh_op_buttons()

    def _refresh_sockets(self):
        queued: Dict[int, int] = {}  # socket -> position of its first queued job
        for i, entry in enumerate(self._queue):
            if entry["cs"] is not None:
                queued.setdefault(entry["cs"], i + 1)
        for cs in range(self.num_slots):
            card = self.cards.get(cs)
            if card is None:  # the socket row is being rebuilt (_set_socket_count)
                continue
            slot, res = self.slots[cs], self._socket_result[cs]
            name = slot["name"] if slot and slot["detected"] else "…"
            broadcast_busy = (
                CS_ALL in self._busy_sockets and slot is not None and slot["detected"]
            )
            reason = self._socket_unavailable(cs)
            if reason == "not wired":
                card.set_state("unknown", "—", "not wired")
            elif reason:
                card.set_state("fail", "—", reason, T["red"])
            elif cs in self._busy_sockets or broadcast_busy:
                card.set_state("busy", name, "working…", T["accent"])
            elif cs in queued:
                card.set_state(
                    "queued", name, f"{self._clock} queued #{queued[cs]}", T["dim"]
                )
            elif slot is None:
                card.set_state("unknown", "—", "not probed")
            elif not slot["detected"]:
                card.set_state("empty", "empty", "no chip detected")
            elif slot.get("unsupported"):
                card.set_state(
                    "warn", "unsupported chip", "no flashprog definition", T["red"]
                )
            else:
                size = human_size(slot["size_kb"] * 1024) if slot["size_kb"] else "?"
                if res == "pass":
                    card.set_state(
                        "pass", slot["name"], f"✓ verified · {size}", T["green"]
                    )
                elif res == "fail":
                    card.set_state("fail", slot["name"], f"✕ failed · {size}", T["red"])
                elif not slot["chips"]:
                    card.set_state(
                        "ok", slot["name"], f"{size} · probe to identify", T["yellow"]
                    )
                elif (
                    len(slot["chips"]) > 1 and self.chip_override.get() == "Auto-detect"
                ):
                    card.set_state(
                        "warn",
                        slot["name"],
                        f"{len(slot['chips'])} defs match · {size}",
                        T["yellow"],
                    )
                else:
                    card.set_state("ok", slot["name"], size)

        if self.has_all and CS_ALL in self.cards:
            self._refresh_all_card(self.cards[CS_ALL], queued)
        self._refresh_op_buttons()  # write is greyed while ALL can't be broadcast to

    def _refresh_all_card(self, all_card: "SocketCard", queued: Dict[int, int]):
        detected = self._detected_slots()
        usable = [d for d in detected if not d.get("unsupported")]
        mode = "one-by-one" if self.one_by_one_var.get() else "broadcast"
        if CS_ALL in self._busy_sockets:
            all_card.set_state(
                "busy", mode, f"{len(detected)} chips · working…", T["accent"]
            )
        elif CS_ALL in queued:
            all_card.set_state(
                "queued", mode, f"{self._clock} queued #{queued[CS_ALL]}", T["dim"]
            )
        elif all(s is None for s in self.slots):
            all_card.set_state("unknown", mode, "probe to check")
        elif not usable:
            all_card.set_state("empty", mode, "no chips")
        elif self.one_by_one_var.get():
            # Each socket gets its own job, so the chips may differ
            all_card.set_state(
                "ok", mode, f"{len(usable)} chip{'s' * (len(usable) > 1)} · in turn"
            )
        elif self._same_type_error(detected):
            all_card.set_state("fail", mode, "chip types differ", T["red"])
        else:
            all_card.set_state(
                "ok",
                "broadcast",
                f"{len(detected)} identical chip{'s' * (len(detected) > 1)}",
            )

    def _mark_busy(self, cs: int, busy: bool):
        (self._busy_sockets.add if busy else self._busy_sockets.discard)(cs)
        self._refresh_sockets()

    def _reset_sockets(self):
        self.slots = [None] * self.num_slots
        self._socket_result = [None] * self.num_slots
        self._socket_ident = [_UNSCANNED] * self.num_slots
        self._fw_caps = None
        self._fw_cs = None
        self._fw_name = None
        self._wired = None
        self._refresh_sockets()
        self._refresh_settings()

    def _update_conn_label(self):
        cur = self.port_entry.get().strip()
        if not self._port_connected:
            text, color = "○ no programmer", T["red"]
        elif self._is_unresponsive(cur) or (
            self._unresponsive and self._unresponsive[0] == cur
        ):
            text, color = f"● {cur} · not responding", T["yellow"]
        else:
            text, color = f"● {cur}", T["green"]
        self.conn_label.configure(text=text, text_color=color)

    def _watch_ports(self):
        """Poll for the programmer being plugged in / removed."""
        try:
            if not self.busy:
                ports = serial.tools.list_ports.comports()
                devices = {p.device for p in ports}
                cur = self.port_entry.get().strip()
                typing = self.focus_get() is not None and isinstance(
                    self.focus_get(), tkinter.Entry
                )
                if cur not in devices and not typing:
                    best = max(ports, key=self._score_port, default=None)
                    if best is not None and self._score_port(best) >= 5:
                        self.port_entry.delete(0, "end")
                        self.port_entry.insert(0, best.device)
                        cur = best.device
                connected = bool(cur) and cur in devices
                if connected != self._port_connected:
                    if self._port_connected is not None:
                        if connected:
                            self.log_note(
                                f"Programmer connected on {cur}", "success"
                            )
                            self._set_led(LED["IDLE"])
                        else:
                            self.log_note(
                                f"Programmer disconnected", "warning"
                            )
                            self._unresponsive = None  # replugged: try it afresh
                            self._reset_sockets()
                    self._port_connected = connected
                    self._refresh_settings()
                self._update_conn_label()
                rp2 = rp2_drive_present()
                if rp2 and not self._rp2_present:
                    self.log_note(
                        "A Pico in bootloader mode (RPI-RP2) is plugged in: install "
                        "firmware on it from settings → firmware.",
                        "info",
                    )
                if rp2 != self._rp2_present:
                    self._rp2_present = rp2
                    self._refresh_settings()
        except Exception:
            pass
        self.after(2000, self._watch_ports)

    # --- live socket scan ---------------------------------------------
    def _scan_tick(self):
        """While idle, every scan interval: read the JEDEC/unique ID of every socket.
        With the live scan off, only the firmware is asked once after connecting
        (CS lines, version); the queue still scans between jobs (_queue_continue)."""
        try:
            port = self.port_entry.get().strip()
            chips = self.scan_var.get()
            if (
                not self.busy
                and not self._scan_running
                and self._port_connected
                and port
                and self._port_accessible(port)
                and (chips or self._fw_caps is None)
                and not self._is_unresponsive(port)
            ):
                self._scan_running = True
                threading.Thread(
                    target=self._scan_worker,
                    args=(port, self._scan_hz(), chips),
                    daemon=True,
                ).start()
        except Exception:
            self._scan_running = False
        delay = SCAN_INTERVALS.get(self.scan_interval_var.get(), SCAN_INTERVAL_MS)
        if self._fw_caps is None:  # firmware not identified yet: don't wait long
            delay = min(delay, SCAN_INTERVAL_MS)
        self.after(delay, self._scan_tick)

    def _is_unresponsive(self, port: str) -> bool:
        """port's Pico didn't answer recently (it's tried again after a pause)."""
        u = self._unresponsive
        return bool(u) and u[0] == port and time.monotonic() < u[1]

    def _mark_unresponsive(self, port: str, why: str):
        first = not (self._unresponsive and self._unresponsive[0] == port)
        self._unresponsive = (port, time.monotonic() + UNRESPONSIVE_PAUSE_S)
        if first:
            self.log_note(
                f"The Pico on {port} doesn't answer ({why}). It may run other or "
                "older firmware, or hang. To install HydraFlasher firmware: hold "
                "BOOTSEL while plugging it in again, then settings → firmware.",
                "warning",
            )
            self._update_conn_label()
            self._refresh_settings()

    def _clear_unresponsive(self, port: str):
        if self._unresponsive and self._unresponsive[0] == port:
            self._unresponsive = None
            self.log_note(f"The Pico on {port} answers again", "info")
            self._update_conn_label()
            self._refresh_settings()

    def _scan_hz(self) -> int:
        return parse_speed(self.scan_speed_var.get())

    def _scan_worker(self, port: str, hz: int = SCAN_SPEED_HZ, chips: bool = True):
        """chips=False: only ask the firmware (command map, name, CS lines)."""
        results, silent = None, False
        if self._port_lock.acquire(blocking=False):
            try:
                prog = Programmer(port)
                try:
                    if self._fw_caps is None:
                        caps = prog.command_map()
                        fw_cs = (
                            prog.num_cs() if S_CMD_Q_NUM_CS in caps else LEGACY_CS_PINS
                        )
                        name = prog.pgm_name() if S_CMD_Q_PGMNAME in caps else ""
                        wired = prog.cs_wired() if S_CMD_Q_CS_WIRED in caps else None
                        self._fw_caps, self._fw_cs, self._fw_name = caps, fw_cs, name
                        self.after(0, self._on_fw_cs, wired)
                    if chips:
                        prog.set_speed(hz)
                        # Not past the firmware's CS lines (they'd be NAKed), not
                        # the sockets detected as not wired
                        results = [
                            (cs, prog.identify(cs)) for cs in self._usable_sockets()
                        ]
                        prog.pins(False)
                finally:
                    prog.close()
            except (NoAnswer, serial.SerialTimeoutException) as e:
                # Other firmware, or a hung one: it takes bytes in, or not even that
                results, silent = None, True
                self.after(0, self._mark_unresponsive, port, str(e))
            except Exception:
                results = None
            finally:
                self._port_lock.release()
        if results is not None or not silent:
            self.after(0, self._clear_unresponsive, port)
        self.after(0, self._apply_scan, results)

    def _on_fw_cs(self, wired: Optional[int] = None):
        """The firmware's CS count (and wiring) are known (first scan after
        connecting)."""
        self._set_wired(wired)
        self._warn_fw_cs()

    def _warn_fw_cs(self):
        """Warn when more sockets are shown than the firmware has CS lines for."""
        if self._fw_cs is not None and self.num_slots > self._fw_cs:
            self.log_note(
                f"The programmer's firmware has {self._fw_cs} CS lines, but "
                f'"sockets" is {self.num_slots}: CS{self._fw_cs} and up can\'t be '
                "used – update the firmware or lower the socket count.",
                "warning",
            )
        self._refresh_sockets()
        self._refresh_settings()

    def _set_wired(self, wired: Optional[int], explicit: bool = False):
        """New wiring detection result (on connect, or explicit: detect again).
        No pull-ups found switches auto to manual, so a board without them (or with
        flaky wiring) doesn't make the count change at every reconnect; only detect
        again switches back to auto when it finds them. A result is logged only when
        it differs from the last one."""
        self._wired = wired
        if wired and wired != self._wired_logged:  # "none" has its own warning
            self._wired_logged = wired
            self.log_note(f"Sockets wired: {self._wired_text(wired)}", "info")
        if wired:
            for cs in range(self.num_slots):  # forget chips seen on unwired lines
                if not wired >> cs & 1:
                    self.slots[cs] = None
                    self._socket_ident[cs] = _UNSCANNED
            if explicit and not self.sockets_auto:
                self.sockets_auto, self._auto_fell_back = True, False
                self.save_config()
                self.log_note("Pull-up resistors found: socket count back to auto", "info")
        elif wired == 0 and self.sockets_auto:
            self.sockets_auto, self._auto_fell_back = False, True
            self.save_config()
            self.log_note(
                "No pull-up resistors found on the CS lines: socket count switched to "
                f"manual ({self.manual_sockets}). Add a 10 kΩ from each /CS to 3V3, "
                "then settings → sockets → detect again.",
                "warning",
            )
        elif wired == 0 and explicit:
            self.log_note(
                "No wired sockets detected (no pull-ups on the CS lines).", "warning"
            )
        self._apply_auto_sockets()
        self._refresh_sockets()
        self._refresh_settings()

    def _apply_auto_sockets(self):
        """Show the count of the current mode: in auto the detected one (or the
        manual one without a detection), in manual the manual one. Waits while jobs
        run or wait (_finish_job applies it)."""
        detected = self._auto_count() if self.sockets_auto else None
        if detected and detected != self.sockets_detected:
            self.sockets_detected = detected
            self.save_config()
        n = detected or self.manual_sockets
        if n == self.num_slots:
            self._sockets_pending = False
            return
        if self.busy or self._queue or self._prod:
            self._sockets_pending = True
            return
        self._sockets_pending = False
        why = "detected" if detected else ("" if self.sockets_auto else "manual")
        self._set_socket_count(n, why)

    def _detect_sockets(self):
        """Ask the firmware again which CS lines are wired (settings button)."""
        port = self.port_entry.get().strip()
        if self.busy or self._queue:
            self.log_note("Sockets can't be detected while jobs run or wait.", "warning")
            return
        if not (self._port_connected and port) or self._is_unresponsive(port):
            self.log_note("No programmer to detect sockets with.", "warning")
            return
        if self._fw_caps is not None and S_CMD_Q_CS_WIRED not in self._fw_caps:
            self.log_note(
                "This firmware can't detect sockets: it needs version 1.2 "
                "(settings → firmware).",
                "warning",
            )
            return

        def work():
            try:
                with self._programmer(port) as prog:
                    caps = self._fw_caps or prog.command_map()
                    wired = prog.cs_wired() if S_CMD_Q_CS_WIRED in caps else None
            except Exception as e:
                self.after(0, self.log_note, f"Socket detection failed: {e}", "error")
                return
            self.after(0, self._set_wired, wired, True)

        threading.Thread(target=work, daemon=True).start()

    def _slot_from_ident(self, cs: int, ident: tuple) -> dict:
        jedec, uid = ident
        if not jedec:
            return {
                "cs": cs,
                "detected": False,
                "chips": [],
                "name": "NOT DETECTED",
                "size_kb": None,
                "jedec": None,
                "uid": None,
            }
        cached = self.jedec_cache.get(jedec)
        cap = int(jedec[4:6], 16)
        size_kb = (
            cached["size_kb"]
            if cached
            else ((1 << cap) // 1024 if 0x10 <= cap <= 0x22 else None)
        )
        chips = list(cached["chips"]) if cached else []
        return {
            "cs": cs,
            "detected": True,
            "chips": chips,
            "name": " / ".join(chips) if chips else f"unknown {jedec}",
            "size_kb": size_kb,
            "jedec": jedec,
            "uid": uid,
        }

    def _apply_scan(self, results: Optional[List[tuple]]):
        self._scan_running = False
        if results is None or self.busy:
            return
        try:
            self._apply_scan_results(results)
        finally:
            if self._queue_wait_scan is not None:
                self._queue_next(self._queue_wait_scan)

    def _apply_scan_results(self, results: List[tuple]):
        changed = False
        unknown = []  # sockets that got a chip flashprog hasn't identified yet
        override = self.chip_override.get()
        misfit = []  # sockets that got a known chip the override isn't a definition of
        for cs, ident in results:
            # The count was lowered, or the line found unwired, since the scan began
            if cs >= self.num_slots or self._socket_unavailable(cs):
                continue
            prev = self._socket_ident[cs]
            if ident == prev:
                continue
            changed = True
            self._socket_ident[cs] = ident
            self._socket_result[cs] = None
            slot = self._slot_from_ident(cs, ident)
            self.slots[cs] = slot
            if ident[0] and not slot["chips"]:
                unknown.append(cs)
            elif ident[0] and override not in ("Auto-detect", *slot["chips"]):
                misfit.append(cs)
            if prev is _UNSCANNED:
                continue
            if ident[0]:
                uid = f" · uid {ident[1]}" if ident[1] else ""
                self.log_note(
                    f"{self._sock(cs)} chip inserted · {slot['name']}{uid}", "soft"
                )
            else:
                self.log_note(f"{self._sock(cs)} chip removed", "dim")
        # With a chip override flashprog only tries that one definition, so a chip it
        # doesn't fit (or one flashprog hasn't identified yet) would never be detected
        if override != "Auto-detect" and (unknown or misfit):
            self.chip_override.set("Auto-detect")
            why = [f"unknown chip on {self._sock(cs)}" for cs in unknown] + [
                f"{self._sock(cs)} holds {self.slots[cs]['name']}" for cs in misfit
            ]
            self.log_note(
                f"Chip override {override} doesn't fit ({'; '.join(why)}) – reset to Auto-detect",
                "info",
            )
        if unknown:
            self._start_identify(unknown)
        if changed:
            self._refresh_sockets()

    def _store_slot(self, cs: int, info: dict):
        """Save flashprog's view of a socket, and teach the JEDEC cache its name."""
        jedec, uid = (
            self._socket_ident[cs]
            if self._socket_ident[cs] is not _UNSCANNED
            else (None, None)
        )
        info["jedec"], info["uid"] = jedec, uid
        # Only learn from auto-detect runs: with -c flashprog reports just the forced
        # definition, which would hide that several definitions match this chip
        used = getattr(self, "_chip_used", self._job.get("chip", "Auto-detect"))
        if jedec and info["chips"] and used == "Auto-detect":
            self.jedec_cache[jedec] = {
                "chips": info["chips"],
                "size_kb": info["size_kb"],
            }
        self.slots[cs] = info

    # ====================== Logging & Helpers ======================
    # Log layout:
    #   ● Job title                    log_job()
    #     ╰  1/4 Step                  log_step()
    #          detail / tool output    log_detail()
    #   ● ✓ result
    #
    #   ● ⚠ message outside a job      log_note()
    #     ╰  detail                    log_detail()
    # Without steps, details hang directly under the job ("╰" on the first one).
    # Only use symbols the log font has (all 1 cell wide): a fallback glyph is
    # wider and shifts the line in the GUI, though a pasted copy looks fine.
    # ⎿ is such a glyph (+6 px in JetBrains Mono); ╰ isn't.
    def _log_insert(self, chunks: Sequence[Tuple[str, str]]):
        follow = self._log_at_end()
        self.log.configure(state="normal")
        for text, tag in chunks:
            self.log.insert("end", text, tag)
        self._trim_log()
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")

    def log_line(self, text: str, tag: str = "normal"):
        self._log_buffer.append((text.rstrip() + "\n", tag))
        self._progress_line_active = False
        if self._log_after_id is None:
            self._log_after_id = self.after(10, self._flush_log_buffer)

    def log_job(self, title: str):
        self.log_line("")
        self.log_line(f"{BULLET} {title}", "accent")
        self._in_step = False
        self._detail_first = True
        self._log_block = "job"

    def log_note(self, text: str, kind: str = "info"):
        """A message outside the job tree (events, refusals, choices): "● ⚠ text".
        kind is the colour tag; warning and error also get an icon. Separated from a
        finished job by an empty line. While a job runs, its tree is left alone."""
        icon = {"warning": "⚠ ", "error": "✕ "}.get(kind, "")
        if self.busy:
            self.log_line(f"{BULLET} {icon}{text}", kind)
            return
        if self._log_block == "job":
            self.log_line("")
        self.log_line(f"{BULLET} {icon}{text}", kind)
        self._log_block = "note"
        self._in_step = False
        self._detail_first = True  # a log_detail() after this hangs under the note

    def log_step(self, text: str):
        self.log_line(f"  ╰  {text}", "step")
        self._in_step = True

    def _detail_prefix(self) -> str:
        if self._in_step:
            return "       "
        prefix = "  ╰  " if self._detail_first else "     "
        self._detail_first = False
        return prefix

    def log_detail(self, text: str, tag: str = "dim"):
        self.log_line(self._detail_prefix() + text, tag)

    def _flush_log_buffer(self):
        self._log_after_id = None
        if not self._log_buffer:
            return
        chunks = list(self._log_buffer)
        self._log_buffer.clear()
        self._log_insert(chunks)

    def _trim_log(self):
        try:
            lines_count = int(self.log.index("end-1c").split(".")[0])
            if lines_count > MAX_LOG_LINES:
                self.log.delete("1.0", f"{lines_count - MAX_LOG_LINES}.0")
        except Exception:
            pass

    def _log_progress(self, text: str):
        """Log a progress line, replacing the previous one instead of piling them up."""
        self._flush_log_buffer()
        follow = self._log_at_end()
        self.log.configure(state="normal")
        if self._progress_line_active:
            self.log.delete("end-2l", "end-1l")
        self.log.insert("end", text.rstrip() + "\n", "dim")
        self.log.configure(state="disabled")
        if follow:
            self.log.see("end")
        self._progress_line_active = True

    def _clear_log(self):
        self._log_buffer.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._progress_line_active = False

    # --- status line -------------------------------------------------
    def update_status(self, text: str, tag: str = "normal"):
        if self.busy:
            self._status = text
        else:
            self._set_idle_status(text, LOG_COLORS.get(tag, T["text"]))

    def update_progress(self, text: str):
        self._sub = text

    def _set_idle_status(self, text: str, color: str, meta: str = ""):
        self.spin_label.configure(text="●", text_color=color)
        self.status_label.configure(text=text, text_color=color)
        self.status_meta.configure(text=meta)
        self._render_bar(None)

    def _render_bar(self, pct: Optional[float], width: int = 24):
        if pct is None:
            self.bar_fill.configure(text="")
            self.bar_empty.configure(text="")
            self.bar_pct.configure(text="")
            return
        filled = int(round(pct / 100 * width))
        self.bar_fill.configure(text="━" * filled)
        self.bar_empty.configure(text="─" * (width - filled))
        self.bar_pct.configure(text=f"{pct:3.0f}%")

    def _tick(self):
        if not self.busy:
            return
        self._spin_i += 1
        self.spin_label.configure(
            text=SPINNER[self._spin_i % len(SPINNER)], text_color=T["accent"]
        )
        sub = f" · {self._sub}" if self._sub else ""
        self.status_label.configure(text=f"{self._status}{sub}", text_color=T["accent"])
        elapsed = time.monotonic() - self._job_started
        left = self._eta - elapsed if self._eta else None
        if left is None:
            parts = [fmt_duration(elapsed)]
        elif left >= 1:  # the line is short of space: the estimate replaces the time
            parts = [f"~{fmt_duration(left + 0.5)} left"]
        else:
            parts = [f"{fmt_duration(elapsed)}, usually {fmt_duration(self._eta)}"]
        if self._queue:
            queued = f"{len(self._queue)} queued"
            estimates = [self._estimate(e) for e in self._queue]
            if self._eta and all(estimates):
                total = max(self._eta - elapsed, 0) + sum(estimates)
                queued += f", ~{fmt_duration(total + 0.5)} total"
            parts.append(queued)
        parts.append("esc to stop")
        self.status_meta.configure(text=f"({' · '.join(parts)})")
        self._render_bar(self._pct)
        self.after(120, self._tick)

    def notify(self, title: str, message: str):
        if self.notify_var.get():
            desktop_notify(title, message)

    human_size = staticmethod(human_size)

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "chip"

    def _selected_cs(self) -> int:
        return self.cs_map[self.cs_display_var.get()]

    def _shortcut(self, fn: Callable):
        if self._settings_open:  # the operations act on the hidden main view
            return
        # Don't hijack Ctrl+V (paste) etc. while the user is typing in a text field
        try:
            if isinstance(self.focus_get(), tkinter.Entry):
                return
        except (KeyError, tkinter.TclError):
            pass
        fn()

    def _drop_focus(self, event):
        try:
            if not isinstance(event.widget, (tkinter.Entry, tkinter.Text)):
                self.focus_set()
        except Exception:
            pass

    # ====================== Dialogs (terminal style) ======================
    def _dialog_shell(
        self, title: str, color: str
    ) -> Tuple[ctk.CTkToplevel, ctk.CTkFrame]:
        dlg = ctk.CTkToplevel(self, fg_color=T["bg"])
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        box = ctk.CTkFrame(
            dlg,
            fg_color=T["panel"],
            border_color=color,
            border_width=1,
            corner_radius=8,
        )
        box.pack(padx=12, pady=12, fill="both", expand=True)
        return dlg, box

    def _show_dialog(self, dlg: ctk.CTkToplevel, focus_widget=None):
        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        def grab():
            try:
                dlg.grab_set()
                (focus_widget or dlg).focus_force()
            except tkinter.TclError:
                pass

        dlg.after(80, grab)
        self.wait_window(dlg)

    def _choose(
        self,
        title: str,
        message: str,
        options: List[str],
        danger: bool = False,
        default: int = 0,
    ) -> Optional[int]:
        """Claude-Code-like selection prompt. Returns chosen index or None (esc)."""
        color = T["red"] if danger else T["accent"]
        dlg, box = self._dialog_shell(title, color)
        ctk.CTkLabel(
            box,
            text=f"{'⚠' if danger else '✻'} {title}",
            font=self.fonts["bold"],
            text_color=color,
            anchor="w",
        ).pack(fill="x", padx=18, pady=(14, 4))
        if message:
            ctk.CTkLabel(
                box,
                text=message,
                font=self.fonts["normal"],
                text_color=T["text"],
                justify="left",
                anchor="w",
                wraplength=520,
            ).pack(fill="x", padx=18, pady=(2, 10))

        state = {"sel": default, "result": None}
        rows = []

        def render():
            for i, (lbl, text) in enumerate(zip(rows, options)):
                active = i == state["sel"]
                lbl.configure(
                    text=f"{'❯' if active else ' '} {i + 1}. {text}",
                    text_color=color if active else T["text"],
                )

        def pick(i):
            state["result"] = i
            dlg.destroy()

        def move(d):
            state["sel"] = (state["sel"] + d) % len(options)
            render()

        for i in range(len(options)):
            lbl = ctk.CTkLabel(
                box,
                text="",
                font=self.fonts["normal"],
                anchor="w",
                cursor="hand2",
                height=22,
            )
            lbl.pack(fill="x", padx=18)
            lbl.bind("<Button-1>", lambda e, i=i: pick(i))
            lbl.bind("<Enter>", lambda e, i=i: (state.update(sel=i), render()))
            rows.append(lbl)
        render()
        ctk.CTkLabel(
            box,
            text="↑/↓ select · enter confirm · esc cancel",
            font=self.fonts["small"],
            text_color=T["faint"],
            anchor="w",
        ).pack(fill="x", padx=18, pady=(10, 12))

        dlg.bind("<Up>", lambda e: move(-1))
        dlg.bind("<Down>", lambda e: move(1))
        dlg.bind("<Tab>", lambda e: move(1))
        dlg.bind("<Return>", lambda e: pick(state["sel"]))
        dlg.bind("<KP_Enter>", lambda e: pick(state["sel"]))
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        for i in range(min(len(options), 9)):
            dlg.bind(str(i + 1), lambda e, i=i: pick(i))
        self._show_dialog(dlg)
        return state["result"]

    def _ask(
        self,
        title: str,
        message: str,
        yes: str = "Yes",
        no: str = "No",
        danger: bool = False,
    ) -> bool:
        return (
            self._choose(
                title, message, [yes, no], danger=danger, default=1 if danger else 0
            )
            == 0
        )

    def _alert(self, title: str, message: str, error: bool = False):
        self._choose(title, message, ["OK"], danger=error)

    def _ask_password(self) -> Optional[str]:
        dlg, box = self._dialog_shell("Sudo required", T["accent"])
        ctk.CTkLabel(
            box,
            text="✻ Sudo required",
            font=self.fonts["bold"],
            text_color=T["accent"],
            anchor="w",
        ).pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            box,
            text="The serial port is not accessible for your user.\nEnter your sudo password:",
            font=self.fonts["normal"],
            text_color=T["text"],
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=18, pady=(2, 8))
        row = ctk.CTkFrame(
            box,
            fg_color=T["bg"],
            border_color=T["border"],
            border_width=1,
            corner_radius=6,
        )
        row.pack(fill="x", padx=18)
        ctk.CTkLabel(
            row, text=">", font=self.fonts["bold"], text_color=T["accent"]
        ).pack(side="left", padx=(10, 4))
        entry = ctk.CTkEntry(
            row,
            show="•",
            width=320,
            font=self.fonts["normal"],
            border_width=0,
            fg_color=T["bg"],
            text_color=T["text"],
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6), pady=4)
        ctk.CTkLabel(
            box,
            text="enter confirm · esc cancel",
            font=self.fonts["small"],
            text_color=T["faint"],
            anchor="w",
        ).pack(fill="x", padx=18, pady=(8, 12))
        result = {"pw": None}

        def ok(_=None):
            result["pw"] = entry.get()
            dlg.destroy()

        entry.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        self._show_dialog(dlg, focus_widget=entry)
        return result["pw"] or None

    # ====================== Serial port & LED ======================
    def _port_accessible(self, port: str) -> bool:
        return not self.is_linux or os.access(port, os.R_OK | os.W_OK)

    @contextmanager
    def _programmer(self, port: str = "") -> Iterator[Programmer]:
        """Exclusive direct access to the programmer (worker thread). port: the job's
        port by default (a firmware update finds the Pico again on its own)."""
        with self._port_lock:
            prog = Programmer(
                port or self._job.get("port") or self.port_entry.get().strip()
            )
            try:
                yield prog
            finally:
                try:
                    prog.pins(False)
                except Exception:
                    pass
                prog.close()

    def _can_use_pico(self, port: str) -> bool:
        """On-device CRC needs the new firmware and direct (non-sudo) port access."""
        return bool(
            self._fw_caps
            and S_CMD_CRC32 in self._fw_caps
            and self._port_accessible(port)
        )

    def _set_led(self, state: int, force: bool = False, wait: bool = False):
        """Best-effort LED update; never prompts and never raises. Nothing while the
        LEDs are turned off in the settings (force: switch them off once). Sent from
        a thread, since opening the port can take seconds; only the newest state
        waiting is sent. wait: send it now (when quitting)."""
        if not (self.leds_var.get() or force):
            return
        port = self.port_entry.get().strip()
        if not port or self._is_unresponsive(port):
            return
        if wait:
            self._send_led(port, state)
            return
        with self._led_mutex:
            self._led_want = (port, state)
            if self._led_running:
                return
            self._led_running = True
        threading.Thread(target=self._led_worker, daemon=True).start()

    def _led_worker(self):
        while True:
            with self._led_mutex:
                job, self._led_want = self._led_want, None
                if job is None:
                    self._led_running = False
                    return
            self._send_led(*job)

    def _send_led(self, port: str, state: int):
        if not self.is_windows and not os.path.exists(port):
            return
        if not self._port_lock.acquire(timeout=1.0):
            return
        try:
            if self._port_accessible(port):
                ser = serial.Serial(port, SERIAL_BAUD, timeout=0.5, write_timeout=0.5)
                try:
                    ser.write(bytes([S_CMD_SET_LED, state]))
                    ser.read(1)
                except serial.SerialTimeoutException as e:
                    # The Pico doesn't take data: tell the scan (and the user)
                    self.after(0, self._mark_unresponsive, port, str(e))
                finally:
                    close_serial(ser)
            elif self.sudo_password:
                # Plain sh, not this program: root can't read the AppImage's FUSE
                # mount (and a packaged app has no Python to run a script with), and
                # root's Python may lack pyserial. Raw mode: the bytes go out as they are.
                script = 'stty -F "$1" raw -echo clocal && printf "$2" > "$1"'
                subprocess.run(
                    [
                        "sudo",
                        "-S",
                        "-p",
                        "",
                        "sh",
                        "-c",
                        script,
                        "sh",
                        port,
                        f"\\{S_CMD_SET_LED:03o}\\{state:03o}",  # printf's octal escapes
                    ],
                    input=self.sudo_password + "\n",
                    text=True,
                    capture_output=True,
                    timeout=5,
                )
        except Exception:
            pass
        finally:
            self._port_lock.release()

    @staticmethod
    def _score_port(p) -> int:
        text = " ".join(
            filter(None, [p.description, p.manufacturer, p.product, p.hwid])
        ).lower()
        score = 0
        if "hydraflasher" in text:
            score += 10
        if p.vid == HYDRA_VID:
            score += 5
        elif p.vid == PICO_VID:
            score += 3
        score += sum(2 for kw in ("pico", "rp2040", "serprog", "tinyusb") if kw in text)
        if score == 0 and any(kw in text for kw in ("acm", "cdc", "usb serial")):
            score = 1
        return score

    def _auto_select_port(self, prefer: str = ""):
        ports = serial.tools.list_ports.comports()
        if not ports:
            self.log_note(
                "No serial ports detected – plug in the programmer.", "warning"
            )
            return

        best = max(ports, key=lambda p: (self._score_port(p), p.device == prefer))
        if self._score_port(best) == 0:
            if prefer and any(p.device == prefer for p in ports):
                self.port_entry.delete(0, "end")
                self.port_entry.insert(0, prefer)
                self.log_note(f"Using saved port {prefer}", "info")
                return
            self.log_note(
                "No HydraFlasher/Pico detected – plug it in, it is picked up automatically.",
                "warning",
            )
            return

        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, best.device)
        self.log_note(
            f"Programmer: {best.device} ({best.description or 'no description'})",
            "success" if self._score_port(best) >= 5 else "info",
        )

    def _refresh_ports(self):
        ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
        self.log_job("Scan serial ports")
        for p in ports:
            self.log_detail(f"{p.device}  {p.description or 'Unknown'}")
        if not ports:
            self.log_detail("(none)")
        self._auto_select_port(prefer=self.port_entry.get().strip())

    # ====================== Sudo ======================
    def _prepare_access(self, port: str) -> Optional[bool]:
        """Returns whether sudo is needed for this port, or None if the job must not start."""
        if not self.is_windows and not os.path.exists(port):
            self._alert(
                "Port not found",
                f"{port} does not exist.\nIs the programmer plugged in?",
                error=True,
            )
            return None
        if self._port_accessible(port):
            return False
        if self.sudo_password is None:
            pw = self._ask_password()
            if not pw:
                self.log_note("Cancelled (no sudo password)", "dim")
                return None
            check = subprocess.run(
                ["sudo", "-S", "-k", "-p", "", "-v"],
                input=pw + "\n",
                text=True,
                capture_output=True,
            )
            if check.returncode != 0:
                self._alert("Sudo", "Wrong sudo password.", error=True)
                return None
            self.sudo_password = pw
            self.log_note("Sudo password remembered for this session.", "info")
            self.log_detail(
                "tip: add yourself to the 'dialout' group (then log in again) to avoid sudo",
                "dim",
            )
        return True

    # ====================== Job runner ======================
    def _start_job(
        self,
        status: str,
        worker: Callable[[], Tuple[bool, str]],
        retry: Optional[Callable[[], None]] = None,
        title: Optional[str] = None,
        production: bool = False,
        cs: Optional[int] = None,
        front: bool = False,
        key: Optional[tuple] = None,
        needs_port: bool = True,
    ) -> bool:
        """Run worker() in a background thread. worker returns (success, message).
        While another job runs, the job is queued instead (except in production mode).
        key identifies the job (default: its title) to refuse starting it twice."""
        queue = self.busy or bool(self._queue)
        if queue and (production or self._prod):
            self.log_note("Another operation is still running.", "warning")
            return False
        title = title or status.rstrip("…")
        reason = self._socket_unavailable(cs) if cs is not None and cs < self.num_slots else ""
        if reason == "not wired":
            self.log_note(
                f"{self._sock(cs)} isn't wired (no pull-up detected) – if it is, set "
                "the socket count by hand in settings.",
                "error",
            )
            return False
        if reason:
            self.log_note(
                f"{self._sock(cs)} has no CS line in the programmer's firmware "
                f"({self._fw_cs} lines) – update the firmware.",
                "error",
            )
            return False
        key = key or (title,)
        if key in {e["key"] for e in self._queue} or (
            self.busy and key == self._job_key
        ):
            self.log_note(f"Already running or queued: {title}", "warning")
            return False
        port = self.port_entry.get().strip()
        if needs_port and self._unresponsive and self._unresponsive[0] == port:
            self.log_note(
                f"The Pico on {port} doesn't answer – {title} not started. Hold BOOTSEL "
                "while plugging it in, then install firmware (settings → firmware).",
                "error",
            )
            return False
        if needs_port:
            if not port:
                self._alert(
                    "No port", "Select the programmer's serial port first.", error=True
                )
                return False
            use_sudo = self._prepare_access(port)
            if use_sudo is None:
                return False
        else:  # firmware update: the Pico may already sit in its bootloader
            use_sudo = False
        chip = self._pick_chip_definition(cs)
        if chip is None:
            return False

        # Snapshot settings so the worker thread never touches Tk variables.
        # A queued job keeps the settings from when it was started.
        job = {
            "port": port,
            "speed": self.spispeed_var.get(),
            "force": self.force_var.get(),
            "progress": self.progress_var.get(),
            "noverify": self.noverify_var.get(),
            "chip": chip,
            # per-chip-type picks, for sockets the job's definition doesn't fit
            "chip_choice": dict(self._chip_choice),
            "sudo": use_sudo,
            "retry": retry,
            "pico": self.fast_verify_var.get() and self._can_use_pico(port),
            "partial": self.partial_var.get() and self._can_use_pico(port),
            "production": production,
            "rows": [],
            "temp_files": [],
            "batch": self._prod["batch"] if (production and self._prod) else "",
            # Unknown firmware (no direct access, e.g. sudo): assume the current one
            "broadcast": self._fw_cs or MAX_SLOTS,
            # Sockets the firmware has a CS line for (on old firmware CS6 is the
            # broadcast, so a probe of "socket 7" would read every chip at once)
            "usable": self._usable_sockets(),
        }
        entry = {
            "status": status,
            "worker": worker,
            "title": title,
            "job": job,
            "cs": cs,
            "key": key,
        }
        if not queue:
            self._launch(entry)
            return True
        if self._retrying or front:  # e.g. a chip-definition retry goes ahead of the queue
            self._queue.insert(0, entry)
        else:
            self._queue.append(entry)
        self.log_note(
            f"Queued #{self._queue.index(entry) + 1}: {entry['title']}", "soft"
        )
        self._refresh_sockets()
        self._refresh_busy_ui()
        return True

    def _launch(self, entry: dict):
        worker = entry["worker"]
        self._job = entry["job"]
        self._job_key = entry["key"]
        # Queued for a chip inserted mid-job (definitions unknown back then): reuse the
        # chip picked since, if it fits the chip the between-jobs scan found
        cs, override = entry["cs"], self.chip_override.get()
        slot = self.slots[cs] if cs is not None and cs < self.num_slots else None
        if (
            self._job["chip"] == "Auto-detect"
            and override != "Auto-detect"
            and slot
            and override in slot["chips"]
        ):
            self._job["chip"] = override
        self._job_output = []
        self._abort_requested = False
        self._entry = entry
        self._eta = self._estimate(entry)
        self.busy = True
        if self._prod:
            for btn in self._op_buttons:
                btn.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.save_config()
        self._set_led(LED["RUNNING"])
        self._status, self._sub, self._pct = entry["status"], "", None
        self._job_started = time.monotonic()
        self.log_job(entry["title"])
        self._refresh_sockets()
        self._refresh_busy_ui()
        self._refresh_settings()
        self._tick()

        def run():
            try:
                ok, msg = worker()
            except JobAborted:
                ok, msg = False, "Operation stopped by user."
            except ProgrammerError as e:
                ok, msg = False, f"Programmer error: {e}"
            except Exception as e:
                ok, msg = False, f"Unexpected error: {e}"
            for f in self._job["temp_files"]:
                try:
                    os.unlink(f)
                except OSError:
                    pass
            self.after(0, self._finish_job, ok, msg)

        threading.Thread(target=run, daemon=True).start()

    def _queue_continue(self):
        """After a job: rescan the sockets first, so the next queued job sees chips that
        were swapped while the bus was busy, then start it."""
        if self.busy or not self._queue:
            return
        self._queue_gen += 1
        gen = self._queue_gen
        port = self.port_entry.get().strip()
        if not (self._port_connected and port and self._port_accessible(port)):
            self.after(0, self._queue_next, gen)  # no direct access (e.g. sudo)
            return
        self._queue_wait_scan = gen
        if not self._scan_running:
            self._scan_running = True
            threading.Thread(
                target=self._scan_worker, args=(port, self._scan_hz()), daemon=True
            ).start()
        self.after(3000, self._queue_next, gen)  # in case the scan fails

    def _queue_next(self, gen: int):
        if gen != self._queue_gen or self.busy or not self._queue:
            return
        self._queue_gen += 1
        self._queue_wait_scan = None
        self._launch(self._queue.pop(0))

    def _clear_queue(self):
        if self._queue:
            self.log_note(f"Cleared {len(self._queue)} queued job(s).", "warning")
        self._queue.clear()
        self._queue_gen += 1
        self._queue_wait_scan = None
        if not self.busy:
            self.stop_button.configure(state="disabled")
        self._refresh_sockets()
        self._refresh_busy_ui()

    def _finish_job(self, ok: bool, msg: str):
        self._report_job(ok, msg)
        self._queue_continue()
        if self._sockets_pending and not (self.busy or self._queue):
            self._apply_auto_sockets()

    def _report_job(self, ok: bool, msg: str):
        self.busy = False
        self._proc = None
        self._busy_sockets.clear()
        self._refresh_sockets()
        for btn in self._op_buttons:
            btn.configure(state="normal")
        if not self._queue:
            self.stop_button.configure(state="disabled")
        self._refresh_busy_ui()
        secs = time.monotonic() - self._job_started
        took = f"(took {fmt_duration(secs)})"
        if ok and not self._abort_requested:
            self._learn_time(self._entry, secs)

        text = "".join(self._job_output)
        retry = self._job.get("retry")
        if not ok and not self._abort_requested and MULTI_MATCH in text and retry:
            self._set_idle_status("Choose a chip definition", T["yellow"])
            self._prompt_chip_selection(text, retry)
            return

        if self._job.get("production") and self._prod:
            rows = [
                r for r in self._job["rows"] if r["operation"] in ("write", "verify")
            ]
            per_socket: Dict[str, bool] = {}
            for r in rows:
                per_socket[r["socket"]] = (
                    per_socket.get(r["socket"], True) and r["result"] == "pass"
                )
            if not per_socket and not ok:
                per_socket = {
                    f"CS{d['cs']}": False for d in self._detected_slots()
                } or {"?": False}
            self._prod["ok"] += sum(per_socket.values())
            self._prod["fail"] += sum(not v for v in per_socket.values())
            self._update_footer()
            msg = f"Batch #{self._prod['batch']}: {msg} · swap chips"

        first = msg.splitlines()[0]
        if ok:
            self.log_line(f"{BULLET} ✓ {msg}", "success")
            self._set_idle_status(trunc(first, 90), T["green"], took)
            if self._job_op(self._entry) not in NOTIFY_FAILURE_ONLY:
                self.notify("Success", msg)
            self._set_led(LED["SUCCESS"])
        else:
            self.log_line(f"{BULLET} ✕ {msg}", "error")
            self._set_idle_status(trunc(first, 90), T["red"], took)
            self.notify("Failed", msg)
            self._set_led(LED["FAILED"])
        self.save_config()
        self._refresh_settings()

    @staticmethod
    def _job_op(entry: dict) -> str:
        """The operation of a queue entry, from its title: "write", "probe", "identify"…"""
        return entry.get("title", "").split(" ", 1)[0].lower()

    # ====================== Time estimates ======================
    def _eta_keys(self, entry: dict) -> List[str]:
        """Signatures a job's duration is remembered under, most specific first:
        operation, sockets, chip, image or chip size and the settings that change the
        time. The first one also names the image file (its size and mtime), so a
        repeated job gets its own time and a rebuilt image still gets an estimate from
        the second. Empty when the job isn't estimated (probe) or its chips are unknown."""
        op = self._job_op(entry)
        cs, key, job = entry.get("cs"), entry.get("key", ()), entry.get("job", {})
        if op not in ETA_OPS or cs is None:
            return []
        if cs < self.num_slots:
            slots, mode = [self.slots[cs]], "1"
        else:
            slots = self._detected_slots()
            each = len(key) > 1 and isinstance(key[1], tuple)  # _start_write_each
            if each:
                slots = [self.slots[c] for c in key[1]]
            mode = f"{'each' if each else 'all'}{len(slots)}"
        if not slots or not all(s and s.get("detected") for s in slots):
            return []
        chip = "+".join(sorted({s["name"] for s in slots}))
        size, image = slots[0].get("size_kb", 0) * 1024, ""
        path = key[2] if len(key) > 2 and isinstance(key[2], str) else ""
        if op in ("write", "verify") and os.path.isfile(path):
            st = os.stat(path)
            size, image = st.st_size, f"{os.path.basename(path)}:{int(st.st_mtime)}"
        flags = "".join(
            f for f, on in (
                ("p", job.get("pico")),
                ("b", job.get("partial")),
                ("n", job.get("noverify")),
            ) if on
        )
        base = f"{op}|{mode}|{chip}|{size}|{job.get('speed', '')}|{flags}"
        return [f"{base}|{image}", base] if image else [base]

    def _estimate(self, entry: dict) -> Optional[float]:
        """Typical duration of this job from earlier runs, None if it never ran."""
        for k in self._eta_keys(entry):
            if k in self.job_times:
                return self.job_times[k]
        return None

    def _learn_time(self, entry: dict, secs: float):
        """Remember a successful job's duration (averaged with the earlier ones)."""
        for k in self._eta_keys(entry):
            old = self.job_times.pop(k, None)  # re-inserted last: newest at the end
            self.job_times[k] = round(secs if old is None else (old + secs) / 2, 1)
        while len(self.job_times) > ETA_MAX_ENTRIES:
            del self.job_times[next(iter(self.job_times))]

    def _socket_chip(self, cs: int, identify: bool = False) -> str:
        """The -c definition for one socket (worker thread). A job's definition is
        chosen for its target; a job over several sockets (probe/read/verify all, a
        one-by-one batch) also meets chip types it doesn't fit, and flashprog with a
        wrong -c finds no chip at all. So per socket: the job's definition if it fits
        this chip, else the one picked for this chip type, else auto-detect. A probe
        (identify) of a chip whose definitions aren't known yet always auto-detects."""
        chip = self._job["chip"]
        if chip == "Auto-detect" or cs >= self.num_slots:
            return chip  # broadcast: one chip type by definition
        slot = self.slots[cs]
        chips = slot["chips"] if slot and slot.get("detected") else []
        if not chips:
            return "Auto-detect" if identify else chip
        if chip in chips:
            return chip
        remembered = self._job.get("chip_choice", {}).get(slot.get("jedec") or "")
        return remembered if remembered in chips else "Auto-detect"

    def _build_command(
        self, extra: List[str], cs: int, noverify: bool = False
    ) -> List[str]:
        job = self._job
        cmd = [
            self.flashrom_path,
            "-p",
            f"serprog:dev={job['port']},spispeed={job['speed']},"
            f"cs={job['broadcast'] if cs == CS_ALL else cs}",
        ]
        cmd += extra
        if job["force"]:
            cmd.append("-f")
        if job["progress"]:
            cmd.append("--progress")
        if noverify:
            cmd.append("-n")
        # Remembered for _store_slot: only auto-detect runs teach the JEDEC cache
        self._chip_used = chip = self._socket_chip(cs, identify=not extra)
        if chip != "Auto-detect":
            cmd += ["-c", chip]
        if job["sudo"]:
            cmd = ["sudo", "-S", "-p", "", "--"] + cmd
        return cmd

    def _exec(
        self,
        extra: List[str],
        cs: int,
        noverify: bool = False,
        quiet: bool = False,
        silent: bool = False,
        show_cmd: bool = True,
    ) -> Tuple[int, str]:
        """Run flashrom/flashprog (worker thread only). Returns (returncode, output).
        quiet: only show errors/warnings/progress, silent: don't log anything."""
        if self._abort_requested:
            raise JobAborted()
        cmd = self._build_command(extra, cs, noverify)
        if not silent and show_cmd:
            shown = cmd[cmd.index("--") + 1 :] if self._job["sudo"] else list(cmd)
            # Show files by name only so commands fit on one line (the real command has full paths)
            shown = [os.path.basename(a) if os.path.isabs(a) else a for a in shown]
            self.after(
                0,
                self.log_detail,
                f"$ {'sudo ' if self._job['sudo'] else ''}{' '.join(shown)}",
                "soft",
            )
        self.after(0, self._reset_progress, silent)
        self.after(0, self._mark_busy, cs, True)

        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
            "stdin": subprocess.PIPE if self._job["sudo"] else subprocess.DEVNULL,
        }
        if self.is_windows:
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW

        output = []
        try:
            with self._port_lock:
                proc = subprocess.Popen(cmd, **popen_kwargs)
                self._proc = proc
                if self._job["sudo"]:
                    proc.stdin.write((self.sudo_password or "") + "\n")
                    proc.stdin.close()
                for line in iter(proc.stdout.readline, ""):
                    output.append(line)
                    if silent or any(p in line for p in NOISE_PATTERNS):
                        continue
                    self.after(0, self._handle_output_line, line, quiet)
                rc = proc.wait()
        finally:
            self._proc = None
            self.after(0, self._mark_busy, cs, False)

        text = "".join(output)
        self._job_output.append(text)
        if self._abort_requested:
            raise JobAborted()
        if self._job["sudo"] and (
            "incorrect password" in text.lower() or "sorry, try again" in text.lower()
        ):
            self.sudo_password = None
        # Keep per-socket info fresh: every run on a single socket re-probes the chip
        if cs < self.num_slots:
            info = self._parse_probe(text, cs)
            if info["detected"]:
                self._store_slot(cs, info)
                self.after(0, self._update_chip_list, info["chips"])
        self._record_result(extra, cs, rc, noverify)
        self.after(0, self._refresh_sockets)
        return rc, text

    def _record_result(self, extra: List[str], cs: int, rc: int, noverify: bool):
        """Remember per-socket pass/fail for the status lights."""
        op = extra[0] if extra else "probe"
        targets = [cs] if cs < self.num_slots else [d["cs"] for d in self._detected_slots()]
        for t in targets:
            if op == "probe":
                self._socket_result[t] = None
            elif rc != 0:
                self._socket_result[t] = "fail"
            elif op == "-v" or (op == "-w" and not noverify):
                self._socket_result[t] = "pass"
            elif op in ("-w", "-E"):
                self._socket_result[t] = None

    def _reset_progress(self, silent: bool = False):
        self._pct = 0.0 if (self._job.get("progress") and not silent) else None
        if not silent:
            self._sub = ""

    def _step(self, n: int, total: int, text: str):
        self.after(0, self.update_status, f"[{n}/{total}] {text}", "info")
        self.after(0, self.log_step, f"{n}/{total} {text}")

    def _detail(self, text: str, tag: str = "dim"):
        """log_detail() from the worker thread."""
        self.after(0, self.log_detail, text, tag)

    def stop(self):
        if self._queue:
            n = len(self._queue)
            # (label, clear the queue, stop the running job)
            options = [(f"Clear the queue ({n} waiting)", True, False)]
            if self.busy:
                options = [
                    ("Stop the current job only", False, True),
                    (f"Stop and clear the queue ({n} waiting)", True, True),
                ]
            options.append(("Keep running", False, False))
            idx = self._choose(
                "Stop operation",
                f"{n} queued job(s) waiting.\n\n"
                "Interrupting an erase or write leaves the chip(s) partially programmed.",
                [o[0] for o in options],
                danger=True,
                default=len(options) - 1,
            )
            if idx is None:
                return
            _, clear, stop = options[idx]
            if clear:
                self._clear_queue()
            if not (stop and self.busy):
                return
        elif not self.busy:
            return
        elif not self._ask(
            "Stop operation",
            "Stop the running operation?\n\n"
            "Interrupting an erase or write leaves the chip(s) partially programmed.",
            yes="Stop it",
            no="Keep running",
            danger=True,
        ):
            return
        self._abort_requested = True
        self.log_note("Stopping...", "warning")
        proc = self._proc
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass

    def _handle_output_line(self, line: str, quiet: bool = False):
        line = line.rstrip()
        if not line:
            return
        percent = self._parse_progress(line)
        if percent is not None:
            if self._job.get("progress"):
                self._pct = percent
            self._log_progress(self._detail_prefix() + line.strip())
            return
        lower = line.lower()
        if "error" in lower or "failed" in lower:
            tag = "error"
        elif "warning" in lower:
            tag = "warning"
        elif quiet:
            return
        elif "found" in lower or "verified" in lower:
            tag = "success"
        else:
            tag = "dim"
        self.log_detail(line.strip(), tag)

    @staticmethod
    def _parse_progress(line: str) -> Optional[float]:
        if "%" not in line:
            return None
        m = re.search(
            r"(Reading|Erasing|Writing|Verifying).*?(\d+(?:\.\d+)?)\s*%", line, re.I
        )
        return float(m.group(2)) if m else None

    def _pick_chip_definition(self, cs: Optional[int]) -> Optional[str]:
        """The chip definition (-c, or "Auto-detect") for a job on cs, None if cancelled.
        Decided when the job is started, not when it runs (a queued job can run much
        later), and per chip: one-by-one may queue different chip types. Order: the
        override if it fits this chip, the choice made for this chip type before,
        auto-detect if only one definition matches, else ask."""
        override = self.chip_override.get()
        if cs is None:
            return override
        slots = self._detected_slots() if cs == CS_ALL else [self.slots[cs]]
        slot = next((s for s in slots if s and s["detected"]), None)
        chips = slot["chips"] if slot else []
        if not chips or override in chips:
            return override  # chip not known yet (the scan resets a misfit override)
        remembered = self._chip_choice.get(slot.get("jedec") or "")
        if remembered in chips:
            return remembered
        if len(chips) == 1:
            return "Auto-detect"
        self._update_chip_list(chips)
        where = "the chips" if cs == CS_ALL else f"the chip on {self._sock(cs)}"
        idx = self._choose(
            "Choose chip definition",
            f"Multiple flash chip definitions match {where}.\n"
            "Pick the one printed on the chip:",
            chips,
        )
        if idx is None:
            self.log_note("Chip selection cancelled.", "error")
            return None
        if slot.get("jedec"):
            self._chip_choice[slot["jedec"]] = chips[idx]
        self.chip_override.set(chips[idx])
        self.log_note(f"Selected chip: {chips[idx]}", "info")
        return chips[idx]

    def _prompt_chip_selection(self, text: str, retry: Callable[[], None]):
        chips = []
        m = re.search(MULTI_MATCH + r".*?:\s*(.+)", text)
        if m:
            chips = re.findall(r'"([^"]+)"', m.group(1))
        if not chips:
            chips = [name for _, name, _ in FOUND_RE.findall(text)]
        chips = list(dict.fromkeys(chips))
        if not chips:
            self.log_note("Could not parse chip names.", "error")
            self._set_led(LED["FAILED"])
            return
        self._update_chip_list(chips)

        idx = self._choose(
            "Choose chip definition",
            "Multiple flash chip definitions match the detected chip(s).\n"
            "Pick the one printed on the chip:",
            chips,
        )
        if idx is None:
            self.log_note("Chip selection cancelled.", "error")
            self._set_idle_status("Operation cancelled", T["red"])
            self._set_led(LED["FAILED"])
            return
        self.chip_override.set(chips[idx])
        self.log_note(f"Selected chip: {chips[idx]} – retrying", "info")
        self._retrying = True
        try:
            retry()
        finally:
            self._retrying = False

    # ====================== Flash log (traceability) ======================
    def _record(
        self,
        cs: int,
        operation: str,
        ok: bool,
        seconds: float,
        image: str = "",
        image_crc: Optional[int] = None,
    ):
        """Append one row to the CSV flash log (worker thread)."""
        slot = self.slots[cs] if cs < self.num_slots else None
        ident = self._socket_ident[cs] if cs < self.num_slots else _UNSCANNED
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "production" if self._job.get("production") else "manual",
            "batch": self._job.get("batch", ""),
            "socket": f"CS{cs}",
            "jedec": (slot or {}).get("jedec")
            or (ident[0] if ident is not _UNSCANNED else "")
            or "",
            "unique_id": (slot or {}).get("uid")
            or (ident[1] if ident is not _UNSCANNED else "")
            or "",
            "chip": slot["name"] if slot else "",
            "operation": operation,
            "image": os.path.basename(image),
            "image_crc32": f"{image_crc:08x}" if image_crc is not None else "",
            "result": "pass" if ok else "fail",
            "seconds": f"{seconds:.1f}",
        }
        self._job["rows"].append(row)
        with self._log_lock:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                new = not os.path.exists(FLASH_LOG)
                with open(FLASH_LOG, "a", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=FLASH_LOG_FIELDS)
                    if new:
                        w.writeheader()
                    w.writerow(row)
            except OSError as e:
                self._detail(f"⚠ could not write flash log: {e}", "warning")

    def _open_flash_log(self):
        if not os.path.exists(FLASH_LOG):
            self._alert(
                "Flash log", f"No entries yet.\nIt will be written to:\n{FLASH_LOG}"
            )
            return
        try:
            if self.is_windows:
                os.startfile(FLASH_LOG)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open" if self.is_linux else "open", FLASH_LOG],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            self._alert("Flash log", FLASH_LOG)

    # ====================== Pico-side checks (CRC) ======================
    def _crc_verify(
        self, data: bytes, detected: List[dict], operation: str, image: str
    ) -> List[Tuple[int, str, bool]]:
        """Verify each chip against the image with the on-device CRC (worker thread)."""
        expected = zlib.crc32(data) & 0xFFFFFFFF
        speed = parse_speed(self._job["speed"])
        rows = []
        with self._programmer() as prog:
            prog.set_speed(speed)
            for d in detected:
                if self._abort_requested:
                    raise JobAborted()
                cs = d["cs"]
                self.after(0, self.update_progress, f"{self._sock(cs)}")
                self.after(0, self._mark_busy, cs, True)
                t0 = time.monotonic()
                try:
                    crc = prog.crc32(cs, 0, len(data), speed, len(data))
                finally:
                    self.after(0, self._mark_busy, cs, False)
                secs = time.monotonic() - t0
                ok = crc == expected
                self._socket_result[cs] = "pass" if ok else "fail"
                if ok:
                    self._detail(
                        result_row(self._sock_col(cs), "✓ match", secs, f"crc {crc:08x}"), "success"
                    )
                else:
                    self._detail(
                        result_row(self._sock_col(cs), "✕ MISMATCH", secs, f"crc {crc:08x} ≠ {expected:08x}"),
                        "error",
                    )
                self._record(cs, operation, ok, secs, image, expected)
                rows.append((cs, d["name"], ok))
                self.after(0, self._refresh_sockets)
        return rows

    def _flashprog_verify(
        self, path: str, data: bytes, detected: List[dict], operation: str
    ) -> List[Tuple[int, str, bool]]:
        """Fallback verify through flashprog, one chip at a time (worker thread)."""
        rows = []
        crc = zlib.crc32(data) & 0xFFFFFFFF
        for d in detected:
            cs = d["cs"]
            self.after(0, self.update_progress, f"{self._sock(cs)}")
            t0 = time.monotonic()
            rc, _ = self._exec(["-v", path], cs, quiet=True, show_cmd=False)
            secs = time.monotonic() - t0
            self._detail(
                result_row(self._sock_col(cs), "✓ match" if rc == 0 else "✕ MISMATCH", secs),
                "success" if rc == 0 else "error",
            )
            self._record(cs, operation, rc == 0, secs, path, crc)
            rows.append((cs, d["name"], rc == 0))
        return rows

    def _blank_check(self, detected: List[dict], unused: List[Range]) -> List[Range]:
        """CRC the image's unused (0xFF) area on each chip; return the pieces that aren't blank."""
        pieces = split_ranges(unused, BLANK_CHECK_PIECE)
        ff_crc: Dict[int, int] = {}
        speed = parse_speed(self._job["speed"])
        dirty: Set[Range] = set()
        with self._programmer() as prog:
            prog.set_speed(speed)
            for d in detected:
                cs = d["cs"]
                chip_size = (d.get("size_kb") or 0) * 1024
                self.after(0, self.update_progress, f"{self._sock(cs)}")
                self.after(0, self._mark_busy, cs, True)
                t0 = time.monotonic()
                mine = []
                try:
                    for start, end in pieces:
                        if self._abort_requested:
                            raise JobAborted()
                        length = end - start
                        if length not in ff_crc:
                            ff_crc[length] = zlib.crc32(b"\xff" * length) & 0xFFFFFFFF
                        if (
                            prog.crc32(cs, start, length, speed, chip_size or end)
                            == ff_crc[length]
                        ):
                            continue
                        # Not blank: narrow it down to erase blocks so only those get erased
                        for bs, be in split_ranges([(start, end)], ERASE_BLOCK):
                            if be - bs not in ff_crc:
                                ff_crc[be - bs] = (
                                    zlib.crc32(b"\xff" * (be - bs)) & 0xFFFFFFFF
                                )
                            if (
                                prog.crc32(cs, bs, be - bs, speed, chip_size or end)
                                != ff_crc[be - bs]
                            ):
                                mine.append((bs, be))
                finally:
                    self.after(0, self._mark_busy, cs, False)
                secs = time.monotonic() - t0
                if mine:
                    self._detail(
                        result_row(
                            self._sock_col(cs), "⚠ not blank", secs,
                            f"{human_size(ranges_size(mine))} will be erased",
                        ),
                        "warning",
                    )
                else:
                    self._detail(result_row(self._sock_col(cs), "✓ blank", secs), "success")
                dirty.update(mine)
        return merge_ranges(list(dirty))

    def _layout_args(self, ranges: List[Range]) -> List[str]:
        """Write a flashprog layout file for these ranges; returns the -l/-i arguments."""
        fd, path = tempfile.mkstemp(suffix=".layout", prefix="hydra_")
        with os.fdopen(fd, "w") as f:
            for i, (start, end) in enumerate(ranges):
                f.write(f"{start:08x}:{end - 1:08x} r{i}\n")
        self._job["temp_files"].append(path)
        args = ["-l", path]
        for i in range(len(ranges)):
            args += ["-i", f"r{i}"]
        return args

    def _plan(self, data: bytes) -> Optional[Tuple[List[Range], List[Range]]]:
        """(used, unused) ranges if a partial write is worthwhile, else None."""
        if not self._job.get("partial"):
            return None
        used, unused = block_ranges(data)
        if ranges_size(used) > PARTIAL_MAX_USED * len(data):
            return None
        return used, unused

    # ====================== Probe parsing / slot state ======================
    @staticmethod
    def _parse_probe(text: str, cs: int) -> dict:
        found = FOUND_RE.findall(text)
        # flashprog's catch-all definitions ("unknown SPI chip (RDID)", "unknown Winbond
        # (ex Nexcom) SPI chip", ...) mean it has no real definition: never learn those
        real = [f for f in found if not is_generic_chip(f[1])]
        chips = list(dict.fromkeys(name for _, name, _ in real))
        unsupported = bool(found) and not real
        return {
            "cs": cs,
            "detected": bool(found),
            "chips": chips,
            "name": " / ".join(chips)
            if chips
            else ("unsupported chip" if unsupported else "NOT DETECTED"),
            "size_kb": int(real[0][2]) if real else None,
            "unsupported": unsupported,
        }

    @staticmethod
    def _slots_key(slots: List[Optional[dict]]) -> tuple:
        """Which sockets are populated and with what size, used to detect swapped chips.
        (Chip names aren't part of it: they change when a chip override is picked.)"""
        return tuple((d["cs"], d["size_kb"]) for d in slots if d and d["detected"])

    def _detected_slots(self) -> List[dict]:
        """Sockets holding a chip (not the unwired ones or those without a CS line)."""
        return [
            d for d in self.slots
            if d and d["detected"] and not self._socket_unavailable(d["cs"])
        ]

    def _unsupported_msg(self, cs: int) -> str:
        return (
            f"{self._sock(cs)} holds an unsupported chip: flashprog has no definition for it, "
            "so it can't be read, erased or written."
        )

    def _same_type_error(self, detected: List[dict]) -> Optional[str]:
        if not detected:
            return "No chips detected. Insert chips or probe with ALL selected first."
        bad = next((d for d in detected if d.get("unsupported")), None)
        if bad:
            return self._unsupported_msg(bad["cs"])
        if (
            len(
                {
                    (d.get("jedec") or frozenset(d["chips"]), d["size_kb"])
                    for d in detected
                }
            )
            > 1
        ):
            chip_list = "\n".join(
                f"  • {self._sock(d['cs'])} → {d['name']} "
                f"({human_size(d['size_kb'] * 1024) if d['size_kb'] else 'unknown size'})"
                for d in detected
            )
            return (
                "The chips in the sockets are not the same type!\n\n"
                f"{chip_list}\n\n"
                "Broadcast only works with identical chips.\n"
                "Select a single socket instead."
            )
        return None

    def _update_chip_list(self, new_chips: List[str]):
        updated = ["Auto-detect"] + sorted(
            set(self.available_chips[1:]) | set(new_chips)
        )
        if updated != self.available_chips:
            self.available_chips = updated
            self.chip_combo.configure(values=updated)

    def _clear_chip_override(self):
        self.chip_override.set("Auto-detect")
        self._refresh_sockets()
        self.log_note("Chip override cleared", "dim")

    def _probe_all_sockets(self) -> List[dict]:
        """Worker thread: probe every socket one by one, return the detected ones."""
        first_output = ""
        for cs in self._job["usable"]:
            self.after(0, self.update_progress, f"{self._sock(cs)}")
            _, text = self._exec([], cs, silent=True)
            first_output = first_output or text
            info = self._parse_probe(text, cs)
            ident = self._socket_ident[cs]
            if not info["detected"] and ident is not _UNSCANNED and ident[0]:
                # The scan sees a chip here: don't call the socket empty
                self._detail(
                    f"⚠ {self._sock(cs)}: flashprog found no chip, but the scan reads JEDEC "
                    f"{ident[0]} – reseat it or probe {self._sock(cs)} on its own",
                    "warning",
                )
                continue
            self._store_slot(cs, info)
        detected = self._detected_slots()
        self.after(0, self._log_slot_table)
        if not detected:
            # Show why it failed (port busy, permission, no chips, ...)
            for line in first_output.splitlines():
                if line.strip() and not any(p in line for p in NOISE_PATTERNS):
                    self._detail(line.strip())
        self.after(0, self._update_chip_list, [c for d in detected for c in d["chips"]])
        self.after(0, self._refresh_sockets)
        return detected

    def _slot_rows(self, slots: Sequence[Optional[dict]]) -> List[Tuple[str, str]]:
        """Aligned "CS0  ● name  size" lines (with log tags) for a socket table."""
        shown = [d for d in slots if d]
        width = min(max((len(d["name"]) for d in shown if d["detected"]), default=0), 32)
        rows = []
        for d in shown:
            if not d["detected"]:
                rows.append((f"{self._sock_col(d['cs'])}  ○ empty", "dim"))
                continue
            size = human_size(d["size_kb"] * 1024) if d["size_kb"] else "?"
            note = f"  · {len(d['chips'])} definitions match" if len(d["chips"]) > 1 else ""
            tag = "warning" if d.get("unsupported") else "success"
            rows.append(
                (f"{self._sock_col(d['cs'])}  ● {trunc(d['name'], width):<{width}}  {size:>9}{note}", tag)
            )
        return rows

    def _log_slot_table(self):
        for text, tag in self._slot_rows(self.slots):
            self.log_detail(text, tag)

    def _reprobe_and_check(
        self, expected_key: tuple
    ) -> Tuple[Optional[List[dict]], str]:
        """Worker thread: re-probe sockets and make sure nothing changed since the user confirmed."""
        detected = self._probe_all_sockets()
        err = self._same_type_error(detected)
        if err:
            return None, err.splitlines()[0]
        if self._slots_key(detected) != expected_key:
            return (
                None,
                "Sockets changed since the last probe – check the log and confirm again.",
            )
        return detected, ""

    # ====================== File Dialogs ======================
    def _run_linux_dialog(
        self, commands: List[List[str]]
    ) -> Tuple[bool, Optional[str]]:
        """Try zenity/kdialog. Returns (a dialog was shown, selected path)."""
        for cmd in commands:
            if not shutil.which(cmd[0]):
                continue
            started = time.monotonic()
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
            except Exception:
                continue
            if (result.returncode != 0 and result.stderr.strip()
                    and time.monotonic() - started < 1.0):
                # Failed to start (missing library, no display): a cancelled dialog
                # takes longer, so try the next one instead of doing nothing
                continue
            path = result.stdout.strip() if result.returncode == 0 else ""
            return True, path or None
        return False, None

    def _ask_open_file(self, title: str, filetypes):
        initial = self.last_dir
        path = None
        shown = False
        if self.is_linux:
            patterns = " ".join(
                p for _, exts in filetypes for p in exts.split() if p != "*.*"
            )
            zenity = [
                "zenity",
                "--file-selection",
                f"--title={title}",
                f"--filename={initial}/",
            ]
            if patterns and patterns != "*":
                zenity += [f"--file-filter={patterns}", "--file-filter=*"]
            shown, path = self._run_linux_dialog(
                [zenity, ["kdialog", "--getopenfilename", initial, f"--title={title}"]]
            )
        if not shown:
            path = filedialog.askopenfilename(
                title=title, filetypes=filetypes, initialdir=initial
            )
        if path:
            self.last_dir = os.path.dirname(path)
        return path or None

    def _ask_save_file(
        self, title: str, defaultextension: str, filetypes, initialfile=None
    ):
        initial = self.last_dir
        path = None
        shown = False
        if self.is_linux:
            target = os.path.join(initial, initialfile or "")
            shown, path = self._run_linux_dialog(
                [
                    [
                        "zenity",
                        "--file-selection",
                        "--save",
                        f"--title={title}",
                        f"--filename={target}",
                    ],
                    ["kdialog", "--getsavefilename", target, f"--title={title}"],
                ]
            )
        if not shown:
            path = filedialog.asksaveasfilename(
                title=title,
                defaultextension=defaultextension,
                filetypes=filetypes,
                initialdir=initial,
                initialfile=initialfile,
            )
        if path:
            self.last_dir = os.path.dirname(path)
        return path or None

    def _ask_directory(self, title: str):
        initial = self.last_dir
        path = None
        shown = False
        if self.is_linux:
            shown, path = self._run_linux_dialog(
                [
                    [
                        "zenity",
                        "--file-selection",
                        "--directory",
                        f"--title={title}",
                        f"--filename={initial}/",
                    ],
                    ["kdialog", "--getexistingdirectory", initial, f"--title={title}"],
                ]
            )
        if not shown:
            path = filedialog.askdirectory(title=title, initialdir=initial)
        if path:
            self.last_dir = path
        return path or None

    def _adjust_file_size(self, path: str, chip_size: int) -> Optional[str]:
        file_size = os.path.getsize(path)
        if file_size == chip_size:
            return path

        base, ext = os.path.splitext(path)
        sizes = f"File is {human_size(file_size)}, chip is {human_size(chip_size)}."
        try:
            if file_size > chip_size:
                if self._ask(
                    "Size mismatch",
                    f"{sizes}\nTruncate the image to the chip size?",
                    yes=f"Truncate to {human_size(chip_size)}",
                    no="Cancel",
                ):
                    adjusted = f"{base}_truncated{ext}"
                    with open(path, "rb") as fin, open(adjusted, "wb") as fout:
                        fout.write(fin.read(chip_size))
                    self.log_note(f"Created truncated file: {adjusted}", "info")
                    return adjusted
            else:
                if self._ask(
                    "Size mismatch",
                    f"{sizes}\nPad the image with 0xFF?",
                    yes=f"Pad to {human_size(chip_size)}",
                    no="Cancel",
                ):
                    adjusted = f"{base}_padded{ext}"
                    with open(path, "rb") as fin, open(adjusted, "wb") as fout:
                        fout.write(fin.read())
                        fout.write(b"\xFF" * (chip_size - file_size))
                    self.log_note(f"Created padded file: {adjusted}", "info")
                    return adjusted
        except OSError as e:
            self._alert("Error", f"Could not create adjusted image:\n{e}", error=True)
            return None

        self.log_note("Operation cancelled due to size mismatch", "dim")
        return None

    def _require_probed(self, cs: int) -> Optional[dict]:
        slot = self.slots[cs] if cs < self.num_slots else None
        if slot and slot.get("unsupported"):
            self._alert("Unsupported chip", self._unsupported_msg(cs), error=True)
            return None
        if not slot or not slot["detected"] or not slot["size_kb"]:
            self._alert(
                "Probe required",
                f"No chip known on {self._sock(cs)}.\nInsert one or probe {self._sock(cs)} first.",
            )
            return None
        return slot

    def _queue_blocked(self, op: str) -> bool:
        """True (and say why) if `op` can't be started while jobs run or are queued."""
        if op not in QUEUE_BLOCKED or not (self.busy or self._queue):
            return False
        self.log_note(f"{QUEUE_BLOCKED[op]}", "warning")
        return True

    def _refresh_busy_ui(self):
        """While jobs run or are queued: show the hands-off banner."""
        active = self.busy or bool(self._queue)
        self.busy_banner.configure(
            text="DO NOT remove or add chips while tasks run." if active else ""
        )
        self._refresh_op_buttons()

    def _broadcast_write_error(self) -> Optional[str]:
        """Why write or erase can't go to ALL right now (None if they can, or ALL isn't
        the target). One-by-one handles every usable chip on its own, so only "no
        chips" stops it."""
        if self._selected_cs() != CS_ALL:
            return None
        detected = self._detected_slots()
        if self._one_by_one():
            if any(not d.get("unsupported") for d in detected):
                return None
            return self._same_type_error(detected) or "No supported chips inserted."
        return self._same_type_error(detected)

    def _one_by_one_targets(self) -> Optional[List[dict]]:
        """The sockets a one-by-one write goes to (unsupported chips skipped), or None
        (after telling the user) if there are none."""
        detected = self._detected_slots()
        usable = [d for d in detected if not d.get("unsupported")]
        if not usable:
            msg = self._broadcast_write_error() or "No chips detected."
            self._alert("Nothing to write", msg, error=True)
            self.log_note(f"{msg.splitlines()[0]}", "error")
            return None
        for d in detected:
            if d.get("unsupported"):
                self.log_note(f"{self._sock(d['cs'])} skipped: unsupported chip", "warning")
        return usable

    def _refresh_op_buttons(self):
        """Grey out operation buttons that can't be used right now: the ones in
        QUEUE_BLOCKED while jobs run or wait, and write while the chips can't be
        broadcast to. They stay clickable, so a click can tell why."""
        if not hasattr(self, "_op_button_by_name"):
            return  # still building the window
        greyed = set(QUEUE_BLOCKED) if (self.busy or self._queue) else set()
        if self._broadcast_write_error():
            greyed.update(("write", "erase"))
        for name, btn in self._op_button_by_name.items():
            style = self._op_button_style[name]
            if name in greyed:
                bg = style["fg_color"] if style["fg_color"] == "transparent" else T["panel_hi"]
                btn.configure(
                    fg_color=bg,
                    text_color=T["faint"],
                    border_color=T["border"],
                    hover_color=T["panel"] if bg == "transparent" else bg,
                )
            else:
                btn.configure(**style)

    def _queue_defers_check(self) -> bool:
        """A job queued now runs later: sockets aren't scanned while the bus is busy, so
        a chip inserted meanwhile isn't known yet. The job checks it when it starts."""
        return (self.busy or bool(self._queue)) and not self._prod

    def _socket_error(self, cs: int, size: int, what: str) -> Optional[str]:
        """Why an image of `size` bytes can't go to/against CS<cs> (worker thread)."""
        slot = self.slots[cs]
        if not slot or not slot["detected"]:
            return f"No chip on {self._sock(cs)} – nothing {what}."
        if slot.get("unsupported"):
            return f"{self._sock(cs)} holds an unsupported chip – nothing {what}."
        if slot["size_kb"] and slot["size_kb"] * 1024 != size:
            return (
                f"Image is {human_size(size)} but {self._sock(cs)} is "
                f"{human_size(slot['size_kb'] * 1024)} – nothing {what}."
            )
        return None

    def _require_same_type(self) -> Optional[List[dict]]:
        detected = self._detected_slots()
        err = self._same_type_error(detected)
        if err:
            self._alert("Cannot use broadcast", err, error=True)
            if err.startswith("The chips in the sockets are not the same type"):
                self.log_note("Can't use ALL: the chips aren't all the same type", "error")
                for text, tag in self._slot_rows(detected):
                    self.log_detail(text, tag)
                self.log_detail(
                    "tick one-by-one to write them in turn, or select single sockets",
                    "dim",
                )
            else:
                self.log_note(err.splitlines()[0], "error")
            return None
        return detected

    # ====================== Operations ======================
    def probe(self):
        if self._queue_blocked("probe"):
            return
        cs = self._selected_cs()
        if cs == CS_ALL:
            self._start_probe_all()
        else:
            self._start_probe(cs)

    def _start_probe(self, cs: int):
        def worker():
            rc, text = self._exec([], cs)
            slot = self._parse_probe(text, cs)
            self._store_slot(cs, slot)
            if not slot["detected"]:
                return False, f"No chip detected on {self._sock(cs)}."
            if slot["unsupported"]:
                return False, self._unsupported_msg(cs)
            if rc != 0:
                return False, f"Probe of {self._sock(cs)} failed."
            size = (
                human_size(slot["size_kb"] * 1024)
                if slot["size_kb"]
                else "unknown size"
            )
            return True, f"{self._sock(cs)}: {slot['name']} ({size})"

        self._start_job(
            f"Probing {self._sock(cs)}…",
            worker,
            retry=lambda: self._start_probe(cs),
            cs=cs,
            title=f"Probe {self._sock(cs)}",
        )

    def _start_identify(self, sockets: List[int]):
        """Probe sockets the live scan found an unknown chip in, so flashprog names it and
        the JEDEC cache learns it. Goes ahead of queued jobs, which then see the chip."""
        names = ", ".join(f"{self._sock(cs)}" for cs in sockets)

        def worker():
            found, unsupported, missed = [], [], []
            for cs in sockets:
                # _exec stores what flashprog found. Several matching definitions are
                # fine here: the chip is identified, a job on it asks which one to use.
                _, text = self._exec([], cs)
                slot = self._parse_probe(text, cs)
                if slot["unsupported"]:
                    unsupported.append(f"{self._sock(cs)}")
                elif slot["detected"]:
                    found.append(f"{self._sock(cs)}: {slot['name']}")
                else:
                    missed.append(f"{self._sock(cs)}")
            problems = []
            if unsupported:
                problems.append(
                    f"Unsupported chip on {', '.join(unsupported)} – flashprog has no definition for it."
                )
            if missed:
                problems.append(f"flashprog found no chip on {', '.join(missed)}.")
            if problems:
                return False, " ".join(problems)
            return True, "Identified " + " · ".join(found)

        self._start_job(
            f"Identifying {names}…",
            worker,
            title=f"Identify unknown chip on {names}",
            cs=sockets[0] if len(sockets) == 1 else None,
            front=True,
        )

    def _start_probe_all(self):
        def worker():
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected in any socket."
            if self._same_type_error(detected):
                self._detail(
                    "⚠ chip types differ: broadcast needs identical chips – "
                    "use one-by-one to write them in turn",
                    "warning",
                )
            return (
                True,
                f"Probe complete — {len(detected)}/{self.num_slots} sockets populated.",
            )

        self._start_job(
            "Probing sockets…", worker, title="Probe all sockets", cs=CS_ALL
        )

    def read(self):
        cs = self._selected_cs()
        if cs == CS_ALL:
            self.read_all()
            return
        slot = self.slots[cs]
        default_name = (
            f"{self._safe_name(slot['name'])}_CS{cs}.bin"
            if slot and slot["detected"]
            else "flash_dump.bin"
        )
        path = self._ask_save_file(
            "Save dump as...",
            ".bin",
            [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if path:
            self._start_read(path, cs)

    def _start_read(self, path: str, cs: int):
        def worker():
            rc, _ = self._exec(["-r", path], cs)
            if rc != 0:
                return False, f"Read of {self._sock(cs)} failed."
            return True, f"Dump saved → {os.path.basename(path)}"

        self._start_job(
            f"Reading {self._sock(cs)}…",
            worker,
            retry=lambda: self._start_read(path, cs),
            key=("read", cs, path),
            cs=cs,
            title=f"Read {self._sock(cs)} → {os.path.basename(path)}",
        )

    def read_all(self):
        folder = self._ask_directory("Select folder to save ALL chip dumps")
        if not folder:
            self.log_note("Read ALL cancelled (no folder selected)", "dim")
            return
        self._start_read_all(folder)

    def _start_read_all(self, folder: str):
        def worker():
            self._step(1, 2, "Probe sockets")
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected."
            self._step(2, 2, f"Read {len(detected)} chip(s)")
            failed = 0
            for d in detected:
                filename = f"{self._safe_name(d['name'])}_CS{d['cs']}.bin"
                self.after(0, self.update_progress, f"{self._sock(d['cs'])}")
                t0 = time.monotonic()
                rc, _ = self._exec(
                    ["-r", os.path.join(folder, filename)],
                    d["cs"],
                    quiet=True,
                    show_cmd=False,
                )
                secs = time.monotonic() - t0
                self._detail(
                    result_row(self._sock_col(d["cs"]), "✓ saved" if rc == 0 else "✕ FAILED", secs, filename),
                    "success" if rc == 0 else "error",
                )
                failed += rc != 0
            if failed:
                return False, f"Read ALL: {failed}/{len(detected)} chip(s) failed."
            return True, f"Read ALL: {len(detected)} dump(s) saved to {folder}"

        self._start_job(
            "Reading all chips…",
            worker,
            retry=lambda: self._start_read_all(folder),
            key=("read", CS_ALL, folder),
            cs=CS_ALL,
            title=f"Read all chips → {folder}",
        )

    def erase(self):
        cs = self._selected_cs()
        if cs == CS_ALL and self._one_by_one():
            # Each socket on its own: the chips may differ
            detected = self._one_by_one_targets()
            if not detected:
                return
            target = (
                f"{len(detected)} chip(s) one by one "
                f"({self._socks(d['cs'] for d in detected)})"
            )
        elif cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            target = f"ALL {len(detected)} chips (broadcast)"
        else:
            target = f"the chip on {self._sock(cs)}"
        if not self._ask(
            "Erase",
            f"Erase {target}?\nThis is irreversible!",
            yes="Erase",
            no="Cancel",
            danger=True,
        ):
            return
        if cs == CS_ALL and self._one_by_one():
            # One job per socket: the first starts, the rest wait in the queue
            for d in detected:
                self._start_erase(d["cs"])
        elif cs == CS_ALL:
            self._start_erase_all(self._slots_key(detected))
        else:
            self._start_erase(cs)

    def _start_erase(self, cs: int):
        def worker():
            rc, _ = self._exec(["-E"], cs)
            return (
                (True, f"{self._sock(cs)} erased successfully.")
                if rc == 0
                else (False, f"Erase of {self._sock(cs)} failed.")
            )

        self._start_job(
            f"Erasing {self._sock(cs)}…",
            worker,
            retry=lambda: self._start_erase(cs),
            cs=cs,
            title=f"Erase {self._sock(cs)}",
        )

    def _start_erase_all(self, expected_key: tuple):
        def worker():
            self._step(1, 2, "Re-probe sockets")
            detected, err = self._reprobe_and_check(expected_key)
            if detected is None:
                return False, err
            self._step(2, 2, f"Erase {len(detected)} chip(s) (broadcast)")
            rc, _ = self._exec(["-E"], CS_ALL, quiet=True)
            if rc != 0:
                return False, "Broadcast erase failed."
            return True, f"All {len(detected)} chips erased."

        self._start_job(
            "Erasing all chips…",
            worker,
            retry=lambda: self._start_erase_all(expected_key),
            cs=CS_ALL,
            title="Erase all chips (broadcast)",
        )

    def write(self):
        if self._prod:
            self._run_batch()
            return
        if not self._write_allowed():
            return
        path = self._ask_open_file(
            "Select file to flash",
            [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")],
        )
        if path:
            self._write_file(path)

    def _write_allowed(self) -> bool:
        """Write is greyed for ALL when the chips can't be written: say why (before a
        file is asked for) and return False."""
        if self._selected_cs() == CS_ALL:
            err = self._broadcast_write_error()
            if err and self._one_by_one():
                self._alert("Nothing to write", err, error=True)
                self.log_note(f"{err.splitlines()[0]}", "error")
                return False
            if err and not self._require_same_type():
                return False
        return True

    def _write_file(self, path: str):
        self._add_recent_file(path)
        cs = self._selected_cs()
        if cs == CS_ALL and self._one_by_one():
            detected = self._one_by_one_targets()
            if not detected:
                return
            # Different chip sizes: no pad/truncate offer, each socket's job checks
            # its own chip against the image when its turn comes
            sizes = {d["size_kb"] for d in detected}
            only = next(iter(sizes))
            chip_size = only * 1024 if len(sizes) == 1 and only else None
            target = (
                f"{len(detected)} chip(s) one by one "
                f"({self._socks(d['cs'] for d in detected)})"
            )
        elif cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            chip_size = detected[0]["size_kb"] * 1024
            target = f"ALL {len(detected)} chips ({self._socks(d['cs'] for d in detected)})"
        elif self._queue_defers_check():
            chip_size, target = None, f"{self._sock(cs)} (checked when its turn comes)"
        else:
            slot = self._require_probed(cs)
            if not slot:
                return
            chip_size = slot["size_kb"] * 1024
            target = f"{self._sock(cs)} ({slot['name']})"

        adjusted = self._adjust_file_size(path, chip_size) if chip_size else path
        if not adjusted:
            return
        queued = " (queued)" if (self.busy or self._queue) else ""
        if not self._ask(
            "Write" + queued,
            f"Flash {os.path.basename(adjusted)}\nto {target}?\n\n"
            "A wrong file can brick the device!",
            yes="Flash it",
            no="Cancel",
            danger=True,
        ):
            return
        if cs == CS_ALL and self._one_by_one():
            # One job per socket: the first starts, the rest wait in the queue
            for d in detected:
                self._start_write(adjusted, d["cs"])
        elif cs == CS_ALL:
            self._start_write_all(adjusted, self._slots_key(detected))
        else:
            self._start_write(adjusted, cs)

    def _write_socket(
        self, path: str, data: bytes, cs: int, step: Callable[[int, int, str], None]
    ) -> Tuple[bool, str]:
        """Blank check, write and verify one socket (worker thread).
        step(n, total, text) logs each of these steps."""
        err = self._socket_error(cs, len(data), "written")
        if err:
            return False, err
        crc = zlib.crc32(data) & 0xFFFFFFFF
        plan = self._plan(data)
        pico_verify = self._job["pico"] and not self._job["noverify"]
        steps = (
            (["Blank check (on Pico)"] if plan else [])
            + ["Write"]
            + (["Verify (on Pico)"] if pico_verify else [])
        )
        n = iter(range(1, len(steps) + 1))
        slot = self.slots[cs] or {"cs": cs, "name": f"CS{cs}"}

        regions = None
        if plan:
            used, unused = plan
            step(
                next(n),
                len(steps),
                steps[0] + f" · {human_size(ranges_size(unused))} unused",
            )
            dirty = self._blank_check([slot], unused)
            regions = merge_ranges(used + dirty)
            if not regions:
                self._detail(
                    "image is empty (all 0xFF) and the chip is blank – nothing to write",
                    "success",
                )

        step(
            next(n),
            len(steps),
            "Write"
            + (
                f" {human_size(ranges_size(regions))} (used blocks)"
                if regions is not None
                else ""
            ),
        )
        t0 = time.monotonic()
        if regions != []:
            extra = ["-w", path] + (self._layout_args(regions) if regions else [])
            # With a Pico-side verify coming, flashprog doesn't need to read the chip back
            noverify = pico_verify or self._job["noverify"]
            rc, _ = self._exec(extra, cs, noverify=noverify)
            if rc != 0:
                self._record(cs, "write", False, time.monotonic() - t0, path, crc)
                return False, f"Write to {self._sock(cs)} failed."
        self._record(cs, "write", True, time.monotonic() - t0, path, crc)

        if pico_verify:
            step(next(n), len(steps), "Verify (on Pico)")
            rows = self._crc_verify(data, [slot], "verify", path)
            if not rows[0][2]:
                return (
                    False,
                    f"{self._sock(cs)} does NOT match {os.path.basename(path)} after writing.",
                )
        return True, f"Write completed → {os.path.basename(path)} ({self._sock(cs)})"

    def _start_write(self, path: str, cs: int, production: bool = False) -> bool:
        def worker():
            data = open(path, "rb").read()
            return self._write_socket(path, data, cs, self._step)

        return self._start_job(
            f"Writing {self._sock(cs)}…",
            worker,
            retry=lambda: self._start_write(path, cs, production),
            key=("write", cs, path),
            cs=cs,
            title=f"Write {os.path.basename(path)} → {self._sock(cs)}",
            production=production,
        )

    def _start_write_each(
        self, path: str, sockets: List[int], production: bool = False
    ) -> bool:
        """One job writing the same image to each socket in turn (production batches
        with one-by-one; outside production, one-by-one queues a job per socket)."""

        def worker():
            data = open(path, "rb").read()
            crc = zlib.crc32(data) & 0xFFFFFFFF
            failed = []
            for i, cs in enumerate(sockets, 1):
                self._step(i, len(sockets), f"{self._sock(cs)}")
                rows_before = len(self._job["rows"])
                ok, msg = self._write_socket(
                    path, data, cs, lambda n, total, text: self._detail(text, "soft")
                )
                if not ok and len(self._job["rows"]) == rows_before:
                    # Refused before writing (no chip, wrong size): still a failed part
                    self._record(cs, "write", False, 0.0, path, crc)
                self._detail(("✓ " if ok else "✕ ") + msg, "success" if ok else "error")
                if not ok:
                    failed.append(cs)
            if failed:
                return False, (
                    f"{len(failed)}/{len(sockets)} socket(s) failed "
                    f"({self._socks(c for c in failed)})."
                )
            return True, f"Written one by one to {len(sockets)} socket(s)."

        names = ", ".join(f"{self._sock(c)}" for c in sockets)
        return self._start_job(
            f"Writing {len(sockets)} chip(s) one by one…",
            worker,
            retry=lambda: self._start_write_each(path, sockets, production),
            key=("write", tuple(sockets), path),
            cs=CS_ALL,
            title=f"Write {os.path.basename(path)} → {names} (one by one)",
            production=production,
        )

    def _start_write_all(
        self, path: str, expected_key: tuple, production: bool = False
    ) -> bool:
        """Broadcast write: all chips are erased + programmed in parallel (broadcast), but every
        read during a broadcast sees all chips driving MISO at once. So the chips are erased
        first (making their contents identical, which flashprog's read-before-write relies on)
        and then verified one by one, never over the shared bus."""

        def worker():
            data = open(path, "rb").read()
            crc = zlib.crc32(data) & 0xFFFFFFFF
            plan = self._plan(data)
            verify = not self._job["noverify"]
            steps = (
                ["Re-probe sockets"]
                + (["Blank check (on Pico)"] if plan else [])
                + ["Erase (broadcast)", "Write (broadcast)"]
                + (
                    [f"Verify ({'on Pico' if self._job['pico'] else 'flashprog'})"]
                    if verify
                    else []
                )
            )
            n = iter(range(1, len(steps) + 1))
            total = len(steps)

            self._step(next(n), total, steps[0])
            detected, err = self._reprobe_and_check(expected_key)
            if detected is None:
                return False, err

            erase_regions = write_regions = None
            if plan:
                used, unused = plan
                self._step(
                    next(n),
                    total,
                    f"Blank check (on Pico) · {human_size(ranges_size(unused))} unused",
                )
                dirty = self._blank_check(detected, unused)
                erase_regions = merge_ranges(used + dirty)
                write_regions = used

            t0 = time.monotonic()
            if erase_regions is None:
                self._step(next(n), total, f"Erase {len(detected)} chip(s) (broadcast)")
                rc, _ = self._exec(["-E"], CS_ALL, quiet=True)
            elif erase_regions:
                self._step(
                    next(n),
                    total,
                    f"Erase {human_size(ranges_size(erase_regions))} "
                    f"on {len(detected)} chip(s) (broadcast)",
                )
                rc, _ = self._exec(
                    ["-E"] + self._layout_args(erase_regions), CS_ALL, quiet=True
                )
            else:
                self._step(next(n), total, "Erase · nothing to erase")
                rc = 0
            if rc != 0:
                return False, "Broadcast erase failed – nothing was written."

            if write_regions is None:
                self._step(next(n), total, f"Write {len(detected)} chip(s) (broadcast)")
                rc, _ = self._exec(["-w", path], CS_ALL, noverify=True, quiet=True)
            elif write_regions:
                self._step(
                    next(n),
                    total,
                    f"Write {human_size(ranges_size(write_regions))} "
                    f"to {len(detected)} chip(s) (broadcast)",
                )
                rc, _ = self._exec(
                    ["-w", path] + self._layout_args(write_regions),
                    CS_ALL,
                    noverify=True,
                    quiet=True,
                )
            else:
                self._step(next(n), total, "Write · image is empty (all 0xFF)")
                rc = 0
            secs = time.monotonic() - t0
            for d in detected:
                self._record(d["cs"], "write", rc == 0, secs, path, crc)
            if rc != 0:
                return False, "Broadcast write failed."
            if not verify:
                self._detail(
                    "⚠ 'skip verify' is on – chips were NOT verified.", "warning"
                )
                return (
                    True,
                    f"Broadcast write completed → {os.path.basename(path)} (unverified)",
                )

            self._step(next(n), total, steps[-1] + f" · {len(detected)} chip(s)")
            if self._job["pico"]:
                rows = self._crc_verify(data, detected, "verify", path)
            else:
                rows = self._flashprog_verify(path, data, detected, "verify")
            failed = [r for r in rows if not r[2]]
            if failed:
                return (
                    False,
                    f"{len(failed)}/{len(rows)} chip(s) failed verification "
                    f"({self._socks(r[0] for r in failed)}).",
                )
            return True, f"Written and verified on {len(rows)} chip(s)."

        return self._start_job(
            "Writing all chips…",
            worker,
            retry=lambda: self._start_write_all(path, expected_key, production),
            key=("write", CS_ALL, path),
            cs=CS_ALL,
            title=f"Write {os.path.basename(path)} → all chips",
            production=production,
        )

    def verify(self):
        path = self._ask_open_file(
            "Select file to verify",
            [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")],
        )
        if path:
            self._verify_file(path)

    def _verify_file(self, path: str):
        cs = self._selected_cs()
        if cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            chip_size = detected[0]["size_kb"] * 1024
        elif self._queue_defers_check():
            chip_size = None
        else:
            slot = self._require_probed(cs)
            if not slot:
                return
            chip_size = slot["size_kb"] * 1024
        adjusted = self._adjust_file_size(path, chip_size) if chip_size else path
        if not adjusted:
            return
        if cs == CS_ALL:
            self._start_verify_all(adjusted)
        else:
            self._start_verify(adjusted, cs)

    def _start_verify(self, path: str, cs: int):
        def worker():
            data = open(path, "rb").read()
            err = self._socket_error(cs, len(data), "verified")
            if err:
                return False, err
            slot = self.slots[cs] or {"cs": cs, "name": f"CS{cs}"}
            if self._job["pico"]:
                ok = self._crc_verify(data, [slot], "verify", path)[0][2]
            else:
                t0 = time.monotonic()
                rc, _ = self._exec(["-v", path], cs)
                ok = rc == 0
                self._record(
                    cs,
                    "verify",
                    ok,
                    time.monotonic() - t0,
                    path,
                    zlib.crc32(data) & 0xFFFFFFFF,
                )
            if not ok:
                return False, f"{self._sock(cs)} does NOT match {os.path.basename(path)}."
            return True, f"{self._sock(cs)} matches {os.path.basename(path)}"

        self._start_job(
            f"Verifying {self._sock(cs)}…",
            worker,
            retry=lambda: self._start_verify(path, cs),
            key=("verify", cs, path),
            cs=cs,
            title=f"Verify {self._sock(cs)} against {os.path.basename(path)}",
        )

    def _start_verify_all(self, path: str):
        def worker():
            data = open(path, "rb").read()
            self._step(1, 2, "Probe sockets")
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected."
            how = "on Pico" if self._job["pico"] else "flashprog"
            self._step(2, 2, f"Verify {len(detected)} chip(s) ({how})")
            if self._job["pico"]:
                rows = self._crc_verify(data, detected, "verify", path)
            else:
                rows = self._flashprog_verify(path, data, detected, "verify")
            failed = [r for r in rows if not r[2]]
            if failed:
                return (
                    False,
                    f"{len(failed)}/{len(rows)} chip(s) do NOT match "
                    f"({self._socks(r[0] for r in failed)}).",
                )
            return True, f"All {len(rows)} chip(s) match {os.path.basename(path)}."

        self._start_job(
            "Verifying all chips…",
            worker,
            retry=lambda: self._start_verify_all(path),
            key=("verify", CS_ALL, path),
            cs=CS_ALL,
            title=f"Verify all chips against {os.path.basename(path)}",
        )

    # ====================== Settings page ======================
    # Replaces the sockets, flashing options and buttons (self._top_view); the status
    # line and the log stay visible below, so a firmware update shows its progress.
    def _toggle_settings(self):
        (self._close_settings if self._settings_open else self._open_settings)()

    def _open_settings(self):
        if self._settings_open:
            return
        if self._settings_view is None:
            self._build_settings_view()
        # Full page: the main view, status line, log and footer step aside. Their
        # pack options are kept to put them back exactly (see _close_settings).
        self._hidden_views = [
            (w, w.pack_info())
            for w in (self._top_view, self._status_line, self._log_panel, self._footer)
        ]
        for w, _ in self._hidden_views:
            w.pack_forget()
        self._settings_view.pack(fill="both", expand=True)
        self._settings_open = True
        self._render_gear()
        self._refresh_settings()
        self._load_binary_version()
        if self._release is None and not self._release_checking:
            self._check_release()

    def _close_settings(self):
        if not self._settings_open:
            return
        self._settings_view.pack_forget()
        for w, info in self._hidden_views:
            info = {k: v for k, v in info.items() if k != "in"}
            # Plain Tk's pack: the saved paddings are already scaled, customtkinter's
            # pack() would scale them again on HiDPI screens
            tkinter.Pack.pack_configure(w, **info)
        self._settings_open = False
        self._render_gear()

    def _settings_row(self, panel, label: str) -> ctk.CTkFrame:
        # (winfo_children() isn't empty for a new CTkFrame: it holds its own canvas)
        first = not getattr(panel, "_settings_rows", 0)
        panel._settings_rows = getattr(panel, "_settings_rows", 0) + 1
        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(10 if first else 3, 3))
        ctk.CTkLabel(
            row, text=label, width=130, anchor="w",
            font=self.fonts["normal"], text_color=T["dim"],
        ).pack(side="left")
        return row

    def _settings_note(self, row, color=None) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            row, text="", anchor="w", font=self.fonts["normal"],
            text_color=color or T["dim"],
        )
        lbl.pack(side="left", padx=(12, 0), fill="x", expand=True)
        return lbl

    def _build_settings_view(self):
        view = ctk.CTkFrame(self._ui_root, fg_color="transparent")
        self._settings_view = view
        top = ctk.CTkFrame(view, fg_color="transparent")
        top.pack(fill="x", pady=(12, 0))
        self._button(top, "← back", self._close_settings, width=90).pack(side="left")
        ctk.CTkLabel(
            top, text="settings", font=self.fonts["bold"], text_color=T["text"]
        ).pack(side="left", padx=(14, 0))
        ctk.CTkLabel(
            top, text="changes apply right away · esc to go back",
            font=self.fonts["small"], text_color=T["faint"],
        ).pack(side="right")
        body = ctk.CTkScrollableFrame(
            view, fg_color="transparent",
            scrollbar_button_color=T["border"],
            scrollbar_button_hover_color=T["border_hi"],
        )
        body.pack(fill="both", expand=True, pady=(4, 0))
        self._settings_body = body
        # customtkinter scrolls on <MouseWheel> only; X11 sends the wheel as buttons 4/5
        self.bind_all("<Button-4>", lambda e: self._settings_wheel(e, -1), add="+")
        self.bind_all("<Button-5>", lambda e: self._settings_wheel(e, 1), add="+")
        w = self._settings_widgets = {}

        self._section(body, "hardware")
        panel = self._panel(body)
        panel.pack(fill="x")
        row = self._settings_row(panel, "sockets")
        counts = [str(n) for n in range(1, MAX_SLOTS + 1)]
        self._sockets_var = ctk.StringVar(value="auto")
        combo = self._combo(row, ["auto"] + counts, self._sockets_var, 80)
        combo.configure(command=self._on_sockets_choice)
        combo.pack(side="left", padx=(12, 0))
        # Auto only: the count to use when nothing is detected
        self._fallback_box = ctk.CTkFrame(row, fg_color="transparent")
        ctk.CTkLabel(
            self._fallback_box, text="otherwise", font=self.fonts["normal"],
            text_color=T["dim"],
        ).pack(side="left", padx=(12, 6))
        self._fallback_var = ctk.StringVar(value=str(self.manual_sockets))
        fallback = self._combo(self._fallback_box, counts, self._fallback_var, 70)
        fallback.configure(command=self._on_fallback_choice)
        fallback.pack(side="left")
        self._button(row, "detect again", self._detect_sockets, width=120).pack(
            side="right"
        )
        w["sockets"] = self._settings_note(row)
        row = self._settings_row(panel, "socket names")
        self._names_frame = ctk.CTkFrame(row, fg_color="transparent")
        self._names_frame.pack(side="left", padx=(12, 0))
        self._names_for = 0  # socket count the name fields were built for
        row = self._settings_row(panel, "live scan")
        TtyToggle(row, "on", self.scan_var, self.fonts["normal"]).pack(
            side="left", padx=(12, 14)
        )
        for text, values, var in (
            ("every", list(SCAN_INTERVALS), self.scan_interval_var),
            ("at", SCAN_SPEEDS, self.scan_speed_var),
        ):
            ctk.CTkLabel(
                row, text=text, font=self.fonts["normal"], text_color=T["dim"]
            ).pack(side="left", padx=(0, 6))
            self._combo(row, values, var, 80).pack(side="left", padx=(0, 12))
        w["scan"] = self._settings_note(row)
        row = self._settings_row(panel, "status LEDs")
        TtyToggle(
            row, "show the job state on the Pico", self.leds_var, self.fonts["normal"]
        ).pack(side="left", padx=(12, 0))
        self.scan_var.trace_add("write", lambda *a: self._on_scan_toggle())
        self.scan_interval_var.trace_add("write", lambda *a: self.save_config())
        self.scan_speed_var.trace_add("write", lambda *a: self.save_config())
        self.leds_var.trace_add("write", lambda *a: self._on_leds_toggle())
        row = self._settings_row(panel, "flashprog")
        w["binary"] = self._settings_note(row, T["text"])
        self._button(row, "auto-detect", self._auto_binary, width=110).pack(side="right")
        self._button(row, "change…", self._change_binary, width=90).pack(
            side="right", padx=(0, 6)
        )
        row.pack_configure(pady=(3, 10))  # the panel's last row

        self._section(body, "firmware")
        panel = self._panel(body)
        panel.pack(fill="x")
        row = self._settings_row(panel, "on the Pico")
        w["fw_pico"] = self._settings_note(row, T["text"])
        row = self._settings_row(panel, "latest release")
        w["fw_latest"] = self._settings_note(row, T["text"])
        self._button(row, "check again", self._check_release, width=110).pack(side="right")
        row = self._settings_row(panel, "install")
        self._button(
            row, "from file…", self._update_firmware_file, width=120
        ).pack(side="left", padx=(12, 0))
        self._fw_latest_button = self._button(
            row, "latest release", self._update_firmware_latest, width=150
        )
        self._fw_latest_button.pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            row, text="or drop a .uf2 on the window",
            font=self.fonts["small"], text_color=T["faint"],
        ).pack(side="left", padx=(12, 0))
        row.pack_configure(pady=(3, 10))  # the panel's last row

        self._section(body, "app")
        panel = self._panel(body)
        panel.pack(fill="x")
        row = self._settings_row(panel, "notifications")
        toggle = TtyToggle(row, "when a job ends", self.notify_var, self.fonts["normal"])
        toggle.pack(side="left", padx=(12, 0))
        w["notify"] = self._settings_note(row)
        self.notify_var.trace_add("write", lambda *a: self.save_config())
        row = self._settings_row(panel, "drag and drop")
        w["dnd"] = self._settings_note(row)
        for key, label, text, fn in (
            ("jedec", "chip IDs", "forget", self._forget_chips),
            ("eta", "time estimates", "reset", self._reset_estimates),
            ("recent", "recent files", "clear", self._clear_recent_files),
        ):
            row = self._settings_row(panel, label)
            w[key] = self._settings_note(row)
            self._button(row, text, fn, color=T["soft"], width=90).pack(side="right")
        row = self._settings_row(panel, "version")
        w["version"] = self._settings_note(row, T["text"])
        row = self._settings_row(panel, "files")
        w["files"] = self._settings_note(row)
        row.pack_configure(pady=(3, 10))  # the panel's last row

        credit = ctk.CTkFrame(body, fg_color="transparent")
        credit.pack(pady=(22, 12))
        self._credit_links = []
        for text, color, link in (
            ("Vibed by Waffelo", T["dim"], True),
            ("·", T["faint"], False),
            (REPO_URL.replace("https://", ""), T["accent"], True),
        ):
            lbl = ctk.CTkLabel(
                credit, text=text, font=self.fonts["small"], text_color=color,
                cursor="hand2" if link else "",
            )
            lbl.pack(side="left", padx=4)
            if link:
                lbl.bind("<Button-1>", lambda e: self._open_repo())
                self._credit_links.append(lbl)
        self._app_commit: Optional[str] = None  # filled in by a background git call
        threading.Thread(target=self._load_app_commit, daemon=True).start()

    def _load_app_commit(self):
        commit = app_commit()
        self.after(0, self._app_commit_loaded, commit)

    def _app_commit_loaded(self, commit: str):
        self._app_commit = commit
        self._refresh_settings()

    def _open_repo(self):
        try:
            webbrowser.open(REPO_URL)
        except Exception:
            self.log_note(f"Couldn't open a browser: {REPO_URL}", "warning")

    def _settings_wheel(self, event, direction: int):
        """Scroll the settings page with the mouse wheel (Linux)."""
        if not self._settings_open:
            return
        canvas = self._settings_body._parent_canvas
        w = event.widget
        while isinstance(w, tkinter.Misc):  # inside the settings page?
            if w is canvas:
                if canvas.yview() != (0.0, 1.0):
                    canvas.yview_scroll(direction * 3, "units")
                return
            w = w.master

    def _refresh_settings(self):
        """Fill in the settings page (only while it's shown)."""
        if not self._settings_open:
            return
        w = self._settings_widgets
        home = os.path.expanduser("~")
        idle = not (self.busy or self._queue or self._prod)
        self._sockets_var.set(str(self.num_slots))
        self._sockets_var.set("auto" if self.sockets_auto else str(self.num_slots))
        self._fallback_var.set(str(self.manual_sockets))
        if self.sockets_auto:
            self._fallback_box.pack(side="left", before=w["sockets"])
        else:
            self._fallback_box.pack_forget()
        w["sockets"].configure(
            text=self._sockets_summary()
            + ("" if idle else " · can't change while jobs run or wait")
        )
        if self._names_for != self.num_slots:
            self._build_name_fields()
        w["scan"].configure(
            text="" if self.scan_var.get()
            else "cards update after jobs and probes only"
        )
        version = f"  · {self._binary_version}" if self._binary_version else ""
        w["binary"].configure(
            text=trunc((self.flashrom_path or "").replace(home, "~") + version, 48)
        )

        text, color = self._fw_summary()
        w["fw_pico"].configure(text=text, text_color=color)
        rel, color = self._release, T["text"]
        if self._release_checking:
            text, color = "checking GitHub…", T["dim"]
        elif rel is None:
            text, color = "not checked", T["dim"]
        elif "error" in rel:
            text, color = trunc(f"couldn't check: {rel['error']}", 60), T["red"]
        elif not rel:
            text, color = "no firmware release published yet", T["dim"]
        else:
            text = f"HydraFlasher {rel['version']} · {rel['date']}"
            installed = fw_version(self._fw_name)
            if installed and version_key(rel["version"]) > version_key(installed):
                text, color = text + " · update available", T["accent"]
            elif installed and installed == rel["version"]:
                text += " · installed"
        w["fw_latest"].configure(text=text, text_color=color)
        has_release = bool(rel) and "error" not in rel
        self._fw_latest_button.configure(
            text=f"latest ({rel['version']})" if has_release else "latest release",
            text_color=T["text"] if has_release else T["faint"],
        )

        if not NOTIFICATIONS_AVAILABLE:
            w["notify"].configure(
                text="needs notify-send (libnotify)" if self.is_linux
                else "needs the plyer package"
            )
        w["dnd"].configure(
            text="on: drop images on sockets or the log" if self._dnd
            else "off: needs the tkinterdnd2 package"
        )
        w["jedec"].configure(text=f"{len(self.jedec_cache)} learned from probes")
        w["eta"].configure(text=f"{len(self.job_times)} remembered job durations")
        w["recent"].configure(text=f"{len(self.recent_files)} in the list")
        w["files"].configure(
            text=f"{CONFIG_FILE.replace(home, '~')} · {DATA_DIR.replace(home, '~')}"
        )
        commit = f" · git {self._app_commit}" if self._app_commit else ""
        w["version"].configure(text=f"HydraFlasher {APP_VERSION}{commit}")

    def _build_name_fields(self):
        """One name field per configured socket, five to a row."""
        for child in self._names_frame.winfo_children():
            child.destroy()
        self._name_entries = {}
        for cs in range(self.num_slots):
            cell = ctk.CTkFrame(self._names_frame, fg_color="transparent")
            cell.grid(row=cs // 5, column=cs % 5, padx=(0, 10), pady=2, sticky="w")
            ctk.CTkLabel(
                cell, text=f"CS{cs}", width=30, anchor="w",
                font=self.fonts["small"], text_color=T["faint"],
            ).pack(side="left")
            entry = ctk.CTkEntry(
                cell, width=84, height=28, font=self.fonts["normal"], corner_radius=6,
                fg_color=T["bg"], border_color=T["border"], border_width=1,
                text_color=T["text"], placeholder_text="name",
            )
            if self.socket_names[cs]:
                entry.insert(0, self.socket_names[cs])
            entry.pack(side="left")
            for event in ("<Return>", "<KP_Enter>", "<FocusOut>"):
                entry.bind(event, lambda e, cs=cs, en=entry: self._set_socket_name(cs, en.get()))
            self._name_entries[cs] = entry
        self._names_for = self.num_slots

    def _set_socket_name(self, cs: int, name: str):
        """Name a socket (empty: no name); the card and later log lines use it."""
        name = self._clean_socket_name(name)
        entry = getattr(self, "_name_entries", {}).get(cs)
        if entry is not None and entry.get() != name:  # show it as stored
            entry.delete(0, "end")
            if name:
                entry.insert(0, name)
        if name == self.socket_names[cs]:
            return
        self.socket_names[cs] = name
        self.save_config()
        if cs in self.cards:
            self.cards[cs].set_title(*self._card_title(cs))
        self._update_footer()
        self.log_note(
            f"CS{cs} named {name}" if name else f"CS{cs}: name removed", "dim"
        )

    def _on_scan_toggle(self):
        on = self.scan_var.get()
        self.save_config()
        self._sockets_label.configure(text=self._sockets_header())
        self.log_note(
            "Live scan on" if on
            else "Live scan off: cards update after jobs and probes only",
            "info" if on else "dim",
        )
        self._refresh_settings()

    def _on_leds_toggle(self):
        self.save_config()
        # Switching them off clears the one that's lit; on starts from idle
        self._set_led(LED["IDLE"], force=True)

    def _fw_summary(self) -> Tuple[str, str]:
        """What the Pico's firmware is, as (text, colour) for the settings page."""
        if self._rp2_present:
            return "a Pico in bootloader mode (RPI-RP2) · install firmware below", T["accent"]
        if not self._port_connected:
            return "no programmer connected", T["dim"]
        port = self.port_entry.get().strip()
        if self._unresponsive and self._unresponsive[0] == port:
            return (
                "not responding (other or hung firmware) · hold BOOTSEL while "
                "plugging it in, then install",
                T["yellow"],
            )
        if self._fw_caps is None:
            port = self.port_entry.get().strip()
            if port and not self._port_accessible(port):
                return "unknown: no direct access to the port (sudo)", T["dim"]
            return "connected · not checked yet", T["dim"]
        version = fw_version(self._fw_name)
        if version:
            return f"HydraFlasher {version} · {self._fw_cs} CS lines", T["text"]
        return (
            f"older firmware ({self._fw_cs} CS lines, no version) · update recommended",
            T["yellow"],
        )

    def _sockets_summary(self) -> str:
        """The socket count's source, for the settings page."""
        lines = "CS0" if self.num_slots == 1 else f"CS0–CS{self.num_slots - 1}"
        if not self.sockets_auto:
            why = " (no pull-ups found)" if self._auto_fell_back else ""
            return f"manual{why} · {lines}"
        if self._wired:
            return f"detected {self._wired_text(self._wired)}"
        if self._fw_caps is None:
            last = " (as last detected)" if self.sockets_detected else ""
            return f"not detected yet · {lines}{last}"
        if S_CMD_Q_CS_WIRED not in self._fw_caps:
            why = "the firmware can't detect (needs 1.2)"
        else:
            why = "nothing detected (no pull-ups on CS)"
        return f"{why} · using {self.num_slots}"

    def _on_sockets_choice(self, value: str):
        if value == "auto":
            if not self.sockets_auto:
                self.sockets_auto, self._auto_fell_back = True, False
                self.save_config()
                self.log_note("Socket count: auto (detected wiring)", "info")
                self._apply_auto_sockets()
                self._refresh_sockets()  # gaps count again
        else:
            n = int(value)
            if self._set_socket_count(n, "manual"):
                was_auto = self.sockets_auto
                self.sockets_auto, self.manual_sockets = False, n
                self._auto_fell_back = False
                self.save_config()
                if was_auto:
                    self._refresh_sockets()  # "not wired" sockets are usable again
        self._refresh_settings()

    def _on_fallback_choice(self, value: str):
        """The count auto mode uses when nothing is detected."""
        self.manual_sockets = int(value)
        self.save_config()
        self._apply_auto_sockets()
        self._refresh_settings()

    def _set_socket_count(self, n: int, why: str = "") -> bool:
        """Change the number of sockets now: cards, targets, shortcuts and per-socket
        state follow. Only while no job runs or waits (jobs refer to socket numbers)."""
        if n == self.num_slots:
            return True
        if not 1 <= n <= MAX_SLOTS:
            return False
        if self.busy or self._queue or self._prod:
            self.log_note(
                "The socket count can't change while jobs run or wait, or in "
                "production mode.",
                "warning",
            )
            return False
        old, sel = self.num_slots, self._selected_cs()
        keep = min(old, n)
        self.num_slots, self.has_all = n, n > 1
        self.slots = self.slots[:keep] + [None] * (n - keep)
        self._socket_result = self._socket_result[:keep] + [None] * (n - keep)
        self._socket_ident = self._socket_ident[:keep] + [_UNSCANNED] * (n - keep)
        self._build_cs_map()
        # The target stays if it still exists, else the first socket
        target = sel if sel in self.cs_labels else 0
        for child in self._sockets_box.winfo_children():
            child.destroy()
        self.cards = {}
        self.cs_display_var.set(self.cs_labels[target])
        self._create_sockets(self._sockets_box)
        self._subtitle.configure(text=f"  {n}× SOIC-16 · serprog")
        self._bind_socket_keys()
        self._register_drop_targets()
        self._refresh_sockets()
        self.save_config()
        self.log_note(f"Socket count: {n}" + (f" ({why})" if why else ""), "info")
        if self._fw_cs is not None and n > self._fw_cs:
            self._warn_fw_cs()
        return True

    def _load_binary_version(self):
        path = self.flashrom_path

        def work():
            version = self._probe_binary(path)
            self.after(0, self._binary_version_loaded, path, version)

        threading.Thread(target=work, daemon=True).start()

    def _binary_version_loaded(self, path: str, version: str):
        if path == self.flashrom_path:
            self._binary_version = version
            self._refresh_settings()

    @staticmethod
    def _probe_binary(path: Optional[str]) -> str:
        """"flashprog 1.2" from `<path> --version`, "" if path isn't flashprog/flashrom."""
        if not path:
            return ""
        try:
            out = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        # "flashprog p1.4 on Linux …", "flashrom v1.3.0 on …"
        m = re.search(r"\b(flashprog|flashrom)\b\S*\s+([pv]?[0-9][\w.\-]*)", out, re.I)
        if m:
            return f"{m.group(1).lower()} {m.group(2)}"
        return "flashprog" if re.search(r"flash(prog|rom)", out, re.I) else ""

    def _change_binary(self):
        path = self._ask_user_for_binary()
        if path:
            self._set_binary(path)

    def _auto_binary(self):
        found = self._auto_find_flashrom()
        if not found:
            self._alert(
                "flashprog", "Neither flashprog nor flashrom was found.", error=True
            )
            return
        self._set_binary(found)

    def _set_binary(self, path: str):
        if self.busy or self._queue:
            self.log_note("The flashprog binary can't change while jobs run or wait.", "warning")
            return
        version = self._probe_binary(path)
        if not version and not self._ask(
            "flashprog",
            f"{os.path.basename(path)} doesn't answer like flashprog or flashrom.\n\n"
            "Use it anyway?",
            yes="Use it",
            no="Cancel",
            danger=True,
        ):
            return
        self.flashrom_path = path
        self._binary_version = version
        self.save_config()
        self.log_note(f"flashprog binary: {path}" + (f" ({version})" if version else ""), "info")
        self._refresh_settings()

    def _forget_chips(self):
        if not self.jedec_cache:
            self.log_note("No chip IDs learned yet.", "dim")
            return
        if self.busy or self._queue:
            self.log_note("Chip IDs can't be forgotten while jobs run or wait.", "warning")
            return
        if not self._ask(
            "Forget chip IDs",
            f"Forget the {len(self.jedec_cache)} chip ID(s) learned from probes?\n\n"
            "Inserted chips are identified again with a probe.",
            yes="Forget",
            no="Cancel",
        ):
            return
        self.jedec_cache.clear()
        self._chip_choice.clear()
        # Unscanned again: the next scan identifies the inserted chips anew
        self._socket_ident = [_UNSCANNED] * self.num_slots
        self.slots = [None] * self.num_slots
        self.save_config()
        self.log_note("Learned chip IDs forgotten", "info")
        self._refresh_sockets()
        self._refresh_settings()

    def _reset_estimates(self):
        n = len(self.job_times)
        self.job_times.clear()
        self.save_config()
        self.log_note(f"Time estimates reset ({n} forgotten)", "dim")
        self._refresh_settings()

    # ====================== Firmware update ======================
    def _check_release(self):
        """Ask GitHub for the newest firmware release (in the background)."""
        if self._release_checking:
            return
        self._release_checking = True
        self._refresh_settings()

        def work():
            try:
                result = latest_fw_release() or {}
            except Exception as e:  # offline, rate limit, bad answer
                result = {"error": str(getattr(e, "reason", e))}
            self.after(0, self._release_checked, result)

        threading.Thread(target=work, daemon=True).start()

    def _release_checked(self, result: dict):
        self._release_checking = False
        self._release = result
        self._refresh_settings()

    def _update_firmware_file(self):
        path = self._ask_open_file(
            "Select the Pico firmware (.uf2)",
            [("Pico firmware", "*.uf2"), ("All files", "*.*")],
        )
        if path:
            self._start_firmware_update(path=path)

    def _update_firmware_latest(self):
        rel = self._release
        if not rel or "error" in rel:
            self.log_note(
                "No firmware release to install – check GitHub first (settings).",
                "warning",
            )
            return
        self._start_firmware_update(release=rel)

    def _start_firmware_update(
        self, path: Optional[str] = None, release: Optional[dict] = None
    ) -> bool:
        """Install a .uf2 (a file, or a GitHub release) on the Pico: restart it into
        its bootloader, copy the file to the RPI-RP2 drive, check what comes back."""
        if self._queue_blocked("firmware"):
            return False
        if self._prod:
            self.log_note("Turn production mode off to update the firmware.", "warning")
            return False
        if path:
            try:
                version = read_uf2(path)["version"]
            except (OSError, ValueError) as e:
                self._alert(
                    "Firmware", f"{os.path.basename(path)} can't be installed:\n{e}",
                    error=True,
                )
                self.log_note(f"{os.path.basename(path)}: {e}", "error")
                return False
            name = os.path.basename(path)
        else:
            version, name = release["version"], release["name"]
        installed = fw_version(self._fw_name)
        now = (
            f"HydraFlasher {installed}" if installed
            else "older firmware" if self._fw_name is not None
            else "unknown"
        )
        if version is None:
            message = (
                f"{name} doesn't look like HydraFlasher firmware.\n"
                "With it, HydraFlasher may not be able to use the Pico.\n\n"
                "Install it anyway?"
            )
        else:
            message = (
                f"Install firmware {version} ({name}) on the Pico?\n"
                f"Now on the Pico: {now}.\n\n"
                "The Pico restarts. The chips in the sockets aren't touched."
            )
        if not self._ask(
            "Update firmware", message, yes="Install", no="Cancel", danger=version is None
        ):
            return False
        # Its steps, and a "hold BOOTSEL" request if the Pico can't restart itself,
        # show in the log, which the settings page hides
        self._close_settings()
        return self._start_job(
            "Updating firmware…",
            lambda: self._firmware_worker(path, release, version),
            title=f"Update firmware → {version or name}",
            key=("firmware",),
            needs_port=False,
        )

    def _firmware_worker(
        self, path: Optional[str], release: Optional[dict], version: Optional[str]
    ) -> Tuple[bool, str]:
        steps = (["Download"] if release else []) + [
            "Restart the Pico into its bootloader",
            "Copy the firmware",
            "Wait for the Pico",
            "Check the new firmware",
        ]
        n, total = iter(range(1, len(steps) + 1)), len(steps)
        if release:
            self._step(next(n), total, f"Download {release['name']}")
            path = self._download_release(release)
            got = read_uf2(path)["version"]
            if got != release["version"]:
                return False, (
                    f"The download holds firmware {got}, not {release['version']}."
                )
        self._step(next(n), total, "Restart the Pico into its bootloader")
        drive = self._enter_bootloader()
        self._step(next(n), total, f"Copy the firmware → {drive}")
        self._copy_uf2(path, drive)
        self._step(next(n), total, "Wait for the Pico to restart")
        port = self._wait_for_pico()
        if not port:
            return False, (
                "The Pico didn't come back as a HydraFlasher serial port – "
                "unplug it and plug it in again."
            )
        self._step(next(n), total, f"Check the new firmware on {port}")
        ident = self._read_fw_identity(port)
        if ident is None:
            return True, f"Firmware copied; {port} can't be opened to check it (sudo)."
        caps, name, cs_lines = ident
        self.after(0, self._firmware_installed, port, caps, name, cs_lines)
        got = fw_version(name)
        self._detail(f"the Pico reports: {name or 'no name'} · {cs_lines} CS lines")
        if version and got != version:
            return False, f"The Pico reports {name or 'older firmware'}, not {version}."
        return True, f"Firmware {got or name} installed · {cs_lines} CS lines"

    def _download_release(self, release: dict) -> str:
        """Download a release's .uf2 into the data folder (worker thread)."""
        folder = os.path.join(DATA_DIR, "firmware")
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, os.path.basename(release["name"]))
        if os.path.isfile(dst) and os.path.getsize(dst) == release.get("size"):
            self._detail(f"already downloaded: {dst}")
            return dst
        req = urllib.request.Request(release["url"], headers={"User-Agent": "HydraFlasher"})
        part = dst + ".part"
        with urllib.request.urlopen(req, timeout=30) as resp, open(part, "wb") as f:
            size = int(resp.headers.get("Content-Length") or release.get("size") or 0)
            done = 0
            while chunk := resp.read(64 * 1024):
                if self._abort_requested:
                    raise JobAborted()
                f.write(chunk)
                done += len(chunk)
                if size:
                    self.after(0, setattr, self, "_pct", min(100.0, done * 100 / size))
        os.replace(part, dst)
        self._detail(f"saved to {dst}")
        return dst

    def _enter_bootloader(self) -> str:
        """Restart the Pico into its bootloader and return the RPI-RP2 drive (worker)."""
        drive = find_rp2_drive()
        if drive:
            self._detail(f"the Pico is already in its bootloader: {drive}")
            return drive
        port, sent = self._job.get("port") or "", False
        if self._is_unresponsive(port):
            self._detail(f"the Pico on {port} doesn't answer: it can't restart itself")
        elif port and os.path.exists(port) or (port and self.is_windows):
            try:
                with self._programmer(port) as prog:
                    prog.bootsel()
                sent = True
                self._detail(f"restart command sent on {port}")
            except Exception as e:  # older firmware without 0x21, no access, …
                self._detail(f"couldn't restart it over {port}: {e}", "warning")
        if not sent:
            self._detail(
                "Hold the BOOTSEL button on the Pico while you plug it in again…",
                "warning",
            )
            self.after(0, self.update_status, "Hold BOOTSEL and plug the Pico in again…")
        deadline = time.monotonic() + (20 if sent else 120)
        while time.monotonic() < deadline:
            if self._abort_requested:
                raise JobAborted()
            drive = find_rp2_drive()
            if drive:
                self._detail(f"bootloader drive: {drive}")
                return drive
            time.sleep(0.5)
        raise ProgrammerError("the Pico's bootloader drive (RPI-RP2) didn't show up")

    def _copy_uf2(self, path: str, drive: str):
        dst = os.path.join(drive, "hydraflasher.uf2")  # the name doesn't matter
        try:
            with open(path, "rb") as fin, open(dst, "wb") as fout:
                shutil.copyfileobj(fin, fout, 64 * 1024)
                fout.flush()
                os.fsync(fout.fileno())
        except OSError as e:
            # The Pico restarts as soon as it has the last block, so the drive can
            # vanish before the file is closed; only a drive that's still there failed
            if find_rp2_drive(mount=False):
                raise ProgrammerError(f"copying to {drive} failed: {e}")
            self._detail("the drive left during the copy: the Pico is restarting")
        if hasattr(os, "sync"):
            os.sync()

    def _wait_for_pico(self, timeout: float = 20) -> Optional[str]:
        """The serial port of the restarted Pico (worker thread), None if it doesn't come."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._abort_requested:
                raise JobAborted()
            port = next(
                (p.device for p in serial.tools.list_ports.comports()
                 if p.vid == HYDRA_VID),
                None,
            )
            if port:
                return port
            time.sleep(0.5)
        return None

    def _read_fw_identity(self, port: str):
        """(command map, name, CS lines) of the firmware on port; None without access."""
        if not self._port_accessible(port):
            return None
        last = None
        for _ in range(10):  # the port takes a moment to accept commands
            try:
                with self._programmer(port) as prog:
                    caps = prog.command_map()
                    name = prog.pgm_name() if S_CMD_Q_PGMNAME in caps else ""
                    cs_lines = prog.num_cs() if S_CMD_Q_NUM_CS in caps else LEGACY_CS_PINS
                return caps, name, cs_lines
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise ProgrammerError(f"the new firmware doesn't answer on {port}: {last}")

    def _firmware_installed(self, port: str, caps: Set[int], name: str, cs_lines: int):
        if self.port_entry.get().strip() != port:
            self.port_entry.delete(0, "end")
            self.port_entry.insert(0, port)
        self._fw_caps, self._fw_name, self._fw_cs = caps, name, cs_lines
        self._refresh_sockets()
        self._refresh_settings()

    # ====================== Drag and drop ======================
    def _load_dnd(self) -> bool:
        """Load tkdnd into this Tk (optional tkinterdnd2 package)."""
        if not DND_AVAILABLE:
            return False
        try:
            getattr(TkinterDnD, "require", TkinterDnD._require)(self)
            return True
        except Exception as e:  # e.g. no tkdnd binary for this platform or Tcl version
            print(f"Drag and drop unavailable: {e}", file=sys.stderr)
            return False

    def _register_drop_targets(self):
        """An image dropped on a socket card is written to it, on the log it's verified
        against the target. tkdnd looks for the nearest registered parent of the widget
        under the pointer, so the rest of the window gets a hint instead."""
        if not self._dnd:
            return
        targets = [(card, cs) for cs, card in self.cards.items()]
        targets += [(self._log_panel, "verify"), (self._ui_root, None)]
        for widget, target in targets:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind(
                "<<DropEnter>>", lambda e, w=widget: self._drop_hover(w, True) or COPY
            )
            widget.dnd_bind("<<DropLeave>>", lambda e, w=widget: self._drop_hover(w, False))
            widget.dnd_bind(
                "<<Drop>>",
                lambda e, w=widget, t=target: self._on_drop(w, t, e.data) or COPY,
            )

    def _drop_hover(self, widget, hover: bool):
        if isinstance(widget, SocketCard):
            widget.set_drop_hover(hover)
        elif widget is self._log_panel:
            widget.configure(border_color=T["green"] if hover else T["border"])

    def _on_drop(self, widget, target, data: str):
        self._drop_hover(widget, False)
        # Not inside the tkdnd callback: the dialogs that follow would keep the file
        # manager's drag waiting until they close
        self.after_idle(self._handle_drop, list(self.tk.splitlist(data)), target)

    def _handle_drop(self, paths: List[str], target):
        """target: a socket (write), "verify" (verify the selected target) or None."""
        if len(paths) != 1 or not os.path.isfile(paths[0]):
            self.log_note("Drop a single image file.", "warning")
            return
        path = paths[0]
        if path.lower().endswith(".uf2"):  # Pico firmware, never an SPI flash image
            self._start_firmware_update(path=path)
            return
        if target is None:
            self.log_note(
                "Drop the image on a socket card to write it, "
                "or on the log to verify the target.",
                "info",
            )
            return
        if self._prod:
            self.log_note(
                "Production mode: the batch image is fixed, the drop is ignored.",
                "warning",
            )
            return
        self.last_dir = os.path.dirname(path)
        if target == "verify":
            self._verify_file(path)
            return
        self._select_target(target)
        if self._write_allowed():
            self._write_file(path)

    # ====================== Production mode ======================
    def _toggle_production(self):
        on = self.production_var.get()
        if on == bool(self._prod):
            return
        if not on:
            p = self._prod
            self._prod = None
            self.write_button.configure(text="write")
            self.log_note(
                f"Production mode off · {p['batch']} batch(es), ✓ {p['ok']} ✕ {p['fail']}",
                "soft",
            )
            self._update_footer()
            if self._sockets_pending:
                self._apply_auto_sockets()
            return
        if self.busy or self._queue:
            self.log_note("Wait for the running/queued jobs first.", "warning")
            self.production_var.set(False)
            return
        path = self._ask_open_file(
            "Select the production image",
            [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")],
        )
        if not path:
            self.production_var.set(False)
            return
        cs = self._selected_cs()
        target = self._target_label(cs)
        if not self._ask(
            "Production mode",
            f"Every batch will erase, write and verify\n{os.path.basename(path)}\n"
            f"to {target} WITHOUT asking again.\n\n"
            "Start a batch with ⏎ or the batch button.",
            yes="Start production",
            no="Cancel",
            danger=True,
        ):
            self.production_var.set(False)
            return
        self._add_recent_file(path)
        self._prod = {
            "path": path,
            "name": os.path.basename(path),
            "batch": 0,
            "ok": 0,
            "fail": 0,
        }
        self.write_button.configure(text="▶ run batch  ⏎")
        self.log_job(f"Production mode · {os.path.basename(path)} → {target}")
        self.log_detail(f"flash log: {FLASH_LOG}")
        self._update_footer()
        self._set_idle_status("Production · insert chips and press ⏎", T["accent"])

    def _run_batch(self):
        if not self._prod or self.busy:
            return
        path = self._prod["path"]
        if not os.path.isfile(path):
            self._alert("Production", f"Image not found:\n{path}", error=True)
            return
        cs = self._selected_cs()
        size = os.path.getsize(path)
        if self._one_by_one(cs):
            # Each socket on its own: one that doesn't fit the image fails alone
            sockets = [
                d["cs"] for d in self._detected_slots() if not d.get("unsupported")
            ]
            if not sockets:
                self._set_idle_status("No chips inserted", T["red"])
                return
            self._prod["batch"] += 1
            self._update_footer()
            if not self._start_write_each(path, sockets, production=True):
                self._prod["batch"] -= 1
                self._update_footer()
            return
        if cs == CS_ALL:
            detected = self._detected_slots()
            err = self._same_type_error(detected)
            if err:
                self.log_note(f"{err.splitlines()[0]}", "error")
                self._set_idle_status(err.splitlines()[0], T["red"])
                return
        else:
            detected = [d for d in [self.slots[cs]] if d and d["detected"]]
            if not detected:
                self._set_idle_status(f"No chip in {self._sock(cs)}", T["red"])
                return
        if detected[0]["size_kb"] and detected[0]["size_kb"] * 1024 != size:
            msg = (
                f"Image is {human_size(size)} but the chips are "
                f"{human_size(detected[0]['size_kb'] * 1024)} – pad/trim the image first."
            )
            self.log_note(f"{msg}", "error")
            self._set_idle_status(msg, T["red"])
            return
        self._prod["batch"] += 1
        self._update_footer()
        if cs == CS_ALL:
            started = self._start_write_all(
                path, self._slots_key(detected), production=True
            )
        else:
            started = self._start_write(path, cs, production=True)
        if not started:
            self._prod["batch"] -= 1
            self._update_footer()

    # ====================== Recent Files ======================
    def _show_recent_menu(self):
        if not self.recent_files:
            self._alert("Recent files", "No recent files yet.")
            return
        options = [
            f"{os.path.basename(p)}  ({trunc(os.path.dirname(p), 40)})"
            for p in self.recent_files
        ]
        idx = self._choose("Recent files", "", options + ["Clear list"])
        if idx is None:
            return
        if idx == len(self.recent_files):
            self._clear_recent_files()
        else:
            self._use_recent_file(self.recent_files[idx])

    def _use_recent_file(self, path: str):
        if not os.path.isfile(path):
            self._alert("Recent files", "File no longer exists.", error=True)
            if path in self.recent_files:
                self.recent_files.remove(path)
                self.save_config()
            return
        target = self.cs_display_var.get()
        idx = self._choose(
            os.path.basename(path),
            f"Target: {target}",
            ["Write to target", "Verify against target", "Cancel"],
        )
        if idx == 0:
            self._write_file(path)
        elif idx == 1:
            self._verify_file(path)

    def _clear_recent_files(self):
        self.recent_files.clear()
        self.save_config()
        self.log_note("Recent files list cleared", "dim")
        self._refresh_settings()

    def _add_recent_file(self, path: str):
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:10]
        self.save_config()

    def _print_welcome_message(self):
        home = os.path.expanduser("~")
        rows = [
            ("✻ Welcome to Hydra Flasher", "accent"),
            ("", "normal"),
            (f"  binary:    {self.flashrom_path.replace(home, '~')}", "dim"),
            (f"  config:    {CONFIG_FILE.replace(home, '~')}", "dim"),
            (f"  flash log: {FLASH_LOG.replace(home, '~')}", "dim"),
        ]
        width = max(len(t) for t, _ in rows) + 2
        top = "╭" + "─" * width
        # The right border sits on a tab stop exactly under the corner ╮, not after
        # padding characters: a glyph the log font lacks (✻) comes from a fallback
        # font with another width and would push that row's border out of line
        self.log.tag_config("welcome_box", tabs=(self.fonts["log"].measure(top),))
        self._box_column = len(top)  # copying turns the tab back into spaces
        chunks = [(top + "╮\n", "accent")]
        for text, tag in rows:
            chunks += [
                ("│ ", ("accent", "welcome_box")),
                (text, tag),
                ("\t│\n", "accent"),
            ]
        chunks += [("╰" + "─" * width + "╯\n", "accent")]
        self._log_insert(chunks)
        self.log_line(" Tips for getting started:", "normal")
        self.log_line(" 1. Insert chips – sockets are detected live", "dim")
        self.log_line(" 2. Back up the chip image(s) with read", "dim")
        tips = [
            "Write – with ALL, identical chips are flashed at once, then verified one by one"
            if self.has_all
            else "Write – blank check, write and verify run in one go",
            "Flashing many chips? Turn on production mode",
        ]
        if self.has_all:
            tips.append(
                "Busy? Start jobs on other sockets anyway – they queue up and run in turn"
            )
        if self._dnd:
            tips.append(
                "Drag an image onto a socket card to write it, onto this log to verify"
            )
        for n, tip in enumerate(tips, 3):
            self.log_line(f" {n}. {tip}", "dim")

    def _on_closing(self):
        if self.busy or self._queue:
            queued = f"\n{len(self._queue)} queued job(s) won't run." if self._queue else ""
            if not self._ask(
                "Operation running",
                "An operation is still running.\n"
                f"Quit anyway? This may leave chip(s) partially programmed.{queued}",
                yes="Quit",
                no="Keep running",
                danger=True,
            ):
                return
            self._queue.clear()
            self._abort_requested = True
            if self._proc:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
        else:
            self._set_led(LED["IDLE"], wait=True)  # before the process ends
        if self._log_after_id:
            self.after_cancel(self._log_after_id)
        self.save_config()
        self.destroy()


def acquire_instance_lock():
    """Lock file held for the app's lifetime (the OS drops it when the process ends,
    even on a crash). None if another instance holds it."""
    os.makedirs(DATA_DIR, exist_ok=True)
    f = open(os.path.join(DATA_DIR, "hydra_flasher.lock"), "a+")
    try:
        if platform.system() == "Windows":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def self_test(out_path: str) -> int:
    """Check that a packaged build (PyInstaller) has everything it needs: Tk with a
    display, customtkinter's theme files, the icons, the serial port listing, the
    optional packages, and that programs it starts get the system's libraries. Writes a JSON report to out_path; exit code 0 = good. Used by
    CI on the .exe and the AppImage (a windowed .exe has no console to print to)."""
    report = {"app": APP_VERSION, "python": sys.version.split()[0], "ok": False}
    try:
        app = ctk.CTk()  # loads customtkinter's theme (bundled data files)
        app.withdraw()
        report["tk"] = app.tk.call("info", "patchlevel")
        for size, data in APP_ICON_PNG.items():
            tkinter.PhotoImage(master=app, data=data)
        tkinter.PhotoImage(master=app, data=LOGO_PNG[1])
        ctk.CTkLabel(app, text="self test").pack()  # fonts, drawing engine
        app.update()
        report["ports"] = len(serial.tools.list_ports.comports())
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle and bundle in os.environ.get("LD_LIBRARY_PATH", ""):
            # kdialog, flashprog, notify-send… would load the bundled libraries
            raise RuntimeError("LD_LIBRARY_PATH for started programs points into the bundle")
        report["notifications"] = NOTIFICATIONS_AVAILABLE
        report["dnd"] = False
        if DND_AVAILABLE:
            getattr(TkinterDnD, "require", TkinterDnD._require)(app)
            report["dnd"] = True
        app.destroy()
        report["ok"] = True
    except Exception as e:  # anything missing from the bundle
        report["error"] = f"{type(e).__name__}: {e}"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(sys.argv[2]))
    # Two instances would both talk to the programmer and garble each other's commands
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "Hydra Flasher",
            "Hydra Flasher is already running.\n\n"
            "Use the open window – two instances would share the programmer "
            "and corrupt each other's commands.",
        )
        sys.exit(1)
    app = HydraUI()
    app.mainloop()
