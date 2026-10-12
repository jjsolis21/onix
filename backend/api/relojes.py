"""
relojes.py — Ónix FM
API de relojes, grilla semanal y programa (modo automático estilo Jazler SoHo).

    GET    /api/v1/relojes                 → relojes con sus filas
    POST   /api/v1/relojes                 → crear (con filas)
    PUT    /api/v1/relojes/{id}            → guardar (reemplaza las filas)
    DELETE /api/v1/relojes/{id}            → borrar (libera sus horas en la grilla)
    POST   /api/v1/relojes/{id}/duplicar   → copia con otro nombre
    GET    /api/v1/relojes/{id}/vista      → cuántos audios cumple cada fila y duración media
    GET    /api/v1/relojes/grilla          → [{dia, hora, reloj_id}]
    PUT    /api/v1/relojes/grilla          → asignar un reloj (o ninguno) a varias celdas
    GET    /api/v1/relojes/eventos         → eventos a hora fija (Jazler "Force Clocks")
    POST   /api/v1/relojes/eventos         → crear: reloj + HH:MM + días + fechas
    PUT    /api/v1/relojes/eventos/{id}
    DELETE /api/v1/relojes/eventos/{id}

    GET    /api/v1/programa                → lo que sonará (cola + plan) con hora estimada
    POST   /api/v1/programa/{uid}/quitar
    POST   /api/v1/programa/{uid}/mover    → {"posicion": n}
    POST   /api/v1/programa/regenerar

Cada cambio en relojes o grilla pide al motor que vuelva a planificar.
"""

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.audio.planner import FILTRO_COLUMNA, TIPOS_RELOJ
from backend.paths import DB_PATH

router = APIRouter(tags=["Relojes"])

# api_server.py asigna aquí el motor (local o remoto) al arrancar
engine = None

CLAVES_FILTRO = set(FILTRO_COLUMNA) | {"energia_min", "energia_max"}


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _replanificar():
    try:
        if engine is not None:
            engine.plan_regenerar()
    except Exception:
        pass   # motor remoto caído: planificará al volver


def _limpiar_filtros(filtros: dict) -> dict:
    out = {}
    for k, v in (filtros or {}).items():
        if k in CLAVES_FILTRO and v not in (None, ""):
            out[k] = int(v) if k.startswith("energia") else str(v).strip().upper()
    return out


class FilaIn(BaseModel):
    tipo_audio: str = "Musica"
    filtros:    dict = Field(default_factory=dict)
    audio_id:   Optional[int] = None
    nota:       str = Field("", max_length=200)

    @field_validator("tipo_audio")
    @classmethod
    def _tipo(cls, v):
        for t in TIPOS_RELOJ:
            if t.lower() == (v or "").strip().lower():
                return t
        raise ValueError(f"tipo_audio debe ser uno de: {', '.join(TIPOS_RELOJ)}")


class RelojIn(BaseModel):
    nombre:       str = Field(..., min_length=1, max_length=80)
    color:        str = Field("#0a9396", pattern=r"^#[0-9a-fA-F]{6}$")
    comentario:   str = Field("", max_length=500)
    compensacion: dict = Field(default_factory=dict)
    filas:        list[FilaIn] = Field(default_factory=list)


class EventoIn(BaseModel):
    nombre:       str = Field(..., min_length=1, max_length=80)
    reloj_id:     int
    hora:         str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")    # HH:MM
    dias:         list[int] = Field(default_factory=lambda: list(range(7)))  # 0=Dom … 6=Sáb
    fecha_inicio: str = Field("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    fecha_fin:    str = Field("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    inmediato:    bool = False
    activo:       bool = True

    @field_validator("dias")
    @classmethod
    def _dias(cls, v):
        dias = sorted({int(d) for d in v})
        if not dias or any(d < 0 or d > 6 for d in dias):
            raise ValueError("Elige al menos un día (0=Dom … 6=Sáb)")
        return dias


class GrillaIn(BaseModel):
    celdas:   list[dict]              # [{"dia": 0-6, "hora": 0-23}]
    reloj_id: Optional[int] = None    # None = quitar el reloj de esas horas


def _reloj_out(conn, r) -> dict:
    filas = [{
        "id": f["id"], "orden": f["orden"], "tipo_audio": f["tipo_audio"],
        "filtros": json.loads(f["filtros"] or "{}"), "audio_id": f["audio_id"], "nota": f["nota"],
        "audio_titulo": f["titulo"],
    } for f in conn.execute(
        "SELECT rf.*, a.titulo FROM reloj_filas rf LEFT JOIN audios a ON a.id = rf.audio_id"
        " WHERE rf.reloj_id = ? ORDER BY rf.orden, rf.id", (r["id"],))]
    horas = conn.execute("SELECT COUNT(*) FROM reloj_grilla WHERE reloj_id = ?", (r["id"],)).fetchone()[0]
    return {"id": r["id"], "nombre": r["nombre"], "color": r["color"], "comentario": r["comentario"],
            "compensacion": json.loads(r["compensacion"] or "{}"), "filas": filas,
            "horas_asignadas": horas}


def _guardar_filas(conn, reloj_id: int, filas: list[FilaIn]):
    conn.execute("DELETE FROM reloj_filas WHERE reloj_id = ?", (reloj_id,))
    for i, f in enumerate(filas):
        if f.audio_id is not None and not conn.execute(
                "SELECT 1 FROM audios WHERE id = ? AND activo = 1", (f.audio_id,)).fetchone():
            raise HTTPException(status_code=422, detail=f"Fila {i + 1}: el audio {f.audio_id} no existe")
        conn.execute(
            "INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, filtros, audio_id, nota)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (reloj_id, i, f.tipo_audio, json.dumps(_limpiar_filtros(f.filtros)), f.audio_id, f.nota))


# ── Relojes ──────────────────────────────────────────────────────────────────

# La grilla va antes que /relojes/{reloj_id}: si no, "grilla" se leería como un id.
@router.get("/api/v1/relojes/grilla", summary="Grilla semanal de relojes")
def grilla(conn: sqlite3.Connection = Depends(get_db)):
    return [dict(g) for g in conn.execute("SELECT dia, hora, reloj_id FROM reloj_grilla")]


@router.put("/api/v1/relojes/grilla", summary="Asignar reloj a horas de la grilla")
def asignar_grilla(body: GrillaIn, conn: sqlite3.Connection = Depends(get_db)):
    if body.reloj_id is not None and not conn.execute(
            "SELECT 1 FROM relojes WHERE id = ?", (body.reloj_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    for c in body.celdas:
        dia, hora = int(c.get("dia", -1)), int(c.get("hora", -1))
        if not (0 <= dia <= 6 and 0 <= hora <= 23):
            raise HTTPException(status_code=422, detail=f"Celda inválida: {c}")
        if body.reloj_id is None:
            conn.execute("DELETE FROM reloj_grilla WHERE dia = ? AND hora = ?", (dia, hora))
        else:
            conn.execute("INSERT OR REPLACE INTO reloj_grilla (dia, hora, reloj_id) VALUES (?, ?, ?)",
                         (dia, hora, body.reloj_id))
    conn.commit()
    _replanificar()
    return grilla(conn)


# ── Eventos a hora fija ──────────────────────────────────────────────────────
# También antes de /relojes/{reloj_id}.

def _evento_out(r) -> dict:
    return {"id": r["id"], "nombre": r["nombre"], "reloj_id": r["reloj_id"], "reloj": r["reloj"],
            "color": r["color"], "hora": r["hora"],
            "dias": [int(d) for d in str(r["dias"] or "").split(",") if d.strip().isdigit()],
            "fecha_inicio": r["fecha_inicio"], "fecha_fin": r["fecha_fin"],
            "inmediato": bool(r["inmediato"]), "activo": bool(r["activo"])}


_SQL_EVENTOS = ("SELECT e.*, r.nombre AS reloj, r.color FROM reloj_eventos e"
                " JOIN relojes r ON r.id = e.reloj_id")


def _validar_evento(conn, body: EventoIn):
    if not conn.execute("SELECT 1 FROM relojes WHERE id = ?", (body.reloj_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    if body.fecha_inicio and body.fecha_fin and body.fecha_fin < body.fecha_inicio:
        raise HTTPException(status_code=422, detail="La fecha final es anterior a la inicial")
    return (body.nombre.strip(), body.reloj_id, body.hora, ",".join(map(str, body.dias)),
            body.fecha_inicio, body.fecha_fin, int(body.inmediato), int(body.activo))


@router.get("/api/v1/relojes/eventos", summary="Eventos a hora fija")
def eventos(conn: sqlite3.Connection = Depends(get_db)):
    return [_evento_out(r) for r in conn.execute(_SQL_EVENTOS + " ORDER BY e.hora, e.nombre")]


@router.post("/api/v1/relojes/eventos", status_code=201, summary="Crear evento a hora fija")
def crear_evento(body: EventoIn, conn: sqlite3.Connection = Depends(get_db)):
    eid = conn.execute(
        "INSERT INTO reloj_eventos (nombre, reloj_id, hora, dias, fecha_inicio, fecha_fin, inmediato, activo)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", _validar_evento(conn, body)).lastrowid
    conn.commit()
    _replanificar()
    return _evento_out(conn.execute(_SQL_EVENTOS + " WHERE e.id = ?", (eid,)).fetchone())


@router.put("/api/v1/relojes/eventos/{evento_id}", summary="Guardar evento a hora fija")
def guardar_evento(evento_id: int, body: EventoIn, conn: sqlite3.Connection = Depends(get_db)):
    valores = _validar_evento(conn, body)
    if not conn.execute(
            "UPDATE reloj_eventos SET nombre = ?, reloj_id = ?, hora = ?, dias = ?, fecha_inicio = ?,"
            " fecha_fin = ?, inmediato = ?, activo = ? WHERE id = ?", (*valores, evento_id)).rowcount:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    conn.commit()
    _replanificar()
    return _evento_out(conn.execute(_SQL_EVENTOS + " WHERE e.id = ?", (evento_id,)).fetchone())


@router.delete("/api/v1/relojes/eventos/{evento_id}", status_code=204, summary="Borrar evento")
def borrar_evento(evento_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("DELETE FROM reloj_eventos WHERE id = ?", (evento_id,)).rowcount:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    conn.commit()
    _replanificar()


@router.get("/api/v1/relojes", summary="Relojes con sus filas")
def listar(conn: sqlite3.Connection = Depends(get_db)):
    return [_reloj_out(conn, r) for r in conn.execute("SELECT * FROM relojes ORDER BY nombre")]


@router.post("/api/v1/relojes", status_code=201, summary="Crear reloj")
def crear(body: RelojIn, conn: sqlite3.Connection = Depends(get_db)):
    try:
        rid = conn.execute(
            "INSERT INTO relojes (nombre, color, comentario, compensacion) VALUES (?, ?, ?, ?)",
            (body.nombre.strip(), body.color, body.comentario,
             json.dumps(_limpiar_filtros(body.compensacion)))).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe un reloj llamado '{body.nombre}'")
    _guardar_filas(conn, rid, body.filas)
    conn.commit()
    _replanificar()
    return _reloj_out(conn, conn.execute("SELECT * FROM relojes WHERE id = ?", (rid,)).fetchone())


@router.put("/api/v1/relojes/{reloj_id}", summary="Guardar reloj")
def guardar(reloj_id: int, body: RelojIn, conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("SELECT 1 FROM relojes WHERE id = ?", (reloj_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    try:
        conn.execute(
            "UPDATE relojes SET nombre = ?, color = ?, comentario = ?, compensacion = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (body.nombre.strip(), body.color, body.comentario,
             json.dumps(_limpiar_filtros(body.compensacion)), reloj_id))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe un reloj llamado '{body.nombre}'")
    _guardar_filas(conn, reloj_id, body.filas)
    conn.commit()
    _replanificar()
    return _reloj_out(conn, conn.execute("SELECT * FROM relojes WHERE id = ?", (reloj_id,)).fetchone())


@router.delete("/api/v1/relojes/{reloj_id}", status_code=204, summary="Borrar reloj")
def borrar(reloj_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("DELETE FROM relojes WHERE id = ?", (reloj_id,)).rowcount:
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    conn.commit()
    _replanificar()


@router.post("/api/v1/relojes/{reloj_id}/duplicar", status_code=201, summary="Duplicar reloj")
def duplicar(reloj_id: int, conn: sqlite3.Connection = Depends(get_db)):
    r = conn.execute("SELECT * FROM relojes WHERE id = ?", (reloj_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    nombre, n = f"{r['nombre']} (copia)", 2
    while conn.execute("SELECT 1 FROM relojes WHERE nombre = ?", (nombre,)).fetchone():
        nombre, n = f"{r['nombre']} (copia {n})", n + 1
    nuevo = conn.execute(
        "INSERT INTO relojes (nombre, color, comentario, compensacion) VALUES (?, ?, ?, ?)",
        (nombre, r["color"], r["comentario"], r["compensacion"])).lastrowid
    conn.execute(
        "INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, filtros, audio_id, nota)"
        " SELECT ?, orden, tipo_audio, filtros, audio_id, nota FROM reloj_filas WHERE reloj_id = ?",
        (nuevo, reloj_id))
    conn.commit()
    return _reloj_out(conn, conn.execute("SELECT * FROM relojes WHERE id = ?", (nuevo,)).fetchone())


@router.get("/api/v1/relojes/{reloj_id}/vista", summary="Vista previa de un reloj")
def vista(reloj_id: int, conn: sqlite3.Connection = Depends(get_db)):
    """
    Por fila: cuántos audios cumplen sus filtros y su duración media. Una fila
    con 0 audios se salta al planificar (avisar al programador).
    """
    from backend.audio.planner import Planner
    r = conn.execute("SELECT * FROM relojes WHERE id = ?", (reloj_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    p = Planner(DB_PATH)
    out, total = [], 0.0
    for f in _reloj_out(conn, r)["filas"]:
        if f["audio_id"]:
            row = conn.execute("SELECT COUNT(*), AVG(duracion) FROM audios WHERE id = ? AND activo = 1",
                               (f["audio_id"],)).fetchone()
        else:
            clauses, params = p._where_filtros(f["filtros"])
            where = " AND ".join(["a.activo = 1", "a.tipo_audio = ?"] + clauses)
            row = conn.execute(f"SELECT COUNT(*), AVG(a.duracion) FROM audios a WHERE {where}",
                               [f["tipo_audio"]] + params).fetchone()
        n, media = row[0], round(row[1] or 0)
        total += media
        out.append({**f, "candidatos": n, "duracion_media": media})
    return {"id": reloj_id, "filas": out, "duracion_ciclo": round(total)}


# ── Programa ─────────────────────────────────────────────────────────────────

def _motor():
    if engine is None:
        raise HTTPException(status_code=503, detail="Motor no disponible")
    return engine


@router.get("/api/v1/programa", tags=["Programa"], summary="Lo que sonará")
def programa():
    try:
        return _motor().programa()
    except (ConnectionError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))


class MoverIn(BaseModel):
    posicion: int = Field(..., ge=0)


@router.post("/api/v1/programa/{uid}/quitar", tags=["Programa"])
def programa_quitar(uid: int):
    _motor().plan_quitar(uid)
    return {"ok": True}


@router.post("/api/v1/programa/{uid}/mover", tags=["Programa"])
def programa_mover(uid: int, body: MoverIn):
    _motor().plan_mover(uid, body.posicion)
    return {"ok": True}


@router.post("/api/v1/programa/regenerar", tags=["Programa"])
def programa_regenerar():
    _motor().plan_regenerar()
    return {"ok": True}
