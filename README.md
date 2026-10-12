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
│   │   └── schemas_pautas.py
│   └── audio/
│       ├── audio_engine.py      ← Motor: crossfade, rotación, tandas, watchdog
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
| `tandas_minutos` | 15,45 | Minutos de cada hora en que salen las pautas |
| `normalizacion_activa` | 1 | Normalizar volumen |
| `normalizacion_lufs` | -16 | Sonoridad objetivo (LUFS) |

## 📢 Pautas y tandas

- La matriz de la pauta marca **qué horas** sale; `tandas_minutos` decide **en qué minuto**.
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
