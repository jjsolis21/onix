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
    POST   /api/v1/playlists/importar        → archivo M3U/M3U8/PLS/TXT: busca cada línea en la biblioteca

MEMOS: cada audio de la playlist puede llevar una nota para el locutor
(items: [{audio_id, nota}]). El Studio la muestra cuando ese audio está al aire.

PROGRAMADA: con fecha y hora ('YYYY-MM-DDTHH:MM') entra sola al aire como un
evento a hora fija (una vez). inmediato = corta lo que suena y entra en punto.

La cola del operador suena antes que el programa automático; las tandas
siguen saliendo a su hora (tienen prioridad sobre la cola).
"""

import re
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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


class ItemIn(BaseModel):
    audio_id: int
    nota:     str = Field("", max_length=500)


class PlaylistIn(BaseModel):
    nombre:     str = Field(..., min_length=1, max_length=80)
    items:      list[Union[int, ItemIn]] = Field(default_factory=list, max_length=2000)   # id o {audio_id, nota}
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


def _guardar_items(conn, pid: int, items: list):
    pares = [(it, "") if isinstance(it, int) else (it.audio_id, (it.nota or "").strip()) for it in items]
    ids = [a for a, _ in pares]
    existentes = {r[0] for r in conn.execute(
        f"SELECT id FROM audios WHERE id IN ({','.join('?' * len(set(ids)))})", list(set(ids)))} if ids else set()
    faltan = [i for i in ids if i not in existentes]
    if faltan:
        raise HTTPException(status_code=422, detail=f"Audios inexistentes: {faltan[:5]}")
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (pid,))
    conn.executemany("INSERT INTO playlist_items (playlist_id, posicion, audio_id, nota) VALUES (?, ?, ?, ?)",
                     [(pid, i, a, n) for i, (a, n) in enumerate(pares)])


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
        "SELECT i.posicion, i.nota, a.id AS audio_id, a.titulo, a.artista, a.tipo_audio, a.duracion,"
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
    filas = conn.execute(
        "SELECT i.audio_id, i.nota FROM playlist_items i JOIN audios a ON a.id = i.audio_id AND a.activo = 1"
        " WHERE i.playlist_id = ? ORDER BY i.posicion", (playlist_id,)).fetchall()
    if not filas:
        raise HTTPException(status_code=404, detail="La playlist está vacía o no existe")
    ids = [f[0] for f in filas]
    try:
        if body.reemplazar:
            engine.clear_queue()
        encoladas = sum(1 for aid, nota in filas if engine.queue_track(aid, nota or ""))
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"ok": True, "encoladas": encoladas, "total": len(ids)}


# ── Importar M3U / M3U8 / PLS / TXT ──────────────────────────────────────────

def _norm(txt: str) -> str:
    """Para comparar: sin tildes, minúsculas, solo letras y números."""
    t = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _stem(ruta: str) -> str:
    """Nombre del archivo sin carpeta ni extensión (rutas de Windows o Linux)."""
    nombre = PureWindowsPath(ruta.replace("/", "\\")).name
    return nombre.rsplit(".", 1)[0] if "." in nombre else nombre


def leer_lista(texto: str) -> list[dict]:
    """[{ruta, titulo}] en orden, desde M3U/M3U8 (#EXTINF), PLS (FileN/TitleN) o una ruta por línea."""
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    if lineas and lineas[0].lower() == "[playlist]":
        rutas, titulos = {}, {}
        for l in lineas[1:]:
            k, _, v = l.partition("=")
            m = re.match(r"(file|title)(\d+)$", k.strip().lower())
            if m:
                (rutas if m.group(1) == "file" else titulos)[int(m.group(2))] = v.strip()
        return [{"ruta": rutas[n], "titulo": titulos.get(n, "")} for n in sorted(rutas)]
    out, titulo = [], ""
    for l in lineas:
        if l.upper().startswith("#EXTINF"):
            titulo = l.split(",", 1)[1].strip() if "," in l else ""
        elif l.startswith("#"):
            continue
        else:
            out.append({"ruta": l, "titulo": titulo})
            titulo = ""
    return out


def emparejar(conn, entradas: list[dict]) -> tuple[list[int], list[str]]:
    """Busca cada entrada en la biblioteca: por nombre de archivo y, si no, por 'Artista - Título'."""
    por_archivo, por_nombre, por_titulo = {}, {}, {}
    for r in conn.execute("SELECT id, titulo, artista, archivo_path FROM audios WHERE activo = 1"):
        por_archivo.setdefault(_norm(_stem(r["archivo_path"] or "")), r["id"])
        por_nombre.setdefault(_norm(f"{r['artista']} {r['titulo']}"), r["id"])
        por_titulo.setdefault(_norm(r["titulo"]), []).append(r["id"])
    ids, faltan = [], []
    for e in entradas:
        stem = _stem(e["ruta"])
        candidatos = [_norm(stem), _norm(e["titulo"])]
        aid = next((por_archivo[c] for c in candidatos if c and c in por_archivo), None) \
            or next((por_nombre[c] for c in candidatos if c and c in por_nombre), None)
        if aid is None:
            # "Título" solo, si en la biblioteca hay una única canción con ese título
            for c in candidatos:
                unicos = por_titulo.get(c) or []
                if len(unicos) == 1:
                    aid = unicos[0]
                    break
        if aid is None:
            faltan.append(e["titulo"] or stem)
        else:
            ids.append(aid)
    return ids, faltan


@router.post("/api/v1/playlists/importar", status_code=201, summary="Importar una playlist M3U/PLS/TXT")
async def importar(request: Request, archivo: UploadFile = File(...), nombre: str = Form(""),
                   conn: sqlite3.Connection = Depends(get_db)):
    crudo = await archivo.read()
    if len(crudo) > 5_000_000:
        raise HTTPException(status_code=413, detail="El archivo es demasiado grande (máx. 5 MB)")
    for cod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texto = crudo.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    entradas = leer_lista(texto)
    if not entradas:
        raise HTTPException(status_code=422, detail="El archivo no tiene canciones (¿es una lista M3U, PLS o TXT?)")
    ids, faltan = emparejar(conn, entradas)
    if not ids:
        raise HTTPException(status_code=422, detail={"msg": "Ninguna canción de la lista está en la biblioteca",
                                                     "no_encontradas": faltan[:200]})
    nombre = (nombre or "").strip() or (archivo.filename or "Importada").rsplit(".", 1)[0]
    pid = _insertar(conn, nombre[:80], f"Importada de {archivo.filename}", None, None, "", False, _autor(request))
    _guardar_items(conn, pid, ids)
    conn.commit()
    return {**detalle(pid, conn), "encontradas": len(ids), "no_encontradas": faltan[:200],
            "total_lista": len(entradas)}
