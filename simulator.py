"""
MY-136 Modbus TCP Simulator
Demo server for presenting the level sensor monitor application.

Cycles through scenarios that demonstrate all visual states:
  1. Normal rise  — hladina stúpa do normálneho pásma
  2. MAX alarm    — hladina prekročí horný limit (červená)
  3. Normal fall  — hladina klesá späť
  4. MIN alarm    — hladina klesne pod dolný limit (červená)
  5. Repeat

Usage:
    python simulator.py                   # default: 127.0.0.1:502
    python simulator.py --port 5020       # custom port (no admin rights needed)
    python simulator.py --mode sine       # sine wave instead of scenario loop
    python simulator.py --speed 2.0       # faster animation
"""

import argparse
import math
import sys
import threading
import time

try:
    from pymodbus.datastore import (ModbusDeviceContext,
                                    ModbusSequentialDataBlock,
                                    ModbusServerContext)
    from pymodbus.server import StartTcpServer
except ImportError as e:
    print(f"ERROR: pymodbus import zlyhal: {e}")
    print("       Spusti: pip install pymodbus")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Register layout  (0-based FC03/FC06 addresses as seen by the client)
# ---------------------------------------------------------------------------
# Address 0 : Slave ID        = 1       (read only)
# Address 1 : Baud rate code  = 3       (9600 bps, read only)
# Address 2 : Pressure unit   = 8       (mmH₂O, read/write)
# Address 3 : Decimal point   = 0       (/1,    read/write)
# Address 4 : Measured value  = 0       (raw mm, simulated)
# Address 5 : Zero offset     = 0       (read/write)
# ---------------------------------------------------------------------------
# NOTE: pymodbus 3.x FC3 datablock has internal offset +1.
#       Block index 0 is unused (dummy). Client address N maps to block[N+1].
# ---------------------------------------------------------------------------

DUMMY   = 0   # block[0] — unused due to pymodbus FC3 offset
INIT_REGS = [DUMMY, 1, 3, 8, 0, 0, 0, 0, 0, 0]   # block[0..9]

FC          = 3        # function code for holding registers
ADDR_UNIT   = 2        # pressure unit register
ADDR_DEC    = 3        # decimal point register
ADDR_VALUE  = 4        # measured value register

LEVEL_MAX   = 2000     # mm
ALARM_MAX   = 1800     # default MAX threshold matching GUI default
ALARM_MIN   =  200     # default MIN threshold matching GUI default
BAR_WIDTH   = 40       # console progress bar width


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

def _bar(level: int) -> str:
    filled = max(0, min(BAR_WIDTH, int((level / LEVEL_MAX) * BAR_WIDTH)))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _print_status(level: int, unit: int, decimal: int, label: str, alarm: bool):
    alarm_tag = " *** ALARM ***" if alarm else "              "
    print(
        f"\r  {label:<20} {level:5d} mm  [{_bar(level)}]"
        f"  unit={unit} dec={decimal}{alarm_tag}",
        end="",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Datastore helpers
# ---------------------------------------------------------------------------

def set_level(ctx, value: int):
    ctx[0].setValues(FC, ADDR_VALUE, [int(value)])


def get_unit_dec(ctx):
    vals = ctx[0].getValues(FC, ADDR_UNIT, 2)
    return vals[0], vals[1]   # unit_idx, decimal_idx


# ---------------------------------------------------------------------------
# Animation modes
# ---------------------------------------------------------------------------

def _scenario_loop(ctx, stop: threading.Event, speed: float):
    """
    Structured demo:
      rise (0 → ALARM_MAX+300) → pause at top → fall (→ ALARM_MIN-300) → pause → repeat
    """
    step  = max(1, int(20 * speed))
    pause = 0.05 / speed

    while not stop.is_set():
        # 1. Rise from 0 to LEVEL_MAX
        top = LEVEL_MAX
        for lvl in range(0, top + 1, step):
            if stop.is_set():
                return
            set_level(ctx, lvl)
            u, d = get_unit_dec(ctx)
            _print_status(lvl, u, d, "Stúpa (rise)...", lvl > ALARM_MAX)
            time.sleep(pause)

        # Pause at top
        for _ in range(25):
            if stop.is_set():
                return
            _print_status(top, *get_unit_dec(ctx), "Vrchol — MAX alarm", True)
            time.sleep(0.06)

        # 2. Fall from top to below MIN threshold
        bottom = max(0, ALARM_MIN - 300)
        for lvl in range(top, bottom - 1, -step):
            if stop.is_set():
                return
            set_level(ctx, lvl)
            u, d = get_unit_dec(ctx)
            alarm = lvl > ALARM_MAX or lvl < ALARM_MIN
            _print_status(lvl, u, d, "Klesá (fall)...", alarm)
            time.sleep(pause)

        # Pause at bottom
        for _ in range(25):
            if stop.is_set():
                return
            _print_status(bottom, *get_unit_dec(ctx), "Dno — MIN alarm", True)
            time.sleep(0.06)


def _sine_loop(ctx, stop: threading.Event, speed: float):
    """Smooth sine-wave oscillation 0 ↔ 2000 mm."""
    phase = 0.0
    while not stop.is_set():
        level = int((math.sin(phase) * 0.5 + 0.5) * LEVEL_MAX)
        set_level(ctx, level)
        u, d = get_unit_dec(ctx)
        alarm = level > ALARM_MAX or level < ALARM_MIN
        _print_status(level, u, d, "Sínus (sine)...", alarm)
        phase += 0.03 * speed
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="MY-136 Modbus TCP Simulator")
    parser.add_argument("--host",  default="127.0.0.1",
                        help="Bind adresa (default: 127.0.0.1)")
    parser.add_argument("--port",  type=int, default=502,
                        help="TCP port (default: 502 vyžaduje admin; použi 5020)")
    parser.add_argument("--mode",  choices=["scenario", "sine"], default="scenario",
                        help="Režim animácie (default: scenario)")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Násobiteľ rýchlosti (default: 1.0)")
    args = parser.parse_args()

    print("=" * 72)
    print("  MY-136 Modbus TCP Simulátor")
    print(f"  Adresa : {args.host}:{args.port}   Slave ID: 1   Režim: {args.mode}")
    print(f"  GUI limity — MAX: {ALARM_MAX} mm   MIN: {ALARM_MIN} mm")
    print("  Ctrl+C — zastaviť")
    print("=" * 72)
    print()

    block   = ModbusSequentialDataBlock(0, INIT_REGS)
    store   = ModbusDeviceContext(hr=block)
    context = ModbusServerContext(devices=store, single=True)

    stop_event  = threading.Event()
    anim_fn     = _sine_loop if args.mode == "sine" else _scenario_loop
    anim_thread = threading.Thread(
        target=anim_fn,
        args=(context, stop_event, args.speed),
        daemon=True,
    )
    anim_thread.start()

    try:
        StartTcpServer(context=context, address=(args.host, args.port))
    except KeyboardInterrupt:
        print("\n\nZastavujem simulátor...")
    except PermissionError:
        print(f"\nCHYBA: Port {args.port} vyžaduje administrátorské práva.")
        print(f"       Skús: python simulator.py --port 5020")
    except OSError as exc:
        print(f"\nCHYBA: {exc}")
        print(f"       Port {args.port} je možno obsadený.")
    finally:
        stop_event.set()


if __name__ == "__main__":
    main()
