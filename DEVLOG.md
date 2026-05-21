# DEVLOG — Sensores Prensa / Load Cell App

## [2026-05-21] FASE 0 — Hardening inicial

### F0.1–F0.2 — .env + SECRET_KEY
- Creado `.env` con `SECRET_KEY=loadcell2024`
- `app.py` ahora usa `python-dotenv`: `os.getenv('SECRET_KEY', 'loadcell2024')`
- Agregar `python-dotenv` a `requirements.txt`

### F0.3 — Cache en memoria
- `calibration.json` y `stroke_cal.json` se cargan una sola vez al primer acceso
- Globals: `_cal_cache`, `_stroke_cal_cache`
- `save_*` actualiza cache + disco en la misma operación

### F0.4 — .gitignore
- Agregados: `.env`, `stroke_cal.json`, `__pycache__/`, `sessions/`, `*.log`

### F0.5 — Este archivo

### F0.6 — fix reference_data.json
- Aplicado en sesión anterior
