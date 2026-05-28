# Manual de uso — Sensores Prensa

> Versión actualizada con cambios T1–T9.

---

## 1. Conexión al Arduino

1. Conecta el Arduino Mega por USB al Mac.
2. Abre la app en `http://localhost:5050`.
3. Haz clic en **↺** para detectar el puerto (aparece algo como `/dev/tty.usbmodem...`).
4. Selecciona el puerto y haz clic en **Conectar**.
5. El badge cambia a 🟢 **Conectado** cuando los datos empiezan a llegar.

> Si no aparece el puerto, ejecuta `ls /dev/tty.usb*` en la terminal.

---

## 2. Calibración del sistema

Accede desde el tab **Config**.

### 2.1 Zero de celdas de carga
1. Asegúrate de que la prensa esté **sin carga**.
2. Haz clic en **Tomar zero ahora**.
3. Los offsets de las 9 celdas se actualizan automáticamente.

### 2.2 Calibración de Distancia (antes "Stroke")
1. Lleva el sensor al punto **mínimo** → **Setear 0 mm**.
2. Lleva el sensor al punto **máximo** → **Setear 100 mm**.
3. Los valores persisten en `dimension_cal.json`.

> El campo se llama **Distancia** en toda la UI. Internamente usa `dimension`.

### 2.3 Calibración manual fina
En **Config → offsets y escalas** ajusta offset y escala de cada celda individualmente.

Fórmula: `kg = (raw − offset) / escala`

---

## 3. Configurar un ensayo

Antes de grabar, haz clic en **🗂 Configurar ensayo** (Monitor tab).

### 3.1 Tipo de ensayo
Selecciona del dropdown. Los tipos se gestionan en **Config → 🗂 Tipos de ensayo**:
- Agrega tipos nuevos con clave (`ej. flex_viga`) y nombre visible.
- Elimina tipos con el botón ✕.
- Los tipos persisten en `ensayo_tipos.json`.

### 3.2 Campos disponibles
| Campo | Descripción |
|-------|-------------|
| Tipo | Tipo predefinido |
| Título / ID | Identificador libre del ensayo |
| Material / Muestra | Descripción del material |
| Dimensiones | Medidas de la muestra |
| Operador | Nombre del técnico |
| Notas | Observaciones libres |
| Foto | Imagen de la muestra (ver §3.3) |

Todos los campos se guardan en `{nombre}_meta.json`.

### 3.3 Foto de la muestra

**Opción A — QR desde celular (recomendado):**
1. En el modal "Configurar ensayo", haz clic en **📱 Generar QR para foto desde celular**.
2. Escanea el QR con la cámara del celular.
3. En la página que se abre, toma o selecciona una foto.
4. El modal muestra preview y "✅ Foto recibida desde celular".
5. La foto queda vinculada al ensayo al detener la grabación.

> El enlace del QR expira en 10 minutos.

**Opción B — Archivo local:**
Usa el input de archivo bajo la sección QR para subir desde el mismo dispositivo.

---

## 4. Grabación

### Manual
1. Configura el ensayo (opcional pero recomendado).
2. Clic en **⏺ Grabar**.
3. Aplica la carga.
4. Clic en **⏹ Detener** → el botón se deshabilita y muestra "⏳ Guardando…" mientras se escribe el archivo.

> No hagas clic en Detener varias veces — la app ignora clics duplicados automáticamente.

### Auto-record
Activa en **Config → Auto-grabar**.

| Parámetro | Descripción |
|-----------|-------------|
| Umbral disparo | kg totales para iniciar grabación |
| Lecturas inicio | lecturas consecutivas sobre umbral |
| Lecturas stop | lecturas bajo el 40% del umbral para detener |

---

## 5. Alarma de límite

En **Config → 🔔 Alarma de límite**:

1. Activa el switch **Activar**.
2. Selecciona el sensor a vigilar (carga total, celda individual, presión o distancia).
3. Ingresa el umbral numérico.

Cuando el sensor alcanza el umbral:
- Suena **3 beeps**.
- Aparece overlay rojo **🚨 ALERTA — LÍMITE ALCANZADO**.

La alarma se resetea automáticamente cuando el valor baja al 90% del umbral.

---

## 6. Monitor

- **Canales:** activa/desactiva celdas, Distancia y Presión individualmente.
  - Presión aparece en el **eje Y derecho** del Chart 1.
  - Distancia también disponible en el panel de canales.
- **Pausar:** congela el gráfico sin detener adquisición.
- **Ventana:** últimos 30 s, 60 s, 2 min, o todo.
- **Eje Y / Eje X:** rango manual; **↺ Auto** restaura automático.

---

## 7. Análisis comparativo

En el tab **Análisis** hay 3 gráficos:

| Gráfico | Ejes |
|---------|------|
| Carga vs Deformación | Carga total (kg) vs Distancia (mm) |
| Carga vs Tiempo | Carga total (kg) vs tiempo (s) |
| Distancia vs Tiempo | Distancia (mm) vs tiempo (s) |

1. Selecciona hasta 6 ensayos del listado.
2. Las curvas se superponen con colores distintos.
3. Haz clic en un badge de la leyenda para ocultar/mostrar esa curva (sincronizado entre gráficos).
4. Exporta cada gráfico con **⬇ PNG**.

---

## 8. Historial de ensayos

### Ensayos destacados ⭐
- Clic en ★/☆ junto a un ensayo para destacarlo.
- Los ensayos destacados aparecen primero en la lista.
- El estado persiste en `_meta.json`.

### Descarga múltiple (ZIP)
1. Clic en el botón **☑** en la cabecera del historial para activar modo selección.
2. Selecciona los ensayos deseados (o usa **Todos**).
3. Clic en **⬇ ZIP**.
4. Se descarga `ensayos_YYYY-MM-DD.zip` con CSV + XLSX + _meta.json + foto por ensayo.

---

## 9. Archivos generados

Por ensayo se generan hasta 4 archivos en `sessions/`:

| Archivo | Contenido |
|---------|-----------|
| `{nombre}.csv` | Datos (t, celda_1…9, dimension, pressure) |
| `{nombre}.xlsx` | Mismo contenido en Excel |
| `{nombre}_meta.json` | Metadata + starred |
| `{nombre}_foto.*` | Foto de la muestra (si se capturó) |

---

## 10. Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| No aparece el puerto | Arduino sin energía o cable dañado | Verifica cable y reinicia Arduino |
| Celdas muestran valores negativos grandes | Offset incorrecto | Repetir zero sin carga |
| Gráfico no avanza | Modo pausa activo | Clic en ▶ Reanudar |
| Auto-record no dispara | Umbral demasiado alto | Bajar umbral en Config |
| Distancia siempre 0 | Calibración no realizada | Calibrar posiciones min/max |
| Ruido alto en celdas sin carga | Piso de ruido bajo | Subir slider en Config |
| QR foto expirado | Pasaron más de 10 min | Generar nuevo QR |
| Botón Detener no responde | Ya está guardando | Esperar confirmación |

---

## 11. Acceso remoto (Cloudflare Tunnel)

La app puede exponerse externamente a través de `sensores.dexfloor.com`:

```bash
./start.sh   # inicia tunnel + app
./stop.sh    # detiene todo
```

El acceso requiere autenticación Microsoft 365 con cuenta `@dexfloor.com`.

---

*Sensores Prensa — dexfloor.com*
