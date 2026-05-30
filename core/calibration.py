"""Calibración genérica. Cada función toma (raw_value, cal_params) y devuelve
el valor en unidad física. El registry elige cuál según sensor['cal'].

Tipos:
  offset_scale -> (raw - offset) / scale          (celdas de carga)
  offset       -> raw - offset                     (presión)
  min_max      -> mapeo lineal raw_min..raw_max a mm_min..mm_max  (dimensión)
"""


def cal_offset_scale(raw, p):
    return round((float(raw) - p['offset']) / p['scale'], 2)


def cal_offset(raw, p):
    return round(float(raw) - float(p.get('offset', 0.0)), 2)


def cal_min_max(raw, p):
    span = p['raw_max'] - p['raw_min']
    if span == 0:
        return round(float(raw), 2)
    return round((float(raw) - p['raw_min']) / span * (p['mm_max'] - p['mm_min']) + p['mm_min'], 2)


_DISPATCH = {
    'offset_scale': cal_offset_scale,
    'offset':       cal_offset,
    'min_max':      cal_min_max,
}


def apply_cal(kind, raw, params):
    fn = _DISPATCH.get(kind)
    if fn is None:
        return round(float(raw), 2)
    return fn(raw, params)
