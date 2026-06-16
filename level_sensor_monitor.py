"""
MY-136 Pressure / Level Sensor Monitor
Modbus TCP — RS485-to-Ethernet converter
"""

import bisect
import csv
import json
import queue
import smtplib
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
from collections import deque
from datetime import datetime, timedelta
from email.message import EmailMessage

# Systémová tray ikona — voliteľná. Ak import zlyhá (napr. Linux bez tray
# podpory), appka sa pri zavretí jednoducho ukončí (bez behu na pozadí).
try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_TRAY = True
except Exception:
    _HAS_TRAY = False
from pathlib import Path
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# --- Constants ---
DEFAULT_IP         = "192.168.1.1"
DEFAULT_PORT       = 502
DEFAULT_SLAVE_ID   = 1
DEFAULT_REFRESH_MS = 1000

HISTORY_MAXLEN = 18000  # body trendu v pamäti (~5 h pri 1 s, ~1 h pri 200 ms)
EVENTS_MAXLEN  = 1000   # záznamy udalostí v pamäti

# Voliteľné rozsahy grafu histórie (sekundy). Dáta sa čerpajú z history.csv
# (drží všetko), takže aj dlhé rozsahy sa zobrazia úplne bez ohľadu na buffer.
HISTORY_SPAN_SECONDS = [900, 3600, 28800, 43200, 86400, 604800]
HISTORY_SPAN_DEFAULT = 3600   # predvolene 1 h


def _default_mail_cfg() -> dict:
    """Predvolené nastavenia e-mailových notifikácií (ukladané v config.json pod kľúčom 'mail')."""
    return {
        "enabled":       False,
        "host":          "",
        "port":          587,
        "tls":           True,    # STARTTLS
        "user":          "",      # prázdne = bez prihlásenia (interný relay)
        "pass":          "",
        "from":          "",
        "to":            "",      # viac príjemcov oddelených čiarkou
        "comm_down_sec": 60,      # prah výpadku komunikácie v sekundách
    }

_DATA_DIR_CACHE = None


def _data_dir() -> Path:
    """
    Prvý zapisovateľný priečinok pre config.json, history.csv a events.log.

    Vo frozen PyInstaller binárke je __file__ v dočasnom _MEIPASS priečinku,
    ktorý sa po zatvorení zmaže — dáta by sa stratili. Preto pri frozen
    režime ukladáme vedľa spustiteľného súboru (.exe), so zálohou v
    užívateľskom profile (ak je priečinok .exe iba na čítanie, napr.
    Program Files). V dev režime vedľa zdrojového súboru.
    """
    global _DATA_DIR_CACHE
    if _DATA_DIR_CACHE is not None:
        return _DATA_DIR_CACHE

    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    candidates = [base, Path.home() / ".my136_level_sensor"]
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            _DATA_DIR_CACHE = d
            return d
        except Exception:
            continue

    _DATA_DIR_CACHE = Path.home()
    return _DATA_DIR_CACHE


def _config_path() -> Path:
    return _data_dir() / "config.json"


def _history_path() -> Path:
    return _data_dir() / "history.csv"


def _events_path() -> Path:
    return _data_dir() / "events.log"


def _load_config() -> dict:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(data: dict):
    try:
        _config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
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

# --- Connection / communication events ---
EV_CONNECTED      = "CONNECTED"
EV_DISCONNECTED   = "DISCONNECTED"
EV_CONNECT_FAILED = "CONNECT_FAILED"
EV_COMM_ERROR     = "COMM_ERROR"
EV_RECOVERED      = "RECOVERED"

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
        "btn_history":      "History",
        "btn_events":       "Conn. log",
        "history_title":    "Level History",
        "events_title":     "Connection Log",
        "btn_clear":        "Clear",
        "btn_export":       "Export CSV…",
        "btn_close":        "Close",
        "no_data":          "No data yet.",
        "hist_points":      "Points",
        "lbl_range":        "Range:",
        "span_all":         "All",
        "span_day":         "day",
        "span_week":        "week",
        "ev_time":          "Time",
        "ev_event":         "Event",
        "ev_detail":        "Detail",
        "ev_outages":       "Outages",
        "ev_CONNECTED":      "Connected",
        "ev_DISCONNECTED":   "Disconnected",
        "ev_CONNECT_FAILED": "Connect failed",
        "ev_COMM_ERROR":     "Comm. error",
        "ev_RECOVERED":      "Recovered",
        "export_ok":        "Exported to",
        "export_none":      "No data to export.",
        "btn_mail":         "E-mail",
        "mail_title":       "E-mail Notifications",
        "mail_enabled":     "Enable e-mail alerts",
        "mail_host":        "SMTP server:",
        "mail_port":        "Port:",
        "mail_tls":         "Use TLS (STARTTLS)",
        "mail_user":        "Login (blank = none):",
        "mail_pass":        "Password:",
        "mail_from":        "From:",
        "mail_to":          "Recipient(s):",
        "mail_commdown":    "Outage threshold (s):",
        "btn_test":         "Send test",
        "btn_save":         "Save",
        "mail_saved":       "E-mail settings saved.",
        "mail_test_ok":     "Test e-mail sent.",
        "mail_test_fail":   "Test failed",
        "mail_sent":        "Alert e-mail sent",
        "mail_fail":        "Alert e-mail failed",
        "mail_incomplete":  "Fill in SMTP server and recipient.",
        "mail_test_subj":   "MY-136: test message",
        "mail_test_body":   "This is a test message from the MY-136 monitor.",
        "mail_commdown_subj": "MY-136: COMMUNICATION OUTAGE",
        "mail_commdown_body": "Connection to the sensor has been unavailable for over {sec} s.\nAddress: {addr}\nTime: {time}",
        "mail_commup_subj":   "MY-136: communication restored",
        "mail_commup_body":   "Connection to the sensor was restored.\nAddress: {addr}\nTime: {time}",
        "mail_level_subj":    "MY-136: LEVEL OUT OF RANGE ({dir})",
        "mail_level_body":    "Measured value {val} {unit} crossed the {dir} limit ({limit} mm).\nTime: {time}",
        "mail_levelok_subj":  "MY-136: level back to normal",
        "mail_levelok_body":  "Measured value {val} {unit} is back within range.\nTime: {time}",
        "tray_show":        "Show",
        "tray_quit":        "Quit",
        "tray_tip":         "MY-136 Monitor",
        "tray_hidden_title":"MY-136 Monitor",
        "tray_hidden_msg":  "Running in the background. Monitoring continues.",
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
        "btn_history":      "História",
        "btn_events":       "Log spojenia",
        "history_title":    "História hladiny",
        "events_title":     "Log spojenia",
        "btn_clear":        "Vymazať",
        "btn_export":       "Export CSV…",
        "btn_close":        "Zavrieť",
        "no_data":          "Zatiaľ žiadne dáta.",
        "hist_points":      "Bodov",
        "lbl_range":        "Rozsah:",
        "span_all":         "Všetko",
        "span_day":         "deň",
        "span_week":        "týždeň",
        "ev_time":          "Čas",
        "ev_event":         "Udalosť",
        "ev_detail":        "Detail",
        "ev_outages":       "Výpadkov",
        "ev_CONNECTED":      "Pripojené",
        "ev_DISCONNECTED":   "Odpojené",
        "ev_CONNECT_FAILED": "Pripojenie zlyhalo",
        "ev_COMM_ERROR":     "Chyba komunikácie",
        "ev_RECOVERED":      "Obnovené",
        "export_ok":        "Exportované do",
        "export_none":      "Žiadne dáta na export.",
        "btn_mail":         "E-mail",
        "mail_title":       "E-mailové notifikácie",
        "mail_enabled":     "Povoliť e-mailové notifikácie",
        "mail_host":        "SMTP server:",
        "mail_port":        "Port:",
        "mail_tls":         "Použiť TLS (STARTTLS)",
        "mail_user":        "Prihlásenie (prázdne = žiadne):",
        "mail_pass":        "Heslo:",
        "mail_from":        "Odosielateľ:",
        "mail_to":          "Príjemca(ovia):",
        "mail_commdown":    "Prah výpadku (s):",
        "btn_test":         "Poslať test",
        "btn_save":         "Uložiť",
        "mail_saved":       "Nastavenia e-mailu uložené.",
        "mail_test_ok":     "Testovací e-mail odoslaný.",
        "mail_test_fail":   "Test zlyhal",
        "mail_sent":        "Notifikačný e-mail odoslaný",
        "mail_fail":        "Notifikačný e-mail zlyhal",
        "mail_incomplete":  "Vyplň SMTP server a príjemcu.",
        "mail_test_subj":   "MY-136: testovacia správa",
        "mail_test_body":   "Toto je testovacia správa z monitora MY-136.",
        "mail_commdown_subj": "MY-136: VÝPADOK KOMUNIKÁCIE",
        "mail_commdown_body": "Spojenie so snímačom je nedostupné viac ako {sec} s.\nAdresa: {addr}\nČas: {time}",
        "mail_commup_subj":   "MY-136: komunikácia obnovená",
        "mail_commup_body":   "Spojenie so snímačom bolo obnovené.\nAdresa: {addr}\nČas: {time}",
        "mail_level_subj":    "MY-136: HLADINA MIMO ROZSAHU ({dir})",
        "mail_level_body":    "Nameraná hodnota {val} {unit} prekročila {dir} limit ({limit} mm).\nČas: {time}",
        "mail_levelok_subj":  "MY-136: hladina späť v norme",
        "mail_levelok_body":  "Nameraná hodnota {val} {unit} je späť v rozsahu.\nČas: {time}",
        "tray_show":        "Zobraziť",
        "tray_quit":        "Ukončiť",
        "tray_tip":         "MY-136 Monitor",
        "tray_hidden_title":"MY-136 Monitor",
        "tray_hidden_msg":  "Beží na pozadí. Monitorovanie pokračuje.",
    },
}

# Farby udalostí podľa typu
EVENT_COLORS = {
    EV_CONNECTED:      "#00c853",
    EV_RECOVERED:      "#00c853",
    EV_DISCONNECTED:   "#ffa726",
    EV_CONNECT_FAILED: "#f44336",
    EV_COMM_ERROR:     "#f44336",
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
        self.lock = threading.Lock()

        # I/O vlákno — jediné vlákno výhradne vlastní Modbus socket.
        # Čítania aj zápisy sú serializované cez write frontu → žiadne
        # súbežné transakcie na jednom sockete, žiadne prelínanie rámcov.
        self._io_thread   = None
        self._stop_event  = threading.Event()
        self._write_queue = queue.Queue()
        self._refresh_ms  = DEFAULT_REFRESH_MS

        self.read_count = 0
        self.error_count = 0
        self.current_value_raw = 0
        self.current_decimal   = 0
        self.current_unit_idx  = 8  # default mmH₂O
        self._initial_sync_done = False

        # História hladiny + log udalostí
        self.history = deque(maxlen=HISTORY_MAXLEN)   # (datetime, real_value, unit_idx)
        self.events  = deque(maxlen=EVENTS_MAXLEN)    # (timestamp_str, type, detail)
        self.outage_count = 0
        self._comm_state = None        # None / "ok" / "error" — na detekciu prechodov
        self._csv_header_done = _history_path().exists()
        self.history_win = None
        self.events_win  = None
        self.history_canvas = None
        self.events_text = None
        self.history_span_sec = HISTORY_SPAN_DEFAULT   # rozsah grafu (s); None = všetko
        self._hist_view = []           # (datetime, value, unit_idx) pre graf — čerpané z CSV
        self._hist_plot = None         # geometria posledného vykreslenia (pre hover kurzor)
        self.span_cb = None
        self.range_label = None

        # Tray (beh na pozadí)
        self.tray_icon = None
        self._tray_notified = False

        # E-mailové notifikácie
        self.mail_win = None
        self._comm_error_since   = None    # datetime začiatku výpadku
        self._comm_down_emailed  = False   # mail o výpadku už odoslaný (1×/epizóda)
        self._alarm_active       = False   # hladina aktuálne mimo rozsahu

        self.lang = "sk"  # default language

        self._cfg = _load_config()
        self._mail_cfg = {**_default_mail_cfg(), **self._cfg.get("mail", {})}
        self._build_ui()
        self._apply_language()

        if _HAS_TRAY:
            self._setup_tray()
            # Zavretie okna (X) → schovať do tray namiesto ukončenia
            self.root.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        else:
            self.root.protocol("WM_DELETE_WINDOW", self._real_quit)

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

        self.history_btn.config(text=t["btn_history"])
        self.events_btn.config(text=t["btn_events"])
        self.mail_btn.config(text=t["btn_mail"])

        # Refresh open auxiliary windows in the new language
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.title(t["history_title"])
            if self.range_label is not None and self.range_label.winfo_exists():
                self.range_label.config(text=t["lbl_range"])
            self._refresh_span_combo()
            self._redraw_history()
        if self.events_win is not None and self.events_win.winfo_exists():
            self.events_win.title(t["events_title"])
            self._rebuild_events_view()

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
        self.refresh_var.trace_add("write", lambda *_: self._sync_refresh_ms())
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

        self.history_btn = tk.Button(
            self.conn_frame, text="", width=10,
            bg=COLOR_BUTTON_LANG, fg="white", relief="flat",
            font=("Segoe UI", 8, "bold"), cursor="hand2",
            command=self._open_history_window
        )
        self.history_btn.grid(row=0, column=9, padx=(8, 2))

        self.events_btn = tk.Button(
            self.conn_frame, text="", width=11,
            bg=COLOR_BUTTON_LANG, fg="white", relief="flat",
            font=("Segoe UI", 8, "bold"), cursor="hand2",
            command=self._open_events_window
        )
        self.events_btn.grid(row=0, column=10, padx=(0, 2))

        self.mail_btn = tk.Button(
            self.conn_frame, text="", width=8,
            bg=COLOR_BUTTON_LANG, fg="white", relief="flat",
            font=("Segoe UI", 8, "bold"), cursor="hand2",
            command=self._open_mail_window
        )
        self.mail_btn.grid(row=0, column=11, padx=(0, 8))

        self.lang_btn = tk.Button(
            self.conn_frame, text="EN", width=4,
            bg=COLOR_BUTTON_LANG, fg="white", relief="flat",
            font=("Segoe UI", 8, "bold"), cursor="hand2",
            command=self._toggle_lang
        )
        self.lang_btn.grid(row=0, column=12, padx=(0, 4))

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
                # retries — pymodbus zopakuje transakciu pri prechodnej chybe
                # ešte predtým, než ju ohlási (stabilnejšie čítanie).
                c = ModbusTcpClient(ip, port=port, timeout=3, retries=3)
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
        self._comm_state = "ok"
        self._cfg["ip"]   = self.ip_var.get()
        self._cfg["port"] = int(self.port_var.get())
        _save_config(self._cfg)
        self._log_event(EV_CONNECTED, f"{self.ip_var.get()}:{self.port_var.get()}")
        self._refresh_statusbar()
        self._start_refresh()

    def _on_connect_error(self, message):
        self.connected = False
        self._set_status(self._t("status_disconn"), COLOR_DISCONNECTED)
        self.connect_btn.config(text=self._t("btn_connect"),
                                state="normal", bg=COLOR_BUTTON_CONNECT)
        self.statusbar.config(text=f"{self._t('conn_error')}: {message}")
        self._comm_state = None
        self._log_event(EV_CONNECT_FAILED, message)
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
        self._comm_state = None
        self._log_event(EV_DISCONNECTED, "")
        self._set_status(self._t("status_disconn"), COLOR_DISCONNECTED)
        self.connect_btn.config(text=self._t("btn_connect"), bg=COLOR_BUTTON_CONNECT)
        self.statusbar.config(text=self._t("disconnected"))
        self.value_display.config(text="—", fg=COLOR_VALUE_TEXT)
        for attr in ("slaveid_label", "baud_label", "unit_disp_label", "decimal_disp_label"):
            getattr(self, attr).config(text="—")

    # ------------------------------------------------------------------
    # Auto-refresh  —  jediné I/O vlákno serializuje všetky transakcie
    # ------------------------------------------------------------------

    def _sync_refresh_ms(self):
        # Volané z hlavného vlákna (trace na refresh_var). I/O vlákno potom
        # číta iba plain int self._refresh_ms — tkinter nie je thread-safe.
        try:
            self._refresh_ms = max(200, int(self.refresh_var.get().strip()))
        except (ValueError, AttributeError):
            pass

    def _get_refresh_ms(self):
        return self._refresh_ms

    def _start_refresh(self):
        self._sync_refresh_ms()
        self._stop_event = threading.Event()
        # Zahodí prípadné nevybavené zápisy z predošlého spojenia
        while True:
            try:
                self._write_queue.get_nowait()
            except queue.Empty:
                break
        self._io_thread = threading.Thread(target=self._io_loop, daemon=True)
        self._io_thread.start()

    def _stop_refresh(self):
        self._stop_event.set()
        th = self._io_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2.0)
        self._io_thread = None

    def _io_loop(self):
        """
        Jediné vlákno, ktoré výhradne vlastní Modbus socket. V každom cykle:
          1. prečíta registre,
          2. počas intervalu čakania promptne vybaví zápisy z fronty.
        Žiadne dve transakcie nikdy nebežia súbežne → maximálne stabilné čítanie.
        """
        while not self._stop_event.is_set():
            self._read_once()
            deadline = time.monotonic() + self._refresh_ms / 1000.0
            while not self._stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    req = self._write_queue.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue
                self._do_write(req)

    def _read_once(self):
        client = self.client
        if client is None or not self.connected:
            return
        try:
            result = client.read_holding_registers(REG_SLAVE_ID, count=5,
                                                    device_id=self.slave_id)
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
                slave_id_reg, baud_str, unit_str, decimal_str,
                raw_value, real_value, unit_idx, decimal_idx
            ))

        except Exception as exc:
            self.error_count += 1
            msg = str(exc)
            self.root.after(0, lambda m=msg: self._handle_comm_error(m))
            # Pokus o obnovenie spojenia pre ďalší cyklus (na tom istom klientovi)
            try:
                if not getattr(client, "connected", True):
                    client.connect()
            except Exception:
                pass

    def _do_write(self, req):
        address, value, label = req
        client = self.client
        if client is None or not self.connected:
            return
        try:
            result = client.write_register(address, value, device_id=self.slave_id)
            if result.isError():
                raise ModbusException(f"FC06 write error — {label}")
            ok = self._t("write_ok")
            self.root.after(0, lambda: self.statusbar.config(
                text=f"{ok}: {label} = {value}"))
        except Exception as exc:
            msg = str(exc)
            self.root.after(0, lambda m=msg: self._handle_comm_error(m))

    # ------------------------------------------------------------------
    # Display update
    # ------------------------------------------------------------------

    def _update_display(self, slave_id_reg, baud_str, unit_str, decimal_str,
                        raw_value, real_value, unit_idx, decimal_idx):
        # Detekcia obnovenia po výpadku komunikácie
        if self._comm_state == "error":
            # Vizuálny indikátor späť na zelený "Pripojený" (inak ostane svietiť "Chyba")
            self._set_status(self._t("status_conn"), COLOR_CONNECTED)
            self._log_event(EV_RECOVERED, "")
            if self._comm_down_emailed:   # mail pošli iba ak sme o výpadku notifikovali
                t = STRINGS[self.lang]
                self._send_email_async(
                    t["mail_commup_subj"],
                    t["mail_commup_body"].format(
                        addr=f"{self.ip_var.get()}:{self.port_var.get()}",
                        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self._comm_state = "ok"
        self._comm_error_since  = None
        self._comm_down_emailed = False

        self.slaveid_label.config(text=str(slave_id_reg))
        self.baud_label.config(text=f"{baud_str} bps")
        self.unit_disp_label.config(text=unit_str)
        self.decimal_disp_label.config(text=decimal_str)
        # _update_tank must run first — it sets self._in_alarm used for value color
        self._update_tank(real_value, unit_idx)
        fmt = f".{decimal_idx}f"
        alarm_color = COLOR_LIMIT_MAX if self._in_alarm else COLOR_VALUE_TEXT
        self.value_display.config(text=f"{real_value:{fmt}}  {unit_str}", fg=alarm_color)

        # E-mail pri prechode hladiny mimo / späť do rozsahu (1× na epizódu)
        self._check_level_alarm(real_value, unit_idx, unit_str)

        # Sync comboboxes only on first successful read after connect
        if not self._initial_sync_done:
            if unit_idx < len(PRESSURE_UNITS):
                self.unit_cb.current(unit_idx)
            if decimal_idx < len(DECIMAL_POINTS):
                self.dec_cb.current(decimal_idx)
            self._initial_sync_done = True

        self._record_history(real_value, unit_idx, unit_str)
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
        # Zápis sa nevykonáva tu, ale v I/O vlákne medzi čítaniami — tak na
        # socket nikdy nepristupujú dve vlákna naraz (samotný zápis robí _do_write).
        self._write_queue.put((address, value, label))

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
        # Zaznamenať iba prechod ok→error (nie každý neúspešný poll), inak by
        # sa log zaplavoval každú sekundu počas trvajúceho výpadku.
        now = datetime.now()
        if self._comm_state != "error":
            self.outage_count += 1
            self._log_event(EV_COMM_ERROR, message)
            self._comm_error_since  = now
            self._comm_down_emailed = False
        self._comm_state = "error"

        # E-mail pri výpadku dlhšom ako nastavený prah (raz za epizódu).
        # Táto metóda sa volá pri každom neúspešnom pollingu, takže prah sa
        # spoľahlivo prekročí počas trvajúceho výpadku.
        if (not self._comm_down_emailed and self._comm_error_since is not None):
            threshold = self._mail_cfg.get("comm_down_sec", 60)
            if (now - self._comm_error_since).total_seconds() >= threshold:
                self._comm_down_emailed = True
                t = STRINGS[self.lang]
                self._send_email_async(
                    t["mail_commdown_subj"],
                    t["mail_commdown_body"].format(
                        sec=threshold,
                        addr=f"{self.ip_var.get()}:{self.port_var.get()}",
                        time=now.strftime("%Y-%m-%d %H:%M:%S")))

        self.statusbar.config(
            text=f"{self._t('err_prefix')}: {message}"
                 f"  |  {STRINGS[self.lang]['sb_reads']}: {self.read_count}"
                 f"  |  {STRINGS[self.lang]['sb_errors']}: {self.error_count}"
        )
        self._set_status(self._t("status_error"), COLOR_ERROR)

    # ------------------------------------------------------------------
    # History + event logging
    # ------------------------------------------------------------------

    def _log_event(self, ev_type, detail=""):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = (ts, ev_type, detail)
        self.events.append(entry)
        # Zápis do súboru — jazykovo nezávislé kódy, vhodné na diagnostiku
        try:
            with open(_events_path(), "a", encoding="utf-8") as f:
                f.write(f"{ts}\t{ev_type}\t{detail}\n")
        except Exception:
            pass
        if self.events_win is not None and self.events_win.winfo_exists():
            self._append_event_row(entry)
            if self.events_outage_label is not None and self.events_outage_label.winfo_exists():
                self.events_outage_label.config(
                    text=f"{self._t('ev_outages')}: {self.outage_count}")

    def _record_history(self, real_value, unit_idx, unit_str):
        now = datetime.now()
        self.history.append((now, real_value, unit_idx))
        # Priebežný zápis do CSV (prežije reštart)
        try:
            with open(_history_path(), "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if not self._csv_header_done:
                    w.writerow(["timestamp", "value", "unit"])
                    self._csv_header_done = True
                w.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), f"{real_value}", unit_str])
        except Exception:
            pass
        if self.history_win is not None and self.history_win.winfo_exists():
            # Doplň živý bod do pohľadu grafu a orež čelo na aktuálny rozsah
            self._hist_view.append((now, real_value, unit_idx))
            if self.history_span_sec is not None:
                cutoff = now - timedelta(seconds=self.history_span_sec)
                i = bisect.bisect_left(self._hist_view, (cutoff,))
                if i > 0:
                    del self._hist_view[:i]
            self._redraw_history()

    # ------------------------------------------------------------------
    # History window (native Canvas trend)
    # ------------------------------------------------------------------

    def _open_history_window(self):
        if self.history_win is not None and self.history_win.winfo_exists():
            self.history_win.lift()
            self.history_win.focus_force()
            return
        t = STRINGS[self.lang]
        win = tk.Toplevel(self.root)
        win.title(t["history_title"])
        win.configure(bg=COLOR_BG)
        win.resizable(True, True)          # roztiahnuteľné myšou
        win.minsize(420, 280)
        self.history_win = win

        # ---- Ovládací riadok: rozsah grafu ----
        ctrl_row = tk.Frame(win, bg=COLOR_BG)
        ctrl_row.pack(fill="x", padx=12, pady=(12, 4))
        self.range_label = tk.Label(ctrl_row, text=t["lbl_range"], bg=COLOR_BG,
                                    fg=COLOR_TEXT, font=("Segoe UI", 9))
        self.range_label.pack(side="left")
        self.span_var = tk.StringVar()
        self.span_cb = ttk.Combobox(ctrl_row, textvariable=self.span_var,
                                    state="readonly", width=10)
        self.span_cb.pack(side="left", padx=(4, 0))
        self.span_cb.bind("<<ComboboxSelected>>", self._on_span_change)
        self._refresh_span_combo()

        # ---- Plátno grafu (roztiahne sa s oknom) ----
        self.history_canvas = tk.Canvas(win, width=640, height=360,
                                        bg=COLOR_PANEL, highlightthickness=0)
        self.history_canvas.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.history_canvas.bind("<Configure>", lambda e: self._redraw_history())
        # Hover: zobraz dátum/čas/hodnotu najbližšieho bodu pri pohybe kurzorom
        self.history_canvas.bind("<Motion>", self._on_history_motion)
        self.history_canvas.bind("<Leave>",
                                 lambda e: self.history_canvas.delete("hover"))

        btn_row = tk.Frame(win, bg=COLOR_BG)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        self.hist_points_label = tk.Label(btn_row, text="", bg=COLOR_BG,
                                          fg=COLOR_TEXT, font=("Segoe UI", 9))
        self.hist_points_label.pack(side="left")
        tk.Button(btn_row, text=t["btn_export"], bg=COLOR_BUTTON_WRITE, fg="white",
                  relief="flat", font=("Segoe UI", 8, "bold"), cursor="hand2",
                  command=self._export_csv).pack(side="right", padx=(4, 0))
        tk.Button(btn_row, text=t["btn_clear"], bg=COLOR_BUTTON_DISC, fg="white",
                  relief="flat", font=("Segoe UI", 8, "bold"), cursor="hand2",
                  command=self._clear_history).pack(side="right", padx=(4, 0))

        win.protocol("WM_DELETE_WINDOW", self._close_history_window)
        self._load_history_view()
        self._redraw_history()

    def _span_label(self, sec):
        if sec is None:
            return self._t("span_all")
        if sec % 604800 == 0:
            n = sec // 604800
            return f"{n} {self._t('span_week')}"
        if sec % 86400 == 0:
            n = sec // 86400
            return f"{n} {self._t('span_day')}"
        if sec < 3600:
            return f"{sec // 60} min"
        return f"{sec // 3600} h"

    def _refresh_span_combo(self):
        """(Znovu)naplní combobox rozsahov v aktuálnom jazyku a vyberie aktívny."""
        if self.span_cb is None or not self.span_cb.winfo_exists():
            return
        self.span_cb.config(values=[self._span_label(s) for s in HISTORY_SPAN_SECONDS])
        try:
            idx = HISTORY_SPAN_SECONDS.index(self.history_span_sec)
        except ValueError:
            idx = HISTORY_SPAN_SECONDS.index(HISTORY_SPAN_DEFAULT)
        self.span_cb.current(idx)

    def _on_span_change(self, *_):
        idx = self.span_cb.current()
        if 0 <= idx < len(HISTORY_SPAN_SECONDS):
            self.history_span_sec = HISTORY_SPAN_SECONDS[idx]
        self._load_history_view()
        self._redraw_history()

    def _load_history_view(self):
        """Načíta body pre aktuálny rozsah grafu z history.csv (drží celú históriu,
        na rozdiel od pamäťového bufferu). Volá sa pri otvorení okna a zmene rozsahu;
        živé body sa potom dopĺňajú v _record_history."""
        span = self.history_span_sec
        cutoff = None
        if span is not None:
            cutoff = datetime.now() - timedelta(seconds=span)
        pts = []
        try:
            with open(_history_path(), "r", encoding="utf-8", newline="") as f:
                r = csv.reader(f)
                next(r, None)   # hlavička
                for row in r:
                    if len(row) < 3:
                        continue
                    try:
                        ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if cutoff is not None and ts < cutoff:
                        continue
                    try:
                        val = float(row[1])
                    except ValueError:
                        continue
                    unit_idx = PRESSURE_UNITS.index(row[2]) if row[2] in PRESSURE_UNITS else 0
                    pts.append((ts, val, unit_idx))
        except FileNotFoundError:
            pts = list(self.history)
        except Exception:
            pts = list(self.history)
        self._hist_view = pts

    def _close_history_window(self):
        if self.history_win is not None:
            self.history_win.destroy()
        self.history_win = None
        self.history_canvas = None
        self.span_cb = None
        self.range_label = None

    def _redraw_history(self):
        c = self.history_canvas
        if c is None or not c.winfo_exists():
            return
        c.delete("all")
        self._hist_plot = None   # zneplatné, kým sa nedokreslí (vetvy s return)
        # Skutočná veľkosť plátna (po roztiahnutí okna); fallback na cget
        W = c.winfo_width()
        H = c.winfo_height()
        if W <= 1:
            W = int(c.cget("width"))
        if H <= 1:
            H = int(c.cget("height"))
        ml, mr, mt, mb = 58, 16, 18, 34
        x0, y0, x1, y1 = ml, mt, W - mr, H - mb
        if x1 - x0 < 20 or y1 - y0 < 20:
            return
        c.create_rectangle(x0, y0, x1, y1, fill=COLOR_TANK_EMPTY,
                           outline=COLOR_TANK_WALL, width=1)

        t = STRINGS[self.lang]
        view = self._hist_view
        total = len(view)

        # Začiatok rozsahu (presne podľa časových pečiatok) — bisect na zoradenom
        # zozname je O(log n), takže redraw je rýchly aj pri státisícoch bodov.
        start = 0
        if self.history_span_sec is not None and view:
            cutoff = view[-1][0] - timedelta(seconds=self.history_span_sec)
            start = bisect.bisect_left(view, (cutoff,))
        n_in_span = total - start

        if hasattr(self, "hist_points_label") and self.hist_points_label.winfo_exists():
            self.hist_points_label.config(
                text=f"{t['hist_points']}: {n_in_span} / {total}")

        if n_in_span <= 0:
            c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=t["no_data"],
                          fill=COLOR_TEXT, font=("Segoe UI", 11))
            return

        # Downsampling — nanajvýš ~1 bod na pixel šírky; vzorkujeme priamo cez
        # indexy bez kopírovania celého rozsahu (rýchle aj pri tisíckach bodov)
        max_pts = max(2, int(x1 - x0))
        if n_in_span > max_pts:
            step = n_in_span / max_pts
            data = [view[start + min(int(i * step), n_in_span - 1)] for i in range(max_pts)]
        else:
            data = view[start:]

        values = [v for (_, v, _) in data]
        vmin, vmax = min(values), max(values)
        if vmin == vmax:
            vmin -= 1
            vmax += 1
        pad = (vmax - vmin) * 0.08
        vmin -= pad
        vmax += pad
        span = vmax - vmin

        # Horizontálna mriežka + Y popisy
        for i in range(5):
            gy = y0 + (y1 - y0) * i / 4
            c.create_line(x0, gy, x1, gy, fill=COLOR_TANK_WALL, width=1)
            val = vmax - span * i / 4
            c.create_text(x0 - 4, gy, text=f"{val:.4g}", fill="#90a4ae",
                          font=("Segoe UI", 7), anchor="e")

        unit_idx = data[-1][2]
        unit_str = PRESSURE_UNITS[unit_idx] if unit_idx < len(PRESSURE_UNITS) else ""
        c.create_text(x0, y0 - 3, text=unit_str, fill="#90a4ae",
                      font=("Segoe UI", 7, "bold"), anchor="sw")

        n = len(values)

        def px(i):
            return x0 if n == 1 else x0 + (x1 - x0) * i / (n - 1)

        def py(v):
            return y1 - (v - vmin) / span * (y1 - y0)

        coords = []
        for i, v in enumerate(values):
            coords.extend([px(i), py(v)])
        if len(coords) >= 4:
            c.create_line(*coords, fill=COLOR_WATER_LIGHT, width=2)

        lx, ly = px(n - 1), py(values[-1])
        c.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=COLOR_VALUE_TEXT, outline="")
        c.create_text(x1, y0 + 2, text=f"{values[-1]:.4g} {unit_str}",
                      fill=COLOR_VALUE_TEXT, font=("Segoe UI", 9, "bold"), anchor="ne")

        c.create_text(x0, y1 + 4, text=data[0][0].strftime("%H:%M:%S"),
                      fill="#90a4ae", font=("Segoe UI", 7), anchor="nw")
        c.create_text(x1, y1 + 4, text=data[-1][0].strftime("%H:%M:%S"),
                      fill="#90a4ae", font=("Segoe UI", 7), anchor="ne")

        # Geometria pre hover kurzor (najbližší bod podľa X)
        self._hist_plot = {
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "vmin": vmin, "span": span,
            "values": values, "data": data, "unit_str": unit_str,
        }

    def _on_history_motion(self, event):
        c = self.history_canvas
        if c is None or not c.winfo_exists():
            return
        p = self._hist_plot
        if not p:
            return
        x0, y0, x1, y1 = p["x0"], p["y0"], p["x1"], p["y1"]
        if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
            c.delete("hover")
            return
        values = p["values"]
        n = len(values)
        if n == 0:
            return
        if n == 1:
            i = 0
            px = x0
        else:
            i = int(round((event.x - x0) / (x1 - x0) * (n - 1)))
            i = max(0, min(n - 1, i))
            px = x0 + (x1 - x0) * i / (n - 1)
        v = values[i]
        py = y1 - (v - p["vmin"]) / p["span"] * (y1 - y0)
        ts = p["data"][i][0]
        unit_str = p["unit_str"]

        c.delete("hover")
        c.create_line(px, y0, px, y1, fill="#ffffff", width=1, dash=(2, 2),
                      tags="hover")
        c.create_oval(px - 4, py - 4, px + 4, py + 4, outline="#ffffff",
                      width=1, fill=COLOR_VALUE_TEXT, tags="hover")

        label = (f"{ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
                 f"{v:.4g} {unit_str}")
        if px > (x0 + x1) / 2:      # popis vľavo od kurzora pri pravom okraji
            tx, anchor = px - 8, "ne"
        else:
            tx, anchor = px + 8, "nw"
        txt_id = c.create_text(tx, y0 + 6, text=label, fill="#ffffff",
                               font=("Segoe UI", 8), anchor=anchor,
                               justify="left", tags="hover")
        bb = c.bbox(txt_id)
        if bb:
            c.create_rectangle(bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2,
                               fill="#263238", outline=COLOR_TANK_WALL,
                               tags="hover")
            c.tag_raise(txt_id)

    def _clear_history(self):
        # Vymaže iba živý pohľad v pamäti; CSV súbor na disku ostáva
        # (po opätovnom otvorení / zmene rozsahu sa graf znova načíta z CSV).
        self.history.clear()
        self._hist_view = []
        self._redraw_history()

    def _export_csv(self):
        data = list(self.history)
        if not data:
            messagebox.showinfo(self._t("history_title"), self._t("export_none"))
            return
        path = filedialog.asksaveasfilename(
            parent=self.history_win, defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile="level_history.csv")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "value", "unit"])
                for dt, v, ui in data:
                    unit_str = PRESSURE_UNITS[ui] if ui < len(PRESSURE_UNITS) else str(ui)
                    w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), f"{v}", unit_str])
            self.statusbar.config(text=f"{self._t('export_ok')}: {path}")
        except Exception as exc:
            messagebox.showerror(self._t("err_prefix"), str(exc))

    # ------------------------------------------------------------------
    # Events window (connection log)
    # ------------------------------------------------------------------

    def _open_events_window(self):
        if self.events_win is not None and self.events_win.winfo_exists():
            self.events_win.lift()
            self.events_win.focus_force()
            return
        t = STRINGS[self.lang]
        win = tk.Toplevel(self.root)
        win.title(t["events_title"])
        win.configure(bg=COLOR_BG)
        win.minsize(440, 320)
        self.events_win = win

        top = tk.Frame(win, bg=COLOR_BG)
        top.pack(fill="x", padx=12, pady=(12, 4))
        self.events_outage_label = tk.Label(
            top, text=f"{t['ev_outages']}: {self.outage_count}",
            bg=COLOR_BG, fg=COLOR_LIMIT_MAX, font=("Segoe UI", 9, "bold"))
        self.events_outage_label.pack(side="left")
        tk.Button(top, text=t["btn_clear"], bg=COLOR_BUTTON_DISC, fg="white",
                  relief="flat", font=("Segoe UI", 8, "bold"), cursor="hand2",
                  command=self._clear_events).pack(side="right")

        text_frame = tk.Frame(win, bg=COLOR_BG)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        sb = tk.Scrollbar(text_frame)
        sb.pack(side="right", fill="y")
        self.events_text = tk.Text(text_frame, bg=COLOR_TANK_EMPTY, fg=COLOR_TEXT,
                                   relief="flat", font=("Consolas", 9), wrap="none",
                                   yscrollcommand=sb.set, state="disabled",
                                   insertbackground=COLOR_TEXT)
        self.events_text.pack(side="left", fill="both", expand=True)
        sb.config(command=self.events_text.yview)
        for ev, col in EVENT_COLORS.items():
            self.events_text.tag_config(ev, foreground=col)

        win.protocol("WM_DELETE_WINDOW", self._close_events_window)
        self._rebuild_events_view()

    def _close_events_window(self):
        if self.events_win is not None:
            self.events_win.destroy()
        self.events_win = None
        self.events_text = None
        self.events_outage_label = None

    def _write_event_line(self, entry):
        ts, ev_type, detail = entry
        label = STRINGS[self.lang].get(f"ev_{ev_type}", ev_type)
        line = f"{ts}   {label}"
        if detail:
            line += f"   —   {detail}"
        self.events_text.insert("end", line + "\n", ev_type)

    def _append_event_row(self, entry):
        if self.events_text is None or not self.events_text.winfo_exists():
            return
        self.events_text.config(state="normal")
        self._write_event_line(entry)
        self.events_text.config(state="disabled")
        self.events_text.see("end")

    def _rebuild_events_view(self):
        if self.events_text is None or not self.events_text.winfo_exists():
            return
        self.events_text.config(state="normal")
        self.events_text.delete("1.0", "end")
        for entry in self.events:
            self._write_event_line(entry)
        self.events_text.config(state="disabled")
        self.events_text.see("end")
        if self.events_outage_label is not None and self.events_outage_label.winfo_exists():
            self.events_outage_label.config(text=f"{self._t('ev_outages')}: {self.outage_count}")

    def _clear_events(self):
        # Vymaže iba živý pohľad v pamäti; events.log na disku ostáva.
        self.events.clear()
        self._rebuild_events_view()

    # ------------------------------------------------------------------
    # E-mail notifications
    # ------------------------------------------------------------------

    def _check_level_alarm(self, real_value, unit_idx, unit_str):
        # _in_alarm je True iba pre hladinu (mmH₂O) — pre iné jednotky sa
        # alarm nevyhodnocuje, takže maily chodia len pre úroveň hladiny.
        in_alarm = bool(getattr(self, "_in_alarm", False))
        t = STRINGS[self.lang]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        val = f"{real_value:.{self.current_decimal}f}"

        if in_alarm and not self._alarm_active:
            self._alarm_active = True
            if real_value > self.max_level_var.get():
                direction, limit = "MAX", self.max_level_var.get()
            else:
                direction, limit = "MIN", self.min_level_var.get()
            self._send_email_async(
                t["mail_level_subj"].format(dir=direction),
                t["mail_level_body"].format(val=val, unit=unit_str,
                                            dir=direction, limit=limit, time=now))
        elif not in_alarm and self._alarm_active:
            self._alarm_active = False
            self._send_email_async(
                t["mail_levelok_subj"],
                t["mail_levelok_body"].format(val=val, unit=unit_str, time=now))

    def _send_email_async(self, subject, body):
        cfg = dict(self._mail_cfg)   # snapshot — vlákno nesmie čítať meniaci sa dict
        if not cfg.get("enabled"):
            return
        if not cfg.get("host") or not cfg.get("to"):
            return
        threading.Thread(target=self._send_email,
                         args=(subject, body, cfg, False), daemon=True).start()

    def _send_email(self, subject, body, cfg, is_test):
        try:
            recipients = [r.strip() for r in cfg.get("to", "").replace(";", ",").split(",")
                          if r.strip()]
            if not recipients:
                raise ValueError("no recipient")
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"]    = cfg.get("from") or cfg.get("user") or "my136@localhost"
            msg["To"]      = ", ".join(recipients)
            msg.set_content(body)

            port = int(cfg.get("port") or 587)
            server = smtplib.SMTP(cfg["host"], port, timeout=15)
            try:
                if cfg.get("tls"):
                    server.starttls()
                if cfg.get("user"):
                    server.login(cfg["user"], cfg.get("pass", ""))
                server.send_message(msg, to_addrs=recipients)
            finally:
                try:
                    server.quit()
                except Exception:
                    pass

            if is_test:
                self.root.after(0, lambda: self.statusbar.config(text=self._t("mail_test_ok")))
            else:
                self.root.after(0, lambda s=subject: self.statusbar.config(
                    text=f"{self._t('mail_sent')}: {s}"))
        except Exception as exc:
            emsg = str(exc)
            key = "mail_test_fail" if is_test else "mail_fail"
            self.root.after(0, lambda m=emsg: self.statusbar.config(
                text=f"{self._t(key)}: {m}"))

    # ------------------------------------------------------------------
    # E-mail settings dialog
    # ------------------------------------------------------------------

    def _open_mail_window(self):
        if self.mail_win is not None and self.mail_win.winfo_exists():
            self.mail_win.lift()
            self.mail_win.focus_force()
            return
        t = STRINGS[self.lang]
        c = self._mail_cfg
        win = tk.Toplevel(self.root)
        win.title(t["mail_title"])
        win.configure(bg=COLOR_BG)
        win.resizable(False, False)
        self.mail_win = win

        frm = tk.Frame(win, bg=COLOR_BG)
        frm.pack(padx=14, pady=12, fill="both")

        self.m_enabled = tk.BooleanVar(value=c.get("enabled", False))
        tk.Checkbutton(frm, text=t["mail_enabled"], variable=self.m_enabled,
                       bg=COLOR_BG, fg=COLOR_TEXT, selectcolor=COLOR_ENTRY,
                       activebackground=COLOR_BG, activeforeground=COLOR_TEXT,
                       font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2,
                                                          sticky="w", pady=(0, 8))

        self.m_host = tk.StringVar(value=c.get("host", ""))
        self.m_port = tk.StringVar(value=str(c.get("port", 587)))
        self.m_tls  = tk.BooleanVar(value=c.get("tls", True))
        self.m_user = tk.StringVar(value=c.get("user", ""))
        self.m_pass = tk.StringVar(value=c.get("pass", ""))
        self.m_from = tk.StringVar(value=c.get("from", ""))
        self.m_to   = tk.StringVar(value=c.get("to", ""))
        self.m_down = tk.StringVar(value=str(c.get("comm_down_sec", 60)))

        def field(r, label_key, var, show=None, width=30):
            tk.Label(frm, text=t[label_key], bg=COLOR_BG, fg=COLOR_TEXT,
                     font=("Segoe UI", 9), anchor="e", width=24).grid(
                         row=r, column=0, sticky="e", pady=3)
            tk.Entry(frm, textvariable=var, width=width, bg=COLOR_ENTRY, fg=COLOR_TEXT,
                     insertbackground=COLOR_TEXT, relief="flat", show=show).grid(
                         row=r, column=1, sticky="w", padx=(6, 0), pady=3)

        field(1, "mail_host", self.m_host)
        field(2, "mail_port", self.m_port, width=10)
        tk.Checkbutton(frm, text=t["mail_tls"], variable=self.m_tls,
                       bg=COLOR_BG, fg=COLOR_TEXT, selectcolor=COLOR_ENTRY,
                       activebackground=COLOR_BG, activeforeground=COLOR_TEXT,
                       font=("Segoe UI", 9)).grid(row=3, column=1, sticky="w", pady=3)
        field(4, "mail_user", self.m_user)
        field(5, "mail_pass", self.m_pass, show="•")
        field(6, "mail_from", self.m_from)
        field(7, "mail_to",   self.m_to)
        field(8, "mail_commdown", self.m_down, width=10)

        btnrow = tk.Frame(win, bg=COLOR_BG)
        btnrow.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(btnrow, text=t["btn_test"], bg=COLOR_BUTTON_CONNECT, fg="white",
                  relief="flat", font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._test_mail).pack(side="left")
        tk.Button(btnrow, text=t["btn_save"], bg=COLOR_BUTTON_WRITE, fg="white",
                  relief="flat", font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self._save_mail_settings).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", self._close_mail_window)

    def _close_mail_window(self):
        if self.mail_win is not None:
            self.mail_win.destroy()
        self.mail_win = None

    def _collect_mail_fields(self) -> dict:
        try:
            port = int(self.m_port.get().strip() or 587)
        except ValueError:
            port = 587
        try:
            down = max(5, int(self.m_down.get().strip() or 60))
        except ValueError:
            down = 60
        return {
            "enabled":       self.m_enabled.get(),
            "host":          self.m_host.get().strip(),
            "port":          port,
            "tls":           self.m_tls.get(),
            "user":          self.m_user.get().strip(),
            "pass":          self.m_pass.get(),
            "from":          self.m_from.get().strip(),
            "to":            self.m_to.get().strip(),
            "comm_down_sec": down,
        }

    def _save_mail_settings(self):
        self._mail_cfg = self._collect_mail_fields()
        self._cfg["mail"] = self._mail_cfg
        _save_config(self._cfg)
        self.statusbar.config(text=self._t("mail_saved"))

    def _test_mail(self):
        cfg = self._collect_mail_fields()
        if not cfg["host"] or not cfg["to"]:
            messagebox.showwarning(self._t("mail_title"), self._t("mail_incomplete"))
            return
        t = STRINGS[self.lang]
        threading.Thread(target=self._send_email,
                         args=(t["mail_test_subj"], t["mail_test_body"], cfg, True),
                         daemon=True).start()

    # ------------------------------------------------------------------
    # Tray (beh na pozadí)
    # ------------------------------------------------------------------

    def _make_tray_image(self):
        # Jednoduchá ikona: nádrž s modrou vodou na tmavom pozadí
        img = Image.new("RGB", (64, 64), (30, 30, 46))
        d = ImageDraw.Draw(img)
        d.rectangle([16, 8, 48, 56], outline=(84, 110, 122), width=3)
        d.rectangle([19, 30, 45, 53], fill=(30, 136, 229))   # voda ~60 %
        return img

    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self._t("tray_show"),
                             self._tray_show, default=True),
            pystray.MenuItem(lambda item: self._t("tray_quit"),
                             self._tray_quit),
        )
        self.tray_icon = pystray.Icon("MY136", self._make_tray_image(),
                                      self._t("tray_tip"), menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _hide_to_tray(self):
        self.root.withdraw()
        # Jednorazové upozornenie, že appka beží ďalej
        if not self._tray_notified and self.tray_icon is not None:
            self._tray_notified = True
            try:
                self.tray_icon.notify(self._t("tray_hidden_msg"),
                                      self._t("tray_hidden_title"))
            except Exception:
                pass

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_show(self, icon=None, item=None):
        # Volané z tray vlákna → presmeruj na hlavné vlákno
        self.root.after(0, self._restore_window)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._real_quit)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _real_quit(self):
        self._stop_refresh()
        with self.lock:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
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
