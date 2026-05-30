"""Registry: lee config/sensors.json y arma el pipeline de procesamiento.

Responsabilidad única: dado un dict crudo del Arduino, devolver el dict
calibrado + filtrado, usando la definición declarativa de cada sensor.

NO conoce Flask, sockets ni grabación. Solo transforma raw -> data.
Los parámetros de calibración y filtro se inyectan en process() para
permitir tuneo en caliente sin reconstruir el registry.
"""
import os
import json

from . import calibration
from . import filters


class Registry:
    def __init__(self, sensors_path):
        with open(sensors_path) as f:
            cfg = json.load(f)
        self.sensors = cfg['sensors']
        self._by_id = {s['id']: s for s in self.sensors}
        # un filtro independiente por sensor (estado aislado)
        self._filters = {
            s['id']: filters.make_filter(s.get('filter', 'none'), s.get('filter_params'))
            for s in self.sensors
        }

    # ── consultas declarativas ──────────────────────────────────────────────
    def ids(self):
        return [s['id'] for s in self.sensors]

    def by_type(self, t):
        return [s['id'] for s in self.sensors if s['type'] == t]

    def get(self, sensor_id):
        return self._by_id[sensor_id]

    def frontend_config(self):
        """Config que el frontend consume para armar gráficos/tabs/ejes."""
        return [
            {k: s[k] for k in ('id', 'type', 'unit', 'chart', 'axis', 'color', 'label') if k in s}
            for s in self.sensors
        ]

    # ── lectura de raw respetando aliases ───────────────────────────────────
    def _read_raw(self, sensor, raw):
        for key in [sensor['raw_key'], *sensor.get('raw_aliases', [])]:
            if key in raw:
                return float(raw[key])
        return 0.0

    # ── pipeline principal ──────────────────────────────────────────────────
    def process(self, raw, cal_params, filter_cfg):
        """raw: dict del Arduino.
        cal_params: dict {sensor_id: params_de_cal}.
        filter_cfg: dict global (median_window, noise_floor, ...).
        Devuelve dict {sensor_id: valor_fisico_filtrado}."""
        out = {}
        win = filter_cfg.get('median_window', 7)
        floor = filter_cfg.get('noise_floor', 0.0)
        for s in self.sensors:
            sid = s['id']
            raw_val = self._read_raw(s, raw)
            val = calibration.apply_cal(s['cal'], raw_val, cal_params.get(sid, {}))
            filt = self._filters[sid]
            if s.get('filter') == 'median':
                val = filt.feed(val, window=win, floor=floor)
            elif s.get('filter') == 'ema':
                val = filt.feed(val)
            # 'none' -> sin tocar
            out[sid] = round(val, 2)
        return out

    def reset_filters(self):
        for f in self._filters.values():
            f.reset()


_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'config', 'sensors.json'
)


def load_registry(path=None):
    return Registry(path or _DEFAULT_PATH)
