"""
MY-136 Pressure / Level Sensor Monitor
Modbus TCP — RS485-to-Ethernet converter
"""

import json
import tkinter as tk
from tkinter import messagebox, ttk
import threading
from pathlib import Path
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# --- Constants ---
DEFAULT_IP         = "192.168.1.1"
DEFAULT_PORT       = 502
DEFAULT_SLAVE_ID   = 1
DEFAULT_REFRESH_MS = 1000

CONFIG_FILE = Path(__file__).parent / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(data: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

# Register addresses (0-based)
REG_SLAVE_ID      = 0  # Address 0: Slave ID (read only)
REG_BAUD_RATE     = 1  # Address 1: baud rate (read only)
REG_PRESSURE_UNIT = 2  # Address 2: pressure unit (read/write)
REG_DECIMAL_PT    = 3  # Address 3: decimal point position (read/write)
REG_VALUE         = 4  # Address 4: measured value (read only)

PRESSURE_UNITS   = ["MPa", "kPa", "Pa", "Bar", "mBar", "kg/cm²", "psi", "mH₂O", "mmH₂O"]
DECIMAL_POINTS   = ["/1 (×1)", "/10 (×0.1)", "/100 (×0.01)", "/1000 (×0.001)"]
DECIMAL_DIVISORS = [1, 10, 100, 1000]
BAUD_RATES       = {0: "1200", 1: "2400", 2: "4800", 3: "9600", 4: "19200", 5: "38400"}

LEVEL_MAX_MM = 2000  # range for mmH₂O tank visualization

# --- Translations ---
STRINGS = {
    "en": {
        "title":            "MY-136 Pressure / Level Sensor Monitor — Modbus TCP",
        "conn_frame":       "Connection",
        "lbl_refresh":      "Refresh (ms):",
        "btn_connect":      "Connect",
        "btn_disconnect":   "Disconnect",
        "status_disconn":   "Disconnected",
        "status_conn":      "Connected",
        "status_connecting":"Connecting…",
        "status_error":     "Error",
        "info_frame":       "Sensor Info  (read only)",
        "lbl_slaveid":      "Slave ID:",
        "lbl_baud":         "Baud rate:",
        "lbl_unit_disp":    "Pressure unit:",
        "lbl_decimal_disp": "Decimal point:",
        "value_frame":      "Measured Value",
        "settings_frame":   "Settings  (read / write)",
        "lbl_unit":         "Pressure unit:",
        "lbl_decimal":      "Decimal point:",
        "btn_write":        "Write",
        "tank_frame":       "Water Level  (mmH₂O  0 – 2000 mm)",
        "ready":            "Ready.",
        "disconnected":     "Disconnected.",
        "err_port":         "Port must be an integer.",
        "err_port_title":   "Invalid input",
        "err_conn_title":   "Connection Error",
        "err_prefix":       "Error",
        "write_ok":         "Write OK",
        "not_connected":    "Not connected.",
        "sb_connected":     "Connected",
        "sb_slave":         "Slave ID",
        "sb_reads":         "Reads",
        "sb_errors":        "Errors",
        "conn_error":       "Connection error",
    },
    "sk": {
        "title":            "MY-136 Monitor tlaku / hladiny — Modbus TCP",
        "conn_frame":       "Pripojenie",
        "lbl_refresh":      "Obnova (ms):",
        "btn_connect":      "Pripojiť",
        "btn_disconnect":   "Odpojiť",
        "status_disconn":   "Odpojený",
        "status_conn":      "Pripojený",
        "status_connecting":"Pripájam…",
        "status_error":     "Chyba",
        "info_frame":       "Info o snímači  (iba čítanie)",
        "lbl_slaveid":      "Slave ID:",
        "lbl_baud":         "Prenosová rýchlosť:",
        "lbl_unit_disp":    "Jednotka tlaku:",
        "lbl_decimal_disp": "Desatinná čiarka:",
        "value_frame":      "Nameraná hodnota",
        "settings_frame":   "Nastavenia  (čítanie / zápis)",
        "lbl_unit":         "Jednotka tlaku:",
        "lbl_decimal":      "Desatinná čiarka:",
        "btn_write":        "Zapísať",
        "tank_frame":       "Hladina vody  (mmH₂O  0 – 2000 mm)",
        "ready":            "Pripravený.",
        "disconnected":     "Odpojený.",
        "err_port":         "Port musí byť celé číslo.",
        "err_port_title":   "Neplatný vstup",
        "err_conn_title":   "Chyba pripojenia",
        "err_prefix":       "Chyba",
        "write_ok":         "Zápis OK",
        "not_connected":    "Nie je pripojené.",
        "sb_connected":     "Pripojený",
        "sb_slave":         "Slave ID",
        "sb_reads":         "Čítaní",
        "sb_errors":        "Chýb",
        "conn_error":       "Chyba pripojenia",
    },
}

# Colors — same dark theme as Waveshare monitor
COLOR_BG             = "#1e1e2e"
COLOR_PANEL          = "#2a2a3e"
COLOR_TEXT           = "#e0e0e0"
COLOR_LABEL          = "#90caf9"
COLOR_CONNECTED      = "#00c853"
COLOR_DISCONNECTED   = "#f44336"
COLOR_ERROR          = "#f44336"
COLOR_BUTTON_CONNECT = "#1565c0"
COLOR_BUTTON_DISC    = "#b71c1c"
COLOR_BUTTON_WRITE   = "#1b5e20"
COLOR_BUTTON_LANG    = "#37474f"
COLOR_ENTRY          = "#3a3a52"
COLOR_WATER          = "#1e88e5"
COLOR_WATER_LIGHT    = "#42a5f5"
COLOR_WATER_ALARM    = "#c62828"
COLOR_WATER_ALARM_LT = "#ef9a9a"
COLOR_LIMIT_MAX      = "#f44336"
COLOR_LIMIT_MIN      = "#ff9800"
COLOR_TANK_WALL      = "#546e7a"
COLOR_TANK_EMPTY     = "#1a2a35"
COLOR_VALUE_TEXT     = "#80deea"


class LevelSensorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.configure(bg=COLOR_BG)
        self.root.resizable(False, False)

        self.client = None
        self.connected = False
        self.slave_id = DEFAULT_SLAVE_ID
        self.refresh_job = None
        self.lock = threading.Lock()

        self.read_count = 0
        self.error_count = 0
        self.current_value_raw = 0
        self.current_decimal   = 0
        self.current_unit_idx  = 8  # default mmH₂O
        self._initial_sync_done = False

        self.lang = "sk"  # default language

        self._cfg = _load_config()
        self._build_ui()
        self._apply_language()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Language
    # ------------------------------------------------------------------

    def _t(self, key):
        return STRINGS[self.lang][key]

    def _toggle_lang(self):
        self.lang = "en" if self.lang == "sk" else "sk"
        self.lang_btn.config(text="SK" if self.lang == "en" else "EN")
        self._apply_language()

    def _apply_language(self):
        t = STRINGS[self.lang]
        self.root.title(t["title"])
        self.conn_frame.config(text=t["conn_frame"])
        self.lbl_refresh.config(text=t["lbl_refresh"])

        if self.connected:
            self.connect_btn.config(text=t["btn_disconnect"])
        else:
            self.connect_btn.config(text=t["btn_connect"])

        self.info_frame.config(text=t["info_frame"])
        self.lbl_slaveid_static.config(text=t["lbl_slaveid"])
        self.lbl_baud_static.config(text=t["lbl_baud"])
        self.lbl_unit_disp_static.config(text=t["lbl_unit_disp"])
        self.lbl_decimal_disp_static.config(text=t["lbl_decimal_disp"])

        self.value_frame.config(text=t["value_frame"])
        self.settings_frame.config(text=t["settings_frame"])

        self.lbl_unit_static.config(text=t["lbl_unit"])
        self.lbl_decimal_static.config(text=t["lbl_decimal"])
        self.write_unit_btn.config(text=t["btn_write"])
        self.write_decimal_btn.config(text=t["btn_write"])

        self.tank_frame.config(text=t["tank_frame"])

        # Update status label text if it matches a known state
        cur = self.status_label.cget("text")
        other = "sk" if self.lang == "en" else "en"
        for key in ("status_disconn", "status_conn", "status_connecting", "status_error"):
            if cur == STRINGS[other][key]:
                self.status_label.config(text=t[key])
                break

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ---- Connection frame ----
        self.conn_frame = tk.LabelFrame(
            self.root, text="", bg=COLOR_PANEL, fg=COLOR_LABEL,
            font=("Segoe UI", 10, "bold"), padx=10, pady=8, bd=2, relief="groove"
        )
        self.conn_frame.grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 6), sticky="ew")

        tk.Label(self.conn_frame, text="IP:", bg=COLOR_PANEL, fg=COLOR_TEXT,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="e")
        self.ip_var = tk.StringVar(value=self._cfg.get("ip", DEFAULT_IP))
        tk.Entry(self.conn_frame, textvariable=self.ip_var, width=16, bg=COLOR_ENTRY,
                 fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
                 relief="flat").grid(row=0, column=1, padx=(4, 12))

        tk.Label(self.conn_frame, text="Port:", bg=COLOR_PANEL, fg=COLOR_TEXT,
                 font=("Segoe UI", 9)).grid(row=0, column=2, sticky="e")
        self.port_var = tk.StringVar(value=str(self._cfg.get("port", DEFAULT_PORT)))
        tk.Entry(self.conn_frame, textvariable=self.port_var, width=7, bg=COLOR_ENTRY,
                 fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
                 relief="flat").grid(row=0, column=3, padx=(4, 12))

        self.lbl_refresh = tk.Label(self.conn_frame, text="", bg=COLOR_PANEL,
                                    fg=COLOR_TEXT, font=("Segoe UI", 9))
        self.lbl_refresh.grid(row=0, column=4, sticky="e")
        self.refresh_var = tk.StringVar(value=str(DEFAULT_REFRESH_MS))
        tk.Entry(self.conn_frame, textvariable=self.refresh_var, width=7, bg=COLOR_ENTRY,
                 fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
                 relief="flat").grid(row=0, column=5, padx=(4, 8))

        self.connect_btn = tk.Button(
            self.conn_frame, text="", width=12,
            bg=COLOR_BUTTON_CONNECT, fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), cursor="hand2",
            command=self._toggle_connection
        )
        self.connect_btn.grid(row=0, column=6, padx=4)

        self.status_canvas = tk.Canvas(self.conn_frame, width=16, height=16,
                                       bg=COLOR_PANEL, highlightthickness=0)
        self.status_canvas.grid(row=0, column=7, padx=(8, 2))
        self.status_dot = self.status_canvas.create_oval(2, 2, 14, 14,
                                                         fill=COLOR_DISCONNECTED, outline="")
        self.status_label = tk.Label(self.conn_frame, text="", bg=COLOR_PANEL,
                                     fg=COLOR_DISCONNECTED, font=("Segoe UI", 9))
        self.status_label.grid(row=0, column=8, padx=(0, 8))

        self.lang_btn = tk.Button(
            self.conn_frame, text="EN", width=4,
            bg=COLOR_BUTTON_LANG, fg="white", relief="flat",
            font=("Segoe UI", 8, "bold"), cursor="hand2",
            command=self._toggle_lang
        )
        self.lang_btn.grid(row=0, column=9, padx=(0, 4))

        # ---- Left column ----
        left_frame = tk.Frame(self.root, bg=COLOR_BG)
        left_frame.grid(row=1, column=0, padx=(12, 6), pady=6, sticky="nsew")

        # -- Sensor info --
        self.info_frame = tk.LabelFrame(
            left_frame, text="", bg=COLOR_PANEL, fg=COLOR_LABEL,
            font=("Segoe UI", 10, "bold"), padx=12, pady=10, bd=2, relief="groove"
        )
        self.info_frame.pack(fill="x", pady=(0, 8))

        self.lbl_slaveid_static   = self._info_row(self.info_frame, 0, "", "slaveid_label")
        self.lbl_baud_static      = self._info_row(self.info_frame, 1, "", "baud_label")
        self.lbl_unit_disp_static = self._info_row(self.info_frame, 2, "", "unit_disp_label")
        self.lbl_decimal_disp_static = self._info_row(self.info_frame, 3, "", "decimal_disp_label")

        # -- Measured value --
        self.value_frame = tk.LabelFrame(
            left_frame, text="", bg=COLOR_PANEL, fg=COLOR_LABEL,
            font=("Segoe UI", 10, "bold"), padx=12, pady=12, bd=2, relief="groove"
        )
        self.value_frame.pack(fill="x", pady=(0, 8))

        self.value_display = tk.Label(
            self.value_frame, text="—", bg=COLOR_PANEL, fg=COLOR_VALUE_TEXT,
            font=("Segoe UI", 28, "bold"), anchor="center"
        )
        self.value_display.pack(fill="x")

        # -- Settings --
        self.settings_frame = tk.LabelFrame(
            left_frame, text="", bg=COLOR_PANEL, fg=COLOR_LABEL,
            font=("Segoe UI", 10, "bold"), padx=12, pady=10, bd=2, relief="groove"
        )
        self.settings_frame.pack(fill="x", pady=(0, 8))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TCombobox",
                        fieldbackground=COLOR_ENTRY, background=COLOR_ENTRY,
                        foreground=COLOR_TEXT, selectbackground=COLOR_ENTRY,
                        selectforeground=COLOR_TEXT)

        self.lbl_unit_static = tk.Label(self.settings_frame, text="", bg=COLOR_PANEL,
                                        fg=COLOR_TEXT, font=("Segoe UI", 9),
                                        width=22, anchor="w")
        self.lbl_unit_static.grid(row=0, column=0, sticky="w", pady=4)
        self.unit_var = tk.StringVar()
        self.unit_cb = ttk.Combobox(self.settings_frame, textvariable=self.unit_var,
                                    values=PRESSURE_UNITS, state="readonly", width=12)
        self.unit_cb.grid(row=0, column=1, padx=(4, 8), pady=4)
        self.unit_cb.current(8)
        self.write_unit_btn = tk.Button(self.settings_frame, text="", width=9,
                                        bg=COLOR_BUTTON_WRITE, fg="white", relief="flat",
                                        font=("Segoe UI", 8, "bold"), cursor="hand2",
                                        command=self._write_unit)
        self.write_unit_btn.grid(row=0, column=2, pady=4)

        self.lbl_decimal_static = tk.Label(self.settings_frame, text="", bg=COLOR_PANEL,
                                           fg=COLOR_TEXT, font=("Segoe UI", 9),
                                           width=22, anchor="w")
        self.lbl_decimal_static.grid(row=1, column=0, sticky="w", pady=4)
        self.decimal_var = tk.StringVar()
        self.dec_cb = ttk.Combobox(self.settings_frame, textvariable=self.decimal_var,
                                   values=DECIMAL_POINTS, state="readonly", width=12)
        self.dec_cb.grid(row=1, column=1, padx=(4, 8), pady=4)
        self.dec_cb.current(0)
        self.write_decimal_btn = tk.Button(self.settings_frame, text="", width=9,
                                           bg=COLOR_BUTTON_WRITE, fg="white", relief="flat",
                                           font=("Segoe UI", 8, "bold"), cursor="hand2",
                                           command=self._write_decimal)
        self.write_decimal_btn.grid(row=1, column=2, pady=4)

        # ---- Right column: tank ----
        self.tank_frame = tk.LabelFrame(
            self.root, text="", bg=COLOR_PANEL, fg=COLOR_LABEL,
            font=("Segoe UI", 10, "bold"), padx=12, pady=12, bd=2, relief="groove"
        )
        self.tank_frame.grid(row=1, column=1, padx=(6, 12), pady=6, sticky="nsew")

        self.tank_canvas = tk.Canvas(
            self.tank_frame, width=120, height=360,
            bg=COLOR_PANEL, highlightthickness=0
        )
        self.tank_canvas.pack(side="left", padx=(0, 8))
        self._build_tank()

        scale_frame = tk.Frame(self.tank_frame, bg=COLOR_PANEL, width=48)
        scale_frame.pack(side="left", fill="y")
        scale_frame.pack_propagate(False)
        for mm in range(2000, -1, -200):
            pct  = mm / LEVEL_MAX_MM
            y_pos = int((1.0 - pct) * 300) + 10
            tk.Label(scale_frame, text=f"{mm}", bg=COLOR_PANEL,
                     fg="#78909c", font=("Segoe UI", 7),
                     anchor="w").place(x=2, y=y_pos - 6)

        # ---- Threshold sliders (MAX / MIN) ----
        thresh_frame = tk.Frame(self.tank_frame, bg=COLOR_PANEL)
        thresh_frame.pack(side="left", fill="y", padx=(8, 0))

        lbl_row = tk.Frame(thresh_frame, bg=COLOR_PANEL)
        lbl_row.pack(side="top")
        tk.Label(lbl_row, text="MAX", bg=COLOR_PANEL, fg=COLOR_LIMIT_MAX,
                 font=("Segoe UI", 7, "bold"), width=5).pack(side="left")
        tk.Label(lbl_row, text="MIN", bg=COLOR_PANEL, fg=COLOR_LIMIT_MIN,
                 font=("Segoe UI", 7, "bold"), width=5).pack(side="left")

        sliders_row = tk.Frame(thresh_frame, bg=COLOR_PANEL)
        sliders_row.pack(side="top", pady=(2, 0))

        self.max_level_var = tk.IntVar(value=1800)
        self.max_scale = tk.Scale(
            sliders_row, from_=LEVEL_MAX_MM, to=0, orient=tk.VERTICAL,
            variable=self.max_level_var, length=300, width=20,
            bg=COLOR_PANEL, fg=COLOR_LIMIT_MAX, troughcolor=COLOR_ENTRY,
            activebackground=COLOR_LIMIT_MAX, highlightthickness=0,
            relief="flat", font=("Segoe UI", 7), showvalue=False,
            resolution=10
        )
        self.max_scale.pack(side="left", padx=2)

        self.min_level_var = tk.IntVar(value=200)
        self.min_scale = tk.Scale(
            sliders_row, from_=LEVEL_MAX_MM, to=0, orient=tk.VERTICAL,
            variable=self.min_level_var, length=300, width=20,
            bg=COLOR_PANEL, fg=COLOR_LIMIT_MIN, troughcolor=COLOR_ENTRY,
            activebackground=COLOR_LIMIT_MIN, highlightthickness=0,
            relief="flat", font=("Segoe UI", 7), showvalue=False,
            resolution=10
        )
        self.min_scale.pack(side="left", padx=2)

        val_row = tk.Frame(thresh_frame, bg=COLOR_PANEL)
        val_row.pack(side="top", pady=(2, 0))
        tk.Label(val_row, textvariable=self.max_level_var, bg=COLOR_PANEL,
                 fg=COLOR_LIMIT_MAX, font=("Segoe UI", 7), width=5).pack(side="left")
        tk.Label(val_row, textvariable=self.min_level_var, bg=COLOR_PANEL,
                 fg=COLOR_LIMIT_MIN, font=("Segoe UI", 7), width=5).pack(side="left")

        self.max_level_var.trace_add("write", lambda *_: self._update_limit_lines())
        self.min_level_var.trace_add("write", lambda *_: self._update_limit_lines())

        self.level_pct_label = tk.Label(
            self.tank_frame, text="0.0 %", bg=COLOR_PANEL,
            fg=COLOR_VALUE_TEXT, font=("Segoe UI", 11, "bold")
        )
        self.level_pct_label.pack(side="bottom", pady=(8, 0))

        # ---- Status bar ----
        self.statusbar = tk.Label(
            self.root, text="", anchor="w",
            bg="#12121e", fg="#78909c",
            font=("Segoe UI", 8), padx=8, pady=4
        )
        self.statusbar.grid(row=2, column=0, columnspan=2, sticky="ew")

        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=0)

    def _info_row(self, parent, row, label_text, attr_name):
        lbl_static = tk.Label(parent, text=label_text, bg=COLOR_PANEL, fg=COLOR_TEXT,
                              font=("Segoe UI", 9), width=22, anchor="w")
        lbl_static.grid(row=row, column=0, sticky="w", pady=3)
        lbl_value = tk.Label(parent, text="—", bg=COLOR_PANEL, fg=COLOR_LABEL,
                             font=("Segoe UI", 9, "bold"), anchor="w")
        lbl_value.grid(row=row, column=1, sticky="w", pady=3)
        setattr(self, attr_name, lbl_value)
        return lbl_static

    # ------------------------------------------------------------------
    # Tank visualization
    # ------------------------------------------------------------------

    TANK_X1, TANK_Y1 = 15, 10
    TANK_X2, TANK_Y2 = 105, 310

    def _build_tank(self):
        c = self.tank_canvas
        x1, y1, x2, y2 = self.TANK_X1, self.TANK_Y1, self.TANK_X2, self.TANK_Y2

        c.create_rectangle(x1, y1, x2, y2, fill=COLOR_TANK_EMPTY,
                           outline=COLOR_TANK_WALL, width=3)
        for i in range(11):
            pct = i / 10
            ty  = y2 - int(pct * (y2 - y1))
            c.create_line(x1, ty, x1 + 8, ty, fill=COLOR_TANK_WALL, width=1)
            c.create_line(x2 - 8, ty, x2,  ty, fill=COLOR_TANK_WALL, width=1)

        self.water_rect = c.create_rectangle(
            x1 + 3, y2 - 3, x2 - 3, y2 - 3, fill=COLOR_WATER, outline="")
        self.water_shine = c.create_rectangle(
            x1 + 5, y2 - 3, x1 + 18, y2 - 3, fill=COLOR_WATER_LIGHT, outline="")
        self.level_text = c.create_text(
            (x1 + x2) // 2, (y1 + y2) // 2,
            text="—", fill=COLOR_TEXT, font=("Segoe UI", 9, "bold"))
        c.create_rectangle(x1, y1, x2, y2, fill="", outline=COLOR_TANK_WALL, width=3)
        self.max_line = c.create_line(x1 + 3, y1, x2 - 3, y1,
                                      fill=COLOR_LIMIT_MAX, width=2, dash=(6, 3))
        self.min_line = c.create_line(x1 + 3, y2, x2 - 3, y2,
                                      fill=COLOR_LIMIT_MIN, width=2, dash=(6, 3))

    def _update_limit_lines(self):
        c = self.tank_canvas
        x1, y1, x2, y2 = self.TANK_X1, self.TANK_Y1, self.TANK_X2, self.TANK_Y2
        max_val = self.max_level_var.get()
        min_val = self.min_level_var.get()
        max_y = y2 - int((max_val / LEVEL_MAX_MM) * (y2 - y1))
        min_y = y2 - int((min_val / LEVEL_MAX_MM) * (y2 - y1))
        c.coords(self.max_line, x1 + 3, max_y, x2 - 3, max_y)
        c.coords(self.min_line, x1 + 3, min_y, x2 - 3, min_y)

    def _update_tank(self, real_value, unit_idx):
        c = self.tank_canvas
        x1, y1, x2, y2 = self.TANK_X1, self.TANK_Y1, self.TANK_X2, self.TANK_Y2

        if unit_idx == 8:  # mmH₂O
            level_mm    = max(0.0, min(float(real_value), LEVEL_MAX_MM))
            pct         = level_mm / LEVEL_MAX_MM
            inside_text = f"{real_value:.{self.current_decimal}f} mm"
            in_alarm    = (real_value < self.min_level_var.get() or
                           real_value > self.max_level_var.get())
        else:
            pct = max(0.0, min(self.current_value_raw / 32767.0, 1.0))
            unit_str    = PRESSURE_UNITS[unit_idx] if unit_idx < len(PRESSURE_UNITS) else ""
            inside_text = f"{real_value:.4g} {unit_str}"
            in_alarm    = False

        # Stored so _update_display can read it after this call returns
        self._in_alarm = in_alarm
        water_color = COLOR_WATER_ALARM    if in_alarm else COLOR_WATER
        shine_color = COLOR_WATER_ALARM_LT if in_alarm else COLOR_WATER_LIGHT
        pct_color   = COLOR_LIMIT_MAX      if in_alarm else COLOR_VALUE_TEXT

        water_top = y2 - int(pct * (y2 - y1))
        c.coords(self.water_rect,  x1 + 3, water_top, x2 - 3,  y2 - 3)
        c.coords(self.water_shine, x1 + 5, water_top, x1 + 18, y2 - 3)
        c.itemconfig(self.water_rect,  fill=water_color)
        c.itemconfig(self.water_shine, fill=shine_color)
        text_y = max(water_top + 12, (y1 + y2) // 2)
        c.coords(self.level_text, (x1 + x2) // 2, text_y)
        c.itemconfig(self.level_text, text=inside_text)
        self.level_pct_label.config(text=f"{pct * 100:.1f} %", fg=pct_color)
        self._update_limit_lines()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _toggle_connection(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        ip = self.ip_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror(self._t("err_port_title"), self._t("err_port"))
            return

        self._set_status(self._t("status_connecting"), "#ffa726")
        self.connect_btn.config(state="disabled")

        def do_connect():
            try:
                c = ModbusTcpClient(ip, port=port, timeout=3)
                if not c.connect():
                    raise ConnectionError(f"Cannot connect to {ip}:{port}")
                with self.lock:
                    self.client    = c
                    self.connected = True
                self.root.after(0, self._on_connected)
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._on_connect_error(msg))

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connected(self):
        self._set_status(self._t("status_conn"), COLOR_CONNECTED)
        self.connect_btn.config(text=self._t("btn_disconnect"),
                                state="normal", bg=COLOR_BUTTON_DISC)
        self.read_count  = 0
        self.error_count = 0
        self._initial_sync_done = False
        _save_config({"ip": self.ip_var.get(), "port": int(self.port_var.get())})
        self._refresh_statusbar()
        self._start_refresh()

    def _on_connect_error(self, message):
        self.connected = False
        self._set_status(self._t("status_disconn"), COLOR_DISCONNECTED)
        self.connect_btn.config(text=self._t("btn_connect"),
                                state="normal", bg=COLOR_BUTTON_CONNECT)
        self.statusbar.config(text=f"{self._t('conn_error')}: {message}")
        messagebox.showerror(self._t("err_conn_title"), message)

    def _disconnect(self):
        self._stop_refresh()
        with self.lock:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None
            self.connected = False
        self._set_status(self._t("status_disconn"), COLOR_DISCONNECTED)
        self.connect_btn.config(text=self._t("btn_connect"), bg=COLOR_BUTTON_CONNECT)
        self.statusbar.config(text=self._t("disconnected"))
        self.value_display.config(text="—", fg=COLOR_VALUE_TEXT)
        for attr in ("slaveid_label", "baud_label", "unit_disp_label", "decimal_disp_label"):
            getattr(self, attr).config(text="—")

    # ------------------------------------------------------------------
    # Auto-refresh
    # ------------------------------------------------------------------

    def _start_refresh(self):
        self._refresh_cycle()

    def _stop_refresh(self):
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None

    def _get_refresh_ms(self):
        try:
            return max(200, int(self.refresh_var.get().strip()))
        except ValueError:
            return DEFAULT_REFRESH_MS

    def _refresh_cycle(self):
        threading.Thread(target=self._poll, daemon=True).start()
        self.refresh_job = self.root.after(self._get_refresh_ms(), self._refresh_cycle)

    def _poll(self):
        with self.lock:
            client   = self.client
            slave_id = self.slave_id

        if client is None or not self.connected:
            return

        try:
            result = client.read_holding_registers(REG_SLAVE_ID, count=5, device_id=slave_id)
            if result.isError():
                raise ModbusException("FC03 read error (addresses 0–4)")

            regs         = result.registers
            slave_id_reg = regs[0]
            baud_code    = regs[1]
            unit_idx     = regs[2]
            decimal_idx  = regs[3]
            raw_value    = regs[4]

            self.current_value_raw = raw_value
            self.current_decimal   = decimal_idx
            self.current_unit_idx  = unit_idx

            divisor    = DECIMAL_DIVISORS[decimal_idx] if decimal_idx < len(DECIMAL_DIVISORS) else 1
            real_value = raw_value / divisor

            baud_str    = BAUD_RATES.get(baud_code, str(baud_code))
            unit_str    = PRESSURE_UNITS[unit_idx] if unit_idx < len(PRESSURE_UNITS) else str(unit_idx)
            decimal_str = DECIMAL_POINTS[decimal_idx] if decimal_idx < len(DECIMAL_POINTS) else str(decimal_idx)

            self.read_count += 1
            self.root.after(0, lambda: self._update_display(
                slave_id_reg, baud_str, unit_str, decimal_str, raw_value, real_value, unit_idx, decimal_idx
            ))

        except Exception as exc:
            self.error_count += 1
            msg = str(exc)
            self.root.after(0, lambda: self._handle_comm_error(msg))

    # ------------------------------------------------------------------
    # Display update
    # ------------------------------------------------------------------

    def _update_display(self, slave_id_reg, baud_str, unit_str, decimal_str,
                        raw_value, real_value, unit_idx, decimal_idx):
        self.slaveid_label.config(text=str(slave_id_reg))
        self.baud_label.config(text=f"{baud_str} bps")
        self.unit_disp_label.config(text=unit_str)
        self.decimal_disp_label.config(text=decimal_str)
        # _update_tank must run first — it sets self._in_alarm used for value color
        self._update_tank(real_value, unit_idx)
        fmt = f".{decimal_idx}f"
        alarm_color = COLOR_LIMIT_MAX if self._in_alarm else COLOR_VALUE_TEXT
        self.value_display.config(text=f"{real_value:{fmt}}  {unit_str}", fg=alarm_color)

        # Sync comboboxes only on first successful read after connect
        if not self._initial_sync_done:
            if unit_idx < len(PRESSURE_UNITS):
                self.unit_cb.current(unit_idx)
            if decimal_idx < len(DECIMAL_POINTS):
                self.dec_cb.current(decimal_idx)
            self._initial_sync_done = True

        self._refresh_statusbar()

    def _refresh_statusbar(self):
        t = STRINGS[self.lang]
        self.statusbar.config(
            text=f"{t['sb_connected']}: {self.ip_var.get()}:{self.port_var.get()}"
                 f"  |  {t['sb_slave']} {self.slave_id}"
                 f"  |  {t['sb_reads']}: {self.read_count}"
                 f"  |  {t['sb_errors']}: {self.error_count}"
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def _write_register(self, address, value, label):
        if not self.connected:
            self.statusbar.config(text=self._t("not_connected"))
            return

        def do_write():
            with self.lock:
                client   = self.client
                slave_id = self.slave_id
            if client is None:
                return
            try:
                result = client.write_register(address, value, device_id=slave_id)
                if result.isError():
                    raise ModbusException(f"FC06 write error — {label}")
                ok = self._t("write_ok")
                self.root.after(0, lambda: self.statusbar.config(
                    text=f"{ok}: {label} = {value}"
                ))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._handle_comm_error(msg))

        threading.Thread(target=do_write, daemon=True).start()

    def _write_unit(self):
        idx = PRESSURE_UNITS.index(self.unit_var.get())
        self._write_register(REG_PRESSURE_UNIT, idx, self._t("lbl_unit").rstrip(":"))

    def _write_decimal(self):
        idx = DECIMAL_POINTS.index(self.decimal_var.get())
        self._write_register(REG_DECIMAL_PT, idx, self._t("lbl_decimal").rstrip(":"))

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_status(self, text, color):
        self.status_canvas.itemconfig(self.status_dot, fill=color)
        self.status_label.config(text=text, fg=color)

    def _handle_comm_error(self, message):
        self.statusbar.config(
            text=f"{self._t('err_prefix')}: {message}"
                 f"  |  {STRINGS[self.lang]['sb_reads']}: {self.read_count}"
                 f"  |  {STRINGS[self.lang]['sb_errors']}: {self.error_count}"
        )
        self._set_status(self._t("status_error"), COLOR_ERROR)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_close(self):
        self._stop_refresh()
        with self.lock:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass
        self.root.destroy()


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    root = tk.Tk()
    root.minsize(720, 480)
    LevelSensorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
