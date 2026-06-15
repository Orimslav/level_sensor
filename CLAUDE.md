You are a technical assistant for an industrial automation engineer. Respond always in Slovak.

## Project: MY-136 Pressure / Level Sensor Monitor

Python GUI application for Modbus TCP pressure/level sensor (MY-136 via RS485-to-Ethernet converter).

### Files
- `level_sensor_monitor.py` — hlavná aplikácia
- `simulator.py` — testovací Modbus TCP server (demo bez fyzického senzora)
- `config.json` — automaticky generovaný, ukladá poslednú IP a port + e-mail nastavenia (kľúč `mail`)
- `history.csv` — automaticky generovaný, priebežný záznam nameranej hodnoty (timestamp, value, unit)
- `events.log` — automaticky generovaný, log udalostí spojenia (CONNECTED / DISCONNECTED / CONNECT_FAILED / COMM_ERROR / RECOVERED)
- `requirements.txt` — `pymodbus>=3.6.0`, `pystray>=0.19.5`, `Pillow>=10.0.0`
- `venv/` — Python virtual environment
- `modbus_komunika__n___protokol_sn__ma__e_tlaku_my-136.pdf` — manuál senzora
- `index.html` — GitHub Pages landing page (SK/EN, download tabuľka, Modbus registre)
- `level_sensor_interactive_demo.html` — interaktívna schéma zapojenia (GitHub Pages)
- `.github/workflows/build-release.yml` — GitHub Actions: build binárky pre všetky platformy pri tagu `v*`

---

## Connection

Modbus TCP, IP configurable, port 502, Slave ID 1

IP a port sa ukladajú do `config.json` pri každom úspešnom pripojení a načítavajú pri štarte.

**Umiestnenie generovaných súborov (`config.json`, `history.csv`, `events.log`):** `_data_dir()` zvolí prvý zapisovateľný priečinok — vo frozen `.exe` vedľa spustiteľného súboru (NIE `_MEIPASS`, ktorý sa pri každom štarte maže — preto sa predtým nastavenia nepamätali), so zálohou v `~/.my136_level_sensor/`. V dev režime vedľa zdrojáku. Výsledok je cachovaný v `_DATA_DIR_CACHE`.

---

## Registers (FC03 Read / FC06 Write, 0-based addressing)

| Adresa | Popis | Prístup |
|--------|-------|---------|
| 0 | Slave ID | read only |
| 1 | Baud rate | read only |
| 2 | Pressure unit — 0=MPa, 1=kPa, 2=Pa, 3=Bar, 4=mBar, 5=kg/cm², 6=psi, 7=mH₂O, 8=mmH₂O | read/write |
| 3 | Decimal point — 0=/1, 1=/10, 2=/100, 3=/1000 | read/write |
| 4 | Measured value (raw) — vydeliť deliteľom desatinného miesta | read only |
| 5 | Zero offset | read/write |

**Poznámka pymodbus 3.x:** `ModbusSequentialDataBlock` pre FC3 má interný offset +1. Block index 0 je dummy — klientská adresa N mapuje na block[N+1].

---

## GUI — implementované funkcie

- Connect/Disconnect tlačidlo s poľami IP, Port, interval obnovy
- Sekcia **Info o snímači** (read only): Slave ID, baud rate, jednotka tlaku, desatinné miesto
- Sekcia **Nastavenia** (read/write): dropdown jednotka tlaku, dropdown desatinné miesto, každý s tlačidlom Zapísať
- Veľký displej nameranej hodnoty — formátovaný podľa nastaveného počtu desatinných miest
- Grafický výškomer — vertikálna nádrž, modrá voda, rozsah 0–2000 mm (aktívny pri jednotke mmH₂O)
- Posuvníky **MIN / MAX** alarmu (predvolené: MIN=200 mm, MAX=1800 mm) — pri prekročení červená farba
- Prerušované čiary na nádrži zobrazujú aktuálnu polohu limitov
- Status bar: IP:port, Slave ID, počet čítaní, počet chýb
- Prepínač jazyka SK/EN
- Tlačidlá **História** a **Log spojenia** (sekcia Pripojenie) — otvárajú samostatné okná (`Toplevel`)

### História hladiny (samostatné okno)
- Trend kreslený natívne na `tk.Canvas` (`_redraw_history`) — žiadna externá závislosť (vedome bez matplotlib kvôli veľkosti binárky)
- Kruhový buffer `self.history` (`deque`, `HISTORY_MAXLEN=18000` bodov ≈ 5 h pri 1 s / 1 h pri 200 ms), záznam `(datetime, real_value, unit_idx)` pri každom úspešnom pollingu (`_record_history`)
- Y os auto-škáluje na min/max dát (+8 % padding), X os = poradie bodov, časové popisy prvý/posledný bod
- **Výber rozsahu** (combobox): `HISTORY_SPAN_SECONDS = [60, 300, 900, 1800, 3600, 10800, None]` (None=Všetko), default 1 h. Filter je **podľa časových pečiatok** (`timedelta`), nie podľa počtu bodov → presný bez ohľadu na interval. `_span_label` / `_refresh_span_combo` / `_on_span_change`. `hist_points_label` ukazuje `zobrazené / celkom`.
- **Okno roztiahnuteľné myšou** (`resizable(True, True)`, `minsize(420,280)`); plátno `fill+expand`, `<Configure>` → prekreslenie podľa skutočnej veľkosti (`winfo_width/height`, fallback `cget`)
- **Downsampling** pri kreslení — max ~1 bod na pixel šírky (rýchle kreslenie aj pri tisíckach bodov)
- Priebežný zápis do `history.csv` (hlavička sa píše raz, `_csv_header_done`) — CSV drží **všetko** bez limitu, na rozdiel od bufferu v pamäti
- Tlačidlá: **Vymazať** (len pamäť, CSV ostáva), **Export CSV…** (`filedialog`, snapshot bufferu)

### E-mailové notifikácie (`_send_email`, dialóg `_open_mail_window`)
- Tlačidlo **E-mail** v sekcii Pripojenie otvára dialóg s SMTP nastaveniami (host, port, TLS/STARTTLS, login, heslo, odosielateľ, príjemcovia oddelení čiarkou, prah výpadku v s) + tlačidlá **Test** a **Uložiť**
- Nastavenia v `config.json` pod kľúčom `mail` (`_default_mail_cfg`); **heslo je plaintext** (zámer — interný nástroj). `_on_connected` merguje ip/port do `self._cfg` (neprepisuje `mail`).
- Odosielanie cez `smtplib` v samostatnom vlákne (`_send_email_async` → `_send_email`); viac príjemcov, STARTTLS, login voliteľný (prázdny = bez auth). Žiadna nová modbus-súvisiaca závislosť.
- **Spúšťače** (1 mail na epizódu, vrátane „obnovené"):
  - *Výpadok komunikácie* > `comm_down_sec` (default 60 s) — kontrola v `_handle_comm_error` (volá sa pri každom zlyhanom pollingu, takže prah sa prekročí); `_comm_error_since`, `_comm_down_emailed`. Obnovenie maile v `_update_display` pri prechode error→ok.
  - *Hladina mimo MIN/MAX* — `_check_level_alarm` z `_update_display`; `_alarm_active` drží stav. Alarm len pre mmH₂O (kde `_in_alarm` dáva zmysel).

### Beh na pozadí — systémová tray ikona
- `pystray` + `Pillow` (`PIL`), import je **voliteľný** (`_HAS_TRAY`): ak zlyhá (napr. Linux bez tray podpory), `WM_DELETE_WINDOW` → `_real_quit` (normálne ukončenie).
- S tray: zavretie okna (X) → `_hide_to_tray` (`root.withdraw()` + jednorazová `notify`), monitorovanie/maily bežia ďalej. Tray menu: **Zobraziť** (default, ľavý klik) / **Ukončiť**.
- Tray ikona beží vo vlastnom vlákne (`icon.run()`); callbacky z tray vlákna sa cez `root.after(0, …)` presúvajú na hlavné vlákno (tkinter nie je thread-safe). `_real_quit` zastaví polling, zatvorí klienta, `icon.stop()` a `root.destroy()`.
- Ikona sa generuje za behu cez PIL (`_make_tray_image` — nádrž s vodou), netreba bundlovať obrázok.

### Log spojenia (samostatné okno)
- `self.events` (`deque`, `EVENTS_MAXLEN=1000`), zobrazené v `tk.Text` s farebnými tagmi podľa typu (`EVENT_COLORS`)
- Typy: `EV_CONNECTED`, `EV_DISCONNECTED`, `EV_CONNECT_FAILED`, `EV_COMM_ERROR`, `EV_RECOVERED`
- `_log_event` pridá do bufferu, zapíše do `events.log` (jazykovo nezávislé kódy) a aktualizuje otvorené okno
- **Detekcia prechodov:** `self._comm_state` (`None`/`ok`/`error`) — `COMM_ERROR` a `outage_count++` sa zaznamenajú IBA pri prechode ok→error (nie každý zlyhaný poll, inak by sa log zaplavoval); `RECOVERED` pri error→ok v `_update_display`
- Počítadlo výpadkov `outage_count` zobrazené v hlavičke okna

### Dôležité implementačné detaily
- `_initial_sync_done` flag — combobox sa syncuje so senzorom **iba raz** po prvom úspešnom čítaní; pri každom ďalšom pollingu sa **neprepísuje** (inak by blokoval zápis používateľa)
- Zobrazovaná hodnota: `real_value = raw_value / DECIMAL_DIVISORS[decimal_idx]`, formát `f".{decimal_idx}f"`
- `_apply_language` pri prepnutí jazyka obnoví aj otvorené okná História/Log (titulky + obsah)

### Komunikačný model — jedno I/O vlákno (stabilita čítania)
Modbus socket **výhradne vlastní jediné vlákno** `_io_loop` (spustené v `_start_refresh`, zastavené cez `_stop_event` v `_stop_refresh`). Tým sú **všetky transakcie serializované** — nikdy nebežia dve naraz, žiadne prelínanie rámcov.
- **Čítanie** `_read_once` raz za cyklus; počas intervalu čakania vlákno promptne vyberá zápisy z `self._write_queue` a vykoná ich cez `_do_write`.
- **Zápis**: `_write_register` (UI) iba vloží `(address, value, label)` do fronty — nespúšťa vlastné vlákno.
- Klient má `retries=3` (pymodbus zopakuje prechodnú chybu); po chybe `_read_once` skúsi `client.connect()` pre ďalší cyklus.
- Interval sa do vlákna prenáša cez plain `self._refresh_ms` (aktualizovaný `trace` na `refresh_var` v hlavnom vlákne) — **tkinter sa z I/O vlákna nevolá** (nie je thread-safe).
- `_stop_refresh` nastaví event a `join(timeout=2.0)`; `_disconnect`/`_on_close` najprv zastavia vlákno, až potom zatvoria klienta.
- **Pozn.:** starý model (nové vlákno na každý poll cez `root.after` + samostatné write vlákno zdieľajúce socket) bol nahradený — spôsoboval kopenie vlákien a kolízie rámcov pri pomalom senzore/timeoutoch.

### Chýba (TODO)
- Register 5: zero offset — nie je implementovaný v GUI
- `history.csv` rastie neobmedzene (~3 MB/deň pri 1 s intervale) — bez rotácie (zámerne jednoduché)

---

## Simulator (`simulator.py`)

Testovací Modbus TCP server, nevyžaduje fyzický senzor.

```bash
python simulator.py --port 502           # štandardný Modbus port
python simulator.py --mode sine          # sínus vlna
python simulator.py --speed 2.0          # rýchlejšia animácia
```

**Režim `scenario` (default):** stúpanie 0→2000 mm → MAX alarm → klesanie → MIN alarm → opakovanie
**Režim `sine`:** hladká sínus vlna 0↔2000 mm

Na začiatku prepína `sys.stdout` na UTF-8 (`errors="replace"`) — inak konzolová animácia (znaky █ ░ á) padá na `UnicodeEncodeError` pri presmerovanom výstupe alebo cp1250 konzole.

V aplikácii nastaviť IP: `127.0.0.1`, Port podľa spusteného simulátora.

---

## GitHub Actions — build a release (`build-release.yml`)

Workflow sa spustí pri push tagu `v*` (napr. `v1.1.0`).
Builduje 4 binárky paralelne (matica jobov):

| Runner | Výstup |
|--------|--------|
| `windows-latest` | `LevelSensorMonitor.exe` |
| `ubuntu-latest` | `LevelSensorMonitor-linux-x64` |
| `ubuntu-24.04-arm` | `LevelSensorMonitor-linux-arm64` |
| `macos-latest` | `LevelSensorMonitor-mac-arm64` |

**Poznámka:** `macos-13` (Intel) bol vyradený GitHub Actions — Intel Mac build nie je možný cez GitHub-hosted runners.
Na Linuxe sa pred buildom inštaluje `python3-tk` cez apt.
Na macOS sa používa Homebrew Python (`python@3.11 + python-tk@3.11`) — framework build s tkinter.
Každá platforma má v matici `python_cmd` (`python` pre Windows, `python3.11` pre ostatné).
`fail-fast: false` — zlyhanie jedného jobu nerušení ostatné.
Všetky súbory sa nahrávajú do GitHub Release (softprops/action-gh-release).

Postup vydania novej verzie:
```bash
git tag v1.x.x
git push origin v1.x.x
```

---

## GitHub Pages

- `index.html` — landing page s download tabuľkou, hardware info, Modbus registrami, simulátorom
- `level_sensor_interactive_demo.html` — interaktívna schéma zapojenia RS485 → Ethernet → PC
- Obe stránky: tmavý motív, SK/EN prepínač, responzívne (mobile-friendly)
- Na mobile: header skrýva nadpis a podnadpis (`.header-title`, `.header-sub` — `display:none`)

---

## Tech stack
- Python 3.x, tkinter, pymodbus 3.12.x
- `ModbusDeviceContext` (nie `ModbusSlaveContext` — premenovaný v pymodbus 3.x)
- `ModbusServerContext(devices=store, single=True)` (nie `slaves=`)
- Tmavý farebný motív — rovnaký štýl ako Waveshare Modbus RTU IO 8CH monitor
