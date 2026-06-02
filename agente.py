#!/usr/bin/env python3
"""agente.py — Puente Arduino → Cloud para Sensores Prensa.

Corre en el Mac que tiene el Arduino enchufado por USB. Lee cada línea JSON
del Arduino y la reenvía CRUDA al servidor cloud por Socket.IO (evento
'agent_raw'). El cloud aplica calibración y máquina de estados con process_raw().

El agente es deliberadamente "tonto": NO calibra, NO graba, NO conoce los
ensayos. Solo lee y reenvía. Toda la lógica vive en el cloud (app.py).

Uso:
    export AGENT_TOKEN=...                 # debe coincidir con el del cloud (.env)
    export CLOUD_URL=https://ensayos.dexfloor.com
    export AGENT_PORT=/dev/cu.usbmodem14101   # opcional; si no, autodetecta
    python3 agente.py

Dependencias en el Mac:
    python3 -m pip install pyserial "python-socketio[client]"
"""
import os
import sys
import time
import json

import serial
from serial.tools import list_ports
import socketio


# ── config desde entorno ─────────────────────────────────────────────────────
CLOUD_URL   = os.getenv('CLOUD_URL', 'https://ensayos.dexfloor.com')
AGENT_TOKEN = os.getenv('AGENT_TOKEN', '')
PORT        = os.getenv('AGENT_PORT', '')          # vacío => autodetectar
BAUD        = int(os.getenv('AGENT_BAUD', '115200'))

if not AGENT_TOKEN:
    print('ERROR: define AGENT_TOKEN (debe coincidir con el del cloud).', file=sys.stderr)
    sys.exit(1)


def find_port():
    """Devuelve el puerto del Arduino. Usa AGENT_PORT si está; si no, autodetecta."""
    if PORT:
        return PORT
    for p in list_ports.comports():
        desc = (p.description or '').lower()
        dev  = (p.device or '').lower()
        if 'usbmodem' in dev or 'arduino' in desc or 'usbserial' in dev:
            return p.device
    return None


def parse_line(line):
    line = line.strip()
    if not line or not (line.startswith('{') and line.endswith('}')):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


# ── cliente Socket.IO con reconexión automática ───────────────────────────────
sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,      # infinito
    reconnection_delay=2,
    reconnection_delay_max=15,
)


@sio.event
def connect():
    print(f'[agente] conectado a {CLOUD_URL}')


@sio.event
def disconnect():
    print('[agente] desconectado del cloud (reintentando…)')


def connect_cloud():
    while True:
        try:
            sio.connect(CLOUD_URL, transports=['websocket'], wait_timeout=10)
            return
        except Exception as e:
            print(f'[agente] no se pudo conectar al cloud: {e}. Reintentando en 5 s…')
            time.sleep(5)


def serial_loop():
    """Bucle principal: abre el Arduino y reenvía cada lectura al cloud."""
    while True:
        port = find_port()
        if not port:
            print('[agente] Arduino no encontrado. Reintentando en 3 s…')
            time.sleep(3)
            continue
        try:
            print(f'[agente] abriendo {port} @ {BAUD}…')
            ser = serial.Serial(port, BAUD, timeout=5)
            time.sleep(2)
            ser.flush()
            print('[agente] leyendo Arduino. Ctrl+C para salir.')
            while True:
                line = ser.readline().decode('utf-8', errors='ignore')
                raw = parse_line(line)
                if raw is None:
                    continue
                if sio.connected:
                    try:
                        sio.emit('agent_raw', {'token': AGENT_TOKEN, 'raw': raw})
                    except Exception as e:
                        print(f'[agente] error enviando: {e}')
        except serial.SerialException as e:
            print(f'[agente] error serial: {e}. Reabriendo en 3 s…')
            time.sleep(3)
        finally:
            try:
                if 'ser' in locals() and ser.is_open:
                    ser.close()
            except Exception:
                pass


def main():
    print(f'[agente] Sensores Prensa — puente Arduino→Cloud')
    print(f'[agente] cloud: {CLOUD_URL}')
    connect_cloud()
    try:
        serial_loop()
    except KeyboardInterrupt:
        print('\n[agente] saliendo…')
    finally:
        try:
            if sio.connected:
                sio.emit('agent_bye', {'token': AGENT_TOKEN})
                sio.disconnect()
        except Exception:
            pass


if __name__ == '__main__':
    main()
