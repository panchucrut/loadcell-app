import os, json, time, glob, threading, csv, statistics, uuid
from collections import deque
from serial.tools import list_ports
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, render_template_string, jsonify, request, redirect, url_for, session, send_file, abort
from flask_socketio import SocketIO
import serial
import msal
from dotenv import load_dotenv
from core.registry import load_registry
from core.recorder import save_session as _recorder_save
try:
    import markdown as _md
    _HAS_MD = True
except ImportError:
    _HAS_MD = False
load_dotenv()

BASE             = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_FILE = os.path.join(BASE, 'calibration.json')
SESSIONS_DIR     = os.path.join(BASE, 'sessions')
FILTER_FILE      = os.path.join(BASE, 'filter_config.json')
DIMENSION_CAL_FILE  = os.path.join(BASE, 'dimension_cal.json')
FOTO_TMP_DIR     = os.path.join(BASE, 'foto_tmp')
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(FOTO_TMP_DIR, exist_ok=True)

# T8: foto tokens {token: {'expires': float, 'path': str|None}}
_foto_tokens = {}

app = Flask(__name__)
_secret = os.getenv('SECRET_KEY')
if not _secret:
    raise RuntimeError('SECRET_KEY no definida en .env — la app no puede iniciar sin ella')
app.config['SECRET_KEY'] = _secret
app.config['SESSION_COOKIE_SECURE']   = True   # solo HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True   # no accesible desde JS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ── Azure AD (F6) ──────────────────────────────────────────────────────────────
AZURE_CLIENT_ID     = os.getenv('AZURE_CLIENT_ID', '')
AZURE_CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET', '')
AZURE_TENANT_ID     = os.getenv('AZURE_TENANT_ID', '')
AZURE_REDIRECT_URI  = os.getenv('AZURE_REDIRECT_URI', 'https://sensores.dexfloor.com/auth/callback')
AZURE_AUTHORITY     = f'https://login.microsoftonline.com/{AZURE_TENANT_ID}'
AZURE_SCOPE         = ['User.Read']
ALLOWED_TENANT      = AZURE_TENANT_ID

def _msal_app():
    return msal.ConfidentialClientApplication(
        AZURE_CLIENT_ID,
        authority=AZURE_AUTHORITY,
        client_credential=AZURE_CLIENT_SECRET,
    )

_LOCAL_MODE = os.getenv('LOCAL_MODE', 'false').lower() == 'true'

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if _LOCAL_MODE:
            return f(*args, **kwargs)
        if not session.get('user'):
            return redirect(url_for('auth_login', next=request.url))
        return f(*args, **kwargs)
    return decorated
_ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'https://sensores.dexfloor.com').split(',')
if _LOCAL_MODE and 'http://localhost:5050' not in _ALLOWED_ORIGINS:
    _ALLOWED_ORIGINS.append('http://localhost:5050')
socketio = SocketIO(app, cors_allowed_origins=_ALLOWED_ORIGINS, async_mode='threading')

DEFAULT_CAL = {
    f'celda_{i}': {'offset': off, 'scale': sc}
    for i, (off, sc) in enumerate([
        (234500, 823.5), (314500, 855.5), (184000, 832.5),
        (166000, 860.0), (109000, 843.4), (167300, 848.7),
        (143700, 838.3), ( 97500, 859.5), ( 31100, 858.1),
    ], start=1)
}

DEFAULT_FILTER = {
    'median_window':      7,
    'noise_floor':        5.0,
    'auto_record':        False,
    'trigger_kg':         30.0,
    'trigger_count':      8,
    'stop_count':         15,
    # ── Fase A: máquina de estados del ensayo (provisional aquí; en paso 3
    #    se migra a ensayo_config.json por tipo) ──────────────────────────
    'trigger_bar':        20.0,    # presión que también dispara el inicio
    'drop_enabled':       True,    # fin por caída brusca desde el pico
    'drop_pct':           30.0,    # % de caída respecto al pico (carga O presión)
    'stab_enabled':       False,   # fin por estabilización
    'stab_pct':           2.0,     # variación máx (% del valor de referencia)
    'stab_secs':          3.0,     # segundos sostenidos estable para terminar
}

ENSAYO_TIPOS_FILE = os.path.join(BASE, 'ensayo_tipos.json')
_ENSAYO_TIPOS_DEFAULT = {
    'comp_cubo':   'Compresión cubo',
    'comp_piso':   'Compresión piso',
    'def_esquina': 'Deformación esquina piso',
    'def_total':   'Deformación total',
}

def _load_ensayo_tipos():
    if os.path.exists(ENSAYO_TIPOS_FILE):
        try:
            with open(ENSAYO_TIPOS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return dict(_ENSAYO_TIPOS_DEFAULT)

def _save_ensayo_tipos(tipos):
    with open(ENSAYO_TIPOS_FILE, 'w') as f:
        json.dump(tipos, f, indent=2, ensure_ascii=False)

ENSAYO_TIPOS = _load_ensayo_tipos()

# ── Paso 3: parámetros de la máquina de estados por tipo de ensayo ──────────
# Cada tipo puede sobrescribir su hito de inicio y condición de término.
# Claves válidas: trigger_kg, trigger_bar, trigger_count, drop_enabled,
# drop_pct, stab_enabled, stab_pct, stab_secs, stop_count.
# Fallback: si el tipo o una clave falta, se usa DEFAULT_FILTER/filter_config.
ENSAYO_CONFIG_FILE = os.path.join(BASE, 'ensayo_config.json')
_ENSAYO_PARAM_KEYS = (
    'trigger_kg', 'trigger_bar', 'trigger_count', 'stop_count',
    'drop_enabled', 'drop_pct', 'stab_enabled', 'stab_pct', 'stab_secs',
)

def _load_ensayo_config():
    if os.path.exists(ENSAYO_CONFIG_FILE):
        try:
            with open(ENSAYO_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_ensayo_config(cfg):
    with open(ENSAYO_CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def _ensayo_params(cfg):
    """Mergea params de estado: DEFAULT_FILTER/filter_config <- override por tipo."""
    base = {k: cfg[k] for k in _ENSAYO_PARAM_KEYS if k in cfg}
    tipo = _ensayo_meta.get('tipo')
    over = _load_ensayo_config().get(tipo, {})
    base.update({k: v for k, v in over.items() if k in _ENSAYO_PARAM_KEYS})
    return base

# ── state ──────────────────────────────────────────────────────────────────────
_lock                 = threading.Lock()
_ser_running          = False
_ser_thread           = None
_recording            = False
_session_buf          = []
_serial_cfg           = {'port': '', 'baud': 115200}
_t_record_start       = None
_dimension_record_offset = 0.0

# F2: ensayo metadata state
_ensayo_meta = {
    'tipo':        'comp_cubo',
    'material':    '',
    'dimensiones': '',
    'operador':    '',
    'notas':       '',
    'titulo':      '',
}

_zero_bufs = {f'celda_{i}': deque(maxlen=30) for i in range(1, 10)}  # raw crudo p/ tara promediada
_pressure_buf = deque(maxlen=60)  # buffer para zero de presión
_last_raw = {}
_last_data = {}   # última lectura calibrada+filtrada en vivo (para tara manual)

import re as _re

def _safe_name(name: str) -> str:
    """Allow only alphanumeric, dash, underscore. Raises 400 on bad input."""
    if not _re.fullmatch(r'[A-Za-z0-9_\-]{1,200}', name):
        from flask import abort
        abort(400, 'Nombre de sesión inválido')
    return name


_DIMENSION_CAL_DEFAULT = {'raw_min': 0.49, 'raw_max': 98.24, 'mm_min': 0.0, 'mm_max': 100.0}
_dimension_cal_cache = None
_cal_cache = None

def load_dimension_cal():
    global _dimension_cal_cache
    if _dimension_cal_cache is None:
        if os.path.exists(DIMENSION_CAL_FILE):
            with open(DIMENSION_CAL_FILE) as f:
                _dimension_cal_cache = json.load(f)
        else:
            _dimension_cal_cache = dict(_DIMENSION_CAL_DEFAULT)
    return _dimension_cal_cache

def save_dimension_cal(sc):
    global _dimension_cal_cache
    _dimension_cal_cache = sc
    with open(DIMENSION_CAL_FILE, 'w') as f:
        json.dump(sc, f, indent=2)

_above_count = 0
_below_count = 0

# ── Fase A: máquina de estados del ensayo ───────────────────────────────────────
# IDLE    : no escucha (auto_record off, o detenido)
# ARMADO  : escuchando triggers, aún no graba
# GRABANDO: capturando filas en _session_buf
_ensayo_state   = 'IDLE'
_peak_total     = 0.0     # pico de carga (kg) durante GRABANDO
_peak_pressure  = 0.0     # pico de presión (bar) durante GRABANDO
_stab_ref_total = None    # referencia de carga para detectar estabilización
_stab_ref_press = None    # referencia de presión para estabilización
_stab_t0        = None    # timestamp en que empezó la ventana estable

def _reset_ensayo_runtime():
    """Limpia el estado runtime del ensayo (no toca _ensayo_state)."""
    global _above_count, _below_count, _peak_total, _peak_pressure
    global _stab_ref_total, _stab_ref_press, _stab_t0
    _above_count = 0
    _below_count = 0
    _peak_total = 0.0
    _peak_pressure = 0.0
    _stab_ref_total = None
    _stab_ref_press = None
    _stab_t0 = None

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
        return {**DEFAULT_FILTER, **cfg}
    return dict(DEFAULT_FILTER)

def save_filter(cfg):
    with open(FILTER_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

if not os.path.exists(FILTER_FILE):
    save_filter(DEFAULT_FILTER)

def _raw_dimension(raw):
    """Normaliza el nombre del sensor de distancia en el BORDE de entrada.
    El Arduino envía 'stroke'. CSVs/firmware viejos podían usar 'stroke_rel'.
    Nombre canónico interno: 'dimension'. Ningún otro módulo debe mirar 'stroke'.
    """
    return float(raw.get('dimension', raw.get('stroke', raw.get('stroke_rel', 0))))

# ── registry declarativo (lee config/sensors.json) ──────────────────────────────
_registry = load_registry()
LOADCELL_IDS = _registry.by_type('loadcell')   # ['celda_1'..'celda_9']
PRESSURE_IDS = _registry.by_type('pressure')
DIMENSION_IDS = _registry.by_type('dimension')

def _build_cal_params(cal, cfg):
    """Arma {sensor_id: params} que el registry necesita, desde los JSON existentes."""
    params = dict(cal)  # celdas: offset/scale
    dim_cal = load_dimension_cal()
    for did in DIMENSION_IDS:
        params[did] = dim_cal
    for pid in PRESSURE_IDS:
        params[pid] = {'offset': float(cfg.get('pressure_offset', 0.0))}
    return params

def apply_cal(raw, cal, cfg=None):
    """Compat: devuelve dict calibrado SIN filtrar (igual contrato que antes,
    pero ahora delega en el registry). El filtrado va aparte en apply_filter."""
    if cfg is None:
        cfg = load_filter()
    params = _build_cal_params(cal, cfg)
    # registry.process aplica cal+filtro juntos; para mantener separación
    # de las dos llamadas del worker, exponemos el processed completo aquí
    # y apply_filter pasa a ser passthrough (ver abajo).
    return _registry.process(raw, params, cfg)

def apply_filter(data, cfg):
    """El filtrado ya ocurrió dentro del registry.process (cal+filtro juntos).
    Se mantiene como passthrough para no alterar el flujo del worker."""
    return data

def _save_session(buf, meta=None):
    """Delega en core.recorder. Firma conservada para los callers existentes."""
    return _recorder_save(SESSIONS_DIR, buf, meta)

# ── Fase A: helpers de la máquina de estados ────────────────────────────────────
def _start_grabando(data):
    """Pasa a GRABANDO: fija tara de tiempo/distancia y resetea picos.
    Asume _lock tomado por el caller."""
    global _ensayo_state, _recording, _session_buf
    global _t_record_start, _dimension_record_offset
    global _peak_total, _peak_pressure
    _reset_ensayo_runtime()
    _session_buf             = []
    _recording               = True
    _ensayo_state            = 'GRABANDO'
    _t_record_start          = data.get('t', 0.0)
    _dimension_record_offset = data.get(DIMENSION_IDS[0], 0.0) if DIMENSION_IDS else 0.0
    _peak_total    = sum(data.get(c, 0.0) for c in LOADCELL_IDS)
    _peak_pressure = data.get(PRESSURE_IDS[0], 0.0) if PRESSURE_IDS else 0.0

def _stop_grabando(reason):
    """Cierra GRABANDO, guarda la sesión y emite 'ensayo'. Vuelve a ARMADO si
    auto_record sigue activo, si no a IDLE. NO debe llamarse con _lock tomado
    (guarda en disco). Devuelve (name, rows)."""
    global _ensayo_state, _recording
    with _lock:
        if not _recording:
            return None, 0
        _recording = False
        buf  = list(_session_buf)
        meta = dict(_ensayo_meta)
        _session_buf.clear()
        _reset_ensayo_runtime()
        cfg_now = load_filter()
        _ensayo_state = 'ARMADO' if cfg_now.get('auto_record') else 'IDLE'
    name = _save_session(buf, meta) if buf else None
    socketio.emit('ensayo', {
        'state':  _ensayo_state,
        'reason': reason,
        'name':   name,
        'rows':   len(buf),
    })
    return name, len(buf)

# ── serial worker ──────────────────────────────────────────────────────────────
def _serial_worker():
    global _ser_running, _recording, _session_buf, _above_count, _below_count, _t_record_start, _dimension_record_offset
    global _ensayo_state, _peak_total, _peak_pressure, _stab_ref_total, _stab_ref_press, _stab_t0
    t0  = time.time()
    ser = None
    _registry.reset_filters()
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
                    if 'pressure' in raw:
                        _pressure_buf.append(float(raw['pressure']))
                    for i in range(1, 10):
                        k = f'celda_{i}'
                        if k in raw:
                            _zero_bufs[k].append(float(raw[k]))
                cal  = load_cal()
                cfg  = load_filter()
                data = apply_cal(raw, cal, cfg)
                data = apply_filter(data, cfg)
                data['t'] = round(time.time() - t0, 2)
                with _lock:
                    _last_data.clear()
                    _last_data.update(data)

                # ── Fase A: máquina de estados del ensayo ───────────────────
                total    = sum(data.get(c, 0.0) for c in LOADCELL_IDS)
                pressure = data.get(PRESSURE_IDS[0], 0.0) if PRESSURE_IDS else 0.0
                ep       = _ensayo_params(cfg)   # params de estado según _ensayo_meta['tipo']
                trig_kg  = float(ep['trigger_kg'])
                trig_bar = float(ep['trigger_bar'])

                # sincronizar ARMADO con el toggle auto_record
                if cfg['auto_record'] and _ensayo_state == 'IDLE':
                    _ensayo_state = 'ARMADO'
                    _reset_ensayo_runtime()
                    socketio.emit('ensayo', {'state': 'ARMADO', 'reason': 'armado'})
                elif not cfg['auto_record'] and _ensayo_state == 'ARMADO':
                    _ensayo_state = 'IDLE'
                    socketio.emit('ensayo', {'state': 'IDLE', 'reason': 'desarmado'})

                if _ensayo_state == 'ARMADO':
                    # inicio: carga >= trigger_kg O presión >= trigger_bar, con histéresis
                    if total >= trig_kg or pressure >= trig_bar:
                        _above_count += 1
                        if _above_count >= int(ep['trigger_count']):
                            with _lock:
                                _start_grabando(data)
                            socketio.emit('ensayo', {'state': 'GRABANDO', 'reason': 'trigger'})
                    else:
                        _above_count = 0

                elif _ensayo_state == 'GRABANDO':
                    # actualizar picos
                    if total > _peak_total:
                        _peak_total = total
                    if pressure > _peak_pressure:
                        _peak_pressure = pressure

                    stop_reason = None

                    # fin por caída brusca desde el pico (carga O presión)
                    if ep.get('drop_enabled'):
                        dp = float(ep['drop_pct']) / 100.0
                        load_drop  = _peak_total > trig_kg and total <= _peak_total * (1 - dp)
                        press_drop = _peak_pressure > 0 and pressure <= _peak_pressure * (1 - dp)
                        if load_drop or press_drop:
                            _below_count += 1
                            if _below_count >= int(ep['stop_count']):
                                stop_reason = 'caida'
                        else:
                            _below_count = 0

                    # fin por estabilización (carga Y presión planas durante stab_secs)
                    if stop_reason is None and ep.get('stab_enabled'):
                        sp = float(ep['stab_pct']) / 100.0
                        if _stab_ref_total is None:
                            _stab_ref_total = total
                            _stab_ref_press = pressure
                            _stab_t0 = data['t']
                        else:
                            load_flat  = abs(total - _stab_ref_total) <= abs(_stab_ref_total) * sp
                            press_flat = abs(pressure - _stab_ref_press) <= abs(_stab_ref_press) * sp
                            if load_flat and press_flat:
                                if data['t'] - _stab_t0 >= float(ep['stab_secs']):
                                    stop_reason = 'estable'
                            else:
                                _stab_ref_total = total
                                _stab_ref_press = pressure
                                _stab_t0 = data['t']

                    if stop_reason is not None:
                        _stop_grabando(stop_reason)

                if _recording and _t_record_start is not None:
                    data['t_rel']      = round(data['t'] - _t_record_start, 2)
                    data['dimension_rel'] = round(data['dimension'] - _dimension_record_offset, 2)
                else:
                    data['t_rel']      = 0.0
                    data['dimension_rel'] = 0.0

                socketio.emit('data', data)
                if _recording:
                    with _lock:
                        rec = {}
                        # celdas
                        for c in LOADCELL_IDS:
                            rec[c] = data[c]
                        rec['total_kg']  = round(sum(data[c] for c in LOADCELL_IDS), 2)
                        rec['dimension'] = data['dimension_rel']
                        rec['pressure']  = data['pressure']
                        rec['t']         = data['t_rel']
                        _session_buf.append(rec)

            except (json.JSONDecodeError, KeyError):
                continue

    except serial.SerialException as e:
        socketio.emit('status', {'connected': False, 'error': str(e)})
    finally:
        if ser and ser.is_open:
            ser.close()
        with _lock:
            _ensayo_state = 'IDLE'
            _recording = False
            _reset_ensayo_runtime()
        socketio.emit('status', {'connected': False})

# ── auth routes (F6) ───────────────────────────────────────────────────────────
@app.route('/auth/login')
def auth_login():
    next_url = request.args.get('next', url_for('index'))
    session['auth_next'] = next_url
    auth_url = _msal_app().get_authorization_request_url(
        AZURE_SCOPE,
        redirect_uri=AZURE_REDIRECT_URI,
        state=next_url,
    )
    return redirect(auth_url)

@app.route('/auth/callback')
def auth_callback():
    code = request.args.get('code')
    if not code:
        return 'Error de autenticación: sin código', 400
    result = _msal_app().acquire_token_by_authorization_code(
        code,
        scopes=AZURE_SCOPE,
        redirect_uri=AZURE_REDIRECT_URI,
    )
    if 'error' in result:
        return f'Error Azure AD: {result.get("error_description", result["error"])}', 401
    claims = result.get('id_token_claims', {})
    # Verificar que pertenece al tenant dexfloor.com
    if claims.get('tid') != ALLOWED_TENANT:
        return 'Acceso denegado: cuenta no pertenece a dexfloor.com', 403
    session['user'] = {
        'name':  claims.get('name', ''),
        'email': claims.get('preferred_username', ''),
        'tid':   claims.get('tid', ''),
    }
    next_url = session.pop('auth_next', url_for('index'))
    return redirect(next_url)

@app.route('/auth/logout')
def auth_logout():
    session.clear()
    logout_url = (
        f'{AZURE_AUTHORITY}/oauth2/v2.0/logout'
        f'?post_logout_redirect_uri={url_for("index", _external=True)}'
    )
    return redirect(logout_url)

@app.route('/auth/me')
@login_required
def auth_me():
    return jsonify(session.get('user'))

# ── main routes ────────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/ports')
@login_required
def get_ports():
    ports = list_ports.comports()
    return jsonify([{'port': p.device, 'desc': p.description} for p in ports])

@app.route('/api/calibration', methods=['GET'])
@login_required
def get_cal():
    return jsonify(load_cal())

@app.route('/api/calibration', methods=['POST'])
@login_required
def post_cal():
    save_cal(request.json)
    return jsonify({'ok': True})

@app.route('/api/filter', methods=['GET'])
@login_required
def get_filter():
    return jsonify(load_filter())

@app.route('/api/filter', methods=['POST'])
@login_required
def post_filter():
    cfg = {**load_filter(), **request.json}
    save_filter(cfg)
    return jsonify({'ok': True})

@app.route('/api/connect', methods=['POST'])
@login_required
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
@login_required
def disconnect():
    global _ser_running
    _ser_running = False
    return jsonify({'ok': True})

@app.route('/api/ensayo/meta', methods=['GET'])
@login_required
def get_ensayo_meta():
    return jsonify({**_ensayo_meta, 'tipos': ENSAYO_TIPOS})

@app.route('/api/ensayo/meta', methods=['POST'])
@login_required
def post_ensayo_meta():
    global _ensayo_meta
    body = request.json or {}
    _ensayo_meta.update({k: v for k, v in body.items() if k in _ensayo_meta})
    return jsonify({'ok': True, 'meta': _ensayo_meta})

# T7: gestión de tipos de ensayo
@app.route('/api/ensayo/tipos', methods=['GET'])
@login_required
def get_ensayo_tipos():
    return jsonify(ENSAYO_TIPOS)

@app.route('/api/ensayo/tipos', methods=['POST'])
@login_required
def post_ensayo_tipos():
    global ENSAYO_TIPOS
    body = request.json or {}
    key   = body.get('key', '').strip()
    label = body.get('label', '').strip()
    if not key or not label:
        abort(400, 'key y label requeridos')
    if not re.match(r'^[a-z0-9_]{1,40}$', key):
        abort(400, 'key solo letras minúsculas, números y _')
    ENSAYO_TIPOS[key] = label
    _save_ensayo_tipos(ENSAYO_TIPOS)
    return jsonify({'ok': True, 'tipos': ENSAYO_TIPOS})

@app.route('/api/ensayo/tipos/<key>', methods=['DELETE'])
@login_required
def delete_ensayo_tipo(key):
    global ENSAYO_TIPOS
    key = key.strip()
    if key not in ENSAYO_TIPOS:
        abort(404, 'tipo no encontrado')
    if len(ENSAYO_TIPOS) <= 1:
        abort(400, 'debe quedar al menos un tipo')
    del ENSAYO_TIPOS[key]
    _save_ensayo_tipos(ENSAYO_TIPOS)
    return jsonify({'ok': True, 'tipos': ENSAYO_TIPOS})

# Paso 3-UI: parámetros de la máquina de estados por tipo (editables en Config)
@app.route('/api/ensayo/config', methods=['GET'])
@login_required
def get_ensayo_config():
    # defaults globales para mostrar como fallback en la UI
    cfg = load_filter()
    defaults = {k: cfg[k] for k in _ENSAYO_PARAM_KEYS if k in cfg}
    return jsonify({'config': _load_ensayo_config(), 'defaults': defaults})

@app.route('/api/ensayo/config', methods=['POST'])
@login_required
def post_ensayo_config():
    body = request.json or {}
    tipo   = (body.get('tipo') or '').strip()
    params = body.get('params') or {}
    if not tipo:
        abort(400, 'tipo requerido')
    if tipo not in ENSAYO_TIPOS:
        abort(400, 'tipo no existe')
    clean = {}
    for k, v in params.items():
        if k not in _ENSAYO_PARAM_KEYS:
            continue
        if k in ('drop_enabled', 'stab_enabled'):
            clean[k] = bool(v)
        elif k in ('trigger_count', 'stop_count'):
            clean[k] = int(v)
        else:
            clean[k] = float(v)
    full = _load_ensayo_config()
    full[tipo] = clean
    _save_ensayo_config(full)
    return jsonify({'ok': True, 'config': full})

@app.route('/api/record/start', methods=['POST'])
@login_required
def rec_start():
    """{manual:true} (default) salta directo a GRABANDO ignorando triggers."""
    global _ensayo_state, _recording, _session_buf, _t_record_start, _dimension_record_offset
    with _lock:
        if _recording:
            return jsonify({'ok': False, 'msg': 'ya estaba grabando', 'state': _ensayo_state})
        # Tara desde la última lectura EN VIVO (el buffer está vacío al iniciar)
        live = dict(_last_data)
        _start_grabando(live)
    socketio.emit('ensayo', {'state': 'GRABANDO', 'reason': 'manual'})
    return jsonify({'ok': True, 'state': 'GRABANDO'})

@app.route('/api/record/stop', methods=['POST'])
@login_required
def rec_stop():
    """Aborta si ARMADO (sin guardar); guarda si GRABANDO."""
    global _ensayo_state
    with _lock:
        state = _ensayo_state
    if state == 'GRABANDO':
        name, rows = _stop_grabando('manual')
        if not name:
            return jsonify({'ok': False, 'msg': 'sin datos', 'state': _ensayo_state})
        return jsonify({'ok': True, 'name': name, 'rows': rows, 'state': _ensayo_state})
    if state == 'ARMADO':
        with _lock:
            _ensayo_state = 'IDLE'
            _reset_ensayo_runtime()
        socketio.emit('ensayo', {'state': 'IDLE', 'reason': 'abortado'})
        return jsonify({'ok': True, 'aborted': True, 'state': 'IDLE'})
    return jsonify({'ok': False, 'msg': 'no estaba grabando ni armado', 'state': state})

@app.route('/api/record/state')
@login_required
def rec_state():
    with _lock:
        return jsonify({
            'state':         _ensayo_state,
            'recording':     _recording,
            'rows':          len(_session_buf),
            'peak_total':    round(_peak_total, 2),
            'peak_pressure': round(_peak_pressure, 2),
        })

@app.route('/api/sessions')
@login_required
def sessions():
    files = sorted(glob.glob(os.path.join(SESSIONS_DIR, '*.csv')), reverse=True)
    out = []
    for f in files:
        try:
            rows = sum(1 for _ in open(f)) - 1
        except Exception:
            rows = '?'
        name = os.path.basename(f)[:-4]
        # try to load meta
        meta_path = os.path.join(SESSIONS_DIR, name + '_meta.json')
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as mf:
                    meta = json.load(mf)
            except Exception:
                pass
        out.append({'name': name, 'rows': rows, 'meta': meta})
    return jsonify(out)

@app.route('/api/sessions/<name>')
@login_required
def session_data(name):
    name = _safe_name(name)
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
@login_required
def del_session(name):
    name = _safe_name(name)
    for ext in ('.csv', '.xlsx', '_meta.json'):
        p = os.path.join(SESSIONS_DIR, name + ext)
        if os.path.exists(p):
            os.remove(p)
    return jsonify({'ok': True})

# T9: descarga múltiple ZIP
@app.route('/api/sessions/download-zip', methods=['POST'])
@login_required
def download_zip():
    import zipfile, io
    body = request.json or {}
    names = body.get('names', [])
    if not names or not isinstance(names, list):
        return jsonify({'error': 'names requerido'}), 400
    names = [_safe_name(n) for n in names if n]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            for ext in ('.csv', '.xlsx', '_meta.json'):
                p = os.path.join(SESSIONS_DIR, name + ext)
                if os.path.exists(p):
                    zf.write(p, f'{name}/{name}{ext}')
            # foto (any extension)
            for img_ext in ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp'):
                p = os.path.join(SESSIONS_DIR, name + '_foto' + img_ext)
                if os.path.exists(p):
                    zf.write(p, f'{name}/{name}_foto{img_ext}')
                    break
    buf.seek(0)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name='ensayos.zip')

# T5: toggle starred
@app.route('/api/sessions/<name>/star', methods=['POST'])
@login_required
def toggle_star(name):
    name = _safe_name(name)
    meta_path = os.path.join(SESSIONS_DIR, name + '_meta.json')
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    meta['starred'] = not meta.get('starred', False)
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return jsonify({'ok': True, 'starred': meta['starred']})

# F2.5: foto del ensayo
@app.route('/api/sessions/<name>/foto', methods=['POST'])
@login_required
def upload_foto(name):
    name = _safe_name(name)
    if 'foto' not in request.files:
        return jsonify({'ok': False, 'msg': 'sin archivo'}), 400
    f = request.files['foto']
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.heic', '.heif'):
        return jsonify({'ok': False, 'msg': 'Tipo de archivo no permitido'}), 400
    foto_path = os.path.join(SESSIONS_DIR, name + '_foto' + ext)
    f.save(foto_path)
    return jsonify({'ok': True, 'path': foto_path})

# T8: foto desde celular vía QR
FOTO_TOKEN_TTL = 600  # 10 minutos

def _clean_foto_tokens():
    now = time.time()
    expired = [k for k, v in _foto_tokens.items() if v['expires'] < now]
    for k in expired:
        p = _foto_tokens[k].get('path')
        if p and os.path.exists(p):
            try: os.remove(p)
            except: pass
        del _foto_tokens[k]

@app.route('/api/foto/token', methods=['POST'])
@login_required
def create_foto_token():
    _clean_foto_tokens()
    token = uuid.uuid4().hex
    _foto_tokens[token] = {'expires': time.time() + FOTO_TOKEN_TTL, 'path': None}
    base_url = request.host_url.rstrip('/')
    return jsonify({'ok': True, 'token': token, 'url': f'{base_url}/foto/{token}'})

@app.route('/foto/<token>', methods=['GET'])
def foto_upload_page(token):
    if token not in _foto_tokens or _foto_tokens[token]['expires'] < time.time():
        return '<h2>Enlace expirado o invalido</h2>', 410
    already = _foto_tokens[token]['path'] is not None
    tmpl = '''<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Foto de muestra</title>
<style>
body{font-family:system-ui,sans-serif;max-width:420px;margin:40px auto;padding:20px;text-align:center;background:#f8f9fa}
h2{color:#1e3a5f;margin-bottom:8px}
.sub{color:#666;font-size:.9rem;margin-bottom:32px}
label.btn{display:inline-block;padding:14px 28px;background:#1e3a5f;color:white;border-radius:8px;font-size:1rem;cursor:pointer}
input[type=file]{display:none}
#preview{margin-top:20px;max-width:100%;border-radius:8px;display:none}
#status{margin-top:16px;font-size:1rem;font-weight:600}
.ok{color:#16a34a}.err{color:#dc2626}
</style></head><body>
{% if already %}<h2>Foto recibida</h2><p class="sub">Ya se recibio una foto para este ensayo.</p>
{% else %}
<h2>Foto de muestra</h2>
<p class="sub">Toma o selecciona una foto de la muestra de ensayo</p>
<label class="btn">Tomar / Seleccionar foto
<input type="file" id="fInput" accept="image/*" capture="environment"></label>
<img id="preview" alt="preview">
<div id="status"></div>
<script>
document.getElementById('fInput').addEventListener('change',function(){
  var f=this.files[0];if(!f)return;
  var r=new FileReader();r.onload=function(e){var i=document.getElementById('preview');i.src=e.target.result;i.style.display='block';};r.readAsDataURL(f);
  var fd=new FormData();fd.append('foto',f);
  var st=document.getElementById('status');st.textContent='Subiendo...';st.className='';
  fetch('/foto/{{token}}/upload',{method:'POST',body:fd}).then(function(r){return r.json();}).then(function(d){
    if(d.ok){st.textContent='Foto enviada correctamente';st.className='ok';}
    else{st.textContent='Error: '+d.msg;st.className='err';}
  }).catch(function(){st.textContent='Error de red';st.className='err';});
});
</script>
{% endif %}
</body></html>'''
    return render_template_string(tmpl, token=token, already=already)

@app.route('/foto/<token>/upload', methods=['POST'])
def foto_upload_receive(token):
    if token not in _foto_tokens or _foto_tokens[token]['expires'] < time.time():
        return jsonify({'ok': False, 'msg': 'token invalido o expirado'}), 410
    if 'foto' not in request.files:
        return jsonify({'ok': False, 'msg': 'sin archivo'}), 400
    f = request.files['foto']
    ext = os.path.splitext(f.filename)[1].lower() if f.filename else '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp'):
        return jsonify({'ok': False, 'msg': 'tipo no permitido'}), 400
    old = _foto_tokens[token].get('path')
    if old and os.path.exists(old):
        try: os.remove(old)
        except: pass
    dest = os.path.join(FOTO_TMP_DIR, f'foto_{token}{ext}')
    f.save(dest)
    _foto_tokens[token]['path'] = dest
    return jsonify({'ok': True})

@app.route('/api/foto/token/<token>/status', methods=['GET'])
@login_required
def foto_token_status(token):
    if token not in _foto_tokens:
        return jsonify({'ok': False, 'ready': False}), 404
    info = _foto_tokens[token]
    if info['expires'] < time.time():
        return jsonify({'ok': False, 'ready': False, 'msg': 'expirado'}), 410
    ready = info['path'] is not None
    result = {'ok': True, 'ready': ready, 'expires_in': int(info['expires'] - time.time())}
    if ready:
        import base64
        try:
            with open(info['path'], 'rb') as fh:
                ext = os.path.splitext(info['path'])[1].lower().lstrip('.')
                mime = 'image/jpeg' if ext in ('jpg','jpeg','heic','heif') else f'image/{ext}'
                result['preview'] = f'data:{mime};base64,{base64.b64encode(fh.read()).decode()}'
        except: pass
    return jsonify(result)

@app.route('/api/foto/token/<token>/claim', methods=['POST'])
@login_required
def foto_token_claim(token):
    if token not in _foto_tokens:
        return jsonify({'ok': False, 'msg': 'token no encontrado'}), 404
    info = _foto_tokens[token]
    if not info['path'] or not os.path.exists(info['path']):
        return jsonify({'ok': False, 'msg': 'sin foto'}), 400
    body = request.json or {}
    name = _safe_name(body.get('name', ''))
    if not name:
        return jsonify({'ok': False, 'msg': 'nombre sesion requerido'}), 400
    ext = os.path.splitext(info['path'])[1]
    dest = os.path.join(SESSIONS_DIR, name + '_foto' + ext)
    import shutil
    shutil.move(info['path'], dest)
    del _foto_tokens[token]
    return jsonify({'ok': True, 'path': dest})

@app.route('/api/calibrate/zero', methods=['POST'])
@login_required
def calibrate_zero():
    # Promediar las muestras crudas del buffer de cada celda reduce ruido
    # en la tara (una sola lectura puede estar contaminada).
    with _lock:
        raw = dict(_last_raw)
        bufs = {f'celda_{i}': list(_zero_bufs[f'celda_{i}']) for i in range(1, 10)}
    if not raw:
        return jsonify({'ok': False, 'msg': 'Sin datos del Arduino'}), 400
    cal = load_cal()
    for i in range(1, 10):
        k = f'celda_{i}'
        samples = bufs.get(k) or ([float(raw[k])] if k in raw else [])
        if samples:
            cal[k]['offset'] = round(sum(samples) / len(samples), 1)
    save_cal(cal)
    return jsonify({'ok': True, 'msg': 'Zero seteado para las 9 celdas (promediado)'})

@app.route('/api/calibrate/pressure/zero', methods=['POST'])
@login_required
def calibrate_pressure_zero():
    if not _pressure_buf:
        return jsonify({'ok': False, 'msg': 'Sin datos del Arduino'}), 400
    avg = round(sum(_pressure_buf) / len(_pressure_buf), 4)
    cfg = load_filter()
    cfg['pressure_offset'] = avg
    save_filter(cfg)
    n = len(_pressure_buf)
    return jsonify({'ok': True, 'offset': avg, 'samples': n,
                    'msg': f'Zero presión: {avg:.2f} bar (promedio {n} lecturas)'})


@app.route('/api/sensors')
@login_required
def get_sensors():
    """Config declarativa para que el frontend arme gráficos/tabs/ejes."""
    return jsonify(_registry.frontend_config())

@app.route('/api/calibrate/dimension', methods=['POST'])
@login_required
def calibrate_dimension():
    with _lock:
        raw = dict(_last_raw)
    if not raw:
        return jsonify({'ok': False, 'msg': 'Sin datos del Arduino'}), 400
    point = (request.json or {}).get('point')
    sc = load_dimension_cal()
    raw_val = _raw_dimension(raw)   # acepta 'stroke' del Arduino
    if point == 'min':
        sc['raw_min'] = raw_val
    elif point == 'max':
        sc['raw_max'] = raw_val
    else:
        return jsonify({'ok': False, 'msg': 'point debe ser min o max'}), 400
    save_dimension_cal(sc)
    return jsonify({'ok': True, 'raw': raw_val, 'dimension_cal': sc})

@app.route('/api/calibrate/dimension', methods=['GET'])
@login_required
def get_dimension_cal():
    return jsonify(load_dimension_cal())

REFERENCE_FILE = os.path.join(BASE, 'reference_data.json')

@app.route('/api/references')
@login_required
def get_references():
    if os.path.exists(REFERENCE_FILE):
        with open(REFERENCE_FILE) as f:
            return jsonify(json.load(f))
    return jsonify([])

MANUAL_FILE = os.path.join(BASE, 'manual.md')

@app.route('/manual')
@login_required
def manual():
    if not os.path.exists(MANUAL_FILE):
        return 'Manual no encontrado', 404
    with open(MANUAL_FILE, encoding='utf-8') as f:
        src = f.read()
    if _HAS_MD:
        html = _md.markdown(src, extensions=['tables', 'fenced_code'])
    else:
        html = f'<pre>{src}</pre>'
    return render_template('manual.html', content=html)

if __name__ == '__main__':
    print('Abre http://localhost:5050 en tu navegador')
    socketio.run(app, host='0.0.0.0', port=5050, debug=False, allow_unsafe_werkzeug=True)
