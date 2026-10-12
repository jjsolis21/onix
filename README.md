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
├── frontend/                    ← Studio (studio/) y Admin (admin/)
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

### Inicio automático con Windows

Doble clic en **`Inicio automatico ONIX.bat`** (una vez). Crea la tarea «ONIX FM» del
Programador de tareas: al iniciar sesión y luego **cada 5 minutos** arranca lo que falte
(motor y/o API, minimizados). Si ONIX ya está en marcha no hace nada; si algo se cerró,
vuelve solo y queda anotado en `logs\inicio.log`. El modo (manual/automático) se recuerda.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1           # ver estado
powershell -ExecutionPolicy Bypass -File scripts\inicio_automatico.ps1 -Quitar   # mantenimiento
```

La tarea corre al **iniciar sesión** (el audio de Windows solo existe dentro de una sesión).
Para volver al aire sola tras un corte de luz hace falta: BIOS «Restore on AC power loss =
Power On», inicio de sesión automático de Windows (`netplwiz`) y que la PC no se suspenda
(`powercfg /change standby-timeout-ac 0`). El script lo recuerda al instalarse.

### Entrar desde otra PC de la red (jefe de programación)

1. En la PC del estudio, una vez: **`Abrir ONIX en la red local.bat`** (pide permisos de
   administrador). Abre el puerto **solo para la red local** y avisa si Windows marcó la red
   como Pública.
2. **Admin › Motor de audio › Usuarios**: crear el usuario (rol *Programación*). Ahí mismo
   aparece la dirección a escribir en la otra PC, p. ej. `http://192.168.1.20:8001/admin/`.

La **PC del estudio no pide contraseña** (si ONIX se reinicia solo, el aire sigue). Las demás
PCs entran con usuario: *Programación* = Admin sin operar el aire ni tocar motor, salidas,
respaldos o usuarios; *Operador* = Studio; *Administrador* = todo.

### Marcas automáticas al cargar música

Como Jazler SoHo: al cargar una canción se detectan el **inicio** (salta el silencio) y el
**punto de mezcla** (donde el final cae 12 dB bajo su volumen normal: sirve para fades y
finales secos). El **intro** (cuándo entra la voz) se sigue marcando a mano. Las marcas
puestas a mano nunca se pisan. Se activa o desactiva en Admin › Motor de audio
(normalización); también analiza la música que ya estaba cargada (≈1 s por canción).

### Respaldos de la base de datos

Una copia por día de relojes, grilla, eventos, pautas, biblioteca (con marcas), cartuchera,
historial y configuración, en `respaldos\` (se conservan 14). Carpeta, cantidad, «Respaldar
ahora» y descarga en **Admin › Motor de audio**. Conviene elegir **otro disco, un USB o una
carpeta sincronizada con la nube**: el panel de problemas avisa si están en el mismo disco.
**Los audios (`media\`) no se copian**: respaldarlos aparte.

Restaurar una copia (corta el aire unos segundos; guarda la base actual por si acaso):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\restaurar_respaldo.ps1
```

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

## 🎛️ Studio: `http://localhost:8001/`

Pantalla del operador al estilo Jazler SoHo (`/studio` redirige aquí). Solo opera el aire:
cargar audios, relojes, pautas y la configuración están en el **Admin** (`/admin/`).
**＋ CARGAR AUDIOS** abre el Admin en la Biblioteca.

- **Reproductor**: cuenta regresiva grande de lo que suena, **cuenta del intro** (cuánto
  queda para hablar encima; cambia a VOZ cuando entra la voz), barra con marcas de intro
  y mezcla, y **SIGUE** con su cuenta "entra en".
- **Adelantar lo que suena**: clic en la barra para marcar el punto y **⏩ IR A m:ss** para
  confirmar (Esc o 5 s cancela). Dos pasos a propósito: un clic accidental no salta el aire.
  No se puede con una cuña de pauta.
- **DETENER AL TERMINAR**: la canción suena completa y no entra nada más, ni en automático.
  El aire queda libre hasta que el operador presiona **REPRODUCIR**.
- **Programa a la izquierda**: tarjetas con color por tipo (tanda en rojo); se **reordena
  arrastrando** y se **sueltan canciones desde la biblioteca** en la posición exacta (✋).
  Clic elige una tarjeta y la barra fija de abajo actúa sobre ella: **▲ Subir · ▼ Bajar ·
  ⤒ Primero · 🎧 Escuchar · ✕ Quitar**. Teclado: ↑/↓ elegir, Alt+↑/↓ mover, Supr quitar.
- **Biblioteca** por tipo (música, jingles, sweepers, cuñas, programas, tips), con búsqueda
  y filtros. Doble clic = a la cola.
- **Derecha**: próxima tanda con cuenta regresiva, historial y cartuchera.
- **Vúmetro** de la salida del AIRE (picos en dBFS, verde/ámbar/rojo, retención de pico),
  medido por el motor 10 veces por segundo; incluye fades y cartuchera.
- **Atajos** (ayuda con `?` o el botón ⌨): Espacio siguiente · 1–8 cartuchera · D detener al
  terminar · L lanzar tanda · P problemas · Ctrl+F o / buscar · Esc cancelar. No funcionan
  mientras se escribe.
- **Aire muerto**: si lo que suena queda `silencio_seg` segundos (10) en silencio, en
  automático pasa a lo siguiente y aparta ese audio 10 min; en manual avisa. Tres saltos en
  10 min = falla de equipo: deja de saltar y solo avisa. Mide lo que ONIX entrega a la
  tarjeta: un cable suelto después de la tarjeta no lo detecta.
- **⚠ PROBLEMAS** (tecla P): lo que está mal ahora (motor, archivos que faltan, relojes sin
  audios, cuñas sin audio, tandas largas, respaldo vacío) y lo que pasó al aire.
- Solo avisa lo importante (errores, tandas, respaldo). Si se pierde la conexión aparece un
  aviso rojo fijo; lo que suena no se corta.

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
