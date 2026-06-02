"""Subida de ensayos a SharePoint vía Microsoft Graph (app-only). Sin pandas.

Diseño:
- Token app-only (client credentials) cacheado en memoria con refresh por expiración.
- La subida NUNCA debe romper el guardado local: todo error se captura y se
  reporta como (False, motivo). El local es la verdad.
- Sube CSV + XLSX + _meta.json + foto (si existe) de una sesión a:
    /sites/{SITE_ID}/drives/{DRIVE_ID}/root:/{BASE_FOLDER}/{name}/{archivo}:/content
- Subida simple PUT (archivos < 4 MB; sesiones de ensayo son pequeñas).

Config por entorno (.env):
  AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID  (ya existen)
  GRAPH_SITE_ID, GRAPH_DRIVE_ID                          (resolver con script)
  GRAPH_BASE_FOLDER  (opcional, default 'Ensayos Pisos DEX')

Requiere permiso de APLICACION Sites.ReadWrite.All + admin consent en Azure.
"""
import os
import time
import threading

_GRAPH = 'https://graph.microsoft.com/v1.0'
_SCOPE = ['https://graph.microsoft.com/.default']

_lock = threading.Lock()
_token = {'value': None, 'exp': 0.0}


def _cfg():
    return {
        'client_id':   os.getenv('AZURE_CLIENT_ID')     or os.getenv('CLIENT_ID', ''),
        'client_secret': os.getenv('AZURE_CLIENT_SECRET') or os.getenv('CLIENT_SECRET', ''),
        'tenant_id':   os.getenv('AZURE_TENANT_ID')     or os.getenv('TENANT_ID', ''),
        'site_id':     os.getenv('GRAPH_SITE_ID', ''),
        'drive_id':    os.getenv('GRAPH_DRIVE_ID', ''),
        'base_folder': os.getenv('GRAPH_BASE_FOLDER', 'Ensayos Pisos DEX'),
    }


def is_configured():
    c = _cfg()
    return all([c['client_id'], c['client_secret'], c['tenant_id'],
                c['site_id'], c['drive_id']])


def _get_token():
    """Token app-only con cache simple. Devuelve str o None."""
    with _lock:
        now = time.time()
        if _token['value'] and now < _token['exp'] - 60:
            return _token['value']
        try:
            import msal
        except ImportError:
            return None
        c = _cfg()
        app = msal.ConfidentialClientApplication(
            c['client_id'],
            authority=f"https://login.microsoftonline.com/{c['tenant_id']}",
            client_credential=c['client_secret'],
        )
        res = app.acquire_token_for_client(scopes=_SCOPE)
        tok = res.get('access_token')
        if not tok:
            return None
        _token['value'] = tok
        _token['exp'] = now + float(res.get('expires_in', 3600))
        return tok


def _put_file(token, remote_path, data_bytes):
    """PUT subida simple con urllib (sin dependencia requests). Devuelve (ok, motivo)."""
    import urllib.request
    import urllib.parse
    import urllib.error
    c = _cfg()
    seg = urllib.parse.quote(remote_path)
    url = (f"{_GRAPH}/sites/{c['site_id']}/drives/{c['drive_id']}"
           f"/root:/{seg}:/content")
    req = urllib.request.Request(
        url, data=data_bytes, method='PUT',
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/octet-stream'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 201):
                return True, 'ok'
            return False, f'HTTP {resp.status}'
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:200]
        return False, f'HTTP {e.code}: {body}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def upload_session(sessions_dir, name):
    """Sube todos los artefactos de la sesión `name` a SharePoint.

    Devuelve (ok: bool, detalle: str). No lanza excepciones.
    `name` es el nombre base (sin extensión), igual al de recorder.save_session.
    """
    if not is_configured():
        return False, 'Graph no configurado (faltan GRAPH_SITE_ID/GRAPH_DRIVE_ID o credenciales)'
    try:
        token = _get_token()
        if not token:
            return False, 'No se pudo obtener token app-only de Graph'

        c = _cfg()
        base = c['base_folder'].strip('/')
        candidates = [
            name + '.csv',
            name + '.xlsx',
            name + '_meta.json',
        ]
        # foto: buscar cualquier archivo que empiece con name y sea imagen
        for fn in os.listdir(sessions_dir):
            low = fn.lower()
            if fn.startswith(name) and low.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                candidates.append(fn)

        uploaded, errors = [], []
        for fn in candidates:
            local = os.path.join(sessions_dir, fn)
            if not os.path.isfile(local):
                continue
            with open(local, 'rb') as f:
                data = f.read()
            remote = f'{base}/{name}/{fn}'
            ok, why = _put_file(token, remote, data)
            (uploaded if ok else errors).append(fn if ok else f'{fn} ({why})')

        if errors:
            return False, 'Fallaron: ' + '; '.join(errors)
        if not uploaded:
            return False, 'No se encontraron archivos para subir'
        return True, 'Subidos: ' + ', '.join(uploaded)
    except Exception as e:  # nunca romper el guardado local
        return False, f'Excepción: {type(e).__name__}: {e}'


# ── Lectura / sync de bajada (instancia CONSULTA_MODE en cloud) ────────────────
def _get_json(token, url):
    """GET JSON con urllib. Devuelve (dict|None, motivo)."""
    import urllib.request, urllib.error
    req = urllib.request.Request(
        url, method='GET',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json as _json
            return _json.loads(resp.read().decode('utf-8')), 'ok'
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:200]
        return None, f'HTTP {e.code}: {body}'
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _get_bytes(token, url):
    """GET binario con urllib. Devuelve (bytes|None, motivo)."""
    import urllib.request, urllib.error
    req = urllib.request.Request(
        url, method='GET', headers={'Authorization': 'Bearer ' + token},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read(), 'ok'
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}'
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _children_url(path):
    """URL de listado de hijos de una carpeta (path relativo al root del drive)."""
    import urllib.parse
    c = _cfg()
    p = path.strip('/')
    if p:
        seg = urllib.parse.quote(p)
        return (f"{_GRAPH}/sites/{c['site_id']}/drives/{c['drive_id']}"
                f"/root:/{seg}:/children")
    return (f"{_GRAPH}/sites/{c['site_id']}/drives/{c['drive_id']}/root/children")


def sync_down(sessions_dir):
    """Baja ensayos desde SharePoint a `sessions_dir` (solo nuevos/cambiados).

    Recorre BASE_FOLDER/<sesion>/<archivos> y los escribe planos en
    sessions_dir (mismo layout que el guardado local). Compara por tamaño:
    si el archivo local existe con igual tamaño, no lo vuelve a bajar.
    No lanza excepciones. Devuelve (ok: bool, detalle: str).
    """
    if not is_configured():
        return False, 'Graph no configurado'
    try:
        token = _get_token()
        if not token:
            return False, 'No se pudo obtener token app-only de Graph'
        c = _cfg()
        base = c['base_folder'].strip('/')

        folders, why = _get_json(token, _children_url(base))
        if folders is None:
            # carpeta base inexistente aún: no es error fatal
            if 'HTTP 404' in (why or ''):
                return True, 'Carpeta base vacía/inexistente'
            return False, why
        bajados, errores = 0, []
        for item in folders.get('value', []):
            if 'folder' not in item:
                continue
            sesion = item.get('name', '')
            if not sesion:
                continue
            files, fwhy = _get_json(token, _children_url(f'{base}/{sesion}'))
            if files is None:
                errores.append(f'{sesion} ({fwhy})')
                continue
            for fitem in files.get('value', []):
                if 'file' not in fitem:
                    continue
                fn = fitem.get('name', '')
                size = fitem.get('size')
                dl = fitem.get('@microsoft.graph.downloadUrl')
                if not fn or not dl:
                    continue
                local = os.path.join(sessions_dir, fn)
                if os.path.isfile(local) and size is not None:
                    if os.path.getsize(local) == size:
                        continue  # ya está, sin cambios
                data, dwhy = _get_bytes(token, dl)
                if data is None:
                    errores.append(f'{sesion}/{fn} ({dwhy})')
                    continue
                tmp = local + '.tmp'
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, local)
                bajados += 1
        if errores:
            return False, f'Bajados {bajados}; fallaron: ' + '; '.join(errores[:5])
        return True, f'Bajados {bajados} archivo(s)'
    except Exception as e:
        return False, f'Excepción: {type(e).__name__}: {e}'
