# Manual de uso — Sensores Prensa

## 1. Conexión al Arduino

1. Conecta el Arduino Mega por USB al Mac.
2. Abre la app en `http://localhost:5050`.
3. Haz clic en **↺** para detectar el puerto (aparece algo como `/dev/tty.usbmodem...`).
4. Selecciona el puerto y haz clic en **Conectar**.
5. El badge cambia a 🟢 **Conectado** cuando los datos empiezan a llegar.

> Si no aparece el puerto, ejecuta `ls /dev/tty.usb*` en la terminal y verifica que el Arduino esté encendido.

---

## 2. Calibración del sistema

Accede desde **Monitor → Calibrar sistema**.

### 2.1 Zero de celdas de carga
1. Asegúrate de que la prensa esté **sin carga**.
2. Haz clic en **Tomar zero ahora**.
3. Los offsets de las 9 celdas se actualizan automáticamente.

### 2.2 Calibración de stroke
1. Lleva el sensor al punto **mínimo** (sin comprimir) → **Setear 0 mm**.
2. Lleva el sensor al punto **máximo** → **Setear 100 mm**.

### 2.3 Calibración manual fina
En **Configuración → Editar offsets y escalas** puedes ajustar offset y escala de cada celda individualmente.

Fórmula: `kg = (raw − offset) / escala`

---

## 3. Tipos de ensayo

Configura el ensayo antes de grabar desde **🗂 Configurar ensayo**.

| Tipo | Prefijo archivo | Uso |
|------|----------------|-----|
| Compresión cubo | `comp_cubo_` | Probeta cúbica de hormigón |
| Compresión piso | `comp_piso_` | Losa o piso en sitio |
| Deformación esquina piso | `def_esquina_` | Flexión en esquina de losa |
| Deformación total | `def_total_` | Deformación máxima de la pieza |

**Campos disponibles:** tipo, material/muestra, dimensiones, operador, notas, foto.

Todos los campos se guardan junto al ensayo como `{nombre}_meta.json`.

---

## 4. Grabación de un ensayo

### Manual
1. Configura el ensayo (opcional pero recomendado).
2. Haz clic en **⏺ Grabar**.
3. Aplica la carga.
4. Haz clic en **⏹ Detener** al terminar.
5. El archivo se guarda en `sessions/` con prefijo del tipo seleccionado.

### Auto-record
Activa en **Configuración → Auto-grabar**.

- **Umbral disparo:** kg totales para iniciar grabación.
- **Lecturas inicio:** lecturas consecutivas sobre el umbral antes de grabar.
- **Lecturas stop:** lecturas consecutivas bajo el 40% del umbral para detener.

La grabación inicia y detiene automáticamente. El badge de estado lo indica.

---

## 5. Monitor

- **Pausar:** congela el gráfico sin detener la adquisición.
- **Canales:** activa/desactiva celdas individualmente o todas/ninguna.
- **Ventana:** muestra los últimos 30 s, 60 s, 2 min, o todo.
- **Eje Y / Eje X:** ajuste manual de rango; **↺ Auto** restaura el automático.
- **🗑:** limpia el buffer del gráfico (no borra archivos).

**Stats en tiempo real:** Carga Total (kg), Celda Máx, Stroke (mm), Presión (bar).

---

## 6. Análisis comparativo

En la pestaña **Análisis**:

1. Selecciona hasta 6 ensayos del listado derecho.
2. El gráfico muestra **Carga total vs Stroke** de cada ensayo superpuesto.
3. Haz clic en un badge de la leyenda para mostrar/ocultar esa curva.
4. Ajusta los ejes con los inputs de rango.
5. Exporta el gráfico con **⬇ PNG**.

---

## 7. Operación móvil in situ

La app es totalmente responsiva. Desde el celular:

1. Conecta el Mac a la misma red WiFi que el celular.
2. Abre `http://<IP-del-Mac>:5050` en el navegador del celular.
   - Encuentra la IP con `ifconfig | grep "inet "` en el Mac.
3. Puedes operar Monitor, grabar ensayos y tomar fotos directamente.

> Próximamente: acceso externo vía `pruebas.dexfloor.com` (Cloudflare Tunnel).

---

## 8. Archivos generados

Por ensayo se generan hasta 4 archivos en `sessions/`:

| Archivo | Contenido |
|---------|-----------|
| `{nombre}.csv` | Datos crudos (t, celda_1…9, stroke, pressure) |
| `{nombre}.xlsx` | Mismo contenido en Excel |
| `{nombre}_meta.json` | Metadata (tipo, material, operador, notas, timestamp) |
| `{nombre}_foto.jpg` | Foto de la muestra (si se capturó) |

---

## 9. Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| No aparece el puerto | Arduino sin energía o cable dañado | Verifica cable y reinicia Arduino |
| Celdas muestran valores negativos grandes | Offset incorrecto | Repetir calibración zero sin carga |
| Gráfico no avanza | Modo pausa activo | Clic en ▶ Reanudar |
| Auto-record no dispara | Umbral demasiado alto | Bajar umbral en Configuración |
| Stroke siempre 0 | Calibración stroke no realizada | Calibrar posiciones min/max |
| Ruido alto en celdas sin carga | Piso de ruido bajo | Subir slider "Piso de ruido" en Config |

---

*Sensores Prensa — dexfloor.com*
