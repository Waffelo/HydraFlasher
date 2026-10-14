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
import zlib
from contextlib import contextmanager
from tkinter import filedialog, messagebox
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import customtkinter as ctk
import serial
import serial.tools.list_ports

try:
    from plyer import notification

    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False

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
S_CMD_O_SPIOP = 0x13
S_CMD_S_SPI_FREQ = 0x14
S_CMD_S_PIN_STATE = 0x15
S_CMD_S_SPI_CS = 0x16
S_CMD_SET_LED = 0x20
S_CMD_CRC32 = 0x22

NUM_SLOTS = 4
CS_ALL = 6
CS_ALL_LABEL = "All chips (CS6)"
SPI_SPEEDS = ["4M", "8M", "12M", "16M", "20M", "24M", "30M", "36M", "40M", "48M"]
SCAN_INTERVAL_MS = 1000
SCAN_SPEED_HZ = 8_000_000
ERASE_BLOCK = 64 * 1024  # granularity of partial writes
BLANK_CHECK_PIECE = 1024 * 1024  # granularity of the blank check
PARTIAL_MAX_USED = 0.9  # above this fraction of used blocks, just write everything

# Must match the firmware's USB descriptors (usb_descriptors.c)
HYDRA_VID = 0xCAFE
PICO_VID = 0x2E8A

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
T = {
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
}
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
        "iVBORw0KGgoAAAANSUhEUgAAACwAAAAoCAYAAACFFRgXAAAJfklEQVR42sWZeXCU5RnAf+/7fbub"
        "bHaTTXZhE+4QE4SAQItccrSMPRBnQC6HHkxpZ+pfrVhG/aMdaqctjqVMtTO2ip0RtXa8sIj1aBXk"
        "GiyikHAYiATIQe5sjr33O97+kYQJkM1lnD4zO7P77fO932+f9znfFUopvgo5tHHxbbat3gcm29Bu"
        "WvaZiGG99251w2vPn6tvBZJKKXu464qvChjg4NoFK5ByH+DtvaYU4baE8dSTZ64+83FdWzsQV8OA"
        "+EqBAT5at2CVEnI/IPtet5VqDCVST/+1vHrPoWuh0FDBRwX4o/WLHtKl4+/LXj8a6W+rD65ftBdY"
        "29+9ChIp2/4kZdrlQNPJxvYXf3+yKq3LjArwgXULXxBCrEma1t6IYVc6NWJOqXl0TcyViLlCUDTU"
        "tRpiiYd+8F7Zq0Bnf1bXhwt37PtLc5e8fLRdCCH6LPY2sNmla1tcujbsH6xQqaSljulCjLGUivca"
        "c1Rc4uD6RVcUPHayqePc1OzMrNwM53IN8WMEU0a4QdanLR0bHz1y4WQudD34jRLHY4cqu7rdXJm3"
        "AO/YseORvhfi8XhNJBJpTbf6yroTL2i2PW60gjKJrLln7/ElgAU09wd5g0vU1tY+0fuhubmZU6dO"
        "Yds2mqZhWRZCCIQQ2LaNUwomF+UyK8c1xK2mPWZYH7k0WaxLMatfJdseNzvgzSxvDUe6bxlY9P5g"
        "AebMmknV1Wo8Hg8ZTge+9gbun5iN3zmwj6ZsxcetMS7jOnMpbK7+b1lZ6P375j8K/QO7JPqOJbfv"
        "vtAefTpqmZVCiArAUkpZaYEbGhooKyujrz/fHa9jRVBDqRiT3Q6y8nIHBA2lLA40RTjcEiViKoQQ"
        "d+Tk5BxctWrVe08lTI9VXx2XlpWZsGwspXBJyTi3g0Vjs0Mlmdr8OYHs5ScaO1cBjUBHj4vcGnSr"
        "V69WZWVlt3yxMC+Tn071DbrtCctmf32YfzdEsOC6Cw1VgsHg8a35sbW5Mmv1yn0nXgJcQDithcvL"
        "y8nKyiIWi91g4eakScpWOGX6h3/cFuOV6k46DAspJbIPqCZgktvB1ZjBYIlo01tnm4DdPb80lQ4W"
        "QFdKUVhYyOXLl4nFYgCUeJxsLclLC2siOGh6+Nw7httma+i6jpTyFr177WZ8yuBExOaVC9cwFANa"
        "vyevW4MGXTQaxbK69Rb7M9k8OSctbBc6/5EB2lxOsh0ONE3rFxagQuVzd+QqKzww+2sT2HWunvqE"
        "lVZ/KKIDFKsYM/0uZmd7mOpxplW+FknwiyOf0xpPXb/2w+njee2LRmJm/4aZuXIu+W4Xfmnzyxlj"
        "2XmhhStRAynlsHy9VyRAU0cn9+a7B4StjybYdhMswNwxOexcOp1ZAS96P7tS2R69/j5Ll2ydFsDn"
        "0K6nzxFZuLwjwWftCRb6M/tVCqdMHj5aQUsPbL7bRWnAS4kvi/GeDAKZTp5cXopSEDG6g7UpmqAx"
        "lmRqjvuGtXwOjYem+Xm8ooXkCKB1gGleJwvSwCpg52eXMSzF5ukTWDY+j8KbIK7nSAFeZ3ct8mc4"
        "mOH39qs3JcvJgyUB/nSxFcMwdCGEHOr0oUsh2Dw5h3TeVBGKsGKin+0Livvd8pHKjGwXv545llda"
        "rCzAJ4ToGqyPABDf+/p09ZMpOfy/xFJKxU37jX9eubZ9z9n6eiAykLXlvflZX/qhSkFVZ4wPalp5"
        "saKO587VUBGKDOleTQjhcWgb7goGvgl4eipdepcIZugjhvykqYMDta180thBOHXjbo7LymB6nift"
        "/e80JzgdFxS79S82BCRvVzW+2ZO1zCF1a0OfDuDDmlZeqqjjWiSRVu/587VoQrBsQh7um6aQynCS"
        "Vy+3IIQgUlDQukZLrd5/pbkNcI4qcFVnjD+fvsK5tvCguu1Jg52fVfFU2RXWz5vJ6sKxBIxuN+ky"
        "7D6ZRaiVr51qUd3vk4NNzkMGfvNSI8+erca0hzdSpSybsrBJrn8a7SePsGlcJnf4Mij0OLkaNVBK"
        "iZt6icEr3WDSHEtSF4njz3D2FzRDhj/VZfDbijbiSLaXBpme7erNw85hlebBZKzbxc/nFPLyd+fy"
        "+F23Xy8I64sL2DRteONdU8Lk8fPN7OsAK38ymqbZg3VoI/ZhIWB+vo87830cuxZiUUEuh+rahp1h"
        "mpMm71Q1IKVkzJgx9kD974gsfAs4sHR8HroU3DUuF3+GY0SpsacBSuu3H25cPOvounkFQghNCCFH"
        "lNZulkxd4+F5Rfzq+MW0Adm3jczSJTOzXd1zoi7IdVPy7n3zn7QUIaFUXAgpHYJiKcWdUog74ko7"
        "DGwEUkKIzi8NDHBn0MeupTP4zYlKQgmDn82Zwu6zNSSt7vQ1depUHLbJumAGy/w56DcGagB4MF3a"
        "l0JM+1HpxMl7ztfWAZEBgU0EnaaNZVrkObUBm5+ZAS/PfmsOe2s6WFPkp952cLwlQmlpKZMmTUJ1"
        "tWFYFmHDJtc5+HGWrQgdrg3d/7uTlRVAF2AAljiwbuEN+xg3bQ40R6kSbvJKSnE6nUgp0VD4jSiF"
        "8TYmJDuQg5x5xKWDDzLGE3a4sSyL06dP09nZiSZgwwQv3w56Bj42SKae3vCvU08AMaCjNzBvsPCZ"
        "sMnuymbCpo2UYWR9C16v95ZRJuAQ3ON3MMerp21LM22De6JXON2ZIpQwWJUNPr+PIo9zSBbWkL0N"
        "ut3z6o6HXgsfakvw/BctIMSQ560pbgcbJmYz3etktEUpYhWhyAN/Kq868selpdPW7v/0Q0CXAOWd"
        "SfZcagUh8Pl8BIPB3kMOsrOzAZg4cSIuV3fnV1RUhBCCmoTFG6lsdlW2cSlmjiqwELhn+D0vPbdi"
        "9tVsh/Z6T3D69A7D4q2IA6lpKKVQSiGEIBAIYBgGGRkZCCEwDIOcnBzC4TCpVIpgMEgoFMIwDDq8"
        "Y9hV1crsYC7zsgS3u2w8+sApPmraVCdsZnj0fs+LDUt9mrDsKhSdQhLvcQul/6OmC3Ly0XUd0zSJ"
        "RCLYtk1eXh51dXXk5ubicDhobGykoKAAXdepra1lypTu4+C6ujqKi4tJpVKUNYZoGT+eZyqu8MDi"
        "2X+YFW1sQlAsBTmWUvYl07HwdGOoqDqaok1343A62Z5J0qt1N+2WrWob46m//O1c3d4jdS2tQKKn"
        "bKveAiOWLFnyQZ8CNmr/0KxZs2bTtm3bWnsqlADkli1bvnPx4sWt18uslCwPuCMrtK5U2DQPP3Ls"
        "/L7KtngMiPf0xdbNHdz/AAG5S/manmigAAAAAElFTkSuQmCC"
    ),
}
_UNSCANNED = ("?", None)


def blend(c1: str, c2: str, t: float) -> str:
    """Mix colour c1 into c2 by factor t (0 → c2, 1 → c1)."""
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


class JobAborted(Exception):
    pass


class ProgrammerError(Exception):
    pass


class Programmer:
    """Direct serprog access for what flashprog can't do: live socket scan,
    unique IDs and the firmware's on-device CRC32 (S_CMD_CRC32)."""

    def __init__(self, port: str):
        self.ser = serial.Serial(port, SERIAL_BAUD, timeout=1, write_timeout=1)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _cmd(
        self, cmd: int, params: bytes = b"", nresp: int = 0, timeout: float = 1.0
    ) -> bytes:
        self.ser.timeout = timeout
        self.ser.write(bytes([cmd]) + params)
        ack = self.ser.read(1)
        if ack != bytes([S_ACK]):
            raise ProgrammerError(
                f"command 0x{cmd:02x}: {'NAK' if ack else 'no answer'}"
            )
        data = self.ser.read(nresp) if nresp else b""
        if len(data) != nresp:
            raise ProgrammerError(f"command 0x{cmd:02x}: short answer")
        return data

    def command_map(self) -> Set[int]:
        bits = int.from_bytes(self._cmd(S_CMD_Q_CMDMAP, nresp=32), "little")
        return {i for i in range(256) if bits >> i & 1}

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
        if jedec in (b"\xff\xff\xff", b"\x00\x00\x00"):
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
        "empty": ("#6e3b36", False),
        "ok": (T["green"], True),
        "warn": (T["yellow"], True),
        "pass": (T["green"], True),
        "fail": (T["red"], True),
        "busy": (T["accent"], True),
        "queued": (T["accent"], False),
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
        self._pulse = False
        self._pulse_id = None

        top = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
        top.pack(fill="x", padx=12, pady=(10, 2))
        self._led = tkinter.Canvas(
            top,
            width=18,
            height=18,
            bg=T["panel"],
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
        self._texts = ("", "")
        self._max_chars = (20, 22)
        self.bind("<Configure>", self._on_resize, add="+")
        self.set_state("unknown", "—", "not probed")

    def _on_resize(self, event):
        chars = tuple(
            max((event.width - 28) // max(self._fonts[f].measure("0"), 1), 6)
            for f in ("normal", "small")
        )
        if chars != self._max_chars:
            self._max_chars = chars
            self._apply_texts()

    def _apply_texts(self):
        name, detail = self._texts
        self._name_lbl.configure(text=trunc(name, self._max_chars[0]))
        self._detail_lbl.configure(text=trunc(detail, self._max_chars[1]))

    def set_selected(self, selected: bool):
        self.configure(border_color=T["accent"] if selected else T["border"])

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
        color, lit = self.STATES[self._state]
        self._led.delete("all")
        if lit:
            halo = blend(
                color,
                T["panel"],
                0.12 if (self._state == "busy" and self._pulse) else 0.32,
            )
            self._led.create_oval(1, 1, 17, 17, fill=halo, outline="")
        self._led.create_oval(5, 5, 13, 13, fill=color, outline="")


class HydraUI(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=T["bg"])
        self.title("Hydra Flasher")
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

        # NOTE: not "self.config" – that would shadow Tk's config() method
        self.cfg = self.load_config()
        self.last_dir = self.cfg.get("last_dir", os.path.expanduser("~"))
        self.recent_files: List[str] = self.cfg.get("recent_files", [])[:10]
        # JEDEC ID -> {"chips": [...], "size_kb": n}, learned from flashprog probes
        self.jedec_cache: Dict[str, dict] = self.cfg.get("jedec_cache", {})

        self.cs_map = {f"{i + 1} (CS{i})": i for i in range(NUM_SLOTS)}
        self.cs_map[CS_ALL_LABEL] = CS_ALL
        self.cs_labels = {v: k for k, v in self.cs_map.items()}

        cs_display = self.cfg.get("cs", "1 (CS0)")
        spispeed = self.cfg.get("spispeed", "16M")

        self.sudo_password: Optional[str] = None
        self.available_chips = ["Auto-detect"]
        # Per socket (index = CS number)
        self.slots: List[Optional[dict]] = [None] * NUM_SLOTS
        self._socket_result: List[Optional[str]] = [
            None
        ] * NUM_SLOTS  # "pass" / "fail" / None
        self._socket_ident: List[tuple] = [
            _UNSCANNED
        ] * NUM_SLOTS  # (jedec, uid) from live scan
        self._busy_sockets: set = set()

        # Live scan / direct programmer access
        self._port_lock = threading.Lock()
        self._fw_caps: Optional[Set[int]] = None
        self._scan_running = False
        self._scan_stable = 0
        self._log_lock = threading.Lock()

        # Production mode
        self._prod: Optional[dict] = None
        self._emptied_since_batch: Set[int] = set()

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
        self._progress_line_active = False
        self._in_step = False
        self._detail_first = False
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
        self.autostart_var = ctk.BooleanVar(value=self.cfg.get("autostart", False))
        self.spispeed_var = ctk.StringVar(
            value=spispeed if spispeed in SPI_SPEEDS else "16M"
        )
        self.cs_display_var = ctk.StringVar(
            value=cs_display if cs_display in self.cs_map else "1 (CS0)"
        )
        self.chip_override = ctk.StringVar(value="Auto-detect")

        self._log_buffer: List[Tuple[str, str]] = []
        self._log_after_id = None

        self._create_ui()

        self._print_welcome_message()
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
        for i in range(NUM_SLOTS):
            self.bind(f"<Alt-Key-{i + 1}>", lambda e, cs=i: self._select_target(cs))
        self.bind("<Alt-Key-a>", lambda e: self._select_target(CS_ALL))
        self.bind("<Control-l>", lambda e: self._clear_log())
        self.bind("<Control-q>", lambda e: self._on_closing())
        self.bind("<Escape>", lambda e: self.stop())
        # Clicking anywhere outside a text field releases focus so shortcuts work again
        self.bind("<Button-1>", self._drop_focus, add="+")

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
                "autostart": self.autostart_var.get(),
                "spispeed": self.spispeed_var.get(),
                "cs": self.cs_display_var.get(),
                "last_dir": self.last_dir,
                "recent_files": self.recent_files,
                "jedec_cache": self.jedec_cache,
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

    def _section(self, parent, text: str):
        ctk.CTkLabel(
            parent, text=text, font=self.fonts["small"], text_color=T["dim"], anchor="w"
        ).pack(fill="x", padx=4, pady=(12, 4))

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

        self._create_header(root)
        self._create_sockets(root)
        self._create_settings(root)
        self._create_actions(root)
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
        if self._logo_img:
            tkinter.Label(
                head, image=self._logo_img, bg=T["bg"], bd=0, highlightthickness=0
            ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            head,
            text="Hydra Flasher" if self._logo_img else "✻ Hydra Flasher",
            font=self.fonts["title"],
            text_color=T["accent"],
        ).pack(side="left")
        ctk.CTkLabel(
            head,
            text="  4× SOIC-16 · serprog",
            font=self.fonts["normal"],
            text_color=T["dim"],
        ).pack(side="left", pady=(3, 0))
        self.conn_label = ctk.CTkLabel(
            head, text="", font=self.fonts["normal"], text_color=T["dim"]
        )
        self.conn_label.pack(side="right")

    def _create_sockets(self, parent):
        self._section(
            parent, "sockets  · live · click to select target  (alt+1…4, alt+a)"
        )
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        self.cards = {}
        for i, cs in enumerate(list(range(NUM_SLOTS)) + [CS_ALL]):
            if cs == CS_ALL:
                card = SocketCard(
                    row, "ALL", "CS6", self.fonts, lambda: self._select_target(CS_ALL)
                )
            else:
                card = SocketCard(
                    row,
                    f"CS{cs}",
                    f"#{cs + 1}",
                    self.fonts,
                    lambda cs=cs: self._select_target(cs),
                )
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            row.grid_columnconfigure(i, weight=1, uniform="sock")
            self.cards[cs] = card
        self.cs_display_var.trace_add("write", lambda *a: self._refresh_selection())
        self._refresh_selection()

    def _create_settings(self, parent):
        self._section(parent, "settings")
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
        TtyToggle(
            row3, "production mode", self.production_var, self.fonts["normal"]
        ).pack(side="left", padx=(0, 18))
        TtyToggle(
            row3,
            "auto-start when all sockets refilled",
            self.autostart_var,
            self.fonts["normal"],
        ).pack(side="left", padx=(0, 18))
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
        for text, cmd, color, filled in buttons:
            btn = self._button(row, text, cmd, color=color, filled=filled)
            btn.pack(side="left", padx=(0, 8), expand=True, fill="x")
            self._op_buttons.append(btn)
        self.write_button = self._op_buttons[-1]
        self.stop_button = self._button(
            row, "■ stop", self.stop, color=T["red"], width=90
        )
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="left", expand=True, fill="x")

    def _create_status_line(self, parent):
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(fill="x", pady=(14, 6))
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
        for tag, color in LOG_COLORS.items():
            self.log.tag_config(tag, foreground=color, lmargin2=hang)
        self.log.configure(state="disabled")

    def _create_footer(self, parent):
        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.pack(fill="x", pady=(6, 0))
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
        target = self._selected_cs()
        tgt = "ALL (broadcast)" if target == CS_ALL else f"CS{target}"
        self.footer_right.configure(
            text=f"target {tgt} · {self.spispeed_var.get()}"
            + ("" if chip == "Auto-detect" else f" · -c {chip}"),
            text_color=T["faint"],
        )

    # ====================== Sockets ======================
    def _select_target(self, cs: int):
        self.cs_display_var.set(self.cs_labels[cs])

    def _refresh_selection(self):
        sel = self._selected_cs()
        for cs, card in self.cards.items():
            card.set_selected(cs == sel)
        if hasattr(self, "footer_right"):
            self._update_footer()

    def _refresh_sockets(self):
        queued: Dict[int, int] = {}  # socket -> position of its first queued job
        for i, entry in enumerate(self._queue):
            if entry["cs"] is not None:
                queued.setdefault(entry["cs"], i + 1)
        for cs in range(NUM_SLOTS):
            slot, res, card = self.slots[cs], self._socket_result[cs], self.cards[cs]
            name = slot["name"] if slot and slot["detected"] else "…"
            broadcast_busy = (
                CS_ALL in self._busy_sockets and slot is not None and slot["detected"]
            )
            if cs in self._busy_sockets or broadcast_busy:
                card.set_state("busy", name, "working…", T["accent"])
            elif cs in queued:
                card.set_state("queued", name, f"queued #{queued[cs]}", T["accent"])
            elif slot is None:
                card.set_state("unknown", "—", "not probed")
            elif not slot["detected"]:
                card.set_state("empty", "empty", "no chip detected")
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

        all_card = self.cards[CS_ALL]
        detected = self._detected_slots()
        if CS_ALL in self._busy_sockets:
            all_card.set_state(
                "busy", "broadcast", f"{len(detected)} chips · working…", T["accent"]
            )
        elif CS_ALL in queued:
            all_card.set_state(
                "queued", "broadcast", f"queued #{queued[CS_ALL]}", T["accent"]
            )
        elif all(s is None for s in self.slots):
            all_card.set_state("unknown", "broadcast", "probe to check")
        elif not detected:
            all_card.set_state("empty", "broadcast", "no chips")
        elif self._same_type_error(detected):
            all_card.set_state("fail", "broadcast", "chip types differ", T["red"])
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
        self.slots = [None] * NUM_SLOTS
        self._socket_result = [None] * NUM_SLOTS
        self._socket_ident = [_UNSCANNED] * NUM_SLOTS
        self._fw_caps = None
        self._refresh_sockets()

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
                            self.log_line(
                                f"{BULLET} Programmer connected on {cur}", "success"
                            )
                            self._set_led(LED["IDLE"])
                        else:
                            self.log_line(
                                f"{BULLET} Programmer disconnected", "warning"
                            )
                            self._reset_sockets()
                    self._port_connected = connected
                self.conn_label.configure(
                    text=f"● {cur}" if connected else "○ no programmer",
                    text_color=T["green"] if connected else T["red"],
                )
        except Exception:
            pass
        self.after(2000, self._watch_ports)

    # --- live socket scan ---------------------------------------------
    def _scan_tick(self):
        """Every second while idle: read the JEDEC/unique ID of every socket."""
        try:
            port = self.port_entry.get().strip()
            if (
                not self.busy
                and not self._scan_running
                and self._port_connected
                and port
                and self._port_accessible(port)
            ):
                self._scan_running = True
                threading.Thread(
                    target=self._scan_worker, args=(port,), daemon=True
                ).start()
        except Exception:
            self._scan_running = False
        self.after(SCAN_INTERVAL_MS, self._scan_tick)

    def _scan_worker(self, port: str):
        results = None
        if self._port_lock.acquire(blocking=False):
            try:
                prog = Programmer(port)
                try:
                    if self._fw_caps is None:
                        self._fw_caps = prog.command_map()
                    prog.set_speed(SCAN_SPEED_HZ)
                    results = [prog.identify(cs) for cs in range(NUM_SLOTS)]
                    prog.pins(False)
                finally:
                    prog.close()
            except Exception:
                results = None
            finally:
                self._port_lock.release()
        self.after(0, self._apply_scan, results)

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
        for cs, ident in enumerate(results):
            prev = self._socket_ident[cs]
            if ident == prev:
                continue
            changed = True
            self._socket_ident[cs] = ident
            self._socket_result[cs] = None
            slot = self._slot_from_ident(cs, ident)
            self.slots[cs] = slot
            if not ident[0]:
                self._emptied_since_batch.add(cs)
            if prev is _UNSCANNED:
                continue
            if ident[0]:
                uid = f" · uid {ident[1]}" if ident[1] else ""
                self.log_line(
                    f"{BULLET} CS{cs} chip inserted · {slot['name']}{uid}", "soft"
                )
            else:
                self.log_line(f"{BULLET} CS{cs} chip removed", "dim")
        if changed:
            self._scan_stable = 0
            self._refresh_sockets()
        else:
            self._scan_stable += 1
        self._maybe_autostart()

    def _store_slot(self, cs: int, info: dict):
        """Save flashprog's view of a socket, and teach the JEDEC cache its name."""
        jedec, uid = (
            self._socket_ident[cs]
            if self._socket_ident[cs] is not _UNSCANNED
            else (None, None)
        )
        info["jedec"], info["uid"] = jedec, uid
        if jedec and info["chips"]:
            self.jedec_cache[jedec] = {
                "chips": info["chips"],
                "size_kb": info["size_kb"],
            }
        self.slots[cs] = info

    # ====================== Logging & Helpers ======================
    # Log layout:
    #   ● Job title                    log_job()
    #     ⎿  1/4 Step                  log_step()
    #          detail / tool output    log_detail()
    #   ● ✓ result
    # Without steps, details hang directly under the job ("⎿" on the first one).
    def _log_insert(self, chunks: Sequence[Tuple[str, str]]):
        self.log.configure(state="normal")
        for text, tag in chunks:
            self.log.insert("end", text, tag)
        self._trim_log()
        self.log.configure(state="disabled")
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

    def log_step(self, text: str):
        self.log_line(f"  ⎿  {text}", "step")
        self._in_step = True

    def _detail_prefix(self) -> str:
        if self._in_step:
            return "       "
        prefix = "  ⎿  " if self._detail_first else "     "
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
        self.log.configure(state="normal")
        if self._progress_line_active:
            self.log.delete("end-2l", "end-1l")
        self.log.insert("end", text.rstrip() + "\n", "dim")
        self.log.configure(state="disabled")
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
        queued = f" · {len(self._queue)} queued" if self._queue else ""
        self.status_meta.configure(
            text=f"({fmt_duration(time.monotonic() - self._job_started)}{queued} · esc to stop)"
        )
        self._render_bar(self._pct)
        self.after(120, self._tick)

    def notify(self, title: str, message: str):
        if NOTIFICATIONS_AVAILABLE:
            try:
                notification.notify(
                    title=title, message=message, app_name="Hydra Flasher", timeout=6
                )
            except Exception:
                pass

    human_size = staticmethod(human_size)

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "chip"

    def _selected_cs(self) -> int:
        return self.cs_map[self.cs_display_var.get()]

    def _shortcut(self, fn: Callable):
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
    def _programmer(self) -> Iterator[Programmer]:
        """Exclusive direct access to the programmer (worker thread)."""
        with self._port_lock:
            prog = Programmer(self._job.get("port") or self.port_entry.get().strip())
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

    def _set_led(self, state: int):
        """Best-effort LED update; never prompts and never raises."""
        port = self.port_entry.get().strip()
        if not port or (not self.is_windows and not os.path.exists(port)):
            return
        if not self._port_lock.acquire(timeout=1.0):
            return
        try:
            if self._port_accessible(port):
                with serial.Serial(
                    port, SERIAL_BAUD, timeout=0.5, write_timeout=0.5
                ) as ser:
                    ser.write(bytes([S_CMD_SET_LED, state]))
                    ser.read(1)
            elif self.sudo_password:
                script = (
                    "import sys, serial\n"
                    "with serial.Serial(sys.argv[1], 115200, timeout=0.5) as s:\n"
                    "    s.write(bytes([0x20, int(sys.argv[2])])); s.read(1)\n"
                )
                subprocess.run(
                    [
                        "sudo",
                        "-S",
                        "-p",
                        "",
                        sys.executable,
                        "-c",
                        script,
                        port,
                        str(state),
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
            self.log_line(
                "⚠ No serial ports detected – plug in the programmer.", "warning"
            )
            return

        best = max(ports, key=lambda p: (self._score_port(p), p.device == prefer))
        if self._score_port(best) == 0:
            if prefer and any(p.device == prefer for p in ports):
                self.port_entry.delete(0, "end")
                self.port_entry.insert(0, prefer)
                self.log_line(f"{BULLET} Using saved port {prefer}", "info")
                return
            self.log_line(
                "⚠ No HydraFlasher/Pico detected – plug it in, it is picked up automatically.",
                "warning",
            )
            return

        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, best.device)
        self.log_line(
            f"{BULLET} Programmer: {best.device} ({best.description or 'no description'})",
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
                self.log_line("→ Cancelled (no sudo password)", "dim")
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
            self.log_line(
                f"{BULLET} Sudo password remembered for this session.", "info"
            )
            self.log_line(
                "  Tip: add yourself to the 'dialout' group (then log in again) to avoid sudo.",
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
    ) -> bool:
        """Run worker() in a background thread. worker returns (success, message).
        While another job runs, the job is queued instead (except in production mode)."""
        queue = self.busy or bool(self._queue)
        if queue and (production or self._prod):
            self.log_line("⚠ Another operation is still running.", "warning")
            return False
        port = self.port_entry.get().strip()
        if not port:
            self._alert(
                "No port", "Select the programmer's serial port first.", error=True
            )
            return False
        use_sudo = self._prepare_access(port)
        if use_sudo is None:
            return False
        if not self._pick_chip_definition(cs):
            return False

        # Snapshot settings so the worker thread never touches Tk variables.
        # A queued job keeps the settings from when it was started.
        job = {
            "port": port,
            "speed": self.spispeed_var.get(),
            "force": self.force_var.get(),
            "progress": self.progress_var.get(),
            "noverify": self.noverify_var.get(),
            "chip": self.chip_override.get(),
            "sudo": use_sudo,
            "retry": retry,
            "pico": self.fast_verify_var.get() and self._can_use_pico(port),
            "partial": self.partial_var.get() and self._can_use_pico(port),
            "production": production,
            "rows": [],
            "temp_files": [],
            "batch": self._prod["batch"] if (production and self._prod) else "",
        }
        entry = {
            "status": status,
            "worker": worker,
            "title": title or status.rstrip("…"),
            "job": job,
            "cs": cs,
        }
        if not queue:
            self._launch(entry)
            return True
        if self._retrying:  # a chip-definition retry goes ahead of the queue
            self._queue.insert(0, entry)
        else:
            self._queue.append(entry)
        self.log_line(
            f"{BULLET} Queued #{self._queue.index(entry) + 1}: {entry['title']}", "soft"
        )
        self._refresh_sockets()
        return True

    def _launch(self, entry: dict):
        worker = entry["worker"]
        self._job = entry["job"]
        # Queued for a chip inserted mid-job (definitions unknown back then): reuse the
        # chip picked since, if it fits the chip the between-jobs scan found
        cs, override = entry["cs"], self.chip_override.get()
        slot = self.slots[cs] if cs is not None and cs < NUM_SLOTS else None
        if (
            self._job["chip"] == "Auto-detect"
            and override != "Auto-detect"
            and slot
            and override in slot["chips"]
        ):
            self._job["chip"] = override
        self._job_output = []
        self._abort_requested = False
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
                target=self._scan_worker, args=(port,), daemon=True
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
            self.log_line(f"⚠ Cleared {len(self._queue)} queued job(s).", "warning")
        self._queue.clear()
        self._queue_gen += 1
        self._queue_wait_scan = None
        if not self.busy:
            self.stop_button.configure(state="disabled")
        self._refresh_sockets()

    def _finish_job(self, ok: bool, msg: str):
        self._report_job(ok, msg)
        self._queue_continue()

    def _report_job(self, ok: bool, msg: str):
        self.busy = False
        self._proc = None
        self._busy_sockets.clear()
        self._refresh_sockets()
        for btn in self._op_buttons:
            btn.configure(state="normal")
        if not self._queue:
            self.stop_button.configure(state="disabled")
        took = f"(took {fmt_duration(time.monotonic() - self._job_started)})"

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
            self._emptied_since_batch.clear()
            self._update_footer()
            msg = f"Batch #{self._prod['batch']}: {msg} · swap chips"

        first = msg.splitlines()[0]
        if ok:
            self.log_line(f"{BULLET} ✓ {msg}", "success")
            self._set_idle_status(trunc(first, 90), T["green"], took)
            self.notify("Success", msg)
            self._set_led(LED["SUCCESS"])
        else:
            self.log_line(f"{BULLET} ✕ {msg}", "error")
            self._set_idle_status(trunc(first, 90), T["red"], took)
            self.notify("Failed", msg)
            self._set_led(LED["FAILED"])
        self.save_config()

    def _build_command(
        self, extra: List[str], cs: int, noverify: bool = False
    ) -> List[str]:
        job = self._job
        cmd = [
            self.flashrom_path,
            "-p",
            f"serprog:dev={job['port']},spispeed={job['speed']},cs={cs}",
        ]
        cmd += extra
        if job["force"]:
            cmd.append("-f")
        if job["progress"]:
            cmd.append("--progress")
        if noverify:
            cmd.append("-n")
        if job["chip"] != "Auto-detect":
            cmd += ["-c", job["chip"]]
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
        if cs < NUM_SLOTS:
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
        targets = [cs] if cs < NUM_SLOTS else [d["cs"] for d in self._detected_slots()]
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
        self.log_line("⚠ Stopping...", "warning")
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

    def _pick_chip_definition(self, cs: Optional[int]) -> bool:
        """Several chip definitions match the target: pick one now rather than when the
        job runs, which for a queued job can be much later. False if cancelled."""
        if cs is None or self.chip_override.get() != "Auto-detect":
            return True
        slots = self._detected_slots() if cs == CS_ALL else [self.slots[cs]]
        chips = next((s["chips"] for s in slots if s and s["detected"]), [])
        if len(chips) < 2:
            return True
        self._update_chip_list(chips)
        where = "the chips" if cs == CS_ALL else f"the chip on CS{cs}"
        idx = self._choose(
            "Choose chip definition",
            f"Multiple flash chip definitions match {where}.\n"
            "Pick the one printed on the chip:",
            chips,
        )
        if idx is None:
            self.log_line("✕ Chip selection cancelled.", "error")
            return False
        self.chip_override.set(chips[idx])
        self.log_line(f"→ Selected chip: {chips[idx]}", "info")
        return True

    def _prompt_chip_selection(self, text: str, retry: Callable[[], None]):
        chips = []
        m = re.search(MULTI_MATCH + r".*?:\s*(.+)", text)
        if m:
            chips = re.findall(r'"([^"]+)"', m.group(1))
        if not chips:
            chips = [name for _, name, _ in FOUND_RE.findall(text)]
        chips = list(dict.fromkeys(chips))
        if not chips:
            self.log_line("✕ Could not parse chip names.", "error")
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
            self.log_line("✕ Chip selection cancelled.", "error")
            self._set_idle_status("Operation cancelled", T["red"])
            self._set_led(LED["FAILED"])
            return
        self.chip_override.set(chips[idx])
        self.log_line(f"→ Selected chip: {chips[idx]} – retrying", "info")
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
        slot = self.slots[cs] if cs < NUM_SLOTS else None
        ident = self._socket_ident[cs] if cs < NUM_SLOTS else _UNSCANNED
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
                self.after(0, self.update_progress, f"CS{cs}")
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
                        f"CS{cs}  ✓ match     crc {crc:08x}  {secs:5.1f}s", "success"
                    )
                else:
                    self._detail(
                        f"CS{cs}  ✕ MISMATCH  crc {crc:08x} ≠ {expected:08x}  {secs:5.1f}s",
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
            self.after(0, self.update_progress, f"CS{cs}")
            t0 = time.monotonic()
            rc, _ = self._exec(["-v", path], cs, quiet=True, show_cmd=False)
            secs = time.monotonic() - t0
            self._detail(
                f"CS{cs}  {'✓ match' if rc == 0 else '✕ MISMATCH'}  {secs:5.1f}s",
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
                self.after(0, self.update_progress, f"CS{cs}")
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
                        f"CS{cs}  {human_size(ranges_size(mine))} not blank → will be erased"
                        f"  {secs:5.1f}s",
                        "warning",
                    )
                else:
                    self._detail(f"CS{cs}  blank   {secs:5.1f}s", "success")
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
        chips = list(dict.fromkeys(name for _, name, _ in found))
        return {
            "cs": cs,
            "detected": bool(found),
            "chips": chips,
            "name": " / ".join(chips) if chips else "NOT DETECTED",
            "size_kb": int(found[0][2]) if found else None,
        }

    @staticmethod
    def _slots_key(slots: List[Optional[dict]]) -> tuple:
        """Which sockets are populated and with what size, used to detect swapped chips.
        (Chip names aren't part of it: they change when a chip override is picked.)"""
        return tuple((d["cs"], d["size_kb"]) for d in slots if d and d["detected"])

    def _detected_slots(self) -> List[dict]:
        return [d for d in self.slots if d and d["detected"]]

    def _same_type_error(self, detected: List[dict]) -> Optional[str]:
        if not detected:
            return "No chips detected. Insert chips or probe with ALL selected first."
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
                f"  • CS{d['cs']} → {d['name']} "
                f"({human_size(d['size_kb'] * 1024) if d['size_kb'] else 'unknown size'})"
                for d in detected
            )
            return (
                "The chips in the sockets are not the same type!\n\n"
                f"{chip_list}\n\n"
                "Broadcast (CS6) only works with identical chips.\n"
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
        self.log_line("→ Chip override cleared", "dim")

    def _probe_all_sockets(self) -> List[dict]:
        """Worker thread: probe every socket one by one, return the detected ones."""
        first_output = ""
        for cs in range(NUM_SLOTS):
            self.after(0, self.update_progress, f"CS{cs}")
            _, text = self._exec([], cs, silent=True)
            first_output = first_output or text
            self._store_slot(cs, self._parse_probe(text, cs))
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

    def _log_slot_table(self):
        for cs, d in enumerate(self.slots):
            if d is None:
                continue
            if d["detected"]:
                size = human_size(d["size_kb"] * 1024) if d["size_kb"] else "?"
                note = (
                    "  (ambiguous – pick a chip override)"
                    if len(d["chips"]) > 1
                    else ""
                )
                self.log_detail(f"CS{cs}  ● {d['name']}  {size}{note}", "success")
            else:
                self.log_detail(f"CS{cs}  ○ empty", "dim")

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
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
            except Exception:
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
                    self.log_line(f"→ Created truncated file: {adjusted}", "info")
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
                    self.log_line(f"→ Created padded file: {adjusted}", "info")
                    return adjusted
        except OSError as e:
            self._alert("Error", f"Could not create adjusted image:\n{e}", error=True)
            return None

        self.log_line("→ Operation cancelled due to size mismatch", "dim")
        return None

    def _require_probed(self, cs: int) -> Optional[dict]:
        slot = self.slots[cs] if cs < NUM_SLOTS else None
        if not slot or not slot["detected"] or not slot["size_kb"]:
            self._alert(
                "Probe required",
                f"No chip known on CS{cs}.\nInsert one or probe CS{cs} first.",
            )
            return None
        return slot

    def _queue_defers_check(self) -> bool:
        """A job queued now runs later: sockets aren't scanned while the bus is busy, so
        a chip inserted meanwhile isn't known yet. The job checks it when it starts."""
        return (self.busy or bool(self._queue)) and not self._prod

    def _socket_error(self, cs: int, size: int, what: str) -> Optional[str]:
        """Why an image of `size` bytes can't go to/against CS<cs> (worker thread)."""
        slot = self.slots[cs]
        if not slot or not slot["detected"]:
            return f"No chip on CS{cs} – nothing {what}."
        if slot["size_kb"] and slot["size_kb"] * 1024 != size:
            return (
                f"Image is {human_size(size)} but CS{cs} is "
                f"{human_size(slot['size_kb'] * 1024)} – nothing {what}."
            )
        return None

    def _require_same_type(self) -> Optional[List[dict]]:
        detected = self._detected_slots()
        err = self._same_type_error(detected)
        if err:
            self._alert("Cannot use broadcast", err, error=True)
            self.log_line(f"✕ {err.splitlines()[0]}", "error")
            return None
        return detected

    # ====================== Operations ======================
    def probe(self):
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
                return False, f"No chip detected on CS{cs}."
            if rc != 0:
                return False, f"Probe of CS{cs} failed."
            size = (
                human_size(slot["size_kb"] * 1024)
                if slot["size_kb"]
                else "unknown size"
            )
            return True, f"CS{cs}: {slot['name']} ({size})"

        self._start_job(
            f"Probing CS{cs}…",
            worker,
            retry=lambda: self._start_probe(cs),
            cs=cs,
            title=f"Probe CS{cs}",
        )

    def _start_probe_all(self):
        def worker():
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected in any socket."
            if self._same_type_error(detected):
                self._detail(
                    "⚠ different chip types – broadcast (CS6) write/erase is disabled",
                    "warning",
                )
            return (
                True,
                f"Probe complete — {len(detected)}/{NUM_SLOTS} sockets populated.",
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
                return False, f"Read of CS{cs} failed."
            return True, f"Dump saved → {os.path.basename(path)}"

        self._start_job(
            f"Reading CS{cs}…",
            worker,
            retry=lambda: self._start_read(path, cs),
            cs=cs,
            title=f"Read CS{cs} → {os.path.basename(path)}",
        )

    def read_all(self):
        folder = self._ask_directory("Select folder to save ALL chip dumps")
        if not folder:
            self.log_line("→ Read ALL cancelled (no folder selected)", "dim")
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
                self.after(0, self.update_progress, f"CS{d['cs']}")
                t0 = time.monotonic()
                rc, _ = self._exec(
                    ["-r", os.path.join(folder, filename)],
                    d["cs"],
                    quiet=True,
                    show_cmd=False,
                )
                secs = time.monotonic() - t0
                self._detail(
                    f"CS{d['cs']}  {'✓ saved' if rc == 0 else '✕ FAILED'}  {filename}  {secs:5.1f}s",
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
            cs=CS_ALL,
            title=f"Read all chips → {folder}",
        )

    def erase(self):
        cs = self._selected_cs()
        if cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            target = f"ALL {len(detected)} chips (broadcast)"
        else:
            target = f"the chip on CS{cs}"
        if not self._ask(
            "Erase",
            f"Erase {target}?\nThis is irreversible!",
            yes="Erase",
            no="Cancel",
            danger=True,
        ):
            return
        if cs == CS_ALL:
            self._start_erase_all(self._slots_key(detected))
        else:
            self._start_erase(cs)

    def _start_erase(self, cs: int):
        def worker():
            rc, _ = self._exec(["-E"], cs)
            return (
                (True, f"CS{cs} erased successfully.")
                if rc == 0
                else (False, f"Erase of CS{cs} failed.")
            )

        self._start_job(
            f"Erasing CS{cs}…",
            worker,
            retry=lambda: self._start_erase(cs),
            cs=cs,
            title=f"Erase CS{cs}",
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
        path = self._ask_open_file(
            "Select file to flash",
            [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")],
        )
        if path:
            self._write_file(path)

    def _write_file(self, path: str):
        self._add_recent_file(path)
        cs = self._selected_cs()
        if cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            chip_size = detected[0]["size_kb"] * 1024
            target = f"ALL {len(detected)} chips (CS{', CS'.join(str(d['cs']) for d in detected)})"
        elif self._queue_defers_check():
            chip_size, target = None, f"CS{cs} (checked when its turn comes)"
        else:
            slot = self._require_probed(cs)
            if not slot:
                return
            chip_size = slot["size_kb"] * 1024
            target = f"CS{cs} ({slot['name']})"

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
        if cs == CS_ALL:
            self._start_write_all(adjusted, self._slots_key(detected))
        else:
            self._start_write(adjusted, cs)

    def _start_write(self, path: str, cs: int, production: bool = False) -> bool:
        def worker():
            data = open(path, "rb").read()
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
                self._step(
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

            self._step(
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
                    return False, f"Write to CS{cs} failed."
            self._record(cs, "write", True, time.monotonic() - t0, path, crc)

            if pico_verify:
                self._step(next(n), len(steps), "Verify (on Pico)")
                rows = self._crc_verify(data, [slot], "verify", path)
                if not rows[0][2]:
                    return (
                        False,
                        f"CS{cs} does NOT match {os.path.basename(path)} after writing.",
                    )
            return True, f"Write completed → {os.path.basename(path)} (CS{cs})"

        return self._start_job(
            f"Writing CS{cs}…",
            worker,
            retry=lambda: self._start_write(path, cs, production),
            cs=cs,
            title=f"Write {os.path.basename(path)} → CS{cs}",
            production=production,
        )

    def _start_write_all(
        self, path: str, expected_key: tuple, production: bool = False
    ) -> bool:
        """Broadcast write: all chips are erased + programmed in parallel over CS6, but every
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
                    f"(CS{', CS'.join(str(r[0]) for r in failed)}).",
                )
            return True, f"Written and verified on {len(rows)} chip(s)."

        return self._start_job(
            "Writing all chips…",
            worker,
            retry=lambda: self._start_write_all(path, expected_key, production),
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
                return False, f"CS{cs} does NOT match {os.path.basename(path)}."
            return True, f"CS{cs} matches {os.path.basename(path)}"

        self._start_job(
            f"Verifying CS{cs}…",
            worker,
            retry=lambda: self._start_verify(path, cs),
            cs=cs,
            title=f"Verify CS{cs} against {os.path.basename(path)}",
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
                    f"(CS{', CS'.join(str(r[0]) for r in failed)}).",
                )
            return True, f"All {len(rows)} chip(s) match {os.path.basename(path)}."

        self._start_job(
            "Verifying all chips…",
            worker,
            retry=lambda: self._start_verify_all(path),
            cs=CS_ALL,
            title=f"Verify all chips against {os.path.basename(path)}",
        )

    # ====================== Production mode ======================
    def _toggle_production(self):
        on = self.production_var.get()
        if on == bool(self._prod):
            return
        if not on:
            p = self._prod
            self._prod = None
            self.write_button.configure(text="write")
            self.log_line(
                f"{BULLET} Production mode off · {p['batch']} batch(es), ✓ {p['ok']} ✕ {p['fail']}",
                "soft",
            )
            self._update_footer()
            return
        if self.busy or self._queue:
            self.log_line("⚠ Wait for the running/queued jobs first.", "warning")
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
        target = "ALL sockets (broadcast)" if cs == CS_ALL else f"CS{cs}"
        if not self._ask(
            "Production mode",
            f"Every batch will erase, write and verify\n{os.path.basename(path)}\n"
            f"to {target} WITHOUT asking again.\n\n"
            "Start a batch with ⏎ or the batch button"
            + (
                ", or automatically once all sockets are refilled."
                if self.autostart_var.get()
                else "."
            ),
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
        self._emptied_since_batch = set(range(NUM_SLOTS))
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
        if cs == CS_ALL:
            detected = self._detected_slots()
            err = self._same_type_error(detected)
            if err:
                self.log_line(f"✕ {err.splitlines()[0]}", "error")
                self._set_idle_status(err.splitlines()[0], T["red"])
                return
        else:
            detected = [d for d in [self.slots[cs]] if d and d["detected"]]
            if not detected:
                self._set_idle_status(f"No chip in CS{cs}", T["red"])
                return
        if detected[0]["size_kb"] and detected[0]["size_kb"] * 1024 != size:
            msg = (
                f"Image is {human_size(size)} but the chips are "
                f"{human_size(detected[0]['size_kb'] * 1024)} – pad/trim the image first."
            )
            self.log_line(f"✕ {msg}", "error")
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

    def _maybe_autostart(self):
        """Start the next batch once every socket was emptied and refilled."""
        if not (self._prod and self.autostart_var.get()) or self.busy:
            return
        filled = [
            cs
            for cs in range(NUM_SLOTS)
            if self._socket_ident[cs] is not _UNSCANNED and self._socket_ident[cs][0]
        ]
        if (
            len(filled) == NUM_SLOTS
            and self._emptied_since_batch >= set(range(NUM_SLOTS))
            and self._scan_stable >= 2
        ):
            self.log_line(f"{BULLET} All sockets refilled – starting batch", "soft")
            self._run_batch()

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
        self.log_line("→ Recent files list cleared", "dim")

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
        chunks = [("╭" + "─" * width + "╮\n", "accent")]
        for text, tag in rows:
            chunks += [
                ("│ ", "accent"),
                (text.ljust(width - 1), tag),
                ("│\n", "accent"),
            ]
        chunks += [("╰" + "─" * width + "╯\n", "accent")]
        self._log_insert(chunks)
        self.log_line(" Tips for getting started:", "normal")
        self.log_line(" 1. Insert chips – sockets are detected live", "dim")
        self.log_line(" 2. Back up the chip image(s) with read", "dim")
        self.log_line(
            " 3. Write – with ALL, identical chips are flashed at once, then verified one by one",
            "dim",
        )
        self.log_line(" 4. Flashing many chips? Turn on production mode", "dim")
        self.log_line(
            " 5. Busy? Start jobs on other sockets anyway – they queue up and run in turn",
            "dim",
        )

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
            self._set_led(LED["IDLE"])
        if self._log_after_id:
            self.after_cancel(self._log_after_id)
        self.save_config()
        self.destroy()


if __name__ == "__main__":
    app = HydraUI()
    app.mainloop()
