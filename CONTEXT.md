# CONTEXT.md — Sensores Prensa App
> Actualizado: 2026-05-22. Leer ANTES de editar cualquier archivo.

## Stack
Flask + SocketIO · Chart.js · Arduino Mega + 9× HX711 · openpyxl + csv (sin pandas)
`python3 app.py` → http://localhost:5050

## Archivos clave
| Archivo | Rol |
|---------|-----|
| `app.py` | Servidor principal Flask+SocketIO |
| `templates/index.html` | UI completa (1108 líneas) |
| `calibration.json` | Offsets y escalas 9 celdas |
| `filter_config.json` | Filtro mediana, noise floor, auto-record |
| `reference_data.json` | Ensayos de referencia para gráfico comparativo |
| `cloudflared-config.yml` | Config túnel Cloudflare → sensores.dexfloor.com |
| `start.sh` / `stop.sh` | Levanta/detiene Flask + tunnel |
| `DEVPLAN.md` | Plan completo de fases F0–F9 |
| `DEVLOG.md` | Bitácora de cambios |

## Hardware
- Mac local IP: 192.168.68.62
- Puerto Arduino: buscar con `ls /dev/tty.usb*`
- Arduino Mega + 9× HX711 via USB
- Ejecutable en escritorio: `SensoresPrensaLauncher.app` → `Contents/MacOS/launch.sh`

## Tunnel Cloudflare
- Tunnel ID: `2ccf5e13-34fb-44e8-9853-744ac846b560`
- Credenciales: `~/.cloudflared/2ccf5e13-34fb-44e8-9853-744ac846b560.json`
- Hostname activo: `sensores.dexfloor.com` (CNAME en dexfloor.com con proxy Cloudflare)
- Hostname alternativo: `sensores.celtavia.cl`

## Fases completadas
| Fase | Descripción |
|------|-------------|
| F0 | dotenv, cache memoria, .gitignore, DEVLOG |
| F1 | T=0 relativo, d=0 relativo, controles rango X/Y |
| F2 | Modal metadata ensayo, prefijo tipo archivo, meta.json, historial enriquecido, foto capture |
| F3 | Pestaña Análisis, comparativo hasta 6 ensayos, rango XY, toggle curvas, exportar PNG |
| F4 | manual.md, ruta /manual Flask, template manual.html, botón 📖 en navbar |
| F5 | cloudflared tunnel, sensores.dexfloor.com, start.sh, stop.sh |

## Siguiente fase
**F6 — Auth Azure AD** (ver DEVPLAN.md §7)
- Registrar app en portal.azure.com con redirect URI: `https://sensores.dexfloor.com/auth/callback`
- msal en Flask, @login_required, restricción a tenant dexfloor.com

## Monitor — 3 gráficos SIEMPRE (invariable)
| # | Canvas ID | Contenido | Ejes |
|---|-----------|-----------|------|
| 1 | `chartCarga` | 9 celdas de carga (kg) vs Tiempo (s) | X=tiempo, Y=kg |
| 2 | `chartDist` | Distancia (mm) vs Tiempo (s) | X=tiempo, Y=mm |
| 3 | `chartCargaDist` | Carga total (kg) vs Distancia (mm) | X=mm, Y=kg |

**Reglas:**
- "Stroke" NO existe en la UI — siempre llamar "Distancia" (variable interna puede seguir siendo stroke)
- Presión NO aparece en gráficos de Monitor (solo en stat-card)
- Chart3 debe actualizarse in-place (no reasignar `.data.datasets`) para evitar bug de no render

## Reglas invariables
- Sin pandas — usar `csv` y `openpyxl`
- Calibración: `kg = (raw - offset) / scale`
- Pressure EMA α=0.2, `_ema_pressure` se resetea al conectar Arduino
- Stroke: raw 0.49→0mm, 98.24→100mm (configurable en filter_config.json)
- SECRET_KEY via .env (nunca hardcodeada)

## Bugs conocidos
| # | Bug | Impacto |
|---|-----|---------|
| B2 | Noise floor: C3–C9 sin carga muestran ruido → escala Y distorsionada | Alto |
| B_chart2 | Chart2 no renderiza en tiempo real si se reasigna `chart2.data.datasets` | Alto |

## Notas de calibración
- Presión: factor corrección 1.6772, scale=0.596282 en calibration.json
- Fórmula igual a celdas: `(raw - offset) / scale`
- Área pistón ∅75mm = 4418 mm² (usado para verificación)
