# Sensores Prensa — Plan de Desarrollo
**Repo:** https://github.com/panchucrut/loadcell-app  
**Fecha:** Mayo 2025  
**Stack actual:** Flask + SocketIO · Chart.js · Arduino Mega + 9× HX711 · openpyxl + csv

---

## 1. Estado actual — Lo que funciona

| Módulo | Estado |
|--------|--------|
| Lectura serial Arduino → Flask | ✅ Operativo |
| Calibración offset/escala 9 celdas | ✅ Operativo |
| Filtro mediana configurable | ✅ Operativo |
| Calibración stroke (min/max) | ✅ Operativo |
| Filtro EMA presión (α=0.2) | ✅ Operativo |
| Auto-record con histéresis | ✅ Operativo |
| Autosave + recovery crash | ✅ Operativo |
| Export CSV + XLSX (sin pandas) | ✅ Operativo |
| UI responsiva (móvil) | ✅ Operativo |
| 3 gráficos en Monitor | ✅ Operativo |
| Wizard calibración in situ | ✅ Operativo |

---

## 2. Bugs conocidos / deuda técnica

| # | Problema | Impacto |
|---|----------|---------|
| B1 | Gráficos referenciales no renderizan — loadRefs() espera estructura distinta a la del JSON real | Alto |
| B2 | Noise floor: C3–C9 sin carga muestran ruido → escala Y distorsionada | Alto |
| B3 | SECRET_KEY hardcodeada en app.py | Alto (seguridad) |
| B4 | T=0 no coincide con inicio de carga — el tiempo es absoluto desde conexión Arduino | Alto |
| B5 | Distancia no parte desde d=0 al iniciar ensayo | Alto |
| B6 | load_cal() y load_dist_cal() se leen en cada línea serial (I/O innecesario) | Medio |
| B7 | stroke_cal.json y .env no están en .gitignore | Bajo |
| B8 | Sin DEVLOG.md | Bajo |

---

## 3. Requerimientos pendientes

| Req | Descripción | Prioridad |
|-----|-------------|-----------|
| R1 | Deploy online — acceso desde cualquier lugar | 🔴 Crítico |
| R2 | Auth restringida a @dexfloor.com (Microsoft 365) | 🔴 Crítico |
| R3 | Almacenamiento cloud de ensayos (subir, consultar, descargar) | 🔴 Crítico |
| R4 | Gráficos referenciales funcionando (fix B1) | 🔴 Crítico |
| R5 | T=0 = inicio de carga; distancia d=0 al iniciar ensayo | 🔴 Crítico |
| R6 | Tipo de ensayo seleccionable antes de grabar | 🟠 Alto |
| R7 | Ajuste manual de rango ejes X e Y en todos los gráficos | 🟠 Alto |
| R8 | Pestaña Análisis — cargar múltiples ensayos de BD y comparar en un gráfico | 🟠 Alto |
| R9 | Gráfico comparativo con ajuste de rango X e Y | 🟠 Alto |
| R10 | Metadata configurable por ensayo (título, material, dimensiones, operador, notas) | 🟠 Alto |
| R11 | Foto por ensayo (JPG / HEIC desde cámara móvil) | 🟠 Alto |
| R12 | Manual de uso accesible desde la página web | 🟠 Alto |
| R13 | Operación móvil in situ sin computador al lado | 🟠 Alto |
| R14 | Seguridad robusta — HTTPS, CSRF, rate limiting, sanitización | 🟠 Alto |
| R15 | DEVLOG.md — bitácora de cambios | 🟡 Medio |
| R16 | Tunnel Cloudflare con dominio pruebas.dexfloor.com | 🟡 Medio |

---

## 4. Tipos de ensayo

Por ahora el comportamiento es idéntico en todos — solo cambia el prefijo del archivo guardado y la metadata.

| Tipo | Prefijo archivo |
|------|----------------|
| Compresión cubo | comp_cubo_ |
| Compresión piso | comp_piso_ |
| Deformación esquina piso | def_esquina_ |
| Deformación total | def_total_ |

**Reglas comunes a todos los tipos:**
- T=0 = momento en que se detecta carga (celda de carga supera umbral, o presión si no hay celdas)
- d=0 = posición del stroke al iniciar el ensayo (offset aplicado automáticamente)
- El nombre del archivo = {prefijo}{timestamp}

**Extensibilidad futura:** cada tipo podrá tener su propio setup (celdas activas, umbrales, cálculos derivados) sin romper los existentes.

---

## 5. Stack tecnológico definitivo

| Capa | Tecnología | Justificación |
|------|-----------|---------------|
| Servidor local | Flask + SocketIO | Ya implementado |
| Hardware | Arduino Mega + 9× HX711 | Ya implementado |
| Auth | Azure AD (MSAL) | Ya incluido en M365 dexfloor.com, sin costo extra |
| Tunnel | Cloudflare Tunnel | Expone Mac local sin abrir puertos, HTTPS automático |
| Storage archivos | OneDrive / SharePoint vía Graph API | M365, acepta CSV, XLSX, JPG, HEIC sin conversión |
| Índice / metadata | SharePoint Lists vía Graph API | Base de datos ligera nativa en M365, sin costo extra |
| UI | Chart.js + Bootstrap 5 | Ya implementado |

**Sin Supabase. Sin Railway. Todo dentro del ecosistema Microsoft 365.**

---

## 6. Arquitectura objetivo

```
┌─────────────────────────────────────┐
│  Mac (local — sala de ensayos)      │
│  Arduino Mega → USB                 │
│  Flask app (puerto 5050)            │
│  cloudflared tunnel                 │
└──────────────┬──────────────────────┘
               │ HTTPS / WSS
        pruebas.dexfloor.com
               │
    ┌──────────▼──────────────┐
    │  Cloudflare (SSL proxy) │
    └──────────┬──────────────┘
               │
    ┌──────────▼──────────────┐     ┌────────────────────────────┐
    │  Flask app              │────▶│  Microsoft Graph API       │
    │  + SocketIO             │     │  ├── OneDrive/SharePoint   │
    │  + MSAL auth middleware │     │  │   CSV, XLSX, JPG, HEIC  │
    └─────────────────────────┘     │  └── SharePoint Lists      │
               │                   │      (índice de ensayos)    │
    Auth via Azure AD               └────────────────────────────┘
    Restringido a @dexfloor.com

Móvil in situ → browser → pruebas.dexfloor.com → UI responsiva
             → controla Mac local vía WebSocket
```

---

## 7. Plan de desarrollo — Fases

### FASE 0 — Higiene y seguridad base (1–2 días)
*Prerequisito obligatorio.*

- [ ] F0.1 Crear .env con SECRET_KEY, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID
- [ ] F0.2 Mover SECRET_KEY a variable de entorno en app.py (python-dotenv)
- [ ] F0.3 Cachear calibration.json y stroke_cal.json en memoria (invalidar solo al guardar)
- [ ] F0.4 Agregar stroke_cal.json y .env a .gitignore
- [ ] F0.5 Crear DEVLOG.md con historial de cambios hasta hoy
- [ ] F0.6 Push del fix reference_data.json al repo

---

### FASE 1 — Fixes críticos de datos y gráficos (1–2 días)
*Sin esto los datos grabados no son confiables.*

- [ ] F1.1 Fix gráficos referenciales — loadRefs() adaptado a estructura real del JSON (desp, carga_n → kgf)
- [ ] F1.2 Fix T=0 — el tiempo relativo al ensayo parte desde el momento en que se supera el umbral de carga (o presión si no hay celdas). Los datos grabados antes del trigger se descartan o se marcan t<0
- [ ] F1.3 Fix d=0 — la distancia en el CSV/XLSX usa distancia_rel (ya calculada en app.py), verificar que el gráfico Monitor la muestre en lugar de distancia absoluta
- [ ] F1.4 Verificar noise floor con datos reales — ajustar valor por defecto si es necesario
- [ ] F1.5 Ajuste manual de rango ejes X e Y en gráfico Monitor (inputs min/max sobre cada gráfico)
- [ ] F1.6 Ajuste manual de rango ejes X e Y en gráfico Referencias

---

### FASE 2 — Metadata y tipo de ensayo (1–2 días)

- [ ] F2.1 Modal "Configurar ensayo" antes de grabar:
  - Tipo: Compresión cubo / Compresión piso / Deformación esquina / Deformación total
  - Campos: material, dimensiones, operador, notas
  - Título auto-generado: {prefijo}{timestamp} (editable)
- [ ] F2.2 Prefijo de archivo según tipo seleccionado
- [ ] F2.3 Guardar metadata en {nombre_ensayo}_meta.json junto al CSV/XLSX
- [ ] F2.4 Mostrar tipo + metadata en historial de sesiones local
- [ ] F2.5 Input foto capture="environment" — JPG/HEIC — guardar localmente por ahora

---

### FASE 3 — Pestaña Análisis (2–3 días)

- [ ] F3.1 Nueva pestaña "Análisis" en la UI (entre Monitor y Referencias)
- [ ] F3.2 Selector múltiple de ensayos desde historial local (y más adelante desde cloud)
- [ ] F3.3 Gráfico comparativo Carga vs Deformación con hasta 6 ensayos superpuestos
- [ ] F3.4 Cada curva con color distinto + leyenda con nombre del ensayo
- [ ] F3.5 Controles de rango X e Y ajustables manualmente (inputs min/max)
- [ ] F3.6 Toggle por curva (mostrar/ocultar individual)
- [ ] F3.7 Botón exportar gráfico como imagen PNG

---

### FASE 4 — Manual de uso online (1 día)

- [ ] F4.1 Crear manual en Markdown (contenido: calibración, tipos de ensayo, operación móvil, interpretación de gráficos, troubleshooting)
- [ ] F4.2 Ruta /manual en Flask que sirve el manual renderizado en HTML
- [ ] F4.3 Botón "📖 Manual" accesible desde navbar en toda la app
- [ ] F4.4 Manual usable sin conexión (incluido en assets estáticos)

---

### FASE 5 — Cloudflare Tunnel + dominio propio (1 día)

- [ ] F5.1 cloudflared tunnel login en el Mac
- [ ] F5.2 Crear túnel nombrado sensores-prensa
- [ ] F5.3 Configurar DNS pruebas.dexfloor.com → tunnel en dashboard Cloudflare
- [ ] F5.4 Script start.sh — levanta Flask + tunnel en una sola llamada
- [ ] F5.5 Verificar SocketIO sobre WSS
- [ ] F5.6 Verificar UI móvil completa sobre HTTPS

---

### FASE 6 — Autenticación Azure AD (2–3 días)

- [ ] F6.1 Registrar app en Azure AD portal (portal.azure.com)
  - Tipo: Web app
  - Redirect URI: https://pruebas.dexfloor.com/auth/callback
  - Scopes: openid, profile, email, Files.ReadWrite, Sites.ReadWrite.All
- [ ] F6.2 Integrar msal en Flask — flujo Authorization Code
- [ ] F6.3 Decorator @login_required en todas las rutas
- [ ] F6.4 Verificar claim tid (tenant ID) para restringir a @dexfloor.com
- [ ] F6.5 Session segura: httponly, samesite=Lax, timeout 8h
- [ ] F6.6 Ruta /logout con revocación de token

---

### FASE 7 — Storage cloud con Microsoft Graph (3–4 días)

- [ ] F7.1 Estructura en SharePoint/OneDrive:
  ```
  Documentos/SensoresPrensaApp/
  ├── ensayos/
  │   └── {nombre_ensayo}/
  │       ├── datos.csv
  │       ├── datos.xlsx
  │       ├── meta.json
  │       └── foto.jpg / foto.heic
  └── referencias/
      └── reference_data.json
  ```
- [ ] F7.2 SharePoint List "EnsayosIndex": nombre, tipo_ensayo, fecha, material, operador, max_carga_kg, path_archivo, path_foto
- [ ] F7.3 Endpoint POST /api/sessions/upload — sube ensayo + foto + metadata, registra en List
- [ ] F7.4 Botón "☁ Subir" en historial local con indicador de progreso
- [ ] F7.5 Pestaña Análisis puede cargar ensayos desde cloud (además de local)
- [ ] F7.6 Descargar ensayo cloud como XLSX
- [ ] F7.7 Subida fotos JPG y HEIC sin conversión

---

### FASE 8 — Seguridad y robustez (2 días, paralela a Fase 7)

- [ ] F8.1 CSRF token en todos los POST del frontend
- [ ] F8.2 Rate limiting: 60 req/min por IP (Flask-Limiter)
- [ ] F8.3 Validación y sanitización de todos los inputs en servidor
- [ ] F8.4 Headers HTTP: CSP, X-Frame-Options, X-Content-Type-Options
- [ ] F8.5 Logging a archivo rotativo (RotatingFileHandler)
- [ ] F8.6 Errores genéricos al cliente — sin stack traces en producción

---

### FASE 9 — PWA + UX móvil in situ (1–2 días)

- [ ] F9.1 manifest.json + icono → instalar como PWA en pantalla de inicio
- [ ] F9.2 Service worker — cachear assets estáticos
- [ ] F9.3 Indicador visible: conectado al Mac / sin señal / grabando
- [ ] F9.4 Vista "modo campo" — stats críticos + botón grabar grande

---

## 8. Orden de ejecución

```
F0 → F1 → F2 → F3 → F4 → F5 → F6 → F7+F8 → F9
base  fix  meta  análisis  manual  tunnel  auth  cloud  PWA

Estimado total: 18–24 días de desarrollo
```

F0–F4 no requieren Azure ni M365 — se pueden hacer con el Mac local.
F5 requiere cloudflared tunnel login en el Mac.
F6 requiere acceso admin a portal.azure.com con cuenta dexfloor.com.
F7 requiere F6 completo (necesita token Graph API autenticado).

---

## 9. Dependencias Python a agregar

```
python-dotenv      # variables de entorno
msal               # auth Azure AD + Microsoft Graph
Flask-Limiter      # rate limiting
markdown           # renderizar manual .md en HTML
```

---

## 10. Prerequisitos bloqueantes (acción de Pancho)

| # | Acción | Antes de |
|---|--------|----------|
| P1 | cloudflared tunnel login en el Mac | Fase 5 |
| P2 | Acceso admin a portal.azure.com con cuenta dexfloor.com | Fase 6 |
| P3 | Confirmar sitio SharePoint donde guardar ensayos | Fase 7 |

---

*Plan actualizado: Mayo 2025 — Stack M365 definitivo (sin Supabase)*
