"""Persistencia de sesiones. CSV + XLSX (openpyxl) + meta.json. Sin pandas."""
import os
import csv
import json
from datetime import datetime


def save_session(sessions_dir, buf, meta=None):
    """Guarda buffer (lista de dicts) a CSV+XLSX+meta. Devuelve nombre o None."""
    if not buf:
        return None
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    tipo = (meta or {}).get('tipo', 'sesion')
    name = f'{tipo}_{ts}'
    keys = list(buf[0].keys())

    csv_path = os.path.join(sessions_dir, name + '.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(buf)

    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(keys)
        for row in buf:
            ws.append([row[k] for k in keys])
        wb.save(os.path.join(sessions_dir, name + '.xlsx'))
    except Exception:
        pass

    if meta:
        meta_out = dict(meta)
        meta_out['timestamp'] = ts
        meta_out['rows'] = len(buf)
        meta_out['nombre_archivo'] = name
        meta_out.setdefault('starred', False)
        with open(os.path.join(sessions_dir, name + '_meta.json'), 'w') as f:
            json.dump(meta_out, f, indent=2, ensure_ascii=False)
    return name
