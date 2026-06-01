#!/usr/bin/env python3
"""
Resuelve site-id y drive-id reales de SharePoint vía Graph API (app-only).
Corre LOCAL en el Mac. Usa las credenciales del .env. No imprime secretos.

Requisitos en Azure (permisos de APLICACION, no delegados) + admin consent:
  - Sites.Read.All  (o Sites.ReadWrite.All)

Uso:
  python3 scripts/resolve_sharepoint_ids.py
"""
import os
import sys

try:
    import msal
except ImportError:
    sys.exit("Falta dependencia: pip install --break-system-packages msal")
import json as _json
import urllib.request
import urllib.error

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CLIENT_ID = os.getenv('AZURE_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET', '')
TENANT_ID = os.getenv('AZURE_TENANT_ID', '')

HOSTNAME = 'dexfloor.sharepoint.com'
SITE_PATH = '/sites/DEXFloor'   # ajustar si el path real difiere

if not (CLIENT_ID and CLIENT_SECRET and TENANT_ID):
    sys.exit("Faltan AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID en .env")

authority = f'https://login.microsoftonline.com/{TENANT_ID}'
app = msal.ConfidentialClientApplication(
    CLIENT_ID, authority=authority, client_credential=CLIENT_SECRET,
)
tok = app.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])
if 'access_token' not in tok:
    sys.exit(f"Error token: {tok.get('error_description', tok.get('error'))}")

H = {'Authorization': 'Bearer ' + tok['access_token']}
G = 'https://graph.microsoft.com/v1.0'

def _get(url):
    req = urllib.request.Request(url, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, _json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')[:300]

# 1) site-id
st, site = _get(f'{G}/sites/{HOSTNAME}:{SITE_PATH}')
if st != 200:
    sys.exit(f"Error site ({st}): {site}")
site_id = site['id']
print("SITE_ID  =", site_id)
print("SITE_NAME=", site.get('displayName'))

# 2) drives del sitio
st, drives = _get(f'{G}/sites/{site_id}/drives')
if st != 200:
    sys.exit(f"Error drives ({st}): {drives}")
print("\nDRIVES disponibles:")
for d in drives.get('value', []):
    print(f"  DRIVE_ID = {d['id']}")
    print(f"     name  = {d.get('name')}  | webUrl = {d.get('webUrl')}")

print("\nGuarda en .env:")
print("  GRAPH_SITE_ID=<SITE_ID de arriba>")
print("  GRAPH_DRIVE_ID=<DRIVE_ID del drive 'Documents'/'Documentos'>")
