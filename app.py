import os, json, time, glob, threading, csv, statistics
from collections import deque
from serial.tools import list_ports
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import serial
from dotenv import load_dotenv
load_dotenv()

BASE             = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_FILE = os.path.join(BASE, 'calibration.json')
SESSIONS_DIR     = os.path.join(BASE, 'sessions')
FILTER_FILE      = os.path.join(BASE, 'filter_config.json')
os.makedirs(SESSIONS_DIR, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'loadcell2024')
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

DEFAULT_CAL = {
    f'celda_{i}': {'offset': off, 'scale': sc}
    for i, (off, sc) in enumerate([
        (234500, 823.5), (314500, 855.5), (184000, 832.5),
        (166000, 860.0), (109000, 843.4), (167300, 848.7),
        (143700, 838.3), ( 97500, 859.5), ( 31100, 858.1),
    ], start=1)
}

DEFAULT_FILTER = {
    'median_window':      7,      # samples for median filter (odd, 1=off)
    'noise_floor':        5.0,    # kg — values below this per cell → 0
    'auto_record':        False,  # auto-start/stop recording
    'trigger_kg':         30.0,   # total kg above noise to start recording
    'trigger_count':      8,      # consecutive readings above trigger to start
    'stop_count':         15,     # consecutive readings below trigger to stop
}

# ── state ─────────────────────────────────────────────────────────────────────
_lock                 = threading.Lock()
_ser_running          = False
_ser_thread           = None
_recording            = False
_session_buf          = []
_serial_cfg           = {'port': '', 'baud': 115200}
_t_record_start       = None   # F1.2: T=0 at ensayo start
_stroke_record_offset = 0.0    # F1.3: d=0 at ensayo start

# Median filter buffers (one deque per cell)
_med_bufs = {f'celda_{i}': deque() for i in range(1, 10)}

# Last raw frame (for calibration)
_last_raw = {}

# Stroke calibration: raw values for 0mm and 100mm
STROKE_CAL_FILE = os.path.join(BASE, 'stroke_cal.json')

# ── in-memory caches ──────────────────────────────────────────────────────────
_STROKE_CAL_DEFAULT = {'raw_min': 0.49, 'raw_max': 98.24, 'mm_min': 0.0, 'mm_max': 100.0}
_stroke_cal_cache = None
_cal_cache = None

def load_stroke_cal():
    global _stroke_cal_cache
    if _stroke_cal_cache is None:
        if os.path.exists(STROKE_CAL_FILE):
            with open(STROKE_CAL_FILE) as f:
                _stroke_cal_cache = json.load(f)
        else:
            _stroke_cal_cache = dict(_STROKE_CAL_DEFAULT)
    return _stroke_cal_cache

def save_stroke_cal(sc):
    global _stroke_cal_cache
    _stroke_cal_cache = sc
    with open(STROKE_CAL_FILE, 'w') as f:
        json.dump(sc, f, indent=2)

# Auto-record counters
_above_count = 0
_below_count = 0

# ── helpers ───────────────────────────────────────────────────────────────────
def load_cal():
    global _cal_cache
    if _cal_cache is None:
        if os.path.exists(CALIBRATION_FILE):
            with open(CALIBRATION_FILE) as f:
                _cal_cache = json.load(f)
        else:
            _cal_cache = dict(DEFAULT_CAL)
    return _cal_cache

def save_cal(cal):
    global _cal_cache
    _cal_cache = cal
    with open(CALIBRATION_FILE, 'w') as f:
        json.dump(cal, f, indent=2)

if not os.path.exists(CALIBRATION_FILE):
    save_cal(DEFAULT_CAL)

def load_filter():
    if os.path.exists(FILTER_FILE):
        with open(FILTER_FILE) as f:
            cfg = json.load(f)
        # fill missing keys with defaults
        return {**DEFAULT_FILTER, **cfg}
    return dict(DEFAULT_FILTER)

def save_filter(cfg):
    with open(FILTER_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

if not os.path.exists(FILTER_FILE):
    save_filter(DEFAULT_FILTER)

def apply_cal(raw, cal):
    out = {}
    for i in range(1, 10):
        k = f'celda_{i}'
        out[k] = round((float(raw.get(k, 0)) - cal[k]['offset']) / cal[k]['scale'], 2)
    sc = load_stroke_cal()
    raw_s = float(raw.get('stroke', 0))
    span = sc['raw_max'] - sc['raw_min']
    out['stroke'] = round((raw_s - sc['raw_min']) / span * (sc['mm_max'] - sc['mm_min']) + sc['mm_min'], 2) if span != 0 else round(raw_s, 2)
    out['pressure'] = round(float(raw.get('pressure', 0)), 2)
    return out

def apply_filter(data, cfg):
    """Median filter + noise floor per cell."""
    w = max(1, int(cfg['median_window']))
    floor = float(cfg['noise_floor'])
    out = dict(data)
    for i in range(1, 10):
        k = f'celda_{i}'
        buf = _med_bufs[k]
        buf.append(data[k])
        if len(buf) > w:
            buf.popleft()
        val = statistics.median(buf)
        out[k] = round(val if abs(val) >= floor else 0.0, 2)
    return out

def _save_session(buf):
    """Save buffer to CSV + XLSX, return session name."""
    if not buf:
        return None
    ts   = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    name = f'sesion_{ts}'
    keys = list(buf[0].keys())
    csv_path = os.path.join(SESSIONS_DIR, name + '.csv')
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
        wb.save(os.path.join(SESSIONS_DIR, name + '.xlsx'))
    except Exception:
        pass
    return name

# ── serial worker ─────────────────────────────────────────────────────────────
def _serial_worker():
    global _ser_running, _recording, _session_buf, _above_count, _below_count, _t_record_start, _stroke_record_offset
    t0  = time.time()
    ser = None
    # Reset median buffers on connect
    for buf in _med_bufs.values():
        buf.clear()
    try:
        ser = serial.Serial(_serial_cfg['port'], _serial_cfg['baud'], timeout=5)
        time.sleep(2)
        ser.flush()
        socketio.emit('status', {'connected': True})

        while _ser_running:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line or not (line.startswith('{') and line.endswith('}')):
                continue
            try:
                raw  = json.loads(line)
                with _lock:
                    _last_raw.update(raw)
                cal  = load_cal()
                cfg  = load_filter()
                data = apply_cal(raw, cal)
                data = apply_filter(data, cfg)
                data['t'] = round(time.time() - t0, 2)

                # ── auto-record logic ────────────────────────────────────────
                if cfg['auto_record']:
                    total = sum(data[f'celda_{i}'] for i in range(1, 10))
                    thr   = float(cfg['trigger_kg'])

                    if not _recording:
                        if total >= thr:
                            _above_count += 1
                            _below_count  = 0
                            if _above_count >= int(cfg['trigger_count']):
                                with _lock:
                                    _session_buf          = []
                                    _recording            = True
                                    _t_record_start       = data['t']
                                    _stroke_record_offset = data['stroke']
                                _above_count = 0
                                socketio.emit('auto_record', {'state': 'started'})
                        else:
                            _above_count = 0
                    else:
                        if total < thr * 0.4:   # hysteresis: stop at 40% of trigger
                            _below_count += 1
                            _above_count  = 0
                            if _below_count >= int(cfg['stop_count']):
                                _recording = False
                                with _lock:
                                    buf = list(_session_buf)
                                _below_count = 0
                                name = _save_session(buf)
                                socketio.emit('auto_record', {
                                    'state': 'stopped',
                                    'name': name,
                                    'rows': len(buf)
                                })
                        else:
                            _below_count = 0
                # ────────────────────────────────────────────────────────────

                # F1.2/F1.3: apply relative time and stroke for live chart
                if _recording and _t_record_start is not None:
                    data['t_rel']      = round(data['t'] - _t_record_start, 2)
                    data['stroke_rel'] = round(data['stroke'] - _stroke_record_offset, 2)
                else:
                    data['t_rel']      = 0.0
                    data['stroke_rel'] = 0.0

                socketio.emit('data', data)
                if _recording:
                    with _lock:
                        rec = dict(data)
                        rec['t']      = data['t_rel']
                        rec['stroke'] = data['stroke_rel']
                        _session_buf.append(rec)

            except (json.JSONDecodeError, KeyError):
                continue

    except serial.SerialException as e:
        socketio.emit('status', {'connected': False, 'error': str(e)})
    finally:
        if ser and ser.is_open:
            ser.close()
        socketio.emit('status', {'connected': False})

# ── routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/ports')
def get_ports():
    ports = list_ports.comports()
    return jsonify([{'port': p.device, 'desc': p.description} for p in ports])

@app.route('/api/calibration', methods=['GET'])
def get_cal():
    return jsonify(load_cal())

@app.route('/api/calibration', methods=['POST'])
def post_cal():
    save_cal(request.json)
    return jsonify({'ok': True})

@app.route('/api/filter', methods=['GET'])
def get_filter():
    return jsonify(load_filter())

@app.route('/api/filter', methods=['POST'])
def post_filter():
    cfg = {**load_filter(), **request.json}
    save_filter(cfg)
    return jsonify({'ok': True})

@app.route('/api/connect', methods=['POST'])
def connect():
    global _ser_running, _ser_thread
    body = request.json or {}
    _serial_cfg['port'] = body.get('port', _serial_cfg['port'])
    _serial_cfg['baud'] = int(body.get('baud', _serial_cfg['baud']))
    if _ser_running:
        _ser_running = False
        if _ser_thread:
            _ser_thread.join(timeout=3)
    _ser_running = True
    _ser_thread  = threading.Thread(target=_serial_worker, daemon=True)
    _ser_thread.start()
    return jsonify({'ok': True})

@app.route('/api/disconnect', methods=['POST'])
def disconnect():
    global _ser_running
    _ser_running = False
    return jsonify({'ok': True})

@app.route('/api/record/start', methods=['POST'])
def rec_start():
    global _recording, _session_buf, _t_record_start, _stroke_record_offset
    with _lock:
        # F1.2/F1.3: capture current t and stroke as zero reference
        last = dict(_session_buf[-1]) if _session_buf else {}
        _t_record_start       = last.get('t', 0.0)
        _stroke_record_offset = last.get('stroke', 0.0)
        _session_buf          = []
        _recording            = True
    return jsonify({'ok': True})

@app.route('/api/record/stop', methods=['POST'])
def rec_stop():
    global _recording
    _recording = False
    with _lock:
        buf = list(_session_buf)
    name = _save_session(buf)
    if not name:
        return jsonify({'ok': False, 'msg': 'sin datos'})
    return jsonify({'ok': True, 'name': name, 'rows': len(buf)})

@app.route('/api/sessions')
def sessions():
    files = sorted(glob.glob(os.path.join(SESSIONS_DIR, '*.csv')), reverse=True)
    out = []
    for f in files:
        try:
            rows = sum(1 for _ in open(f)) - 1
        except Exception:
            rows = '?'
        out.append({'name': os.path.basename(f)[:-4], 'rows': rows})
    return jsonify(out)

@app.route('/api/sessions/<name>')
def session_data(name):
    path = os.path.join(SESSIONS_DIR, name + '.csv')
    if not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return jsonify({})
    result = {k: [] for k in rows[0]}
    for row in rows:
        for k, v in row.items():
            try:    result[k].append(float(v))
            except: result[k].append(v)
    return jsonify(result)

@app.route('/api/sessions/<name>', methods=['DELETE'])
def del_session(name):
    for ext in ('.csv', '.xlsx'):
        p = os.path.join(SESSIONS_DIR, name + ext)
        if os.path.exists(p):
            os.remove(p)
    return jsonify({'ok': True})

@app.route('/api/calibrate/zero', methods=['POST'])
def calibrate_zero():
    """Set current raw values as offset (zero) for all load cells."""
    with _lock:
        raw = dict(_last_raw)
    if not raw:
        return jsonify({'ok': False, 'msg': 'Sin datos del Arduino'}), 400
    cal = load_cal()
    for i in range(1, 10):
        k = f'celda_{i}'
        if k in raw:
            cal[k]['offset'] = float(raw[k])
    save_cal(cal)
    return jsonify({'ok': True, 'msg': 'Zero seteado para las 9 celdas'})

@app.route('/api/calibrate/stroke', methods=['POST'])
def calibrate_stroke():
    """Set stroke calibration point. body: {"point": "min"|"max"}"""
    with _lock:
        raw = dict(_last_raw)
    if not raw:
        return jsonify({'ok': False, 'msg': 'Sin datos del Arduino'}), 400
    point = (request.json or {}).get('point')
    sc = load_stroke_cal()
    raw_val = float(raw.get('stroke', 0))
    if point == 'min':
        sc['raw_min'] = raw_val
    elif point == 'max':
        sc['raw_max'] = raw_val
    else:
        return jsonify({'ok': False, 'msg': 'point debe ser min o max'}), 400
    save_stroke_cal(sc)
    return jsonify({'ok': True, 'raw': raw_val, 'stroke_cal': sc})

@app.route('/api/calibrate/stroke', methods=['GET'])
def get_stroke_cal():
    return jsonify(load_stroke_cal())

REFERENCE_FILE = os.path.join(BASE, 'reference_data.json')

@app.route('/api/references')
def get_references():
    if os.path.exists(REFERENCE_FILE):
        with open(REFERENCE_FILE) as f:
            return jsonify(json.load(f))
    return jsonify([])

if __name__ == '__main__':
    print('Abre http://localhost:5050 en tu navegador')
    socketio.run(app, host='0.0.0.0', port=5050, debug=False, allow_unsafe_werkzeug=True)
