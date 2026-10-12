# ÓNIX FM — Automatizador de radio

API FastAPI + SQLite, motor de audio sobre BASS y dos interfaces web:
**Studio** (consola del operador, `/`) y **Admin** (biblioteca, pautas, `/admin/`).

## 📁 Estructura

```
onix/
├── backend/
│   ├── paths.py                 ← Rutas únicas: BD, media/, respaldo/
│   ├── api/
│   │   ├── api_server.py        ← FastAPI REST + WebSocket
│   │   ├── reportes.py          ← Top 40, historial, cumplimiento de pautas
│   │   ├── relojes.py           ← Relojes, grilla semanal y programa
│   │   └── schemas_pautas.py
│   └── audio/
│       ├── audio_engine.py      ← Motor: crossfade, programa, tandas, watchdog
│       ├── planner.py           ← Relojes: arma las próximas horas
│       ├── engine_service.py    ← Motor como proceso aparte (socket local)
│       ├── engine_client.py     ← Cliente del motor remoto (lo usa la API)
│       ├── loudness.py          ← Medición LUFS (ffmpeg) para normalizar
│       └── eventbus.py
├── scripts/
│   ├── init_db.py               ← Esquema y migraciones (idempotentes)
│   └── iniciar_onix.ps1         ← Arranque en Windows (motor + API)
├── media/                       ← Audios (no van a git)
│   ├── library/  COMERCIALES/  staging/
│   └── respaldo/                ← Lista de emergencia (ver abajo)
├── frontend/                    ← Studio (index.html) y Admin (admin/)
└── tests/                       ← Pruebas automáticas (pytest)
```

## 🚀 Ejecución

### Windows (emisión real con `bass.dll`)

```powershell
pip install -r requirements.txt
winget install Gyan.FFmpeg          # para la normalización de volumen
powershell -ExecutionPolicy Bypass -File scripts\iniciar_onix.ps1 -Puerto 8001
```

Abre el motor de audio en una ventana minimizada y la API en la actual.
Studio: `http://localhost:8001` · Admin: `http://localhost:8001/admin/`.
**Cerrar o reiniciar la API no corta el aire**: el motor sigue sonando y la
API se reconecta sola. Para apagar todo, cerrar también la ventana del motor.

Modo simple (motor dentro de la API, como antes; reiniciar la API corta el aire):

```powershell
$env:ONIX_PORT = "8001"; python backend\api\api_server.py
```

La base de datos se crea y migra sola al arrancar.

### Docker (vista previa, sin sonido)

```bash
docker compose up -d --build      # http://localhost:8765
```

El motor corre simulado (`mock`): sirve para ver la interfaz y probar la API.

### Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Corren en cualquier sistema (motor simulado, BD y audios temporales).

## ⚙️ Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `RADIO_DB_PATH` | `backend/api/radio_core.db` | Base de datos (API y motor usan la misma) |
| `RADIO_MEDIA_ROOT` | `media/` del proyecto | Carpeta de audios |
| `ONIX_PORT` | `8000` | Puerto de la API |
| `ONIX_ENGINE` | `local` | `remoto` = la API usa el motor de `engine_service.py` |
| `ONIX_ENGINE_PORT` | `8766` | Puerto local del motor (solo escucha en 127.0.0.1) |
| `ONIX_AUDIO_DEVICE` | `-1` | Dispositivo BASS (`-1` predeterminado, `0` sin sonido) |
| `ONIX_AUDIO_BACKEND` | — | `mock` fuerza el motor simulado |
| `ONIX_FFMPEG` | ffmpeg del PATH | Ruta a ffmpeg (también vale `ffmpeg.exe` en la raíz o `bin/`) |
| `ONIX_RELOAD` | — | `1` recarga automática (corta el audio; solo desarrollo) |

## 🎚️ Parámetros del motor (tabla `config`, editables en Studio → Ingesta)

| Clave | Default | Descripción |
|---|---|---|
| `modo` | manual | `manual` o `automatico` |
| `crossfade_seg` | 4 | Crossfade entre canciones sin fades propios |
| `ventana_artista_min` | 60 | Minutos antes de repetir artista |
| `max_mismo_genero` | 2 | Máximo de canciones seguidas con la misma voz |
| `ducking_db` | -12 | Atenuación de la música al disparar la cartuchera |
| `dispositivo_aire` | -1 | Tarjeta de sonido del AIRE (-1 = predeterminada de Windows) |
| `dispositivo_preescucha` | -1 | Tarjeta de la PREESCUCHA (escucha interna) |
| `dispositivo_cartuchera` | -2 | Tarjeta de la cartuchera (-2 = la misma del aire) |
| `horas_planificadas` | 2 | Cuánto planifica el programa por adelantado |
| `aleatoriedad` | 3 | 0 = estricta (la que hace más tiempo no suena); N = al azar entre N |
| `tolerancia_tanda_seg` | 90 | Margen para que la tanda caiga en su minuto |
| `separacion_cancion_dias` / `_ventana_min` | 1 / 60 | No repetir la canción en la misma franja de días anteriores |
| `tandas_minutos` | 15,45 | Minutos de cada hora en que salen las pautas |
| `normalizacion_activa` | 1 | Normalizar volumen |
| `normalizacion_lufs` | -16 | Sonoridad objetivo (LUFS) |

## 🕐 Relojes y programa (modo automático estilo Jazler SoHo)

- **Reloj** (Admin → 08 Relojes): filas en orden, por ejemplo *ID → Éxito → Nuevo → Éxito*.
  Cada fila pide un tipo de audio con filtros (rotación, género, voz, origen, energía) o un
  audio fijo. Las filas se repiten hasta que termina la hora; cada hora empieza en la fila 1.
- **Grilla semanal**: qué reloj rige cada hora de cada día. Se pinta arrastrando un rectángulo.
  Las horas sin reloj suenan solo con música.
- **Programa** (columna izquierda del Studio): las próximas `horas_planificadas` con hora
  estimada, agrupadas por reloj, con las tandas como marcadores. Se puede preescuchar, subir o
  quitar cada ítem, o regenerar todo. En automático se toca solo; en manual avanza con ▶/⏭.
- **Compensación**: si una canción haría llegar tarde la tanda (más que `tolerancia_tanda_seg`),
  se elige otra que encaje (⚖ en el Studio), de la categoría de compensación del reloj.
- Reglas: separación de artista, máximo de voces seguidas, sin repetir en el plan, no repetir
  la canción en la misma franja de los días anteriores y `aleatoriedad` (0 = estricta).

## 🔊 Salidas de audio (aire, preescucha y cartuchera)

ONIX usa dos salidas, cada una en la tarjeta de sonido que elijas en
**Admin → 06 Motor de Audio**:

- **AIRE**: lo que va al transmisor o al codificador de streaming (música, tandas, cartuchera).
- **PREESCUCHA**: escucha interna en cabina. El botón 🎧 del Studio y del editor de
  cartuchera suena aquí y **nunca** sale al aire.

Si las dos usan la misma tarjeta, la preescucha se oye al aire: conviene una
segunda tarjeta (una USB sencilla sirve). El cambio de tarjeta se aplica desde la
próxima pista, sin cortar lo que suena.

## 📢 Pautas y tandas

- **Automático**: las tandas salen solas en el primer cambio de pista después de su minuto.
- **Manual**: el Studio muestra la barra ámbar *TANDA EN ESPERA* y el operador elige
  *LANZAR AHORA* o *AL TERMINAR LA CANCIÓN*. Si no la lanza dentro de su hora, se pierde
  y figura como faltante en el reporte del cliente.
- La matriz de la pauta marca **qué horas** sale; `tandas_minutos` decide **en qué minuto**.
- **Secciones** (Admin → 06 → Tandas; como las prioridades de Jazler): la tanda sale en orden de
  sección, por defecto *1 Identificación → 2 Cortina de entrada → 3 Comerciales → 4 Cortina de salida*.
  Cada pauta elige su sección. Las **comerciales** se reparten entre las tandas de la hora; las
  demás (ID, cortinas) salen en **cada** tanda de sus horas. Las **opcionales** solo salen si la
  tanda lleva al menos una cuña comercial. El reporte por cliente solo cuenta las comerciales.
- **Inicio inmediato** (`tandas_inmediatas`, p. ej. `0`): en automático esa tanda corta la canción
  con fade y sale en punto (✂ y ⚡ en el programa). En manual decide el operador.
- **Duración máxima** (`tanda_max_seg`): si una tanda la supera, el Studio avisa (⚠).
- Las pautas de cada hora se reparten entre sus tandas separando anunciantes
  del mismo rubro, y dentro de una tanda nunca van dos del mismo rubro seguidas.
- Una tanda sale completa en el primer cambio de pista después de su minuto.
- Cada pauta sale como máximo una vez por hora y `menciones_diarias` veces al día.
- Si la pauta tiene `cue_intro` / `cue_outro`, la cuña empieza y termina ahí.
- Lo que no sale en su hora se descarta (evento `pauta_missed`).

## 🔊 Normalización de volumen

Cada audio se mide una vez con ffmpeg (EBU R128) en segundo plano: lo que se
sube y la biblioteca existente. El motor aplica `objetivo − LUFS medido`, sin
pasar un pico de −1 dBTP ni subir más de +6 dB. `ganancia_db` por audio es un
ajuste manual extra. Estado: `GET /api/v1/normalizacion`. Sin ffmpeg, los
audios suenan sin normalizar.

## 🛟 Lista de respaldo

En modo automático, si la rotación no tiene nada que sonar (biblioteca vacía,
base de datos caída, archivos rotos), el motor toma audios de
`media/respaldo/` en vez de quedarse en silencio. Conviene dejar ahí 20–30
canciones seguras. El Studio avisa con `respaldo_activado`.

## 📊 Historial y reportes (Admin → 05)

- **Historial**: todo lo que salió al aire, con filtros por fecha, tipo, origen y texto; exporta CSV (Excel).
- **Reporte por cliente**: por cada pauta, programadas vs emitidas y % de cumplimiento en el
  período; detalle por día, CSV y *comprobante* imprimible para entregar al cliente.
- **Estadísticas (07)**: Top 40 por día/semana/mes y música que no suena hace 30+ días.
