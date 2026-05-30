"""Filtros puros. Sin estado global: el estado vive en objetos que el caller posee.

Cada filtro es una clase con .feed(value) -> value_filtrado y .reset().
Esto permite un filtro independiente por sensor (lo construye el registry).
"""
import statistics
from collections import deque


class NoFilter:
    """Passthrough. Para sensores sin filtrado (p.ej. dimension)."""
    def feed(self, value):
        return value

    def reset(self):
        pass


class MedianFilter:
    """Mediana móvil + noise floor. window y floor se pasan en feed() para
    permitir tuneo en caliente desde filter_config.json sin reconstruir."""
    def __init__(self):
        self._buf = deque()

    def feed(self, value, window=7, floor=0.0):
        w = max(1, int(window))
        self._buf.append(value)
        while len(self._buf) > w:
            self._buf.popleft()
        val = statistics.median(self._buf)
        return val if abs(val) >= float(floor) else 0.0

    def reset(self):
        self._buf.clear()


class EMAFilter:
    """Media móvil exponencial. alpha en [0,1]; menor alpha = más suave."""
    def __init__(self, alpha=0.2):
        self.alpha = float(alpha)
        self._state = None

    def feed(self, value):
        if self._state is None:
            self._state = value
        else:
            self._state = self.alpha * value + (1 - self.alpha) * self._state
        return self._state

    def reset(self):
        self._state = None


def make_filter(kind, params=None):
    params = params or {}
    if kind == 'median':
        return MedianFilter()
    if kind == 'ema':
        return EMAFilter(alpha=params.get('alpha', 0.2))
    return NoFilter()
