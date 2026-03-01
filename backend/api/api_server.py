"""
api_server.py — Ónix FM Digital  v2.1
REST + WebSockets. Columnas canónicas de `audios`:
    id, titulo, artista, genero_vocal, energia, origen,
    tipo_audio, ruta_archivo, puntos_audio, duracion_seg,
    ultima_reproduccion, veces_reproducido, activo, notas

NUEVOS ENDPOINTS v2.1:
    GET  /api/v1/audios/{id}          — un track por ID
    PUT  /api/v1/audios/{id}          — editar metadatos
    GET  /api/v1/bloques              — listar bloques horarios
    POST /api/v1/bloques              — crear bloque
    PUT  /api/v1/bloques/{id}         — editar bloque
    DELETE /api/v1/bloques/{id}       — eliminar bloque
    GET  /api/v1/bloques/{id}/tracks  — tracks de un bloque
    POST /api/v1/bloques/{id}/tracks  — agregar track a bloque
    DELETE /api/v1/bloques/{id}/tracks/{audio_id} — quitar track

CORRECCIONES v2.1:
    ✔ ruta_archivo (NO archivo_path) en INSERT y toda la API
    ✔ genero_vocal, puntos_audio, duracion_seg, notas en INSERT
    ✔ Duplicado amigable: "Este archivo ya existe en el sistema"
    ✔ Doble conn.close() eliminado
    ✔ Broadcast WS al Admin cuando cambia la biblioteca
"""

import asyncio
import json
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Set, Optional

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, UploadFile, File, Form
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import shutil
import re
import unicodedata
from pydantic import BaseModel


# ══════════════════════════════════════════════════════════════════════════════
# INSTRUCCIONES DE INSTALACIÓN (para nuevos colaboradores):
#   1. pip install -r requirements.txt
#   2. python scripts/init_db.py          ← crea radio_core.db
#   3. python backend/api/api_server.py   ← lanza el servidor en :8000
#
# El servidor acepta conexiones desde la red local (host="0.0.0.0").
# Studio: http://TU_IP:8000/      Admin: http://TU_IP:8000/admin
# ══════════════════════════════════════════════════════════════════════════════

# ── Rutas base (CORRECCIÓN CRÍTICA WINDOWS) ───────────────────────────────────
# Usamos Path(__file__).resolve() en lugar de Path.cwd().
# La diferencia es fundamental:
#   · Path.cwd()     → depende de DESDE DÓNDE lances el script (frágil)
#   · Path(__file__) → siempre apunta AL ARCHIVO, sin importar el IDE o terminal
#
# api_server.py está en: /tu-proyecto/backend/api/api_server.py
# Subimos 3 niveles (.parent × 3) para llegar a /tu-proyecto/
_THIS_FILE   = Path(__file__).resolve()          # → .../backend/api/api_server.py
BASE_DIR     = _THIS_FILE.parent.parent.parent   # → /tu-proyecto/
DB_PATH      = BASE_DIR / "radio_core.db"
MEDIA_DIR    = BASE_DIR / "media"
FRONTEND_DIR = BASE_DIR / "frontend"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# Extensiones de audio reconocidas para el escaneo de carpetas
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".opus"}


def _sanitize_filename(s: str) -> str:
    """
    Convierte una cadena en un nombre de archivo seguro para todos los OS:
        1. Elimina acentos y diacriticos  (José → Jose, Ópera → Opera)
        2. Convierte espacios y separadores a guion bajo
        3. Elimina caracteres especiales que no son permitidos en Windows/Linux
        4. Limita a 80 caracteres para compatibilidad maxima

    Ejemplos:
        "Juan Pérez"       → "Juan_Perez"
        "¡Rock & Roll!"    → "Rock_Roll"
        "Açaí – Tropicál"  → "Acai_Tropical"
    """
    s = s.strip()
    # Paso 1: Normalizar Unicode a NFD (descompone diacriticos)
    # En NFD, "é" se convierte en "e" + combinando acento,
    # lo que permite eliminar solo el acento sin perder la letra base.
    s = unicodedata.normalize("NFD", s)
    # Eliminar todos los caracteres combinantes (acentos, tildes, etc.)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # Paso 2: Reemplazar separadores comunes por guion bajo
    s = re.sub(r"[\s\-–—/\\]+", "_", s)
    # Paso 3: Eliminar caracteres no ASCII seguros (conserva a-z, A-Z, 0-9, _, .)
    s = re.sub(r"[^\w\.-]", "", s)
    # Paso 4: Limpiar guiones bajos multiples y bordes
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "audio"


def _build_media_path(tipo_audio: str, genero_vocal: str,
                      artista: str, titulo: str, ext: str = ".mp3") -> Path:
    """
    Construye la ruta jerarquica dentro de media/:
        media/{tipo_audio}/{genero_vocal}/{artista}_{titulo}{ext}

    Ejemplos:
        media/Musica/Hombre/Juan_Gabriel_Querida.mp3
        media/Efecto/Sin_genero/Onix_Jingle_Cortina.mp3
        media/Cuña/Sin_genero/Pepsi_Spot_30s.mp3

    El genero_vocal solo aplica a Musica; para otros tipos usa "Sin_genero".
    """
    # Para tipos que no usan genero vocal, usar carpeta generica
    genero_folder = genero_vocal if tipo_audio == "Musica" else "Sin_genero"

    folder = MEDIA_DIR / _sanitize_filename(tipo_audio) / _sanitize_filename(genero_folder)
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{_sanitize_filename(artista)}_{_sanitize_filename(titulo)}{ext}"
    return folder / filename

sys.path.insert(0, str(BASE_DIR))
try:
    from backend.audio.audio_engine import engine
    AUDIO_ENGINE_AVAILABLE = True
except ImportError:
    engine = None
    AUDIO_ENGINE_AVAILABLE = False
    print("[API] AVISO: audio_engine no encontrado. Endpoints de reproducción deshabilitados.")


# ══════════════════════════════════════════════════════════════════════════════
# WebSocket Manager
# ══════════════════════════════════════════════════════════════════════════════
class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop):
        self._loop = loop

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    def broadcast_sync(self, event: str, payload: dict):
        """Puente síncrono→asíncrono (llamado desde el hilo del motor)."""
        message = json.dumps({"event": event, "data": payload})
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)

    async def broadcast_async(self, event: str, payload: dict):
        """Broadcast desde contexto async (endpoints REST)."""
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


def _subscribe_engine_events():
    if not AUDIO_ENGINE_AVAILABLE:
        return
    for ev in ("track_started", "track_ended", "crossfade_start",
               "mode_changed", "effect_fired", "engine_stopped", "playback_error",
               "watchdog_recovery", "watchdog_recovered"):
        engine.bus.subscribe(ev, ws_manager.broadcast_sync)

    # Suscribir ejecutor de movimientos diferidos.
    # Cuando el motor emite 'execute_deferred_move', este handler
    # realiza el shutil.move y actualiza la BD de forma segura.
    def _execute_deferred_move(event: str, data: dict):
        audio_id = data.get("audio_id")
        new_path = data.get("new_path")
        if not audio_id or not new_path:
            return
        conn = sqlite3.connect(str(DB_PATH))
        try:
            row = conn.execute(
                "SELECT ruta_archivo FROM audios WHERE id=?", (audio_id,)
            ).fetchone()
            if not row:
                return
            orig = Path(row[0])
            new  = Path(new_path)
            if orig.exists() and orig != new:
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(orig), str(new))
                conn.execute(
                    "UPDATE audios SET ruta_archivo=? WHERE id=?",
                    (str(new), audio_id)
                )
                conn.commit()
                log.info("[API] Movimiento diferido ejecutado: %s -> %s", orig, new)
                ws_manager.broadcast_sync("library_updated", {
                    "action": "moved", "audio_id": audio_id, "new_path": str(new)
                })
        except Exception as exc:
            log.error("[API] Error en movimiento diferido: %s", exc)
        finally:
            conn.close()

    engine.bus.subscribe("execute_deferred_move", _execute_deferred_move)


# ══════════════════════════════════════════════════════════════════════════════
# DB helpers — tablas de bloques (creadas en lifespan si no existen)
# ══════════════════════════════════════════════════════════════════════════════
def _ensure_bloques_tables():
    """
    Crea las tablas de bloques horarios si no existen.
    Separado de init_db.py para no romper el script original.
    """
    conn = sqlite3.connect(DB_PATH)
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
            id          INTEGER  PRIMARY KEY AUTOINCREMENT,
            bloque_id   INTEGER  NOT NULL REFERENCES bloques_horarios(id) ON DELETE CASCADE,
            audio_id    INTEGER  NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
            posicion    INTEGER  NOT NULL DEFAULT 0,
            UNIQUE(bloque_id, audio_id)
        );

        CREATE INDEX IF NOT EXISTS idx_bt_bloque ON bloque_tracks(bloque_id);
        CREATE INDEX IF NOT EXISTS idx_bt_audio  ON bloque_tracks(audio_id);
    """)
    conn.commit()
    conn.close()


def get_conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ══════════════════════════════════════════════════════════════════════════════
# Lifespan
# ══════════════════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    ws_manager.set_loop(asyncio.get_event_loop())
    _subscribe_engine_events()
    _ensure_bloques_tables()
    if AUDIO_ENGINE_AVAILABLE:
        engine.start()
        print("[API] Motor de audio iniciado.")
    print(f"[API] Frontend: {FRONTEND_DIR}")
    yield
    if AUDIO_ENGINE_AVAILABLE:
        engine.stop()
    print("[API] Motor detenido.")


# ══════════════════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════════════════
app = FastAPI(title="Ónix FM Digital API", version="2.1.0", lifespan=lifespan)
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

            if cmd == "play":
                engine.play_track(int(msg["audio_id"]))
            elif cmd == "queue":
                engine.queue_track(int(msg["audio_id"]))
            elif cmd == "fire_effect":
                engine.fire_effect(int(msg["audio_id"]), int(msg.get("canal_offset", 0)))
            elif cmd == "set_mode":
                engine.set_mode(msg["mode"])
            elif cmd == "ping":
                await ws.send_text(json.dumps({"event": "pong"}))

    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Estado
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/v1/status")
def get_status():
    return engine.status if AUDIO_ENGINE_AVAILABLE else {"modo": "sin_motor"}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Audios (CRUD)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/v1/audios")
def list_audios(tipo: str = None, origen: str = None, q: str = None, limit: int = 500):
    """Lista audios con filtros opcionales. Soporta búsqueda de texto libre."""
    conn   = get_conn()
    clauses = ["activo = 1"]
    params: list = []

    if tipo:
        clauses.append("tipo_audio = ?")
        params.append(tipo)
    if origen:
        clauses.append("origen = ?")
        params.append(origen)
    if q:
        clauses.append("(titulo LIKE ? OR artista LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

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
    energia:       int        = Form(3),
    origen:        str        = Form("Nacional"),
    tipo_audio:    str        = Form("Musica"),
    intro_sec:     float      = Form(0.0),
    mix_point_sec: float      = Form(0.0),
    notas:         str        = Form(""),
):
    # ── Construir ruta jerarquica y renombrar automaticamente ────────────────
    # Formato: media/{tipo_audio}/{genero_vocal}/{artista}_{titulo}.ext
    # El archivo se renombra al guardar. El nombre original del cliente
    # se descarta para garantizar consistencia en el sistema de archivos.
    ext  = Path(archivo.filename).suffix.lower() or ".mp3"
    dest = _build_media_path(tipo_audio, genero_vocal, artista, titulo, ext)

    # ── Verificar duplicado ANTES de guardar en disco ─────────────────────────
    conn     = get_conn()
    existing = conn.execute(
        "SELECT id FROM audios WHERE ruta_archivo = ?", (str(dest),)
    ).fetchone()
    conn.close()

    if existing or dest.exists():
        raise HTTPException(400, "Este archivo ya existe en el sistema")

    # ── Guardar en disco con nombre canonico ──────────────────────────────────
    raw_bytes = await archivo.read()
    dest.write_bytes(raw_bytes)

    # ── Duración con mutagen (opcional) ──────────────────────────────────────
    duracion_seg = 0.0
    try:
        from mutagen import File as MutagenFile
        meta = MutagenFile(str(dest))
        if meta and meta.info:
            duracion_seg = meta.info.length
    except ImportError:
        pass

    puntos_audio = json.dumps({"intro_sec": intro_sec, "mix_point_sec": mix_point_sec})

    # ── INSERT (10 columnas exactas) ──────────────────────────────────────────
    conn   = get_conn()
    new_id = None
    try:
        cur = conn.execute(
            """
            INSERT INTO audios
                (titulo, artista, genero_vocal, energia, origen,
                 tipo_audio, ruta_archivo, puntos_audio, duracion_seg, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (titulo, artista, genero_vocal, energia, origen,
             tipo_audio, str(dest), puntos_audio, duracion_seg, notas)
        )
        conn.commit()
        new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Este archivo ya existe en el sistema")
    finally:
        conn.close()   # único close

    result = {"id": new_id, "titulo": titulo, "artista": artista, "ruta_archivo": str(dest)}

    # ── Notificar a Studio y Admin en tiempo real ─────────────────────────────
    await ws_manager.broadcast_async("library_updated", {
        "action":   "created",
        "audio_id": new_id,
        "tipo":     tipo_audio,
    })

    return result


@app.put("/api/v1/audios/{audio_id}")
async def update_audio(audio_id: int, body: dict):
    """
    Actualiza metadatos de un track. No modifica el archivo físico.
    Acepta cualquier subconjunto de: titulo, artista, genero_vocal,
    energia, origen, tipo_audio, puntos_audio, notas.
    """
    allowed = {"titulo", "artista", "genero_vocal", "energia",
               "origen", "tipo_audio", "puntos_audio", "notas"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No hay campos válidos para actualizar")

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values     = list(updates.values()) + [audio_id]

    conn = get_conn()
    cur  = conn.execute(
        f"UPDATE audios SET {set_clause} WHERE id=? AND activo=1", values
    )
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(404, f"Track {audio_id} no encontrado")

    await ws_manager.broadcast_async("library_updated", {
        "action":   "updated",
        "audio_id": audio_id,
    })
    return {"updated": audio_id, "fields": list(updates.keys())}


@app.delete("/api/v1/audios/{audio_id}")
async def delete_audio(audio_id: int):
    """Soft-delete: marca inactivo sin borrar el archivo físico."""
    conn = get_conn()
    conn.execute("UPDATE audios SET activo=0 WHERE id=?", (audio_id,))
    conn.commit()
    conn.close()

    await ws_manager.broadcast_async("library_updated", {
        "action":   "deleted",
        "audio_id": audio_id,
    })
    return {"deleted": audio_id}


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
    """Lista todos los bloques horarios con sus tracks asignados."""
    conn   = get_conn()
    bloques = conn.execute(
        "SELECT * FROM bloques_horarios WHERE activo=1 ORDER BY orden, hora_inicio"
    ).fetchall()

    result = []
    for b in bloques:
        bd = dict(b)
        tracks = conn.execute(
            """
            SELECT a.id, a.titulo, a.artista, a.duracion_seg, bt.posicion
            FROM bloque_tracks bt
            JOIN audios a ON bt.audio_id = a.id
            WHERE bt.bloque_id = ? AND a.activo = 1
            ORDER BY bt.posicion
            """,
            (b["id"],)
        ).fetchall()
        bd["tracks"] = [dict(t) for t in tracks]
        result.append(bd)

    conn.close()
    return result


@app.post("/api/v1/bloques", status_code=201)
async def create_bloque(body: dict):
    nombre      = body.get("nombre", "Bloque nuevo").strip()
    hora_inicio = body.get("hora_inicio", "08:00")
    hora_fin    = body.get("hora_fin",    "09:00")
    notas       = body.get("notas",       "")

    if not nombre:
        raise HTTPException(400, "El nombre es obligatorio")

    conn = get_conn()
    cur  = conn.execute(
        "INSERT INTO bloques_horarios (nombre, hora_inicio, hora_fin, notas) VALUES (?,?,?,?)",
        (nombre, hora_inicio, hora_fin, notas)
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()

    await ws_manager.broadcast_async("bloques_updated", {"action": "created", "bloque_id": new_id})
    return {"id": new_id, "nombre": nombre, "hora_inicio": hora_inicio, "hora_fin": hora_fin}


@app.put("/api/v1/bloques/{bloque_id}")
async def update_bloque(bloque_id: int, body: dict):
    allowed = {"nombre", "hora_inicio", "hora_fin", "orden", "notas"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No hay campos válidos")

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn = get_conn()
    cur  = conn.execute(
        f"UPDATE bloques_horarios SET {set_clause} WHERE id=?",
        list(updates.values()) + [bloque_id]
    )
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        raise HTTPException(404, f"Bloque {bloque_id} no encontrado")

    await ws_manager.broadcast_async("bloques_updated", {"action": "updated", "bloque_id": bloque_id})
    return {"updated": bloque_id}


@app.delete("/api/v1/bloques/{bloque_id}")
async def delete_bloque(bloque_id: int):
    conn = get_conn()
    conn.execute("UPDATE bloques_horarios SET activo=0 WHERE id=?", (bloque_id,))
    conn.commit()
    conn.close()
    await ws_manager.broadcast_async("bloques_updated", {"action": "deleted", "bloque_id": bloque_id})
    return {"deleted": bloque_id}


@app.get("/api/v1/bloques/{bloque_id}/tracks")
def get_bloque_tracks(bloque_id: int):
    conn   = get_conn()
    tracks = conn.execute(
        """
        SELECT a.id, a.titulo, a.artista, a.duracion_seg, bt.posicion
        FROM bloque_tracks bt
        JOIN audios a ON bt.audio_id = a.id
        WHERE bt.bloque_id = ? AND a.activo = 1
        ORDER BY bt.posicion
        """,
        (bloque_id,)
    ).fetchall()
    conn.close()
    return [dict(t) for t in tracks]


@app.post("/api/v1/bloques/{bloque_id}/tracks", status_code=201)
async def add_track_to_bloque(bloque_id: int, body: dict):
    audio_id = body.get("audio_id")
    if not audio_id:
        raise HTTPException(400, "audio_id es obligatorio")

    conn = get_conn()
    # Obtener siguiente posición
    row = conn.execute(
        "SELECT COALESCE(MAX(posicion)+1, 0) AS pos FROM bloque_tracks WHERE bloque_id=?",
        (bloque_id,)
    ).fetchone()
    pos = row["pos"] if row else 0

    try:
        conn.execute(
            "INSERT INTO bloque_tracks (bloque_id, audio_id, posicion) VALUES (?,?,?)",
            (bloque_id, audio_id, pos)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "Este track ya está en el bloque")
    finally:
        conn.close()

    await ws_manager.broadcast_async("bloques_updated", {
        "action": "track_added", "bloque_id": bloque_id, "audio_id": audio_id
    })
    return {"bloque_id": bloque_id, "audio_id": audio_id, "posicion": pos}


@app.delete("/api/v1/bloques/{bloque_id}/tracks/{audio_id}")
async def remove_track_from_bloque(bloque_id: int, audio_id: int):
    conn = get_conn()
    conn.execute(
        "DELETE FROM bloque_tracks WHERE bloque_id=? AND audio_id=?",
        (bloque_id, audio_id)
    )
    conn.commit()
    conn.close()
    await ws_manager.broadcast_async("bloques_updated", {
        "action": "track_removed", "bloque_id": bloque_id, "audio_id": audio_id
    })
    return {"removed": audio_id, "bloque_id": bloque_id}


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Movimiento fisico de archivos
# ══════════════════════════════════════════════════════════════════════════════

@app.put("/api/v1/audios/{audio_id}/mover")
async def mover_audio(audio_id: int, body: dict):
    """
    Mueve fisicamente un archivo de audio a una nueva ubicacion jerarquica
    y actualiza la columna ruta_archivo en la base de datos.

    Cuerpo esperado:
        { "tipo_audio": "Musica", "genero_vocal": "Mujer",
          "artista": "...", "titulo": "..." }

    SEGURIDAD: Si el archivo esta siendo reproducido en este momento, el
    movimiento se encola para ejecutarse cuando termine la pista. Esto evita
    que el motor intente leer un archivo que acaba de moverse.
    """
    conn = get_conn()
    row  = conn.execute("SELECT * FROM audios WHERE id=? AND activo=1", (audio_id,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, f"Audio {audio_id} no encontrado")

    track = dict(row)

    # Determinar nueva ruta segun los parametros recibidos
    tipo_nuevo   = body.get("tipo_audio",   track["tipo_audio"])
    genero_nuevo = body.get("genero_vocal", track.get("genero_vocal", "Instrumental"))
    artista_nuevo = body.get("artista",     track["artista"])
    titulo_nuevo  = body.get("titulo",      track["titulo"])

    ext       = Path(track["ruta_archivo"]).suffix or ".mp3"
    new_path  = _build_media_path(tipo_nuevo, genero_nuevo, artista_nuevo, titulo_nuevo, ext)
    orig_path = Path(track["ruta_archivo"])

    if new_path == orig_path:
        return {"mensaje": "El archivo ya esta en la ruta correcta", "ruta": str(new_path)}

    if not orig_path.exists():
        raise HTTPException(400, f"Archivo de origen no encontrado en disco: {orig_path}")

    # ── Verificar si la pista esta siendo reproducida ahora mismo ─────────────
    is_playing = (AUDIO_ENGINE_AVAILABLE and
                  engine.is_track_playing(audio_id))

    if is_playing:
        # Diferir el movimiento hasta que termine la pista actual.
        # El motor ejecutara shutil.move y actualizara la BD cuando
        # detecte que el canal ya no tiene ese track activo.
        if AUDIO_ENGINE_AVAILABLE:
            engine.defer_file_move(audio_id, str(new_path))

        return {
            "estado":  "diferido",
            "mensaje": "El archivo esta en reproduccion. El movimiento se ejecutara al terminar la pista.",
            "nueva_ruta": str(new_path),
        }

    # ── Mover el archivo fisicamente ─────────────────────────────────────────
    try:
        shutil.move(str(orig_path), str(new_path))
    except Exception as exc:
        raise HTTPException(500, f"Error al mover el archivo: {exc}")

    # ── Actualizar la BD ──────────────────────────────────────────────────────
    updates: dict = {"ruta_archivo": str(new_path)}
    if "tipo_audio"   in body: updates["tipo_audio"]   = tipo_nuevo
    if "genero_vocal" in body: updates["genero_vocal"] = genero_nuevo
    if "artista"      in body: updates["artista"]       = artista_nuevo
    if "titulo"       in body: updates["titulo"]        = titulo_nuevo

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn = get_conn()
    conn.execute(
        f"UPDATE audios SET {set_clause} WHERE id=?",
        list(updates.values()) + [audio_id]
    )
    conn.commit()
    conn.close()

    await ws_manager.broadcast_async("library_updated", {
        "action":    "moved",
        "audio_id":  audio_id,
        "new_path":  str(new_path),
    })

    return {
        "estado":     "completado",
        "audio_id":   audio_id,
        "ruta_nueva": str(new_path),
        "campos_actualizados": list(updates.keys()),
    }


@app.post("/api/v1/media/escanear")
async def escanear_media():
    """
    Escanea la carpeta media/ en busca de archivos de audio que no esten
    registrados en la base de datos. Util cuando el encargado pego archivos
    manualmente en las carpetas sin pasar por el formulario de ingesta.

    Retorna tres listas:
        nuevos       — archivos encontrados pero no en la BD (listos para registrar)
        ya_conocidos — archivos que ya tienen registro en la BD
        huerfanos    — registros en la BD cuyo archivo ya no existe en disco
    """
    conn = get_conn()
    # Obtener todas las rutas conocidas en la BD
    rows = conn.execute(
        "SELECT id, titulo, artista, ruta_archivo FROM audios WHERE activo=1"
    ).fetchall()
    conn.close()

    rutas_conocidas: dict[str, dict] = {
        r["ruta_archivo"]: dict(r) for r in rows
    }

    nuevos:       list[dict] = []
    ya_conocidos: list[dict] = []
    huerfanos:    list[dict] = []

    # ── Escanear archivos en disco ────────────────────────────────────────────
    if MEDIA_DIR.exists():
        for audio_file in MEDIA_DIR.rglob("*"):
            if audio_file.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            if not audio_file.is_file():
                continue

            str_path = str(audio_file)

            if str_path in rutas_conocidas:
                ya_conocidos.append({
                    "ruta":   str_path,
                    "id":     rutas_conocidas[str_path]["id"],
                    "titulo": rutas_conocidas[str_path]["titulo"],
                })
            else:
                # Intentar extraer artista y titulo del nombre de archivo
                base   = audio_file.stem
                partes = re.split(r"[_-]", base, maxsplit=1)
                nuevos.append({
                    "ruta":            str_path,
                    "nombre_archivo":  audio_file.name,
                    "artista_sugerido": partes[0].strip() if len(partes) >= 2 else "",
                    "titulo_sugerido":  partes[1].strip() if len(partes) >= 2 else base,
                    "tipo_detectado":  _detect_tipo_from_path(audio_file),
                    "tamano_mb":       round(audio_file.stat().st_size / 1024 / 1024, 2),
                })

    # ── Verificar huerfanos (registros sin archivo en disco) ──────────────────
    for ruta, track in rutas_conocidas.items():
        if not Path(ruta).exists():
            huerfanos.append({
                "id":    track["id"],
                "titulo": track["titulo"],
                "artista": track["artista"],
                "ruta":  ruta,
            })

    return {
        "resumen": {
            "nuevos":        len(nuevos),
            "ya_conocidos":  len(ya_conocidos),
            "huerfanos":     len(huerfanos),
            "total_en_disco":len(nuevos) + len(ya_conocidos),
        },
        "nuevos":       nuevos,
        "ya_conocidos": ya_conocidos,
        "huerfanos":    huerfanos,
    }


@app.post("/api/v1/media/registrar_encontrado")
async def registrar_encontrado(body: dict):
    """
    Registra en la BD un archivo encontrado por el escaneo manual.
    Recibe los metadatos del archivo y lo inserta como nuevo audio.
    """
    ruta    = body.get("ruta", "")
    titulo  = body.get("titulo", "").strip()
    artista = body.get("artista", "Desconocido").strip()
    tipo    = body.get("tipo_audio", "Musica")
    genero  = body.get("genero_vocal", "Instrumental")
    energia = int(body.get("energia", 3))
    origen  = body.get("origen", "Nacional")

    if not ruta or not titulo:
        raise HTTPException(400, "Campos 'ruta' y 'titulo' son obligatorios")
    if not Path(ruta).exists():
        raise HTTPException(400, f"El archivo no existe en disco: {ruta}")

    # Obtener duracion con mutagen si esta disponible
    duracion_seg = 0.0
    try:
        from mutagen import File as MutagenFile
        meta = MutagenFile(ruta)
        if meta and meta.info:
            duracion_seg = meta.info.length
    except ImportError:
        pass

    puntos = json.dumps({"intro_sec": 0, "mix_point_sec": 0})

    conn   = get_conn()
    new_id = None
    try:
        cur = conn.execute(
            "INSERT INTO audios"
            " (titulo, artista, genero_vocal, energia, origen,"
            "  tipo_audio, ruta_archivo, puntos_audio, duracion_seg, notas)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (titulo, artista, genero, energia, origen,
             tipo, ruta, puntos, duracion_seg, "Registrado via escaneo manual")
        )
        conn.commit()
        new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Este archivo ya existe en el sistema")
    finally:
        conn.close()

    await ws_manager.broadcast_async("library_updated", {
        "action": "created", "audio_id": new_id, "tipo": tipo
    })
    return {"id": new_id, "titulo": titulo, "artista": artista}


def _detect_tipo_from_path(file_path: Path) -> str:
    """
    Intenta detectar el tipo de audio basandose en la carpeta donde vive.
    Por ejemplo, media/Musica/... -> "Musica".
    """
    parts = file_path.parts
    for i, part in enumerate(parts):
        if part.lower() == "media" and i + 1 < len(parts):
            subfolder = parts[i + 1].lower()
            if "musica" in subfolder or "music" in subfolder:
                return "Musica"
            if "efecto" in subfolder or "jingle" in subfolder:
                return "Efecto"
            if "cuna" in subfolder or "publicidad" in subfolder or "ads" in subfolder:
                return "Cuña"
            if "tips" in subfolder:
                return "Tips"
    return "Musica"


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — Configuración
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/v1/config")
def get_config():
    conn = get_conn()
    rows = conn.execute("SELECT clave, valor, descripcion FROM config").fetchall()
    conn.close()
    return {r["clave"]: r["valor"] for r in rows}


@app.put("/api/v1/config/{clave}")
def update_config(clave: str, body: dict):
    valor = str(body.get("valor", ""))
    conn  = get_conn()
    conn.execute("UPDATE config SET valor=? WHERE clave=?", (valor, clave))
    conn.commit()
    conn.close()
    return {"updated": clave, "valor": valor}


# ══════════════════════════════════════════════════════════════════════════════
# Frontend estático
#
# ORDEN IMPORTANTE EN FASTAPI:
#   1. Primero declaramos los @app.get() con sus rutas exactas ("/", "/admin")
#   2. Luego montamos los archivos estáticos con app.mount("/static", ...)
#
# ¿Por qué importa el orden? app.mount() crea una sub-aplicación que actúa como
# un "catch-all" para su prefijo. Si la montáramos en "/" capturaría TODO.
# Al montarla en "/static" solo captura /static/*, sin interferir con /admin.
# Pero si los @app.get() estuvieran registrados DESPUÉS del mount en algún
# escenario de reload o middleware, podrían perderse. Declararlos primero es
# la práctica recomendada por la documentación de FastAPI.
#
# Adicionalmente, las rutas NO están dentro de un bloque `if` para garantizar
# que siempre se registren, aunque FRONTEND_DIR no exista aún.
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def serve_studio():
    """Sirve el Studio (interfaz de aire). Acceso: http://localhost:8000/"""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {
        "mensaje": "Studio no encontrado",
        "ruta_esperada": str(index_file),
        "tip": "Coloca index.html en la carpeta 'frontend/' de la raíz del proyecto",
    }


@app.get("/admin")
def serve_admin():
    """
    Sirve el panel Admin. Acceso: http://localhost:8000/admin
    Coloca admin.html en la misma carpeta que index.html (frontend/).
    """
    admin_file = FRONTEND_DIR / "admin.html"
    if admin_file.exists():
        return FileResponse(str(admin_file))
    return {
        "mensaje": "admin.html no encontrado",
        "ruta_esperada": str(admin_file),
        "tip": "Copia admin.html a la carpeta 'frontend/' junto a index.html",
    }


# Archivos estáticos (CSS, JS, imágenes) — SIEMPRE después de los @app.get()
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
else:
    print(f"[API] ⚠ FRONTEND_DIR no encontrada: {FRONTEND_DIR}")
    print("[API]   Crea la carpeta 'frontend/' en la raíz del proyecto.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)
