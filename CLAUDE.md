You are a technical assistant for an industrial automation engineer. Respond always in Slovak.

## Project: MY-136 Pressure / Level Sensor Monitor

Python GUI application for Modbus TCP pressure/level sensor (MY-136 via RS485-to-Ethernet converter).

### Files
- `level_sensor_monitor.py` — hlavná aplikácia
- `simulator.py` — testovací Modbus TCP server (demo bez fyzického senzora)
- `config.json` — automaticky generovaný, ukladá poslednú IP a port
- `requirements.txt` — `pymodbus>=3.6.0`
- `venv/` — Python virtual environment
- `modbus_komunika__n___protokol_sn__ma__e_tlaku_my-136.pdf` — manuál senzora
- `index.html` — GitHub Pages landing page (SK/EN, download tabuľka, Modbus registre)
- `level_sensor_interactive_demo.html` — interaktívna schéma zapojenia (GitHub Pages)
- `.github/workflows/build-release.yml` — GitHub Actions: build binárky pre všetky platformy pri tagu `v*`

---

## Connection

Modbus TCP, IP configurable, port 502, Slave ID 1

IP a port sa ukladajú do `config.json` pri každom úspešnom pripojení a načítavajú pri štarte.

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

### Dôležité implementačné detaily
- `_initial_sync_done` flag — combobox sa syncuje so senzorom **iba raz** po prvom úspešnom čítaní; pri každom ďalšom pollingu sa **neprepísuje** (inak by blokoval zápis používateľa)
- Zobrazovaná hodnota: `real_value = raw_value / DECIMAL_DIVISORS[decimal_idx]`, formát `f".{decimal_idx}f"`
- Zápis do registrov beží v samostatnom vlákne; polling a write vlákno zdieľajú klienta — lock chráni iba získanie referencie, nie samotnú komunikáciu (known limitation)

### Chýba (TODO)
- Register 5: zero offset — nie je implementovaný v GUI

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
