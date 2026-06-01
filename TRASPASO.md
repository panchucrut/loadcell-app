# TRASPASO — Estándar de continuidad entre conversaciones

Este archivo es el **prompt base** para iniciar una conversación nueva con Claude sin perder contexto.
Cópialo y pégalo tal cual al abrir un chat nuevo. **No tienes que rellenar nada.**
Cuando Claude te pida el estado de git, pega la salida y listo.

---

## Prompt para pegar (copiar todo el bloque)

```
Proyecto Sensores Prensa — Load Cell App. Repo: https://github.com/panchucrut/loadcell-app.git. Branch de trabajo: refactor/sensors-declarative (rama buena, completa: presión + seguridad SEC-001..008 + todos los fixes). La rama main quedó vieja en 8ecaae9 — ignorarla. Stack: Flask+SocketIO+Chart.js, 9 celdas HX711, Arduino Mega→USB→Mac (puerto /dev/cu.usbmodem14101), sin pandas (solo csv+openpyxl), módulos en core/.

PRIMER PASO OBLIGATORIO de esta conversación: pídeme que pegue la salida de `git rev-parse --short HEAD && git log --oneline -3` y úsala como estado real. NUNCA asumas el HEAD ni lo des por sabido: el hash cambia tras cada git am aunque el contenido sea idéntico. No generes ningún patch antes de confirmar el HEAD real conmigo.

ARRANQUE LOCAL (siempre exactamente así): lsof -ti:5050 | xargs kill -9; SECRET_KEY=x LOCAL_MODE=true python3 app.py → http://localhost:5050. Si se arranca SIN SECRET_KEY y LOCAL_MODE, el @login_required redirige a Azure AD (que no existe en local), devuelve HTML, y provoca errores 500 y "Unexpected token '<'... is not valid JSON". PROD: gunicorn -k eventlet -w 1 -b 127.0.0.1:5050 app:app (app.py lanza RuntimeError a propósito si se intenta arrancar con Werkzeug fuera de LOCAL_MODE).

FLUJO DE TRABAJO (obligatorio): Claude NO corre nada en el Mac del usuario ni tiene acceso a su GitHub. Claude trabaja en su propio contenedor: clona el repo, edita, valida, commitea y entrega un archivo .patch. El usuario lo aplica en su Mac con: git am ~/Downloads/X.patch && git push origin refactor/sensors-declarative, luego reinicia la app y recarga el navegador con Cmd+Shift+R.
Reglas al generar patches: confirmar el HEAD real primero; generar con <HEADreal>..HEAD; usar --binary si el patch incluye archivos binarios; SIEMPRE simular git am en un clon limpio en /tmp antes de entregar. Validar JS tocado: extraer los <script> sin src, sustituir Jinja {{...}} por []/0, correr node --check. Validar backend: SECRET_KEY=x LOCAL_MODE=true python3 -c "import app".
Estilo: respuestas MUY CORTAS. El usuario se frustra con texto largo y con diagnósticos erróneos — VERIFICAR con datos reales antes de afirmar nada. Dar un solo comando a la vez si está perdido. Cuando se necesite un log o traceback, insistir en que lo pegue el usuario (Claude no puede obtenerlo). Tokens/credenciales nunca en el chat. La SEGURIDAD del software es la prioridad #1 del usuario.

HISTORIAL RECIENTE (commits, del más viejo al más nuevo): SEC-005 self-host de librerías + SRI → fix CSP (SEC-004 bloqueaba el <script> inline propio; se añadió 'unsafe-inline' a script-src) → fix NameError re→_re en post_ensayo_tipos (causaba 500 al agregar tipos de ensayo) → feat alarma "Cualquier celda (individual)". Toda la seguridad SEC-001..008 está completa y pusheada. Librerías (bootstrap 5.3.2, chart.js 4.4.0, socket.io 4.7.2, qrcodejs 1.0.0) self-hosted en /static/vendor con integrity SRI; CSP sin CDNs externos salvo 'unsafe-inline' para el script inline propio.

ALARMA DE LÍMITE (templates/index.html: UI ~L385-410, JS checkAlarm/showAlarmPopup ~L869-916): selector alarmSensor con opciones — total (suma de las 9 celdas), any_cell (primera celda individual que cruza el umbral; el popup indica cuál, ej. "Celda 3"), pressure, dimension. Histéresis: se rearma al bajar por debajo del 90% del umbral. La carga TOTAL se calcula como suma de celdas (sum de LOADCELL_IDS).

TAREA EN CURSO — afinar la máquina de estados con CERO REAL:
Pendiente inmediato: re-tarar la celda c9 desde la UI con la prensa DESCARGADA (c9 marcaba 26 kg estando desconectada = offset de calibración malo) y confirmar que las 9 celdas marcan ~0 kg. Solo entonces capturar el ruido en cero para fijar parámetros con datos reales.
Parámetros actuales en filter_config.json: median_window=7, noise_floor=5.0, trigger_kg=30, trigger_count=8, stop_count=15, pressure_offset=12.1603. Defaults adicionales en app.py L93-106: trigger_bar=20, drop_pct=30, stab_pct=2.0, stab_secs=3.0. Worker de la máquina de estados en app.py ~L435-560 (calcula total, evalúa triggers/pico/drop/estabilización). Con la captura en cero real fijar: noise_floor (~2-3 kg probable), median_window, alpha del EMA de presión, trigger_kg/trigger_bar/trigger_count, drop_pct/stop_count, stab_pct/stab_secs.

BACKLOG (próximas tareas, no urgentes): (1) actualizar la rama main para que apunte a refactor/sensors-declarative y evitar la confusión de ramas divergentes. (2) Deploy online: ya existe cloudflared-config.yml en el repo; falta completar registro de app en Azure AD + redirect URI, definir sitio SharePoint destino, storage vía OneDrive/SharePoint Graph (Supabase fue descartado), hosting cPanel en ensayos.dexfloor.com. (3) PCB en EasyEDA PRO: resolver borneras/screw terminals sin símbolo → completar esquemático → generar Gerbers → ordenar en JLCPCB.
```

---

## Cómo mantenerlo al día

Las secciones **HISTORIAL RECIENTE**, **ALARMA / features** y **TAREA EN CURSO** cambian con el tiempo.
Al cerrar una conversación, pídele a Claude: *"dame el prompt de traspaso actualizado"* y reemplaza el bloque de arriba con el que te entregue.
El bloque de **FLUJO** y **ARRANQUE** es estable y casi nunca cambia.
