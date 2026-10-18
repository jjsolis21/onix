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
from backend.database import DualIntegrityError, MariaDBConnection
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.audio.planner import FILTRO_COLUMNA, TIP_MODOS, TIPOS_RELOJ
from backend.paths import DB_PATH

router = APIRouter(tags=["Relojes"])

# api_server.py asigna aquí el motor (local o remoto) al arrancar
engine = None

CLAVES_FILTRO = set(FILTRO_COLUMNA) | {"energia_min", "energia_max", "anio_min", "anio_max", "prioridad"}
CLAVES_NUMERO = {"energia_min", "energia_max", "anio_min", "anio_max", "prioridad"}


from backend.database import get_db


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
            if k in CLAVES_NUMERO:
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    pass                      # un número mal escrito no filtra (ni rompe el reloj)
            elif k == "epoca":
                s = str(v).strip()
                if s.upper() not in ("TODAS", "TODOS", "ALL", ""):
                    out[k] = s
            else:
                out[k] = str(v).strip().upper()
    return out


class FilaIn(BaseModel):
    tipo_audio: str = "Musica"
    filtros:    dict = Field(default_factory=dict)
    audio_id:   Optional[int] = None
    nota:       str = Field("", max_length=200)
    tip_modo:   str = ""          # solo filas de Tips: '' (suena sola) | intro_inicio | intro_final | puente

    @field_validator("tip_modo")
    @classmethod
    def _tip_modo(cls, v):
        v = (v or "").strip().lower()
        if v and v not in TIP_MODOS:
            raise ValueError(f"tip_modo debe ser uno de: {', '.join(TIP_MODOS)} (o vacío)")
        return v

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
    respaldo:     dict = Field(default_factory=dict)
    filas:        list[FilaIn] = Field(default_factory=list)


class EventoIn(BaseModel):
    nombre:       str = Field(..., min_length=1, max_length=80)
    reloj_id:     int
    hora:         str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")    # HH:MM
    hasta:        str = Field("", pattern=r"^(([01]\d|2[0-3]):[0-5]\d)?$")  # '' = una vuelta
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
        "tip_modo": (f["tip_modo"] if "tip_modo" in f.keys() else "") or "",
        "audio_titulo": f["titulo"],
    } for f in conn.execute(
        "SELECT rf.*, a.titulo FROM reloj_filas rf LEFT JOIN audios a ON a.id = rf.audio_id"
        " WHERE rf.reloj_id = ? ORDER BY rf.orden, rf.id", (r["id"],))]
    horas = conn.execute("SELECT COUNT(*) FROM reloj_grilla WHERE reloj_id = ?", (r["id"],)).fetchone()[0]
    return {"id": r["id"], "nombre": r["nombre"], "color": r["color"], "comentario": r["comentario"],
            "compensacion": json.loads(r["compensacion"] or "{}"),
            "respaldo": json.loads(r["respaldo"] or "{}") if "respaldo" in r.keys() else {}, "filas": filas,
            "horas_asignadas": horas}


def _guardar_filas(conn, reloj_id: int, filas: list[FilaIn]):
    conn.execute("DELETE FROM reloj_filas WHERE reloj_id = ?", (reloj_id,))
    for i, f in enumerate(filas):
        if f.audio_id is not None and not conn.execute(
                "SELECT 1 FROM audios WHERE id = ? AND activo = 1", (f.audio_id,)).fetchone():
            raise HTTPException(status_code=422, detail=f"Fila {i + 1}: el audio {f.audio_id} no existe")
        conn.execute(
            "INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, filtros, audio_id, nota, tip_modo)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (reloj_id, i, f.tipo_audio, json.dumps(_limpiar_filtros(f.filtros)), f.audio_id, f.nota,
             f.tip_modo if f.tipo_audio == "Tips" else ""))


# ── Relojes ──────────────────────────────────────────────────────────────────

# La grilla va antes que /relojes/{reloj_id}: si no, "grilla" se leería como un id.
@router.get("/api/v1/relojes/grilla", summary="Grilla semanal de relojes")
def grilla(conn: MariaDBConnection = Depends(get_db)):
    return [dict(g) for g in conn.execute("SELECT dia, hora, reloj_id FROM reloj_grilla")]


@router.put("/api/v1/relojes/grilla", summary="Asignar reloj a horas de la grilla")
def asignar_grilla(body: GrillaIn, conn: MariaDBConnection = Depends(get_db)):
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
            conn.execute("REPLACE INTO reloj_grilla (dia, hora, reloj_id) VALUES (?, ?, ?)",
                         (dia, hora, body.reloj_id))
    conn.commit()
    _replanificar()
    return grilla(conn)


# ── Día específico (también antes de /relojes/{reloj_id}) ────────────────────

class DiaIn(BaseModel):
    fecha:    str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    horas:    list[int]
    accion:   str = Field("reloj", pattern=r"^(reloj|musica|semana)$")   # semana = quitar la excepción
    reloj_id: Optional[int] = None


@router.get("/api/v1/relojes/dias", summary="Días específicos: excepciones a la grilla semanal")
def dias_especificos(fecha: Optional[str] = None, conn: MariaDBConnection = Depends(get_db)):
    """Sin fecha: las fechas futuras (y de ayer) con excepciones. Con fecha: sus horas."""
    if fecha:
        return [dict(r) for r in conn.execute(
            "SELECT fecha, hora, reloj_id FROM reloj_dias WHERE fecha = ? ORDER BY hora", (fecha,))]
    return [{"fecha": r[0], "horas": r[1]} for r in conn.execute(
        "SELECT fecha, COUNT(*) FROM reloj_dias WHERE fecha >= DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
        " GROUP BY fecha ORDER BY fecha")]


@router.put("/api/v1/relojes/dias", summary="Asignar horas de un día específico")
def asignar_dia(body: DiaIn, conn: MariaDBConnection = Depends(get_db)):
    if body.accion == "reloj" and (body.reloj_id is None or not conn.execute(
            "SELECT 1 FROM relojes WHERE id = ?", (body.reloj_id,)).fetchone()):
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    for h in body.horas:
        if not 0 <= int(h) <= 23:
            raise HTTPException(status_code=422, detail=f"Hora inválida: {h}")
        if body.accion == "semana":
            conn.execute("DELETE FROM reloj_dias WHERE fecha = ? AND hora = ?", (body.fecha, h))
        else:
            conn.execute("REPLACE INTO reloj_dias (fecha, hora, reloj_id) VALUES (?, ?, ?)",
                         (body.fecha, h, body.reloj_id if body.accion == "reloj" else None))
    conn.commit()
    _replanificar()
    return dias_especificos(body.fecha, conn)


# ── Vista previa de un reloj sin guardar ─────────────────────────────────────

class VistaIn(BaseModel):
    filas:    list[FilaIn] = Field(default_factory=list)
    respaldo: dict = Field(default_factory=dict)


def _tanda_media_seg(conn) -> float:
    """Cuánto ocupan las tandas en una hora (promedio de los últimos 7 días con cuñas)."""
    row = conn.execute(
        "SELECT SUM(a.duracion), COUNT(DISTINCT SUBSTR(h.iniciado_en, 1, 13)) FROM historial_emision h"
        " JOIN audios a ON a.id = h.audio_id WHERE h.pauta_id IS NOT NULL"
        " AND h.iniciado_en >= DATE_SUB(NOW(), INTERVAL 7 DAY)").fetchone()
    return round((row[0] or 0) / row[1]) if row and row[1] else 0.0


def _contar(conn, f: dict, pendientes: bool = False) -> tuple[int, int]:
    """
    Audios que el automático PUEDE elegir para una fila (o un audio fijo) y su duración media.
    La música sin aprobar (Bandeja de entrada) no cuenta: el planificador no la toca. Con pendientes=True se
    cuentan justo esas, para avisar de que la fila está vacía por falta de aprobación y no por falta de canciones.
    """
    from backend.audio.planner import Planner
    if f.get("audio_id"):
        if pendientes:
            return 0, 0
        row = conn.execute("SELECT COUNT(*), AVG(duracion) FROM audios WHERE id = ? AND activo = 1",
                           (f["audio_id"],)).fetchone()
    else:
        tipo = f.get("tipo_audio", "Musica")
        if pendientes and tipo != "Musica":
            return 0, 0
        clauses, params = Planner(DB_PATH)._where_filtros(_limpiar_filtros(f.get("filtros") or {}))
        base = ["a.activo = 1", "a.tipo_audio = ?"]
        if tipo == "Musica":
            base.append("COALESCE(a.aprobada, 1) = " + ("0" if pendientes else "1"))
        where = " AND ".join(base + clauses)
        row = conn.execute(f"SELECT COUNT(*), AVG(a.duracion) FROM audios a WHERE {where}", [tipo] + params).fetchone()
    return row[0], round(row[1] or 0)


def _vista_filas(conn, filas: list[dict]) -> dict:
    """
    Por fila: audios que cumplen el filtro, duración media y minuto en que
    arranca en la primera vuelta. Además la hora simulada: las filas se repiten
    en orden hasta llenar lo que dejan las tandas (como "Completar dando vuelta
    al reloj" de Jazler, que en ONIX siempre está activo).
    """
    try:
        crossfade = float(conn.execute("SELECT valor FROM config WHERE clave = 'crossfade_seg'").fetchone()[0])
    except (TypeError, ValueError):
        crossfade = 4.0
    out, t = [], 0.0
    for f in filas:
        n, media = _contar(conn, f)
        encima = f.get("tipo_audio") == "Tips" and bool(f.get("tip_modo"))   # tip sobre la música: no ocupa tiempo
        efectiva = 0 if encima else (max(1.0, media - crossfade) if media else 0)
        out.append({**f, "candidatos": n, "duracion_media": media, "inicio": round(t),
                    "pendientes": _contar(conn, f, pendientes=True)[0]})   # esperan en la Bandeja de entrada
        t += efectiva
    ciclo = round(t)
    tandas = _tanda_media_seg(conn)
    disponible = max(0, 3600 - tandas)
    # Hora simulada: qué fila cae en cada momento (las filas sin audios se saltan)
    linea, t, vueltas = [], 0.0, 0
    validas = [(i, f) for i, f in enumerate(out) if f["candidatos"] and f["duracion_media"]
               and not (f.get("tipo_audio") == "Tips" and f.get("tip_modo"))]
    while validas and t < disponible and len(linea) < 200:
        for i, f in validas:
            if t >= disponible:
                break
            d = max(1.0, f["duracion_media"] - crossfade)
            linea.append({"fila": i, "inicio": round(t), "dur": round(min(d, disponible - t))})
            t += d
        vueltas += 1
    ultima = linea[-1]["fila"] if linea else None
    return {"filas": out, "duracion_ciclo": ciclo, "tandas_seg": tandas, "disponible_seg": disponible,
            "vueltas": round(disponible / ciclo, 1) if ciclo else 0, "linea": linea,
            "ultima_fila": ultima, "saltadas": [i for i, f in enumerate(out) if not f["candidatos"]]}


@router.post("/api/v1/relojes/vista-previa", summary="Cómo queda la hora con estas filas (sin guardar)")
def vista_previa(body: VistaIn, conn: MariaDBConnection = Depends(get_db)):
    v = _vista_filas(conn, [f.model_dump() for f in body.filas])
    v["respaldo_candidatos"] = _contar(conn, {"tipo_audio": "Musica", "filtros": body.respaldo})[0] \
        if body.respaldo else None
    return v


# ── Eventos a hora fija ──────────────────────────────────────────────────────
# También antes de /relojes/{reloj_id}.

def _evento_out(r) -> dict:
    return {"id": r["id"], "nombre": r["nombre"], "reloj_id": r["reloj_id"], "reloj": r["reloj"],
            "color": r["color"], "hora": r["hora"], "hasta": (r["hasta"] if "hasta" in r.keys() else "") or "",
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
    if body.hasta and body.hasta == body.hora:
        raise HTTPException(status_code=422, detail="«Dura hasta» no puede ser igual a la hora de inicio")
    return (body.nombre.strip(), body.reloj_id, body.hora, ",".join(map(str, body.dias)),
            body.fecha_inicio, body.fecha_fin, int(body.inmediato), int(body.activo), body.hasta)


@router.get("/api/v1/relojes/eventos", summary="Eventos a hora fija")
def eventos(conn: MariaDBConnection = Depends(get_db)):
    return [_evento_out(r) for r in conn.execute(_SQL_EVENTOS + " ORDER BY e.hora, e.nombre")]


@router.post("/api/v1/relojes/eventos", status_code=201, summary="Crear evento a hora fija")
def crear_evento(body: EventoIn, conn: MariaDBConnection = Depends(get_db)):
    eid = conn.execute(
        "INSERT INTO reloj_eventos (nombre, reloj_id, hora, dias, fecha_inicio, fecha_fin, inmediato, activo, hasta)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", _validar_evento(conn, body)).lastrowid
    conn.commit()
    _replanificar()
    return _evento_out(conn.execute(_SQL_EVENTOS + " WHERE e.id = ?", (eid,)).fetchone())


@router.put("/api/v1/relojes/eventos/{evento_id}", summary="Guardar evento a hora fija")
def guardar_evento(evento_id: int, body: EventoIn, conn: MariaDBConnection = Depends(get_db)):
    valores = _validar_evento(conn, body)
    if not conn.execute(
            "UPDATE reloj_eventos SET nombre = ?, reloj_id = ?, hora = ?, dias = ?, fecha_inicio = ?,"
            " fecha_fin = ?, inmediato = ?, activo = ?, hasta = ? WHERE id = ?", (*valores, evento_id)).rowcount:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    conn.commit()
    _replanificar()
    return _evento_out(conn.execute(_SQL_EVENTOS + " WHERE e.id = ?", (evento_id,)).fetchone())


@router.delete("/api/v1/relojes/eventos/{evento_id}", status_code=204, summary="Borrar evento")
def borrar_evento(evento_id: int, conn: MariaDBConnection = Depends(get_db)):
    if not conn.execute("DELETE FROM reloj_eventos WHERE id = ?", (evento_id,)).rowcount:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    conn.commit()
    _replanificar()


@router.get("/api/v1/relojes", summary="Relojes con sus filas")
def listar(conn: MariaDBConnection = Depends(get_db)):
    return [_reloj_out(conn, r) for r in conn.execute("SELECT * FROM relojes ORDER BY nombre")]


@router.post("/api/v1/relojes", status_code=201, summary="Crear reloj")
def crear(body: RelojIn, conn: MariaDBConnection = Depends(get_db)):
    try:
        rid = conn.execute(
            "INSERT INTO relojes (nombre, color, comentario, compensacion, respaldo) VALUES (?, ?, ?, ?, ?)",
            (body.nombre.strip(), body.color, body.comentario,
             json.dumps(_limpiar_filtros(body.compensacion)),
             json.dumps(_limpiar_filtros(body.respaldo)))).lastrowid
    except DualIntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe un reloj llamado '{body.nombre}'")
    _guardar_filas(conn, rid, body.filas)
    conn.commit()
    _replanificar()
    return _reloj_out(conn, conn.execute("SELECT * FROM relojes WHERE id = ?", (rid,)).fetchone())


@router.put("/api/v1/relojes/{reloj_id}", summary="Guardar reloj")
def guardar(reloj_id: int, body: RelojIn, conn: MariaDBConnection = Depends(get_db)):
    if not conn.execute("SELECT 1 FROM relojes WHERE id = ?", (reloj_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    try:
        conn.execute(
            "UPDATE relojes SET nombre = ?, color = ?, comentario = ?, compensacion = ?, respaldo = ?,"
            " updated_at = NOW() WHERE id = ?",
            (body.nombre.strip(), body.color, body.comentario,
             json.dumps(_limpiar_filtros(body.compensacion)),
             json.dumps(_limpiar_filtros(body.respaldo)), reloj_id))
    except DualIntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe un reloj llamado '{body.nombre}'")
    _guardar_filas(conn, reloj_id, body.filas)
    conn.commit()
    _replanificar()
    return _reloj_out(conn, conn.execute("SELECT * FROM relojes WHERE id = ?", (reloj_id,)).fetchone())


@router.delete("/api/v1/relojes/{reloj_id}", status_code=204, summary="Borrar reloj")
def borrar(reloj_id: int, conn: MariaDBConnection = Depends(get_db)):
    if not conn.execute("DELETE FROM relojes WHERE id = ?", (reloj_id,)).rowcount:
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    conn.commit()
    _replanificar()


@router.post("/api/v1/relojes/{reloj_id}/duplicar", status_code=201, summary="Duplicar reloj")
def duplicar(reloj_id: int, conn: MariaDBConnection = Depends(get_db)):
    r = conn.execute("SELECT * FROM relojes WHERE id = ?", (reloj_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    nombre, n = f"{r['nombre']} (copia)", 2
    while conn.execute("SELECT 1 FROM relojes WHERE nombre = ?", (nombre,)).fetchone():
        nombre, n = f"{r['nombre']} (copia {n})", n + 1
    nuevo = conn.execute(
        "INSERT INTO relojes (nombre, color, comentario, compensacion, respaldo) VALUES (?, ?, ?, ?, ?)",
        (nombre, r["color"], r["comentario"], r["compensacion"], r["respaldo"])).lastrowid
    conn.execute(
        "INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, filtros, audio_id, nota, tip_modo)"
        " SELECT ?, orden, tipo_audio, filtros, audio_id, nota, tip_modo FROM reloj_filas WHERE reloj_id = ?",
        (nuevo, reloj_id))
    conn.commit()
    return _reloj_out(conn, conn.execute("SELECT * FROM relojes WHERE id = ?", (nuevo,)).fetchone())


@router.get("/api/v1/relojes/{reloj_id}/vista", summary="Vista previa de un reloj")
def vista(reloj_id: int, conn: MariaDBConnection = Depends(get_db)):
    """
    Por fila: cuántos audios cumplen sus filtros y su duración media. Una fila
    con 0 audios se salta al planificar (avisar al programador).
    """
    r = conn.execute("SELECT * FROM relojes WHERE id = ?", (reloj_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Reloj no encontrado")
    return {"id": reloj_id, **_vista_filas(conn, _reloj_out(conn, r)["filas"])}


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
