import customtkinter as ctk
import subprocess
import threading
import os
import platform
import json
import re
import shutil
import sys
import time
import tkinter
import tkinter.font
from tkinter import filedialog, messagebox
import serial.tools.list_ports
import serial
from typing import Callable, List, Optional, Sequence, Tuple

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
else:
    CONFIG_FILE = os.path.expanduser("~/.config/hydra_flasher.json")

SERIAL_BAUD = 115200
LED = {"IDLE": 0, "RUNNING": 1, "SUCCESS": 2, "FAILED": 3}
CMD_SET_LED = 0x20
CREATE_NO_WINDOW = 0x08000000 if platform.system() == "Windows" else 0
MAX_LOG_LINES = 5000

NUM_SLOTS = 4
CS_ALL = 6
CS_ALL_LABEL = "All chips (CS6)"
SPI_SPEEDS = ["4M", "8M", "12M", "16M", "20M", "24M", "30M", "36M", "40M", "48M"]

# Must match the firmware's USB descriptors (usb_descriptors.c)
HYDRA_VID = 0xCAFE
PICO_VID = 0x2E8A

FOUND_RE = re.compile(r'Found (.*?)\s*flash chip "([^"]+)" \((\d+) kB')
MULTI_MATCH = "Multiple flash chip definitions match"
NOISE_PATTERNS = [
    "flashprog is free software", "flashrom is free software", "Using clock_gettime",
    "serprog: Programmer name", "Warning: NAK to query operation buffer size",
    "get the source code at", "on Linux", "on Windows",
]

# ── Theme (Claude Code terminal look) ───────────────────────────────
T = {
    "bg": "#141413", "panel": "#1c1c1a", "panel_hi": "#262624",
    "border": "#34332f", "border_hi": "#57554e",
    "text": "#e8e6dc", "soft": "#b5b3a8", "dim": "#8b8981", "faint": "#55544f",
    "accent": "#d97757", "accent_hi": "#e8906f",
    "green": "#7ec27a", "red": "#e5655b", "yellow": "#e3b341",
}
LOG_COLORS = {"success": T["green"], "error": T["red"], "info": T["soft"], "warning": T["yellow"],
              "normal": T["text"], "dim": T["dim"], "accent": T["accent"]}
MONO_FONTS = ["JetBrains Mono", "JetBrainsMono Nerd Font Mono", "JetBrainsMono Nerd Font", "Fira Code",
              "Cascadia Mono", "Cascadia Code", "SF Mono", "Menlo", "Hack", "Consolas",
              "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono", "Courier New"]
SPINNER = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"]
BULLET = "⏺" if platform.system() == "Darwin" else "●"


def blend(c1: str, c2: str, t: float) -> str:
    """Mix colour c1 into c2 by factor t (0 → c2, 1 → c1)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(a, b))


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    return f"{sec}s" if sec < 60 else f"{sec // 60}m {sec % 60:02d}s"


class JobAborted(Exception):
    pass


class TtyToggle(ctk.CTkLabel):
    """Terminal style checkbox: [x] label"""

    def __init__(self, master, text: str, variable: ctk.BooleanVar, font):
        super().__init__(master, text="", font=font, cursor="hand2")
        self._toggle_text = text
        self._toggle_var = variable
        self.bind("<Button-1>", lambda e: self._toggle_var.set(not self._toggle_var.get()))
        variable.trace_add("write", lambda *a: self._render())
        self._render()

    def _render(self):
        on = self._toggle_var.get()
        self.configure(text=f"[{'x' if on else ' '}] {self._toggle_text}",
                       text_color=T["accent"] if on else T["dim"])


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
    }

    def __init__(self, master, title: str, tag: str, fonts: dict, on_click: Callable[[], None]):
        super().__init__(master, fg_color=T["panel"], border_color=T["border"], border_width=2,
                         corner_radius=8, cursor="hand2")
        self._state = "unknown"
        self._pulse = False
        self._pulse_id = None

        top = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
        top.pack(fill="x", padx=12, pady=(10, 2))
        self._led = tkinter.Canvas(top, width=18, height=18, bg=T["panel"], highlightthickness=0, cursor="hand2")
        self._led.pack(side="left")
        self._title_lbl = ctk.CTkLabel(top, text=title, font=fonts["bold"], text_color=T["text"], cursor="hand2")
        self._title_lbl.pack(side="left", padx=(8, 0))
        self._tag_lbl = ctk.CTkLabel(top, text=tag, font=fonts["small"], text_color=T["faint"], cursor="hand2")
        self._tag_lbl.pack(side="right")
        self._name_lbl = ctk.CTkLabel(self, text="", font=fonts["normal"], text_color=T["text"],
                                 anchor="w", cursor="hand2", height=18)
        self._name_lbl.pack(fill="x", padx=12)
        self._detail_lbl = ctk.CTkLabel(self, text="", font=fonts["small"], text_color=T["dim"],
                                   anchor="w", cursor="hand2", height=16)
        self._detail_lbl.pack(fill="x", padx=12, pady=(0, 10))

        for w in (self, top, self._led, self._title_lbl, self._tag_lbl, self._name_lbl, self._detail_lbl):
            w.bind("<Button-1>", lambda e: on_click())
        self._fonts = fonts
        self._texts = ("", "")
        self._max_chars = (20, 22)
        self.bind("<Configure>", self._on_resize, add="+")
        self.set_state("unknown", "—", "not probed")

    def _on_resize(self, event):
        chars = tuple(max((event.width - 28) // max(self._fonts[f].measure("0"), 1), 6)
                      for f in ("normal", "small"))
        if chars != self._max_chars:
            self._max_chars = chars
            self._apply_texts()

    def _apply_texts(self):
        name, detail = self._texts
        self._name_lbl.configure(text=trunc(name, self._max_chars[0]))
        self._detail_lbl.configure(text=trunc(detail, self._max_chars[1]))

    def set_selected(self, selected: bool):
        self.configure(border_color=T["accent"] if selected else T["border"])

    def set_state(self, state: str, name: str, detail: str, detail_color: Optional[str] = None):
        self._state = state
        self._texts = (name, detail)
        self._name_lbl.configure(text_color=T["text"] if state != "unknown" else T["faint"])
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
            halo = blend(color, T["panel"], 0.12 if (self._state == "busy" and self._pulse) else 0.32)
            self._led.create_oval(1, 1, 17, 17, fill=halo, outline="")
        self._led.create_oval(5, 5, 13, 13, fill=color, outline="")


class HydraUI(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=T["bg"])
        self.title("Hydra Flasher")
        self.geometry("1000x860")
        self.minsize(860, 680)

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

        self.cs_map = {f"{i + 1} (CS{i})": i for i in range(NUM_SLOTS)}
        self.cs_map[CS_ALL_LABEL] = CS_ALL
        self.cs_labels = {v: k for k, v in self.cs_map.items()}

        cs_display = self.cfg.get("cs", "1 (CS0)")
        spispeed = self.cfg.get("spispeed", "16M")

        self.sudo_password: Optional[str] = None
        self.available_chips = ["Auto-detect"]
        # Last probe result per socket (index = CS number)
        self.slots: List[Optional[dict]] = [None] * NUM_SLOTS
        self._socket_result: List[Optional[str]] = [None] * NUM_SLOTS   # "pass" / "fail" / None
        self._busy_sockets: set = set()

        # Job state
        self.busy = False
        self._job: dict = {}
        self._job_output: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._abort_requested = False
        self._progress_line_active = False
        self._out_first = False
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
        self.spispeed_var = ctk.StringVar(value=spispeed if spispeed in SPI_SPEEDS else "16M")
        self.cs_display_var = ctk.StringVar(value=cs_display if cs_display in self.cs_map else "1 (CS0)")
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

        shortcuts = {
            "<Control-p>": self.probe, "<Control-r>": self.read, "<Control-e>": self.erase,
            "<Control-w>": self.write, "<Control-v>": self.verify,
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
        self.cfg.update({
            "port": self.port_entry.get().strip(),
            "flashrom_path": self.flashrom_path,
            "force": self.force_var.get(),
            "progress": self.progress_var.get(),
            "noverify": self.noverify_var.get(),
            "spispeed": self.spispeed_var.get(),
            "cs": self.cs_display_var.get(),
            "last_dir": self.last_dir,
            "recent_files": self.recent_files,
        })
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
                "/usr/bin/flashprog", "/usr/bin/flashrom",
                "/usr/local/bin/flashprog", "/usr/local/bin/flashrom",
                os.path.expanduser("~/bin/flashprog"), os.path.expanduser("~/bin/flashrom"),
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
        filetypes = [("Executable", "*.exe" if self.is_windows else "*"), ("All files", "*.*")]
        path = self._ask_open_file(title, filetypes)
        return path if path and os.path.isfile(path) else None

    def _exit_no_binary(self):
        messagebox.showerror("Error", "No valid flashrom/flashprog binary found or selected.\nExiting.")
        self.destroy()
        sys.exit(1)

    # ====================== UI ======================
    def _panel(self, parent, **kw) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=T["panel"], border_color=T["border"],
                            border_width=1, corner_radius=8, **kw)

    def _section(self, parent, text: str):
        ctk.CTkLabel(parent, text=text, font=self.fonts["small"], text_color=T["dim"],
                     anchor="w").pack(fill="x", padx=4, pady=(12, 4))

    def _button(self, parent, text: str, command, color: Optional[str] = None,
                filled: bool = False, width: int = 110) -> ctk.CTkButton:
        color = color or T["text"]
        if filled:
            return ctk.CTkButton(parent, text=text, command=command, width=width, height=32,
                                 corner_radius=6, font=self.fonts["bold"], fg_color=color,
                                 hover_color=blend(color, "#ffffff", 0.85), text_color=T["bg"],
                                 text_color_disabled=blend(T["bg"], color, 0.5))
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=32,
                             corner_radius=6, font=self.fonts["normal"], fg_color="transparent",
                             border_width=1, border_color=blend(color, T["panel"], 0.45),
                             hover_color=T["panel_hi"], text_color=color,
                             text_color_disabled=T["faint"])

    def _combo(self, parent, values, variable, width) -> ctk.CTkComboBox:
        return ctk.CTkComboBox(
            parent, values=values, variable=variable, width=width, height=30, state="readonly",
            font=self.fonts["normal"], dropdown_font=self.fonts["normal"],
            fg_color=T["bg"], border_color=T["border"], border_width=1,
            button_color=T["border"], button_hover_color=T["border_hi"], text_color=T["text"],
            dropdown_fg_color=T["panel"], dropdown_hover_color=T["panel_hi"], dropdown_text_color=T["text"],
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
        ctk.CTkLabel(head, text="✻ Hydra Flasher", font=self.fonts["title"],
                     text_color=T["accent"]).pack(side="left")
        ctk.CTkLabel(head, text="  4× SOIC-16 · serprog", font=self.fonts["normal"],
                     text_color=T["dim"]).pack(side="left", pady=(3, 0))
        self.conn_label = ctk.CTkLabel(head, text="", font=self.fonts["normal"], text_color=T["dim"])
        self.conn_label.pack(side="right")

    def _create_sockets(self, parent):
        self._section(parent, "sockets  · click to select target  (alt+1…4, alt+a)")
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        self.cards = {}
        for i, cs in enumerate(list(range(NUM_SLOTS)) + [CS_ALL]):
            if cs == CS_ALL:
                card = SocketCard(row, "ALL", "CS6", self.fonts, lambda: self._select_target(CS_ALL))
            else:
                card = SocketCard(row, f"CS{cs}", f"#{cs + 1}", self.fonts,
                                  lambda cs=cs: self._select_target(cs))
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
            ctk.CTkLabel(p, text=text, font=self.fonts["normal"], text_color=T["dim"]).pack(side="left", padx=(0, 8))

        label(row1, "port")
        self.port_entry = ctk.CTkEntry(row1, width=190, height=30, font=self.fonts["normal"], corner_radius=6,
                                       fg_color=T["bg"], border_color=T["border"], border_width=1,
                                       text_color=T["text"], placeholder_text="/dev/ttyACM0 or COM4")
        self.port_entry.pack(side="left")
        self._button(row1, "↻", self._refresh_ports, width=34).pack(side="left", padx=(6, 22))
        label(row1, "speed")
        self._combo(row1, SPI_SPEEDS, self.spispeed_var, 90).pack(side="left", padx=(0, 22))
        label(row1, "chip")
        self.chip_combo = self._combo(row1, self.available_chips, self.chip_override, 240)
        self.chip_combo.pack(side="left", fill="x", expand=True)
        self._button(row1, "×", self._clear_chip_override, width=34).pack(side="left", padx=(6, 0))

        row2 = ctk.CTkFrame(panel, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(2, 10))
        for text, var in [("force -f", self.force_var), ("progress", self.progress_var),
                          ("skip verify -n", self.noverify_var)]:
            TtyToggle(row2, text, var, self.fonts["normal"]).pack(side="left", padx=(0, 22))
        self._button(row2, "recent files ▸", self._show_recent_menu, color=T["soft"],
                     width=130).pack(side="right")

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
        self.stop_button = self._button(row, "■ stop", self.stop, color=T["red"], width=90)
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="left", expand=True, fill="x")

    def _create_status_line(self, parent):
        line = ctk.CTkFrame(parent, fg_color="transparent")
        line.pack(fill="x", pady=(14, 6))
        self.spin_label = ctk.CTkLabel(line, text="●", width=18, font=self.fonts["bold"], text_color=T["dim"])
        self.spin_label.pack(side="left")
        self.status_label = ctk.CTkLabel(line, text="", font=self.fonts["normal"], text_color=T["text"])
        self.status_label.pack(side="left", padx=(6, 0))
        self.status_meta = ctk.CTkLabel(line, text="", font=self.fonts["normal"], text_color=T["dim"])
        self.status_meta.pack(side="left", padx=(8, 0))
        self.bar_pct = ctk.CTkLabel(line, text="", font=self.fonts["normal"], text_color=T["soft"], width=44)
        self.bar_pct.pack(side="right")
        self.bar_empty = ctk.CTkLabel(line, text="", font=self.fonts["normal"], text_color=T["faint"])
        self.bar_empty.pack(side="right")
        self.bar_fill = ctk.CTkLabel(line, text="", font=self.fonts["normal"], text_color=T["accent"])
        self.bar_fill.pack(side="right")

    def _create_log_area(self, parent):
        panel = self._panel(parent)
        panel.pack(fill="both", expand=True)
        self.log = ctk.CTkTextbox(panel, font=self.fonts["log"], wrap="word", fg_color=T["panel"],
                                  text_color=T["text"], border_width=0, corner_radius=8,
                                  scrollbar_button_color=T["border"], scrollbar_button_hover_color=T["border_hi"],
                                  activate_scrollbars=True)
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        for tag, color in LOG_COLORS.items():
            self.log.tag_config(tag, foreground=color)
        self.log.configure(state="disabled")

    def _create_footer(self, parent):
        foot = ctk.CTkFrame(parent, fg_color="transparent")
        foot.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(foot, text="^P probe · ^R read · ^V verify · ^E erase · ^W write · esc stop · ^L clear",
                     font=self.fonts["small"], text_color=T["faint"]).pack(side="left")
        self.footer_right = ctk.CTkLabel(foot, text="", font=self.fonts["small"], text_color=T["faint"])
        self.footer_right.pack(side="right")
        self.spispeed_var.trace_add("write", lambda *a: self._update_footer())
        self.chip_override.trace_add("write", lambda *a: self._update_footer())
        self._update_footer()

    def _update_footer(self):
        chip = self.chip_override.get()
        target = self._selected_cs()
        tgt = "ALL (broadcast)" if target == CS_ALL else f"CS{target}"
        self.footer_right.configure(
            text=f"target {tgt} · {self.spispeed_var.get()}" + ("" if chip == "Auto-detect" else f" · -c {chip}"))

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
        for cs in range(NUM_SLOTS):
            slot, res, card = self.slots[cs], self._socket_result[cs], self.cards[cs]
            broadcast_busy = CS_ALL in self._busy_sockets and slot is not None and slot["detected"]
            if cs in self._busy_sockets or broadcast_busy:
                card.set_state("busy", slot["name"] if slot and slot["detected"] else "…", "working…", T["accent"])
            elif slot is None:
                card.set_state("unknown", "—", "not probed")
            elif not slot["detected"]:
                card.set_state("empty", "empty", "no chip detected")
            else:
                size = self.human_size(slot["size_kb"] * 1024) if slot["size_kb"] else "?"
                if res == "pass":
                    card.set_state("pass", slot["name"], f"✓ verified · {size}", T["green"])
                elif res == "fail":
                    card.set_state("fail", slot["name"], f"✕ failed · {size}", T["red"])
                elif len(slot["chips"]) > 1 and self.chip_override.get() == "Auto-detect":
                    card.set_state("warn", slot["name"], f"{len(slot['chips'])} defs match · {size}", T["yellow"])
                else:
                    card.set_state("ok", slot["name"], size)

        all_card = self.cards[CS_ALL]
        detected = self._detected_slots()
        if CS_ALL in self._busy_sockets:
            all_card.set_state("busy", "broadcast", f"{len(detected)} chips · working…", T["accent"])
        elif all(s is None for s in self.slots):
            all_card.set_state("unknown", "broadcast", "probe to check")
        elif not detected:
            all_card.set_state("empty", "broadcast", "no chips")
        elif self._same_type_error(detected):
            all_card.set_state("fail", "broadcast", "chip types differ", T["red"])
        else:
            all_card.set_state("ok", "broadcast", f"{len(detected)} identical chip{'s' * (len(detected) > 1)}")

    def _mark_busy(self, cs: int, busy: bool):
        (self._busy_sockets.add if busy else self._busy_sockets.discard)(cs)
        self._refresh_sockets()

    def _watch_ports(self):
        """Poll for the programmer being plugged in / removed."""
        try:
            if not self.busy:
                ports = serial.tools.list_ports.comports()
                devices = {p.device for p in ports}
                cur = self.port_entry.get().strip()
                typing = self.focus_get() is not None and isinstance(self.focus_get(), tkinter.Entry)
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
                            self.log_line(f"{BULLET} Programmer connected on {cur}", "success")
                            self._set_led(LED["IDLE"])
                        else:
                            self.log_line(f"{BULLET} Programmer disconnected", "warning")
                            self.slots = [None] * NUM_SLOTS
                            self._socket_result = [None] * NUM_SLOTS
                            self._refresh_sockets()
                    self._port_connected = connected
                self.conn_label.configure(
                    text=f"● {cur}" if connected else "○ no programmer",
                    text_color=T["green"] if connected else T["red"])
        except Exception:
            pass
        self.after(2000, self._watch_ports)

    # ====================== Logging & Helpers ======================
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

    def log_cmd(self, text: str):
        self.log_line(f"{BULLET} {text}", "accent")
        self._out_first = True

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

    def _out_prefix(self) -> str:
        prefix = "  ⎿  " if self._out_first else "     "
        self._out_first = False
        return prefix

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
        self.spin_label.configure(text=SPINNER[self._spin_i % len(SPINNER)], text_color=T["accent"])
        sub = f" · {self._sub}" if self._sub else ""
        self.status_label.configure(text=f"{self._status}{sub}", text_color=T["accent"])
        self.status_meta.configure(text=f"({fmt_duration(time.monotonic() - self._job_started)} · esc to stop)")
        self._render_bar(self._pct)
        self.after(120, self._tick)

    def notify(self, title: str, message: str):
        if NOTIFICATIONS_AVAILABLE:
            try:
                notification.notify(title=title, message=message, app_name="Hydra Flasher", timeout=6)
            except Exception:
                pass

    @staticmethod
    def human_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 ** 2:
            return f"{size / 1024:.1f} KiB"
        return f"{size / (1024 ** 2):.1f} MiB"

    @staticmethod
    def _safe_name(name: str) -> str:
        return re.sub(r'[^A-Za-z0-9._-]+', "_", name).strip("_") or "chip"

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
    def _dialog_shell(self, title: str, color: str) -> Tuple[ctk.CTkToplevel, ctk.CTkFrame]:
        dlg = ctk.CTkToplevel(self, fg_color=T["bg"])
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        box = ctk.CTkFrame(dlg, fg_color=T["panel"], border_color=color, border_width=1, corner_radius=8)
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

    def _choose(self, title: str, message: str, options: List[str], danger: bool = False,
                default: int = 0) -> Optional[int]:
        """Claude-Code-like selection prompt. Returns chosen index or None (esc)."""
        color = T["red"] if danger else T["accent"]
        dlg, box = self._dialog_shell(title, color)
        ctk.CTkLabel(box, text=f"{'⚠' if danger else '✻'} {title}", font=self.fonts["bold"],
                     text_color=color, anchor="w").pack(fill="x", padx=18, pady=(14, 4))
        if message:
            ctk.CTkLabel(box, text=message, font=self.fonts["normal"], text_color=T["text"],
                         justify="left", anchor="w", wraplength=520).pack(fill="x", padx=18, pady=(2, 10))

        state = {"sel": default, "result": None}
        rows = []

        def render():
            for i, (lbl, text) in enumerate(zip(rows, options)):
                active = i == state["sel"]
                lbl.configure(text=f"{'❯' if active else ' '} {i + 1}. {text}",
                              text_color=color if active else T["text"])

        def pick(i):
            state["result"] = i
            dlg.destroy()

        def move(d):
            state["sel"] = (state["sel"] + d) % len(options)
            render()

        for i in range(len(options)):
            lbl = ctk.CTkLabel(box, text="", font=self.fonts["normal"], anchor="w", cursor="hand2", height=22)
            lbl.pack(fill="x", padx=18)
            lbl.bind("<Button-1>", lambda e, i=i: pick(i))
            lbl.bind("<Enter>", lambda e, i=i: (state.update(sel=i), render()))
            rows.append(lbl)
        render()
        ctk.CTkLabel(box, text="↑/↓ select · enter confirm · esc cancel", font=self.fonts["small"],
                     text_color=T["faint"], anchor="w").pack(fill="x", padx=18, pady=(10, 12))

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

    def _ask(self, title: str, message: str, yes: str = "Yes", no: str = "No", danger: bool = False) -> bool:
        return self._choose(title, message, [yes, no], danger=danger, default=1 if danger else 0) == 0

    def _alert(self, title: str, message: str, error: bool = False):
        self._choose(title, message, ["OK"], danger=error)

    def _ask_password(self) -> Optional[str]:
        dlg, box = self._dialog_shell("Sudo required", T["accent"])
        ctk.CTkLabel(box, text="✻ Sudo required", font=self.fonts["bold"], text_color=T["accent"],
                     anchor="w").pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(box, text="The serial port is not accessible for your user.\nEnter your sudo password:",
                     font=self.fonts["normal"], text_color=T["text"], justify="left",
                     anchor="w").pack(fill="x", padx=18, pady=(2, 8))
        row = ctk.CTkFrame(box, fg_color=T["bg"], border_color=T["border"], border_width=1, corner_radius=6)
        row.pack(fill="x", padx=18)
        ctk.CTkLabel(row, text=">", font=self.fonts["bold"], text_color=T["accent"]).pack(side="left", padx=(10, 4))
        entry = ctk.CTkEntry(row, show="•", width=320, font=self.fonts["normal"], border_width=0,
                             fg_color=T["bg"], text_color=T["text"])
        entry.pack(side="left", fill="x", expand=True, padx=(0, 6), pady=4)
        ctk.CTkLabel(box, text="enter confirm · esc cancel", font=self.fonts["small"],
                     text_color=T["faint"], anchor="w").pack(fill="x", padx=18, pady=(8, 12))
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

    def _set_led(self, state: int):
        """Best-effort LED update; never prompts and never raises."""
        port = self.port_entry.get().strip()
        if not port or (not self.is_windows and not os.path.exists(port)):
            return
        try:
            if self._port_accessible(port):
                with serial.Serial(port, SERIAL_BAUD, timeout=0.5, write_timeout=0.5) as ser:
                    ser.write(bytes([CMD_SET_LED, state]))
                    ser.read(1)
            elif self.sudo_password:
                script = ("import sys, serial\n"
                          "with serial.Serial(sys.argv[1], 115200, timeout=0.5) as s:\n"
                          "    s.write(bytes([0x20, int(sys.argv[2])])); s.read(1)\n")
                subprocess.run(["sudo", "-S", "-p", "", sys.executable, "-c", script, port, str(state)],
                               input=self.sudo_password + "\n", text=True,
                               capture_output=True, timeout=5)
        except Exception:
            pass

    @staticmethod
    def _score_port(p) -> int:
        text = " ".join(filter(None, [p.description, p.manufacturer, p.product, p.hwid])).lower()
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
            self.log_line("⚠ No serial ports detected – plug in the programmer.", "warning")
            return

        best = max(ports, key=lambda p: (self._score_port(p), p.device == prefer))
        if self._score_port(best) == 0:
            if prefer and any(p.device == prefer for p in ports):
                self.port_entry.delete(0, "end")
                self.port_entry.insert(0, prefer)
                self.log_line(f"{BULLET} Using saved port {prefer}", "info")
                return
            self.log_line("⚠ No HydraFlasher/Pico detected – plug it in, it is picked up automatically.", "warning")
            return

        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, best.device)
        self.log_line(f"{BULLET} Programmer: {best.device} ({best.description or 'no description'})",
                      "success" if self._score_port(best) >= 5 else "info")

    def _refresh_ports(self):
        ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
        self.log_cmd("Scan serial ports")
        for p in ports:
            self.log_line(f"{self._out_prefix()}{p.device}  {p.description or 'Unknown'}", "dim")
        if not ports:
            self.log_line(f"{self._out_prefix()}(none)", "dim")
        self._auto_select_port(prefer=self.port_entry.get().strip())

    # ====================== Sudo ======================
    def _prepare_access(self, port: str) -> Optional[bool]:
        """Returns whether sudo is needed for this port, or None if the job must not start."""
        if not self.is_windows and not os.path.exists(port):
            self._alert("Port not found", f"{port} does not exist.\nIs the programmer plugged in?", error=True)
            return None
        if self._port_accessible(port):
            return False
        if self.sudo_password is None:
            pw = self._ask_password()
            if not pw:
                self.log_line("→ Cancelled (no sudo password)", "dim")
                return None
            check = subprocess.run(["sudo", "-S", "-k", "-p", "", "-v"], input=pw + "\n",
                                   text=True, capture_output=True)
            if check.returncode != 0:
                self._alert("Sudo", "Wrong sudo password.", error=True)
                return None
            self.sudo_password = pw
            self.log_line(f"{BULLET} Sudo password remembered for this session.", "info")
            self.log_line("  Tip: add yourself to the 'dialout' group (then log in again) to avoid sudo.", "dim")
        return True

    # ====================== Job runner ======================
    def _start_job(self, status: str, worker: Callable[[], Tuple[bool, str]],
                   retry: Optional[Callable[[], None]] = None) -> bool:
        """Run worker() in a background thread. worker returns (success, message)."""
        if self.busy:
            self.log_line("⚠ Another operation is still running.", "warning")
            return False
        port = self.port_entry.get().strip()
        if not port:
            self._alert("No port", "Select the programmer's serial port first.", error=True)
            return False
        use_sudo = self._prepare_access(port)
        if use_sudo is None:
            return False

        # Snapshot settings so the worker thread never touches Tk variables
        self._job = {
            "port": port, "speed": self.spispeed_var.get(), "force": self.force_var.get(),
            "progress": self.progress_var.get(), "noverify": self.noverify_var.get(),
            "chip": self.chip_override.get(), "sudo": use_sudo, "retry": retry,
        }
        self._job_output = []
        self._abort_requested = False
        self.busy = True
        for btn in self._op_buttons:
            btn.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.save_config()
        self._set_led(LED["RUNNING"])
        self._status, self._sub, self._pct = status, "", None
        self._job_started = time.monotonic()
        self.log_line("")
        self._tick()

        def run():
            try:
                ok, msg = worker()
            except JobAborted:
                ok, msg = False, "Operation stopped by user."
            except Exception as e:
                ok, msg = False, f"Unexpected error: {e}"
            self.after(0, self._finish_job, ok, msg)

        threading.Thread(target=run, daemon=True).start()
        return True

    def _finish_job(self, ok: bool, msg: str):
        self.busy = False
        self._proc = None
        self._busy_sockets.clear()
        self._refresh_sockets()
        for btn in self._op_buttons:
            btn.configure(state="normal")
        self.stop_button.configure(state="disabled")
        took = f"(took {fmt_duration(time.monotonic() - self._job_started)})"

        text = "".join(self._job_output)
        retry = self._job.get("retry")
        if not ok and not self._abort_requested and MULTI_MATCH in text and retry:
            self._set_idle_status("Choose a chip definition", T["yellow"])
            self._prompt_chip_selection(text, retry)
            return

        first = msg.splitlines()[0]
        if ok:
            self.log_line(f"{BULLET} {msg}", "success")
            self._set_idle_status(trunc(first, 90), T["green"], took)
            self.notify("Success", msg)
            self._set_led(LED["SUCCESS"])
        else:
            self.log_line(f"{BULLET} ✕ {msg}", "error")
            self._set_idle_status(trunc(first, 90), T["red"], took)
            self.notify("Failed", msg)
            self._set_led(LED["FAILED"])

    def _build_command(self, extra: List[str], cs: int, noverify: bool = False) -> List[str]:
        job = self._job
        cmd = [self.flashrom_path, "-p", f"serprog:dev={job['port']},spispeed={job['speed']},cs={cs}"]
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

    def _exec(self, extra: List[str], cs: int, noverify: bool = False,
              quiet: bool = False, silent: bool = False) -> Tuple[int, str]:
        """Run flashrom/flashprog (worker thread only). Returns (returncode, output).
        quiet: hide banner noise, silent: don't stream output to the log at all."""
        if self._abort_requested:
            raise JobAborted()
        cmd = self._build_command(extra, cs, noverify)
        if not silent:
            shown = cmd[cmd.index("--") + 1:] if self._job["sudo"] else list(cmd)
            shown[0] = os.path.basename(shown[0])
            self.after(0, self.log_cmd, f"{'sudo ' if self._job['sudo'] else ''}{' '.join(shown)}")
        self.after(0, self._reset_progress, silent)
        self.after(0, self._mark_busy, cs, True)

        popen_kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "bufsize": 1,
                        "stdin": subprocess.PIPE if self._job["sudo"] else subprocess.DEVNULL}
        if self.is_windows:
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW

        output = []
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
            self._proc = proc
            if self._job["sudo"]:
                proc.stdin.write((self.sudo_password or "") + "\n")
                proc.stdin.close()
            for line in iter(proc.stdout.readline, ''):
                output.append(line)
                if silent or (quiet and any(p in line for p in NOISE_PATTERNS)):
                    continue
                self.after(0, self._handle_output_line, line)
            rc = proc.wait()
        finally:
            self._proc = None
            self.after(0, self._mark_busy, cs, False)

        text = "".join(output)
        self._job_output.append(text)
        if self._abort_requested:
            raise JobAborted()
        if self._job["sudo"] and ("incorrect password" in text.lower() or "sorry, try again" in text.lower()):
            self.sudo_password = None
        # Keep per-socket info fresh: every run on a single socket re-probes the chip
        if cs < NUM_SLOTS:
            info = self._parse_probe(text, cs)
            if info["detected"]:
                self.slots[cs] = info
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
        self.after(0, self.log_line, f"✻ Step {n}/{total} · {text}", "accent")

    def stop(self):
        if not self.busy:
            return
        if not self._ask("Stop operation",
                         "Stop the running operation?\n\n"
                         "Interrupting an erase or write leaves the chip(s) partially programmed.",
                         yes="Stop it", no="Keep running", danger=True):
            return
        self._abort_requested = True
        self.log_line("⚠ Stopping...", "warning")
        proc = self._proc
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass

    def _handle_output_line(self, line: str):
        line = line.rstrip()
        if not line:
            return
        percent = self._parse_progress(line)
        if percent is not None:
            if self._job.get("progress"):
                self._pct = percent
            self._log_progress(self._out_prefix() + line.strip())
            return
        lower = line.lower()
        if "error" in lower or "failed" in lower:
            tag = "error"
        elif "warning" in lower:
            tag = "warning"
        elif "found" in lower or "verified" in lower:
            tag = "success"
        else:
            tag = "dim"
        self.log_line(self._out_prefix() + line, tag)

    @staticmethod
    def _parse_progress(line: str) -> Optional[float]:
        if '%' not in line:
            return None
        m = re.search(r'(Reading|Erasing|Writing|Verifying).*?(\d+(?:\.\d+)?)\s*%', line, re.I)
        return float(m.group(2)) if m else None

    def _prompt_chip_selection(self, text: str, retry: Callable[[], None]):
        chips = []
        m = re.search(MULTI_MATCH + r'.*?:\s*(.+)', text)
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

        idx = self._choose("Choose chip definition",
                           "Multiple flash chip definitions match the detected chip(s).\n"
                           "Pick the one printed on the chip:", chips)
        if idx is None:
            self.log_line("✕ Chip selection cancelled.", "error")
            self._set_idle_status("Operation cancelled", T["red"])
            self._set_led(LED["FAILED"])
            return
        self.chip_override.set(chips[idx])
        self.log_line(f"→ Selected chip: {chips[idx]} – retrying", "info")
        retry()

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
            return "No chips detected. Probe with ALL selected first."
        if len({(frozenset(d["chips"]), d["size_kb"]) for d in detected}) > 1:
            chip_list = "\n".join(
                f"  • CS{d['cs']} → {d['name']} "
                f"({self.human_size(d['size_kb'] * 1024) if d['size_kb'] else 'unknown size'})"
                for d in detected
            )
            return ("The chips in the sockets are not the same type!\n\n"
                    f"{chip_list}\n\n"
                    "Broadcast (CS6) only works with identical chips.\n"
                    "Select a single socket instead.")
        return None

    def _update_chip_list(self, new_chips: List[str]):
        updated = ["Auto-detect"] + sorted(set(self.available_chips[1:]) | set(new_chips))
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
            self.slots[cs] = self._parse_probe(text, cs)
        detected = self._detected_slots()
        self.after(0, self._log_slot_table)
        if not detected:
            # Show why it failed (port busy, permission, no chips, ...)
            for line in first_output.splitlines():
                if line.strip() and not any(p in line for p in NOISE_PATTERNS):
                    self.after(0, self.log_line, "     " + line, "dim")
        self.after(0, self._update_chip_list, [c for d in detected for c in d["chips"]])
        self.after(0, self._refresh_sockets)
        return detected

    def _log_slot_table(self):
        self.log_cmd("Probe sockets")
        self._out_first = True
        for cs, d in enumerate(self.slots):
            if d is None:
                continue
            prefix = self._out_prefix()
            if d["detected"]:
                size = self.human_size(d["size_kb"] * 1024) if d["size_kb"] else "?"
                note = "  (ambiguous – pick a chip override)" if len(d["chips"]) > 1 else ""
                self.log_line(f"{prefix}CS{cs}  ● {d['name']}  {size}{note}", "success")
            else:
                self.log_line(f"{prefix}CS{cs}  ○ empty", "dim")

    def _log_summary(self, title: str, rows: List[Tuple[int, str, bool]]):
        def out():
            self.log_cmd(title)
            for cs, name, ok in rows:
                self.log_line(f"{self._out_prefix()}CS{cs}  {'✓ passed' if ok else '✕ FAILED'}  {name}",
                              "success" if ok else "error")
        self.after(0, out)

    def _reprobe_and_check(self, expected_key: tuple) -> Tuple[Optional[List[dict]], str]:
        """Worker thread: re-probe sockets and make sure nothing changed since the user confirmed."""
        detected = self._probe_all_sockets()
        err = self._same_type_error(detected)
        if err:
            return None, err.splitlines()[0]
        if self._slots_key(detected) != expected_key:
            return None, "Sockets changed since the last probe – check the log and confirm again."
        return detected, ""

    # ====================== File Dialogs ======================
    def _run_linux_dialog(self, commands: List[List[str]]) -> Tuple[bool, Optional[str]]:
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
            patterns = " ".join(p for _, exts in filetypes for p in exts.split() if p != "*.*")
            zenity = ["zenity", "--file-selection", f"--title={title}", f"--filename={initial}/"]
            if patterns and patterns != "*":
                zenity += [f"--file-filter={patterns}", "--file-filter=*"]
            shown, path = self._run_linux_dialog([zenity, ["kdialog", "--getopenfilename", initial, f"--title={title}"]])
        if not shown:
            path = filedialog.askopenfilename(title=title, filetypes=filetypes, initialdir=initial)
        if path:
            self.last_dir = os.path.dirname(path)
        return path or None

    def _ask_save_file(self, title: str, defaultextension: str, filetypes, initialfile=None):
        initial = self.last_dir
        path = None
        shown = False
        if self.is_linux:
            target = os.path.join(initial, initialfile or "")
            shown, path = self._run_linux_dialog([
                ["zenity", "--file-selection", "--save", f"--title={title}", f"--filename={target}"],
                ["kdialog", "--getsavefilename", target, f"--title={title}"],
            ])
        if not shown:
            path = filedialog.asksaveasfilename(title=title, defaultextension=defaultextension,
                                                filetypes=filetypes, initialdir=initial, initialfile=initialfile)
        if path:
            self.last_dir = os.path.dirname(path)
        return path or None

    def _ask_directory(self, title: str):
        initial = self.last_dir
        path = None
        shown = False
        if self.is_linux:
            shown, path = self._run_linux_dialog([
                ["zenity", "--file-selection", "--directory", f"--title={title}", f"--filename={initial}/"],
                ["kdialog", "--getexistingdirectory", initial, f"--title={title}"],
            ])
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
        sizes = f"File is {self.human_size(file_size)}, chip is {self.human_size(chip_size)}."
        try:
            if file_size > chip_size:
                if self._ask("Size mismatch", f"{sizes}\nTruncate the image to the chip size?",
                             yes=f"Truncate to {self.human_size(chip_size)}", no="Cancel"):
                    adjusted = f"{base}_truncated{ext}"
                    with open(path, 'rb') as fin, open(adjusted, 'wb') as fout:
                        fout.write(fin.read(chip_size))
                    self.log_line(f"→ Created truncated file: {adjusted}", "info")
                    return adjusted
            else:
                if self._ask("Size mismatch", f"{sizes}\nPad the image with 0xFF?",
                             yes=f"Pad to {self.human_size(chip_size)}", no="Cancel"):
                    adjusted = f"{base}_padded{ext}"
                    with open(path, 'rb') as fin, open(adjusted, 'wb') as fout:
                        fout.write(fin.read())
                        fout.write(b'\xFF' * (chip_size - file_size))
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
            self._alert("Probe required", f"Probe CS{cs} first so the chip size is known.")
            return None
        return slot

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
            self.slots[cs] = slot
            if not slot["detected"]:
                return False, f"No chip detected on CS{cs}."
            if rc != 0:
                return False, f"Probe of CS{cs} failed."
            size = self.human_size(slot["size_kb"] * 1024) if slot["size_kb"] else "unknown size"
            return True, f"CS{cs}: {slot['name']} ({size})"

        self._start_job(f"Probing CS{cs}…", worker, retry=lambda: self._start_probe(cs))

    def _start_probe_all(self):
        def worker():
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected in any socket."
            if self._same_type_error(detected):
                self.after(0, self.log_line, "⚠ Sockets contain different chip types – broadcast (CS6) "
                                             "write/erase is disabled until they match.", "warning")
            return True, f"Probe complete — {len(detected)}/{NUM_SLOTS} sockets populated."

        self._start_job("Probing sockets…", worker)

    def read(self):
        cs = self._selected_cs()
        if cs == CS_ALL:
            self.read_all()
            return
        slot = self.slots[cs]
        default_name = (f"{self._safe_name(slot['name'])}_CS{cs}.bin"
                        if slot and slot["detected"] else "flash_dump.bin")
        path = self._ask_save_file("Save dump as...", ".bin",
                                   [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")],
                                   initialfile=default_name)
        if path:
            self._start_read(path, cs)

    def _start_read(self, path: str, cs: int):
        def worker():
            rc, _ = self._exec(["-r", path], cs)
            if rc != 0:
                return False, f"Read of CS{cs} failed."
            return True, f"Dump saved → {os.path.basename(path)}"

        self._start_job(f"Reading CS{cs}…", worker, retry=lambda: self._start_read(path, cs))

    def read_all(self):
        folder = self._ask_directory("Select folder to save ALL chip dumps")
        if not folder:
            self.log_line("→ Read ALL cancelled (no folder selected)", "dim")
            return
        self._start_read_all(folder)

    def _start_read_all(self, folder: str):
        def worker():
            self._step(1, 2, "Probing sockets")
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected."
            self._step(2, 2, f"Reading {len(detected)} chip(s)")
            rows = []
            for d in detected:
                filename = f"{self._safe_name(d['name'])}_CS{d['cs']}.bin"
                self.after(0, self.update_progress, f"CS{d['cs']} → {filename}")
                rc, _ = self._exec(["-r", os.path.join(folder, filename)], d["cs"], quiet=True)
                rows.append((d["cs"], d["name"], rc == 0))
            self._log_summary("Read all · summary", rows)
            failed = [r for r in rows if not r[2]]
            if failed:
                return False, f"Read ALL: {len(failed)}/{len(rows)} chip(s) failed."
            return True, f"Read ALL: {len(rows)} dump(s) saved to {folder}"

        self._start_job("Reading all chips…", worker, retry=lambda: self._start_read_all(folder))

    def erase(self):
        cs = self._selected_cs()
        if cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            target = f"ALL {len(detected)} chips (broadcast)"
        else:
            target = f"the chip on CS{cs}"
        if not self._ask("Erase", f"Erase {target}?\nThis is irreversible!", yes="Erase", no="Cancel", danger=True):
            return
        if cs == CS_ALL:
            self._start_erase_all(self._slots_key(detected))
        else:
            self._start_erase(cs)

    def _start_erase(self, cs: int):
        def worker():
            rc, _ = self._exec(["-E"], cs)
            return (True, f"CS{cs} erased successfully.") if rc == 0 else (False, f"Erase of CS{cs} failed.")

        self._start_job(f"Erasing CS{cs}…", worker, retry=lambda: self._start_erase(cs))

    def _start_erase_all(self, expected_key: tuple):
        def worker():
            self._step(1, 2, "Re-probing sockets")
            detected, err = self._reprobe_and_check(expected_key)
            if detected is None:
                return False, err
            self._step(2, 2, f"Erasing {len(detected)} chip(s) (broadcast)")
            rc, _ = self._exec(["-E"], CS_ALL, quiet=True)
            if rc != 0:
                return False, "Broadcast erase failed."
            return True, f"All {len(detected)} chips erased."

        self._start_job("Erasing all chips…", worker, retry=lambda: self._start_erase_all(expected_key))

    def write(self):
        path = self._ask_open_file("Select file to flash", [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")])
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
        else:
            slot = self._require_probed(cs)
            if not slot:
                return
            chip_size = slot["size_kb"] * 1024
            target = f"CS{cs} ({slot['name']})"

        adjusted = self._adjust_file_size(path, chip_size)
        if not adjusted:
            return
        if not self._ask("Write", f"Flash {os.path.basename(adjusted)}\nto {target}?\n\n"
                                  "A wrong file can brick the device!", yes="Flash it", no="Cancel", danger=True):
            return
        if cs == CS_ALL:
            self._start_write_all(adjusted, self._slots_key(detected))
        else:
            self._start_write(adjusted, cs)

    def _start_write(self, path: str, cs: int):
        def worker():
            rc, _ = self._exec(["-w", path], cs, noverify=self._job["noverify"])
            if rc != 0:
                return False, f"Write to CS{cs} failed."
            return True, f"Write completed → {os.path.basename(path)} (CS{cs})"

        self._start_job(f"Writing CS{cs}…", worker, retry=lambda: self._start_write(path, cs))

    def _start_write_all(self, path: str, expected_key: tuple):
        """Broadcast write: all chips are erased + programmed in parallel over CS6, but every
        read during a broadcast sees all chips driving MISO at once. So the chips are erased
        first (making their contents identical, which flashprog's read-before-write relies on)
        and then verified one by one, never over the shared bus."""
        def worker():
            verify = not self._job["noverify"]
            total = 4 if verify else 3
            self._step(1, total, "Re-probing sockets")
            detected, err = self._reprobe_and_check(expected_key)
            if detected is None:
                return False, err

            self._step(2, total, f"Erasing {len(detected)} chip(s) (broadcast)")
            rc, _ = self._exec(["-E"], CS_ALL, quiet=True)
            if rc != 0:
                return False, "Broadcast erase failed – nothing was written."

            self._step(3, total, f"Writing {len(detected)} chip(s) (broadcast)")
            rc, _ = self._exec(["-w", path], CS_ALL, noverify=True, quiet=True)
            if rc != 0:
                return False, "Broadcast write failed."
            if not verify:
                self.after(0, self.log_line, "⚠ 'skip verify' is on – chips were NOT verified.", "warning")
                return True, f"Broadcast write completed → {os.path.basename(path)} (unverified)"

            self._step(4, total, f"Verifying {len(detected)} chip(s) individually")
            rows = self._verify_each(path, detected)
            self._log_summary("Write all · verify summary", rows)
            failed = [r for r in rows if not r[2]]
            if failed:
                return False, f"Write ALL: {len(failed)}/{len(rows)} chip(s) failed verification " \
                              f"(CS{', CS'.join(str(r[0]) for r in failed)})."
            return True, f"Write ALL completed and verified on {len(rows)} chip(s)."

        self._start_job("Writing all chips…", worker, retry=lambda: self._start_write_all(path, expected_key))

    def _verify_each(self, path: str, detected: List[dict]) -> List[Tuple[int, str, bool]]:
        rows = []
        for d in detected:
            self.after(0, self.update_progress, f"CS{d['cs']}")
            rc, _ = self._exec(["-v", path], d["cs"], quiet=True)
            rows.append((d["cs"], d["name"], rc == 0))
        return rows

    def verify(self):
        path = self._ask_open_file("Select file to verify", [("Binary ROM", "*.bin *.rom"), ("All files", "*.*")])
        if path:
            self._verify_file(path)

    def _verify_file(self, path: str):
        cs = self._selected_cs()
        if cs == CS_ALL:
            detected = self._require_same_type()
            if not detected:
                return
            chip_size = detected[0]["size_kb"] * 1024
        else:
            slot = self._require_probed(cs)
            if not slot:
                return
            chip_size = slot["size_kb"] * 1024
        adjusted = self._adjust_file_size(path, chip_size)
        if not adjusted:
            return
        if cs == CS_ALL:
            self._start_verify_all(adjusted)
        else:
            self._start_verify(adjusted, cs)

    def _start_verify(self, path: str, cs: int):
        def worker():
            rc, _ = self._exec(["-v", path], cs)
            if rc != 0:
                return False, f"CS{cs} does NOT match {os.path.basename(path)}."
            return True, f"CS{cs} matches {os.path.basename(path)}"

        self._start_job(f"Verifying CS{cs}…", worker, retry=lambda: self._start_verify(path, cs))

    def _start_verify_all(self, path: str):
        def worker():
            self._step(1, 2, "Probing sockets")
            detected = self._probe_all_sockets()
            if not detected:
                return False, "No chips detected."
            self._step(2, 2, f"Verifying {len(detected)} chip(s) against {os.path.basename(path)}")
            rows = self._verify_each(path, detected)
            self._log_summary("Verify all · summary", rows)
            failed = [r for r in rows if not r[2]]
            if failed:
                return False, f"Verify ALL: {len(failed)}/{len(rows)} chip(s) do NOT match " \
                              f"(CS{', CS'.join(str(r[0]) for r in failed)})."
            return True, f"Verify ALL: all {len(rows)} chip(s) match."

        self._start_job("Verifying all chips…", worker, retry=lambda: self._start_verify_all(path))

    # ====================== Recent Files ======================
    def _show_recent_menu(self):
        if not self.recent_files:
            self._alert("Recent files", "No recent files yet.")
            return
        options = [f"{os.path.basename(p)}  ({trunc(os.path.dirname(p), 40)})" for p in self.recent_files]
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
        idx = self._choose(os.path.basename(path), f"Target: {target}",
                           ["Write to target", "Verify against target", "Cancel"])
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
            (f"  binary: {self.flashrom_path.replace(home, '~')}", "dim"),
            (f"  config: {CONFIG_FILE.replace(home, '~')}", "dim"),
        ]
        width = max(len(t) for t, _ in rows) + 2
        chunks = [("╭" + "─" * width + "╮\n", "accent")]
        for text, tag in rows:
            chunks += [("│ ", "accent"), (text.ljust(width - 1), tag), ("│\n", "accent")]
        chunks += [("╰" + "─" * width + "╯\n", "accent")]
        self._log_insert(chunks)
        self.log_line("")
        self.log_line(" Tips for getting started:", "normal")
        self.log_line(" 1. Probe with ALL selected to scan every socket", "dim")
        self.log_line(" 2. Back up the chip image(s) with read", "dim")
        self.log_line(" 3. Write – with ALL, identical chips are flashed at once, then verified one by one", "dim")
        self.log_line("")

    def _on_closing(self):
        if self.busy:
            if not self._ask("Operation running",
                             "An operation is still running.\n"
                             "Quit anyway? This may leave chip(s) partially programmed.",
                             yes="Quit", no="Keep running", danger=True):
                return
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
