"""
Entrypoint WSGI para cPanel (Passenger) — instancia ONLINE de SOLO CONSULTA.

cPanel "Setup Python App" arranca la app importando la variable `application`
desde este archivo. Passenger corre WSGI síncrono: NO hay Socket.IO en vivo
(Monitor en tiempo real). Esta instancia es de consulta: ver/analizar ensayos
ya guardados. La captura desde el Arduino vive solo en el Mac local.

Forzamos CONSULTA_MODE=true ANTES de importar app, como defensa adicional:
aunque el .env de cPanel ya debe traerlo, esto garantiza que esta instancia
nunca exponga Monitor/Config ni las rutas de hardware/captura/config.
"""
import os

# Defensa: esta instancia es SIEMPRE de consulta, pase lo que pase en .env.
os.environ['CONSULTA_MODE'] = 'true'
# Nunca bypass de auth en el cloud.
os.environ['LOCAL_MODE'] = 'false'

# app.py llama load_dotenv() al importarse: el .env de cPanel define
# SECRET_KEY, ALLOWED_ORIGINS, AZURE_*, GRAPH_*, etc.
from app import app as application
