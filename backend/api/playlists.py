"""
playlists.py — Ónix FM
Playlists al estilo Jazler: se generan desde el patrón de un reloj (1 a 12
horas de música con las mismas reglas del aire) y el Studio las lanza a la
cola del operador.

    GET    /api/v1/playlists                 → lista (cantidad y duración)
    POST   /api/v1/playlists/generar         → {nombre, reloj_id, horas}
    GET    /api/v1/playlists/{id}            → con sus audios
    DELETE /api/v1/playlists/{id}
    POST   /api/v1/playlists/{id}/lanzar     → {reemplazar: bool} a la cola del Studio

La cola del operador suena antes que el programa automático; las tandas
siguen saliendo a su hora (tienen prioridad sobre la cola).
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
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


class GenerarIn(BaseModel):
    nombre:   str = Field(..., min_length=1, max_length=80)
    reloj_id: int
    horas:    float = Field(..., ge=0.25, le=12)


class LanzarIn(BaseModel):
    reemplazar: bool = False


_SQL_LISTA = """
    SELECT p.id, p.nombre, p.descripcion, p.reloj_id, p.horas, p.created_at,
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


@router.post("/api/v1/playlists/generar", status_code=201, summary="Generar playlist desde un reloj")
def generar(body: GenerarIn, conn: sqlite3.Connection = Depends(get_db)):
    from backend.audio.planner import Planner
    if not conn.execute("SELECT 1 FROM relojes WHERE id = ?", (body.reloj_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    items = Planner(DB_PATH).generar_lista(body.reloj_id, body.horas)
    if not items:
        raise HTTPException(status_code=422, detail="El reloj no consigue audios: revisa sus filas")
    try:
        pid = conn.execute(
            "INSERT INTO playlists (nombre, descripcion, reloj_id, horas) VALUES (?, ?, ?, ?)",
            (body.nombre.strip(), f"{body.horas:g} h desde el reloj", body.reloj_id, body.horas)).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe una playlist llamada '{body.nombre}'")
    conn.executemany("INSERT INTO playlist_items (playlist_id, posicion, audio_id) VALUES (?, ?, ?)",
                     [(pid, i, it["audio_id"]) for i, it in enumerate(items)])
    conn.commit()
    return detalle(pid, conn)


@router.get("/api/v1/playlists/{playlist_id}", summary="Playlist con sus audios")
def detalle(playlist_id: int, conn: sqlite3.Connection = Depends(get_db)):
    p = conn.execute(f"{_SQL_LISTA} WHERE p.id = ? GROUP BY p.id", (playlist_id,)).fetchone()
    if not p:
        raise HTTPException(status_code=404, detail="Playlist no encontrada")
    items = [dict(r) for r in conn.execute(
        "SELECT i.posicion, a.id AS audio_id, a.titulo, a.artista, a.tipo_audio, a.duracion,"
        " COALESCE(a.categoria, '') AS rotacion FROM playlist_items i JOIN audios a ON a.id = i.audio_id"
        " WHERE i.playlist_id = ? ORDER BY i.posicion", (playlist_id,))]
    return {**dict(p), "items": items}


@router.delete("/api/v1/playlists/{playlist_id}", status_code=204, summary="Borrar playlist")
def borrar(playlist_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,)).rowcount:
        raise HTTPException(status_code=404, detail="Playlist no encontrada")
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    conn.commit()


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
