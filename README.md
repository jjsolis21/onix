# Solís FM Digital — MVP

## 📁 Estructura del Proyecto

```
solis_fm/
│
├── radio_core.db               ← SQLite (generado por init_db.py)
├── media/                      ← Archivos de audio subidos
│
├── scripts/
│   └── init_db.py              ← Inicialización y esquema de la BD
│
├── backend/
│   ├── __init__.py
│   ├── audio/
│   │   ├── __init__.py
│   │   └── audio_engine.py     ← Motor de audio (hilo separado)
│   └── api/
│       ├── __init__.py
│       └── api_server.py       ← FastAPI REST + WebSocket
│
└── frontend/
    ├── index.html              ← UI principal (Mobile First)
    ├── css/
    │   └── main.css            ← CSS Grid, paleta chocolate, Mobile First
    └── js/
        └── app.js              ← Lógica UI, WS, autocomplete, VU meters
```

## 🚀 Instalación y Ejecución

### 1. Instalar dependencias Python

```bash
pip install fastapi uvicorn websockets pygame mutagen
```

> **Alternativa sin pygame:** El motor incluye un `MockBackend` que funciona
> sin hardware de audio (útil para desarrollo).

### 2. Inicializar la base de datos

```bash
cd solis_fm
python scripts/init_db.py
```

### 3. Iniciar el servidor

```bash
cd solis_fm
python backend/api/api_server.py
```

### 4. Abrir la interfaz

Navegar a: `http://localhost:8000`

---

## 🏗️ Arquitectura de Módulos

```
┌─────────────────────────────────────────────────────────┐
│                     FRONTEND (Browser)                   │
│  index.html + main.css + app.js                          │
│  WebSocket client ←──────────────────────────────────┐   │
└─────────────────────────────────────────────────────────┘
                           │ WS / REST
┌─────────────────────────────────────────────────────────┐
│                   api_server.py (FastAPI)                │
│  /ws  /api/v1/*  ←→  engine.bus.subscribe(...)          │
└───────────────────────────┬─────────────────────────────┘
                            │ llamadas directas
┌───────────────────────────▼─────────────────────────────┐
│              audio_engine.py (Thread separado)           │
│                                                          │
│  EventBus ──► track_started, track_ended,               │
│               crossfade_start, effect_fired...           │
│                                                          │
│  RotationAlgorithm ←── radio_core.db (SQLite)           │
│  PygameBackend / MockBackend                             │
└─────────────────────────────────────────────────────────┘
```

## 🔌 Punto de Extensión: Módulo de Publicidad

Para integrar el gestor de publicidad **sin modificar el núcleo**:

### Backend Python
```python
# ads_manager.py (nuevo módulo)
from backend.audio.audio_engine import engine

def on_track_ended(event, data):
    if should_play_ad_block():
        ad_track_id = get_next_ad()
        engine.play_track(ad_track_id)

engine.bus.subscribe("track_ended", on_track_ended)
```

### Frontend JavaScript
```javascript
// En app.js o en un archivo ads.js separado
SolisWS.onEvent("ad_scheduled", (data) => {
    showAdBanner(data.cuña_titulo);
});
```

### API
```python
# En api_server.py, agregar:
from backend.ads import ads_router
app.include_router(ads_router, prefix="/api/v1/ads")
```

---

## ⚙️ Variables de Configuración (tabla `config`)

| Clave                | Default | Descripción                              |
|---------------------|---------|------------------------------------------|
| `modo`              | manual  | `manual` o `automatico`                  |
| `crossfade_seg`     | 4       | Duración del crossfade en segundos       |
| `ventana_artista_min` | 60    | Minutos antes de repetir artista         |
| `max_mismo_genero`  | 2       | Máximo de canciones consecutivas iguales |
| `forzar_nacional`   | false   | Forzar solo música Nacional en auto      |
| `ducking_db`        | -12     | Atenuación de música durante efectos     |
