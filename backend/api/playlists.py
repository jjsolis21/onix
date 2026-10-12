"""
playlists.py — Ónix FM
Playlists al estilo Jazler: se generan desde el patrón de un reloj (1 a 12
horas de música con las mismas reglas del aire) y el Studio las lanza a la
cola del operador.

    GET    /api/v1/playlists                 → lista (cantidad y duración)
    POST   /api/v1/playlists                 → manual: {nombre, items: [audio_id…], programada, inmediato}
    POST   /api/v1/playlists/generar         → Auto Playlist: {nombre, reloj_id (o null = la grilla),
                                                horas, inicio, programar, inmediato}
    GET    /api/v1/playlists/{id}            → con sus audios (y minuto de cada uno)
    PUT    /api/v1/playlists/{id}            → guardar nombre, audios en orden y programación
    DELETE /api/v1/playlists/{id}
    POST   /api/v1/playlists/{id}/lanzar     → {reemplazar: bool} a la cola del Studio

PROGRAMADA: con fecha y hora ('YYYY-MM-DDTHH:MM') entra sola al aire como un
evento a hora fija (una vez). inmediato = corta lo que suena y entra en punto.

La cola del operador suena antes que el programa automático; las tandas
siguen saliendo a su hora (tienen prioridad sobre la cola).
"""

import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.paths import DB_PATH

router = APIRouter(tags=["Playlists"])

# api_server.py asigna aquí el motor (local o remoto) al arrancar
engine = None


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_FECHA_HORA = r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})?$"


class GenerarIn(BaseModel):
    nombre:    str = Field(..., min_length=1, max_length=80)
    reloj_id:  Optional[int] = None            # None = el reloj de la grilla en cada hora
    horas:     float = Field(..., ge=0.25, le=12)
    inicio:    str = Field("", pattern=_FECHA_HORA)   # fecha y hora para la que se arma
    programar: bool = False                    # que entre sola al aire a esa hora
    inmediato: bool = False


class PlaylistIn(BaseModel):
    nombre:     str = Field(..., min_length=1, max_length=80)
    items:      list[int] = Field(default_factory=list, max_length=2000)
    programada: str = Field("", pattern=_FECHA_HORA)
    inmediato:  bool = False


def _autor(request: Request) -> str:
    u = getattr(request.state, "usuario", None) or {}
    return u.get("nombre") or u.get("usuario") or ("Estudio" if u.get("local") else "")


class LanzarIn(BaseModel):
    reemplazar: bool = False


_SQL_LISTA = """
    SELECT p.id, p.nombre, p.descripcion, p.reloj_id, p.horas, p.created_at,
           p.programada, p.inmediato, p.autor,
           r.nombre AS reloj, r.color,
           COUNT(i.id) AS audios, COALESCE(SUM(a.duracion), 0) AS duracion
    FROM playlists p
    LEFT JOIN relojes r ON r.id = p.reloj_id
    LEFT JOIN playlist_items i ON i.playlist_id = p.id
    LEFT JOIN audios a ON a.id = i.audio_id
"""


@router.get("/api/v1/playlists", summary="Playlists")
def listar(q: str = "", conn: sqlite3.Connection = Depends(get_db)):
    where, params = "WHERE p.activo = 1", []
    if q.strip():
        where += " AND UPPER(p.nombre) LIKE ?"
        params.append(f"%{q.strip().upper()}%")
    return [dict(r) for r in conn.execute(
        f"{_SQL_LISTA} {where} GROUP BY p.id ORDER BY p.created_at DESC, p.id DESC", params)]


def _replanificar():
    try:
        if engine is not None:
            engine.plan_regenerar()
    except Exception:
        pass


def _insertar(conn, nombre: str, descripcion: str, reloj_id, horas, programada: str,
              inmediato: bool, autor: str) -> int:
    try:
        return conn.execute(
            "INSERT INTO playlists (nombre, descripcion, reloj_id, horas, programada, inmediato, autor)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nombre.strip(), descripcion, reloj_id, horas, programada, int(inmediato), autor)).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe una playlist llamada '{nombre}'")


def _guardar_items(conn, pid: int, ids: list[int]):
    existentes = {r[0] for r in conn.execute(
        f"SELECT id FROM audios WHERE id IN ({','.join('?' * len(set(ids)))})", list(set(ids)))} if ids else set()
    faltan = [i for i in ids if i not in existentes]
    if faltan:
        raise HTTPException(status_code=422, detail=f"Audios inexistentes: {faltan[:5]}")
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
    conn.executemany("INSERT INTO playlist_items (playlist_id, posicion, audio_id) VALUES (?, ?, ?)",
                     [(pid, i, a) for i, a in enumerate(ids)])


@router.post("/api/v1/playlists", status_code=201, summary="Crear playlist a mano")
def crear(body: PlaylistIn, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    pid = _insertar(conn, body.nombre, "Armada a mano", None, None, body.programada, body.inmediato, _autor(request))
    _guardar_items(conn, pid, body.items)
    conn.commit()
    if body.programada:
        _replanificar()
    return detalle(pid, conn)


@router.post("/api/v1/playlists/generar", status_code=201, summary="Auto Playlist desde un reloj o la grilla")
def generar(body: GenerarIn, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    from backend.audio.planner import Planner
    if body.reloj_id is not None and not conn.execute(
            "SELECT 1 FROM relojes WHERE id = ?", (body.reloj_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    if body.programar and not body.inicio:
        raise HTTPException(status_code=422, detail="Para programarla elige fecha y hora")
    inicio = datetime.fromisoformat(body.inicio) if body.inicio else None
    items = Planner(DB_PATH).generar_lista(body.reloj_id, body.horas, inicio)
    if not items:
        raise HTTPException(status_code=422, detail="El reloj no consigue audios: revisa sus filas")
    desc = f"{body.horas:g} h · " + ("reloj fijo" if body.reloj_id else "según la grilla")
    pid = _insertar(conn, body.nombre, desc, body.reloj_id, body.horas,
                    body.inicio if body.programar else "", body.inmediato, _autor(request))
    _guardar_items(conn, pid, [it["audio_id"] for it in items])
    conn.commit()
    if body.programar:
        _replanificar()
    return detalle(pid, conn)


@router.put("/api/v1/playlists/{playlist_id}", summary="Guardar playlist")
def guardar(playlist_id: int, body: PlaylistIn, conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Playlist no encontrada")
    try:
        conn.execute("UPDATE playlists SET nombre = ?, programada = ?, inmediato = ? WHERE id = ?",
                     (body.nombre.strip(), body.programada, int(body.inmediato), playlist_id))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe una playlist llamada '{body.nombre}'")
    _guardar_items(conn, playlist_id, body.items)
    conn.commit()
    _replanificar()
    return detalle(playlist_id, conn)


@router.get("/api/v1/playlists/{playlist_id}", summary="Playlist con sus audios")
def detalle(playlist_id: int, conn: sqlite3.Connection = Depends(get_db)):
    p = conn.execute(f"{_SQL_LISTA} WHERE p.id = ? GROUP BY p.id", (playlist_id,)).fetchone()
    if not p:
        raise HTTPException(status_code=404, detail="Playlist no encontrada")
    items = [dict(r) for r in conn.execute(
        "SELECT i.posicion, a.id AS audio_id, a.titulo, a.artista, a.tipo_audio, a.duracion,"
        " a.cue_intro, a.cue_outro, a.ultima_reproduccion,"
        " COALESCE(a.categoria, '') AS rotacion FROM playlist_items i JOIN audios a ON a.id = i.audio_id"
        " WHERE i.playlist_id = ? ORDER BY i.posicion", (playlist_id,))]
    return {**dict(p), "items": items}


@router.delete("/api/v1/playlists/{playlist_id}", status_code=204, summary="Borrar playlist")
def borrar(playlist_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,)).rowcount:
        raise HTTPException(status_code=404, detail="Playlist no encontrada")
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    conn.commit()
    _replanificar()


@router.post("/api/v1/playlists/{playlist_id}/lanzar", summary="Lanzar la playlist a la cola del Studio")
def lanzar(playlist_id: int, body: LanzarIn = LanzarIn(), conn: sqlite3.Connection = Depends(get_db)):
    if engine is None:
        raise HTTPException(status_code=503, detail="Motor no disponible")
    ids = [r[0] for r in conn.execute(
        "SELECT i.audio_id FROM playlist_items i JOIN audios a ON a.id = i.audio_id AND a.activo = 1"
        " WHERE i.playlist_id = ? ORDER BY i.posicion", (playlist_id,))]
    if not ids:
        raise HTTPException(status_code=404, detail="La playlist está vacía o no existe")
    try:
        if body.reemplazar:
            engine.clear_queue()
        encoladas = sum(1 for aid in ids if engine.queue_track(aid))
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"ok": True, "encoladas": encoladas, "total": len(ids)}
