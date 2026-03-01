"""
api_server.py — Ónix FM Digital  v3.0  (SaaS Edition)
================================================================================

Columnas canónicas de `audios` (fuente de verdad: init_db.py + migraciones):
    id, titulo, artista, genero_vocal, subgenero, categoria, anio,
    energia, origen, tipo_audio, ruta_archivo, puntos_audio,
    duracion_seg, ultima_reproduccion, veces_reproducido, activo, notas

Tipos de audio soportados: Musica | Efecto | Cuña | Sweeper | Programa | Tips

Rutas físicas automáticas:
    media/{tipo_audio}/{genero_vocal}/{categoria}/{artista}_{titulo}{ext}
    Ej: media/Musica/Merengue/Clasicos/Juan_Gabriel_Querida.mp3

Nuevos endpoints v3.0:
    PUT  /api/v1/audios/{id}/mover      — reorganización física dinámica
    GET  /api/v1/stats                  — contadores de inventario en tiempo real
    GET  /api/v1/audios/filtros         — valores únicos para dropdowns de filtros
    PUT  /api/v1/audios/{id}            — edición avanzada (todos los campos)

Movimiento seguro de archivos (Copy-and-Delete):
    1. Copia el archivo al destino nuevo.
    2. Verifica integridad (tamaño de bytes).
    3. Solo entonces elimina el original.
    Esto evita pérdidas por corte de energía o errores de IO.
"""

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import sys
import unicodedata
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, UploadFile, File, Form
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("API")

# ── Rutas base ─────────────────────────────────────────────────────────────────
_THIS_FILE   = Path(__file__).resolve()
BASE_DIR     = _THIS_FILE.parent.parent.parent   # ajustar según posición real
DB_PATH      = BASE_DIR / "radio_core.db"
MEDIA_DIR    = BASE_DIR / "media"
FRONTEND_DIR = BASE_DIR / "frontend"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Tipos de audio habilitados (fuente de verdad para validaciones y la UI)
TIPOS_AUDIO = ["Musica", "Efecto", "Cuña", "Sweeper", "Programa", "Tips"]

# Categorías de rotación musical
CATEGORIAS  = ["Nuevo", "Reciente", "Exito", "Clasico", "Desconocido"]

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".opus"}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de nombres de archivo y rutas
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize(s: str, max_len: int = 60) -> str:
    """Elimina acentos y caracteres no seguros para sistemas de archivos."""
    s = s.strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[\s\-–—/\\]+", "_", s)
    s = re.sub(r"[^\w\.-]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "audio"


def _build_media_path(
    tipo_audio: str,
    genero_vocal: str,
    categoria: str,
    artista: str,
    titulo: str,
    ext: str = ".mp3"
) -> Path:
    """
    Construye la ruta jerárquica canónica:
        media/{tipo}/{genero_o_sin_genero}/{categoria}/{artista}_{titulo}{ext}

    Para tipos no musicales (Efecto, Cuña, Sweeper, Programa, Tips),
    el género no aplica semánticamente, por lo que usamos 'Sin_genero'.
    La categoría sí aplica para organizar campañas o bloques.
    """
    usar_genero = tipo_audio == "Musica"
    genero_dir  = _sanitize(genero_vocal) if usar_genero else "Sin_genero"
    cat_dir     = _sanitize(categoria) if categoria else "Desconocido"

    folder   = MEDIA_DIR / _sanitize(tipo_audio) / genero_dir / cat_dir
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{_sanitize(artista)}_{_sanitize(titulo)}{ext}"
    return folder / filename


def _safe_copy_and_delete(src: Path, dst: Path) -> bool:
    """
    Mueve un archivo usando la estrategia Copy-and-Delete:
      1. Copia src → dst (atómica en cuanto a escritura).
      2. Compara tamaños para verificar integridad.
      3. Solo si coinciden, elimina src.

    Retorna True si el movimiento fue exitoso, False si falló.
    En caso de fallo, el archivo original no se borra.
    """
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))          # copia con metadatos
        if dst.stat().st_size != src.stat().st_size:
            dst.unlink(missing_ok=True)           # corrompe: abortar
            log.error("[MoverArchivo] Fallo de integridad: tamaños no coinciden.")
            return False
        src.unlink()                              # solo ahora borramos el original
        log.info("[MoverArchivo] %s → %s", src.name, dst)
        return True
    except Exception as exc:
        log.error("[MoverArchivo] Error en Copy-and-Delete: %s", exc)
        dst.unlink(missing_ok=True)               # limpiar copia parcial
        return False


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket Manager
# ══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop): self._loop = loop

    async def connect(self, ws: WebSocket):
        await ws.accept(); self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    def broadcast_sync(self, event: str, payload: dict):
        """Puente síncrono → asíncrono (desde hilo del motor de audio)."""
        msg = json.dumps({"event": event, "data": payload})
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    async def broadcast_async(self, event: str, payload: dict):
        await self._broadcast(json.dumps({"event": event, "data": payload}))

    async def _broadcast(self, message: str):
        dead: Set[WebSocket] = set()
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self.active -= dead


ws_manager = ConnectionManager()


# ══════════════════════════════════════════════════════════════════════════════
# DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _migrate_db():
    """
    Migración incremental: agrega columnas nuevas si no existen.
    Esto permite actualizar la BD sin perder datos ni re-ejecutar init_db.
    """
    conn = sqlite3.connect(str(DB_PATH))
    cur  = conn.cursor()

    # Columnas nuevas de v3.0
    nuevas_columnas = [
        ("subgenero",  "TEXT DEFAULT ''"),
        ("categoria",  "TEXT DEFAULT 'Desconocido'"),
        ("anio",       "INTEGER DEFAULT 0"),
    ]
    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(audios)")}
    for col_name, col_def in nuevas_columnas:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE audios ADD COLUMN {col_name} {col_def}")
            log.info("[DB] Columna añadida: audios.%s", col_name)

    # Ampliar el CHECK de tipo_audio (SQLite no permite modificar CHECK in-place,
    # pero los INSERTs nuevos ya usan la validación en Python; el CHECK original
    # de init_db era restrictivo — lo ignoramos con un trigger IF NEEDED).
    # Para v3.0 la validación de tipos se hace en Python.

    # Tabla de bloques horarios
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bloques_horarios (
            id          INTEGER  PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT     NOT NULL,
            hora_inicio TEXT     NOT NULL DEFAULT '08:00',
            hora_fin    TEXT     NOT NULL DEFAULT '09:00',
            orden       INTEGER  NOT NULL DEFAULT 0,
            activo      INTEGER  NOT NULL DEFAULT 1,
            notas       TEXT     DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS bloque_tracks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            bloque_id INTEGER NOT NULL REFERENCES bloques_horarios(id) ON DELETE CASCADE,
            audio_id  INTEGER NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
            posicion  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(bloque_id, audio_id)
        );
        CREATE INDEX IF NOT EXISTS idx_bt_bloque ON bloque_tracks(bloque_id);
        CREATE INDEX IF NOT EXISTS idx_bt_audio  ON bloque_tracks(audio_id);
    """)
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Motor de audio (opcional)
# ══════════════════════════════════════════════════════════════════════════════

sys.path.insert(0, str(BASE_DIR))
try:
    from backend.audio.audio_engine import engine
    AUDIO_ENGINE_AVAILABLE = True
except ImportError:
    engine = None
    AUDIO_ENGINE_AVAILABLE = False
    log.warning("[API] audio_engine no disponible — modo sin motor.")


def _subscribe_engine_events():
    if not AUDIO_ENGINE_AVAILABLE:
        return
    for ev in ("track_started", "track_ended", "crossfade_start", "mode_changed",
               "effect_fired", "engine_stopped", "playback_error",
               "watchdog_recovery", "watchdog_recovered", "watchdog_stall"):
        engine.bus.subscribe(ev, ws_manager.broadcast_sync)

    def _execute_deferred_move(event: str, data: dict):
        audio_id = data.get("audio_id")
        new_path = data.get("new_path")
        if not audio_id or not new_path:
            return
        conn = sqlite3.connect(str(DB_PATH))
        try:
            row = conn.execute("SELECT ruta_archivo FROM audios WHERE id=?", (audio_id,)).fetchone()
            if not row:
                return
            src = Path(row[0])
            dst = Path(new_path)
            if src.exists() and src != dst:
                if _safe_copy_and_delete(src, dst):
                    conn.execute("UPDATE audios SET ruta_archivo=? WHERE id=?", (str(dst), audio_id))
                    conn.commit()
                    ws_manager.broadcast_sync("library_updated", {"action": "moved", "audio_id": audio_id})
        except Exception as exc:
            log.error("[API] Error en movimiento diferido: %s", exc)
        finally:
            conn.close()

    engine.bus.subscribe("execute_deferred_move", _execute_deferred_move)


# ══════════════════════════════════════════════════════════════════════════════
# Lifespan
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_manager.set_loop(asyncio.get_event_loop())
    _migrate_db()
    _subscribe_engine_events()
    if AUDIO_ENGINE_AVAILABLE:
        engine.start()
        log.info("[API] Motor de audio iniciado.")
    log.info("[API] Frontend: %s", FRONTEND_DIR)
    yield
    if AUDIO_ENGINE_AVAILABLE:
        engine.stop()
    log.info("[API] Motor detenido.")


# ══════════════════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Ónix FM Digital API", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket
# ══════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    status = engine.status if AUDIO_ENGINE_AVAILABLE else {"modo": "sin_motor"}
    await ws.send_text(json.dumps({"event": "status", "data": status}))
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            cmd = msg.get("cmd")

            if not AUDIO_ENGINE_AVAILABLE:
                await ws.send_text(json.dumps({"event": "error", "data": {"msg": "Motor no disponible"}}))
                continue

            if   cmd == "play":        engine.play_track(int(msg["audio_id"]))
            elif cmd == "queue":       engine.queue_track(int(msg["audio_id"]))
            elif cmd == "fire_effect": engine.fire_effect(int(msg["audio_id"]), int(msg.get("canal_offset", 0)))
            elif cmd == "set_mode":    engine.set_mode(msg["mode"])
            elif cmd == "ping":        await ws.send_text(json.dumps({"event": "pong"}))

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Estado e inventario
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/status")
def get_status():
    return engine.status if AUDIO_ENGINE_AVAILABLE else {"modo": "sin_motor"}


@app.get("/api/v1/stats")
def get_stats():
    """
    Contadores de inventario en tiempo real por tipo de audio.
    El Admin los muestra como badges 'X Canciones', 'X Jingles', etc.
    """
    conn  = get_conn()
    rows  = conn.execute(
        "SELECT tipo_audio, COUNT(*) AS cnt FROM audios WHERE activo=1 GROUP BY tipo_audio"
    ).fetchall()
    conn.close()
    totals = {r["tipo_audio"]: r["cnt"] for r in rows}
    return {
        "Musica":   totals.get("Musica",   0),
        "Efecto":   totals.get("Efecto",   0),
        "Cuña":     totals.get("Cuña",     0),
        "Sweeper":  totals.get("Sweeper",  0),
        "Programa": totals.get("Programa", 0),
        "Tips":     totals.get("Tips",     0),
        "total":    sum(totals.values()),
    }


@app.get("/api/v1/audios/filtros")
def get_filter_values():
    """
    Retorna los valores únicos existentes en la BD para poblar los dropdowns
    de filtros del Admin (géneros, artistas, años, categorías).
    Filtra solo registros activos para mantener los dropdowns limpios.
    """
    conn   = get_conn()
    gen    = [r[0] for r in conn.execute("SELECT DISTINCT genero_vocal FROM audios WHERE activo=1 AND genero_vocal!='' ORDER BY genero_vocal").fetchall()]
    art    = [r[0] for r in conn.execute("SELECT DISTINCT artista FROM audios WHERE activo=1 ORDER BY artista LIMIT 200").fetchall()]
    anios  = [r[0] for r in conn.execute("SELECT DISTINCT anio FROM audios WHERE activo=1 AND anio>0 ORDER BY anio DESC").fetchall()]
    cats   = [r[0] for r in conn.execute("SELECT DISTINCT categoria FROM audios WHERE activo=1 AND categoria!='' ORDER BY categoria").fetchall()]
    subg   = [r[0] for r in conn.execute("SELECT DISTINCT subgenero FROM audios WHERE activo=1 AND subgenero!='' ORDER BY subgenero").fetchall()]
    conn.close()
    return {"generos": gen, "artistas": art, "anios": anios, "categorias": cats, "subgeneros": subg}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Audios CRUD
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/audios")
def list_audios(
    tipo:      str = None,
    origen:    str = None,
    genero:    str = None,
    categoria: str = None,
    artista:   str = None,
    anio:      int = None,
    q:         str = None,
    limit:     int = 500,
):
    """Lista audios con filtros combinados. Todos los parámetros son opcionales."""
    conn    = get_conn()
    clauses = ["activo = 1"]
    params: list = []

    if tipo:      clauses.append("tipo_audio = ?");       params.append(tipo)
    if origen:    clauses.append("origen = ?");           params.append(origen)
    if genero:    clauses.append("genero_vocal = ?");     params.append(genero)
    if categoria: clauses.append("categoria = ?");        params.append(categoria)
    if artista:   clauses.append("artista = ?");          params.append(artista)
    if anio:      clauses.append("anio = ?");             params.append(anio)
    if q:
        clauses.append("(titulo LIKE ? OR artista LIKE ? OR subgenero LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    where = " AND ".join(clauses)
    rows  = conn.execute(
        f"SELECT * FROM audios WHERE {where} ORDER BY artista, titulo LIMIT ?",
        params + [limit]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/v1/audios/autocomplete")
def autocomplete_artista(q: str, limit: int = 10):
    if len(q) < 1:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT artista FROM audios WHERE artista LIKE ? ORDER BY artista LIMIT ?",
        (f"{q}%", limit)
    ).fetchall()
    conn.close()
    return [r["artista"] for r in rows]


@app.get("/api/v1/audios/{audio_id}/stream")
def stream_audio(audio_id: int):
    """
    Sirve el archivo de audio como respuesta binaria para la preescucha
    en el Admin. No requiere que media/ esté montado como estático.
    Usa FileResponse que soporta Range headers (necesario para <audio>).
    """
    conn = get_conn()
    row  = conn.execute("SELECT ruta_archivo, titulo FROM audios WHERE id=? AND activo=1", (audio_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Audio {audio_id} no encontrado")
    path = Path(row["ruta_archivo"])
    if not path.exists():
        raise HTTPException(404, f"Archivo físico no encontrado: {path}")
    # Determinar media_type por extensión
    ext_to_mime = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
        ".ogg": "audio/ogg",  ".aac": "audio/aac", ".m4a": "audio/mp4",
        ".opus": "audio/opus",
    }
    media_type = ext_to_mime.get(path.suffix.lower(), "audio/mpeg")
    return FileResponse(str(path), media_type=media_type,
                        headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@app.get("/api/v1/audios/{audio_id}")
def get_audio(audio_id: int):
    conn = get_conn()
    row  = conn.execute("SELECT * FROM audios WHERE id=?", (audio_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Track {audio_id} no encontrado")
    return dict(row)


@app.post("/api/v1/audios", status_code=201)
async def create_audio(
    archivo:       UploadFile = File(...),
    titulo:        str        = Form(...),
    artista:       str        = Form(...),
    genero_vocal:  str        = Form("Instrumental"),
    subgenero:     str        = Form(""),
    categoria:     str        = Form("Desconocido"),
    anio:          int        = Form(0),
    energia:       int        = Form(3),
    origen:        str        = Form("Nacional"),
    tipo_audio:    str        = Form("Musica"),
    intro_sec:     float      = Form(0.0),
    mix_point_sec: float      = Form(0.0),
    notas:         str        = Form(""),
):
    # Validar tipo
    if tipo_audio not in TIPOS_AUDIO:
        raise HTTPException(400, f"tipo_audio inválido. Permitidos: {TIPOS_AUDIO}")

    ext  = Path(archivo.filename).suffix.lower() or ".mp3"
    dest = _build_media_path(tipo_audio, genero_vocal, categoria, artista, titulo, ext)

    # Verificar duplicado antes de guardar en disco
    conn     = get_conn()
    existing = conn.execute("SELECT id FROM audios WHERE ruta_archivo=?", (str(dest),)).fetchone()
    conn.close()
    if existing or dest.exists():
        raise HTTPException(400, "Este archivo ya existe en el sistema")

    # Guardar en disco
    raw_bytes = await archivo.read()
    dest.write_bytes(raw_bytes)

    # Duración con mutagen
    duracion_seg = 0.0
    try:
        from mutagen import File as MutagenFile
        meta = MutagenFile(str(dest))
        if meta and meta.info:
            duracion_seg = meta.info.length
    except ImportError:
        pass

    puntos_audio = json.dumps({"intro_sec": intro_sec, "mix_point_sec": mix_point_sec})

    conn   = get_conn()
    new_id = None
    try:
        cur = conn.execute("""
            INSERT INTO audios
                (titulo, artista, genero_vocal, subgenero, categoria, anio,
                 energia, origen, tipo_audio, ruta_archivo, puntos_audio, duracion_seg, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (titulo, artista, genero_vocal, subgenero, categoria, anio,
              energia, origen, tipo_audio, str(dest), puntos_audio, duracion_seg, notas))
        conn.commit()
        new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Este archivo ya existe en el sistema")
    finally:
        conn.close()

    result = {"id": new_id, "titulo": titulo, "artista": artista, "ruta_archivo": str(dest)}

    await ws_manager.broadcast_async("library_updated", {
        "action":   "created",
        "audio_id": new_id,
        "tipo":     tipo_audio,
    })
    await ws_manager.broadcast_async("stats_updated", {})

    return result


@app.put("/api/v1/audios/{audio_id}")
async def update_audio(audio_id: int, body: dict):
    """
    Edición avanzada: acepta todos los campos editables.
    Si cambian tipo_audio, genero_vocal o categoria, llama internamente
    a la lógica de mover archivo para mantener la carpeta sincronizada.
    """
    allowed = {
        "titulo", "artista", "genero_vocal", "subgenero", "categoria", "anio",
        "energia", "origen", "tipo_audio", "puntos_audio", "notas"
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No hay campos válidos para actualizar")

    # Verificar si hay que mover el archivo físicamente
    campos_ruta = {"tipo_audio", "genero_vocal", "categoria", "artista", "titulo"}
    necesita_mover = bool(campos_ruta.intersection(updates.keys()))

    conn = get_conn()
    row  = conn.execute("SELECT * FROM audios WHERE id=? AND activo=1", (audio_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Track {audio_id} no encontrado")

    track = dict(row)

    if necesita_mover and track.get("ruta_archivo"):
        # Calcular nueva ruta con los campos actualizados
        tipo_nuevo    = updates.get("tipo_audio",   track["tipo_audio"])
        genero_nuevo  = updates.get("genero_vocal", track.get("genero_vocal", "Instrumental"))
        cat_nuevo     = updates.get("categoria",    track.get("categoria", "Desconocido"))
        artista_nuevo = updates.get("artista",      track["artista"])
        titulo_nuevo  = updates.get("titulo",       track["titulo"])
        ext           = Path(track["ruta_archivo"]).suffix or ".mp3"

        nueva_ruta = _build_media_path(tipo_nuevo, genero_nuevo, cat_nuevo, artista_nuevo, titulo_nuevo, ext)
        orig_ruta  = Path(track["ruta_archivo"])

        if nueva_ruta != orig_ruta and orig_ruta.exists():
            # Copy-and-Delete seguro
            is_playing = AUDIO_ENGINE_AVAILABLE and engine.is_track_playing(audio_id)
            if is_playing:
                engine.defer_file_move(audio_id, str(nueva_ruta))
            else:
                if _safe_copy_and_delete(orig_ruta, nueva_ruta):
                    updates["ruta_archivo"] = str(nueva_ruta)
                else:
                    raise HTTPException(500, "Error al mover el archivo físico. No se guardaron los cambios.")

    # Aplicar updates en BD
    allowed_with_ruta = allowed | {"ruta_archivo"}
    updates_final = {k: v for k, v in updates.items() if k in allowed_with_ruta}
    set_clause = ", ".join(f"{k}=?" for k in updates_final)
    values     = list(updates_final.values()) + [audio_id]

    conn = get_conn()
    cur  = conn.execute(f"UPDATE audios SET {set_clause} WHERE id=? AND activo=1", values)
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(404, f"Track {audio_id} no encontrado")

    await ws_manager.broadcast_async("library_updated", {"action": "updated", "audio_id": audio_id})
    return {"updated": audio_id, "fields": list(updates_final.keys())}


@app.delete("/api/v1/audios/{audio_id}")
async def delete_audio(audio_id: int):
    """Soft-delete: marca inactivo sin borrar el archivo físico."""
    conn = get_conn()
    conn.execute("UPDATE audios SET activo=0 WHERE id=?", (audio_id,))
    conn.commit()
    conn.close()
    await ws_manager.broadcast_async("library_updated", {"action": "deleted", "audio_id": audio_id})
    await ws_manager.broadcast_async("stats_updated", {})
    return {"deleted": audio_id}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Movimiento físico explícito (desde botón en Admin)
# ══════════════════════════════════════════════════════════════════════════════

@app.put("/api/v1/audios/{audio_id}/mover")
async def mover_audio(audio_id: int, body: dict):
    """
    Reubica un archivo en la carpeta jerárquica correcta sin editar metadatos.
    Acepta cualquier subconjunto de {tipo_audio, genero_vocal, categoria, artista, titulo}.
    Usa Copy-and-Delete para garantizar integridad.
    Si el track está en reproducción, el motor difiere el movimiento.
    """
    conn = get_conn()
    row  = conn.execute("SELECT * FROM audios WHERE id=? AND activo=1", (audio_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Audio {audio_id} no encontrado")

    track = dict(row)

    tipo_nuevo    = body.get("tipo_audio",   track["tipo_audio"])
    genero_nuevo  = body.get("genero_vocal", track.get("genero_vocal", "Instrumental"))
    cat_nuevo     = body.get("categoria",    track.get("categoria", "Desconocido"))
    artista_nuevo = body.get("artista",      track["artista"])
    titulo_nuevo  = body.get("titulo",       track["titulo"])

    ext       = Path(track["ruta_archivo"]).suffix or ".mp3"
    new_path  = _build_media_path(tipo_nuevo, genero_nuevo, cat_nuevo, artista_nuevo, titulo_nuevo, ext)
    orig_path = Path(track["ruta_archivo"])

    if new_path == orig_path:
        return {"estado": "sin_cambio", "ruta": str(new_path)}

    if not orig_path.exists():
        raise HTTPException(400, f"Archivo no encontrado en disco: {orig_path}")

    is_playing = AUDIO_ENGINE_AVAILABLE and engine.is_track_playing(audio_id)

    if is_playing:
        engine.defer_file_move(audio_id, str(new_path))
        return {"estado": "diferido", "nueva_ruta": str(new_path),
                "mensaje": "Track en reproducción — se moverá al terminar."}

    if not _safe_copy_and_delete(orig_path, new_path):
        raise HTTPException(500, "Error durante Copy-and-Delete. Archivo original intacto.")

    # Actualizar la BD con la nueva ruta y campos modificados
    campos_bd: dict = {"ruta_archivo": str(new_path)}
    for k, v in [("tipo_audio", tipo_nuevo), ("genero_vocal", genero_nuevo),
                 ("categoria", cat_nuevo), ("artista", artista_nuevo), ("titulo", titulo_nuevo)]:
        if k in body:
            campos_bd[k] = v

    set_clause = ", ".join(f"{k}=?" for k in campos_bd)
    conn = get_conn()
    conn.execute(f"UPDATE audios SET {set_clause} WHERE id=?", list(campos_bd.values()) + [audio_id])
    conn.commit()
    conn.close()

    await ws_manager.broadcast_async("library_updated", {
        "action": "moved", "audio_id": audio_id, "new_path": str(new_path)
    })
    return {"estado": "completado", "ruta_nueva": str(new_path)}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Reproducción
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/v1/play/{audio_id}")
def play_track(audio_id: int):
    if not AUDIO_ENGINE_AVAILABLE:
        raise HTTPException(503, "Motor de audio no disponible")
    ok = engine.play_track(audio_id)
    if not ok:
        raise HTTPException(404, f"Track {audio_id} no encontrado o error al reproducir")
    return {"playing": audio_id}


@app.post("/api/v1/effect/{audio_id}")
def fire_effect(audio_id: int, canal: int = 0):
    if not AUDIO_ENGINE_AVAILABLE:
        raise HTTPException(503, "Motor de audio no disponible")
    ok = engine.fire_effect(audio_id, canal)
    return {"fired": ok, "audio_id": audio_id}


@app.post("/api/v1/mode/{mode}")
def set_mode(mode: str):
    if mode not in ("manual", "automatico"):
        raise HTTPException(400, "Modo inválido. Usa 'manual' o 'automatico'")
    if AUDIO_ENGINE_AVAILABLE:
        engine.set_mode(mode)
    return {"modo": mode}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Bloques Horarios
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/bloques")
def list_bloques():
    conn    = get_conn()
    bloques = conn.execute(
        "SELECT * FROM bloques_horarios WHERE activo=1 ORDER BY orden, hora_inicio"
    ).fetchall()
    result = []
    for b in bloques:
        bd = dict(b)
        tracks = conn.execute("""
            SELECT a.id, a.titulo, a.artista, a.duracion_seg, bt.posicion
            FROM bloque_tracks bt
            JOIN audios a ON bt.audio_id=a.id
            WHERE bt.bloque_id=? AND a.activo=1
            ORDER BY bt.posicion
        """, (b["id"],)).fetchall()
        bd["tracks"] = [dict(t) for t in tracks]
        result.append(bd)
    conn.close()
    return result


@app.post("/api/v1/bloques", status_code=201)
async def create_bloque(body: dict):
    nombre = body.get("nombre", "Bloque nuevo").strip()
    if not nombre:
        raise HTTPException(400, "El nombre es obligatorio")
    conn = get_conn()
    cur  = conn.execute(
        "INSERT INTO bloques_horarios (nombre, hora_inicio, hora_fin, notas) VALUES (?,?,?,?)",
        (nombre, body.get("hora_inicio", "08:00"), body.get("hora_fin", "09:00"), body.get("notas", ""))
    )
    conn.commit(); new_id = cur.lastrowid; conn.close()
    await ws_manager.broadcast_async("bloques_updated", {"action": "created", "bloque_id": new_id})
    return {"id": new_id, "nombre": nombre}


@app.put("/api/v1/bloques/{bloque_id}")
async def update_bloque(bloque_id: int, body: dict):
    allowed = {"nombre", "hora_inicio", "hora_fin", "orden", "notas"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "Sin campos válidos")
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn = get_conn()
    cur  = conn.execute(
        f"UPDATE bloques_horarios SET {set_clause} WHERE id=?",
        list(updates.values()) + [bloque_id]
    )
    conn.commit(); conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, f"Bloque {bloque_id} no encontrado")
    await ws_manager.broadcast_async("bloques_updated", {"action": "updated", "bloque_id": bloque_id})
    return {"updated": bloque_id}


@app.delete("/api/v1/bloques/{bloque_id}")
async def delete_bloque(bloque_id: int):
    conn = get_conn()
    conn.execute("UPDATE bloques_horarios SET activo=0 WHERE id=?", (bloque_id,))
    conn.commit(); conn.close()
    await ws_manager.broadcast_async("bloques_updated", {"action": "deleted", "bloque_id": bloque_id})
    return {"deleted": bloque_id}


@app.post("/api/v1/bloques/{bloque_id}/tracks", status_code=201)
async def add_track_to_bloque(bloque_id: int, body: dict):
    audio_id = body.get("audio_id")
    if not audio_id:
        raise HTTPException(400, "audio_id es obligatorio")
    conn = get_conn()
    row  = conn.execute(
        "SELECT COALESCE(MAX(posicion)+1, 0) AS pos FROM bloque_tracks WHERE bloque_id=?", (bloque_id,)
    ).fetchone()
    pos = row["pos"] if row else 0
    try:
        conn.execute("INSERT INTO bloque_tracks (bloque_id, audio_id, posicion) VALUES (?,?,?)",
                     (bloque_id, audio_id, pos))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Track ya está en el bloque")
    finally:
        conn.close()
    await ws_manager.broadcast_async("bloques_updated", {
        "action": "track_added", "bloque_id": bloque_id, "audio_id": audio_id
    })
    return {"bloque_id": bloque_id, "audio_id": audio_id, "posicion": pos}


@app.delete("/api/v1/bloques/{bloque_id}/tracks/{audio_id}")
async def remove_track_from_bloque(bloque_id: int, audio_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM bloque_tracks WHERE bloque_id=? AND audio_id=?", (bloque_id, audio_id))
    conn.commit(); conn.close()
    await ws_manager.broadcast_async("bloques_updated", {
        "action": "track_removed", "bloque_id": bloque_id, "audio_id": audio_id
    })
    return {"removed": audio_id}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Escaneo de carpetas
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/v1/media/escanear")
async def escanear_media():
    conn = get_conn()
    rows = conn.execute("SELECT id, titulo, artista, ruta_archivo FROM audios WHERE activo=1").fetchall()
    conn.close()

    rutas_conocidas = {r["ruta_archivo"]: dict(r) for r in rows}
    nuevos, ya_conocidos, huerfanos = [], [], []

    if MEDIA_DIR.exists():
        for f in MEDIA_DIR.rglob("*"):
            if f.suffix.lower() not in AUDIO_EXTENSIONS or not f.is_file():
                continue
            sp = str(f)
            if sp in rutas_conocidas:
                ya_conocidos.append({"ruta": sp, "id": rutas_conocidas[sp]["id"],
                                      "titulo": rutas_conocidas[sp]["titulo"]})
            else:
                partes = re.split(r"[_-]", f.stem, maxsplit=1)
                nuevos.append({
                    "ruta": sp, "nombre_archivo": f.name,
                    "artista_sugerido": partes[0].strip() if len(partes) >= 2 else "",
                    "titulo_sugerido":  partes[1].strip() if len(partes) >= 2 else f.stem,
                    "tipo_detectado":   _detect_tipo_from_path(f),
                    "tamano_mb":        round(f.stat().st_size / 1048576, 2),
                })

    for ruta, track in rutas_conocidas.items():
        if not Path(ruta).exists():
            huerfanos.append({"id": track["id"], "titulo": track["titulo"],
                               "artista": track["artista"], "ruta": ruta})

    return {
        "resumen": {"nuevos": len(nuevos), "ya_conocidos": len(ya_conocidos),
                    "huerfanos": len(huerfanos)},
        "nuevos": nuevos, "ya_conocidos": ya_conocidos, "huerfanos": huerfanos,
    }


@app.post("/api/v1/media/registrar_encontrado")
async def registrar_encontrado(body: dict):
    ruta   = body.get("ruta", "")
    titulo = body.get("titulo", "").strip()
    if not ruta or not titulo:
        raise HTTPException(400, "Campos 'ruta' y 'titulo' son obligatorios")
    if not Path(ruta).exists():
        raise HTTPException(400, f"Archivo no existe en disco: {ruta}")

    duracion_seg = 0.0
    try:
        from mutagen import File as MutagenFile
        meta = MutagenFile(ruta)
        if meta and meta.info:
            duracion_seg = meta.info.length
    except ImportError:
        pass

    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO audios
                (titulo, artista, genero_vocal, subgenero, categoria, anio,
                 energia, origen, tipo_audio, ruta_archivo, puntos_audio, duracion_seg, notas)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (titulo,
              body.get("artista", "Desconocido"),
              body.get("genero_vocal", "Instrumental"),
              body.get("subgenero", ""),
              body.get("categoria", "Desconocido"),
              int(body.get("anio", 0)),
              int(body.get("energia", 3)),
              body.get("origen", "Nacional"),
              body.get("tipo_audio", "Musica"),
              ruta,
              json.dumps({"intro_sec": 0, "mix_point_sec": 0}),
              duracion_seg,
              "Registrado via escaneo manual"))
        conn.commit()
        new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Este archivo ya existe en el sistema")
    finally:
        conn.close()

    await ws_manager.broadcast_async("library_updated", {"action": "created", "audio_id": new_id})
    await ws_manager.broadcast_async("stats_updated", {})
    return {"id": new_id, "titulo": titulo}


def _detect_tipo_from_path(file_path: Path) -> str:
    parts = file_path.parts
    for i, part in enumerate(parts):
        if part.lower() == "media" and i + 1 < len(parts):
            sub = parts[i + 1].lower()
            if "musica" in sub or "music" in sub: return "Musica"
            if "efecto" in sub or "jingle" in sub: return "Efecto"
            if "cuna" in sub or "publicidad" in sub: return "Cuña"
            if "sweeper" in sub: return "Sweeper"
            if "programa" in sub: return "Programa"
            if "tips" in sub: return "Tips"
    return "Musica"


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Configuración
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/config")
def get_config():
    conn = get_conn()
    rows = conn.execute("SELECT clave, valor FROM config").fetchall()
    conn.close()
    return {r["clave"]: r["valor"] for r in rows}


@app.put("/api/v1/config/{clave}")
def update_config(clave: str, body: dict):
    valor = str(body.get("valor", ""))
    conn  = get_conn()
    conn.execute("UPDATE config SET valor=? WHERE clave=?", (valor, clave))
    conn.commit(); conn.close()
    return {"updated": clave, "valor": valor}


# ══════════════════════════════════════════════════════════════════════════════
# Frontend estático
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def serve_studio():
    f = FRONTEND_DIR / "index.html"
    return FileResponse(str(f)) if f.exists() else {"error": "index.html no encontrado"}

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# Montar media/ como estático
if MEDIA_DIR.exists():
    app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# Nueva montura para el Panel Admin modular
ADMIN_DIR = FRONTEND_DIR / "admin"
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
