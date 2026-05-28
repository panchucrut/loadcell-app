import os, json, time, glob, threading, csv, statistics, uuid
from collections import deque
from serial.tools import list_ports
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, render_template_string, jsonify, request, redirect, url_for, session, send_file
from flask_socketio import SocketIO
import serial
import msal
from dotenv import load_dotenv
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

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('auth_login', next=request.url))
        return f(*args, **kwargs)
    return decorated
_ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'https://sensores.dexfloor.com').split(',')
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
}

ENSAYO_TIPOS_FILE = os.path.join(BASE_DIR, 'ensayo_tipos.json')
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

_med_bufs = {f'celda_{i}': deque() for i in range(1, 10)}
_last_raw = {}

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

def apply_cal(raw, cal):
    out = {}
    for i in range(1, 10):
        k = f'celda_{i}'
        out[k] = round((float(raw.get(k, 0)) - cal[k]['offset']) / cal[k]['scale'], 2)
    sc = load_dimension_cal()
    raw_s = float(raw.get('dimension', 0))
    span = sc['raw_max'] - sc['raw_min']
    out['dimension'] = round((raw_s - sc['raw_min']) / span * (sc['mm_max'] - sc['mm_min']) + sc['mm_min'], 2) if span != 0 else round(raw_s, 2)
    out['pressure'] = round(float(raw.get('pressure', 0)), 2)
    return out

def apply_filter(data, cfg):
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

def _save_session(buf, meta=None):
    """Save buffer to CSV + XLSX + meta.json, return session name."""
    if not buf:
        return None
    ts   = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    tipo = (meta or {}).get('tipo', 'sesion')
    name = f'{tipo}_{ts}'
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
    # F2.3: save meta.json
    if meta:
        meta_out = dict(meta)
        meta_out['timestamp'] = ts
        meta_out['rows'] = len(buf)
        meta_out['nombre_archivo'] = name
        meta_out.setdefault('starred', False)
        with open(os.path.join(SESSIONS_DIR, name + '_meta.json'), 'w') as f:
            json.dump(meta_out, f, indent=2, ensure_ascii=False)
    return name

# ── serial worker ──────────────────────────────────────────────────────────────
def _serial_worker():
    global _ser_running, _recording, _session_buf, _above_count, _below_count, _t_record_start, _dimension_record_offset
    t0  = time.time()
    ser = None
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
                                    _dimension_record_offset = data['dimension']
                                _above_count = 0
                                socketio.emit('auto_record', {'state': 'started'})
                        else:
                            _above_count = 0
                    else:
                        if total < thr * 0.4:
                            _below_count += 1
                            _above_count  = 0
                            if _below_count >= int(cfg['stop_count']):
                                _recording = False
                                with _lock:
                                    buf = list(_session_buf)
                                    meta = dict(_ensayo_meta)
                                _below_count = 0
                                name = _save_session(buf, meta)
                                socketio.emit('auto_record', {
                                    'state': 'stopped',
                                    'name': name,
                                    'rows': len(buf)
                                })
                        else:
                            _below_count = 0

                if _recording and _t_record_start is not None:
                    data['t_rel']      = round(data['t'] - _t_record_start, 2)
                    data['dimension_rel'] = round(data['dimension'] - _dimension_record_offset, 2)
                else:
                    data['t_rel']      = 0.0
                    data['dimension_rel'] = 0.0

                socketio.emit('data', data)
                if _recording:
                    with _lock:
                        rec = dict(data)
                        rec['t']      = data['t_rel']
                        rec['dimension'] = data['dimension_rel']
                        _session_buf.append(rec)

            except (json.JSONDecodeError, KeyError):
                continue

    except serial.SerialException as e:
        socketio.emit('status', {'connected': False, 'error': str(e)})
    finally:
        if ser and ser.is_open:
            ser.close()
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

@app.route('/api/record/start', methods=['POST'])
@login_required
def rec_start():
    global _recording, _session_buf, _t_record_start, _dimension_record_offset
    with _lock:
        last = dict(_session_buf[-1]) if _session_buf else {}
        _t_record_start       = last.get('t', 0.0)
        _dimension_record_offset = last.get('dimension', 0.0)
        _session_buf          = []
        _recording            = True
    return jsonify({'ok': True})

@app.route('/api/record/stop', methods=['POST'])
@login_required
def rec_stop():
    global _recording
    with _lock:
        was_recording = _recording
        _recording = False
        if not was_recording:
            return jsonify({'ok': False, 'msg': 'no estaba grabando'})
        buf  = list(_session_buf)
        _session_buf.clear()
        meta = dict(_ensayo_meta)
    name = _save_session(buf, meta)
    if not name:
        return jsonify({'ok': False, 'msg': 'sin datos'})
    return jsonify({'ok': True, 'name': name, 'rows': len(buf)})

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

@app.route('/api/calibrate/dimension', methods=['POST'])
@login_required
def calibrate_dimension():
    with _lock:
        raw = dict(_last_raw)
    if not raw:
        return jsonify({'ok': False, 'msg': 'Sin datos del Arduino'}), 400
    point = (request.json or {}).get('point')
    sc = load_dimension_cal()
    raw_val = float(raw.get('dimension', 0))
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
