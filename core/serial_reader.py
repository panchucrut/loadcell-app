"""Lectura del puerto serial. Aísla parsing del transporte.

parse_line(str) -> dict | None   (None si la línea no es JSON válido del Arduino)
SerialReader corre en su propio thread y entrega dicts crudos vía callback.
"""
import json
import time
import serial


def parse_line(line):
    line = line.strip()
    if not line or not (line.startswith('{') and line.endswith('}')):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


class SerialReader:
    def __init__(self, port, baud=115200, timeout=5):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._ser = None
        self._running = False

    def open(self):
        self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
        time.sleep(2)
        self._ser.flush()

    def readlines(self):
        """Generador de dicts crudos. Corta cuando self._running pasa a False."""
        self._running = True
        while self._running:
            line = self._ser.readline().decode('utf-8', errors='ignore')
            raw = parse_line(line)
            if raw is not None:
                yield raw

    def stop(self):
        self._running = False

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
