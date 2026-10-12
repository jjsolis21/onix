"""
reportes.py — Ónix FM
Estadísticas y reportes de emisión (todo sale de historial_emision).

    GET /api/v1/stats/top40?period=day|week|month   → más emitidos
    GET /api/v1/stats/top40/forgotten?dias=30        → música que no suena hace N días
    GET /api/v1/stats/detalle/{audio_id}             → ficha de auditoría de un audio
    GET /api/v1/historial/buscar                     → historial con filtros y paginación
    GET /api/v1/reportes/pautas                      → cumplimiento por pauta (cliente)
    GET /api/v1/reportes/pautas/{pauta_id}           → detalle por día de una pauta

Los reportes aceptan ?formato=csv para descargar (se abre en Excel).

CUMPLIMIENTO DE UNA PAUTA
    programadas = horas marcadas en su matriz dentro del período (y dentro de
                  sus fechas de vigencia), como máximo menciones_diarias por día.
                  Del día de hoy solo cuentan las horas ya cumplidas.
    emitidas    = salidas registradas en historial_emision con ese pauta_id.
    cumplimiento = emitidas / programadas (tope 100 %).
"""

import csv
import io
import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.paths import DB_PATH

router = APIRouter(tags=["Estadísticas"])

PERIODOS = {"day": 1, "week": 7, "month": 30}


def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _tipo_reporte(tipo_audio: Optional[str]) -> str:
    """Etiqueta que usa el módulo de Estadísticas del Admin."""
    return "PAUTA" if tipo_audio == "Cuña" else "TEMA"


def _csv(nombre: str, filas: list[dict]) -> Response:
    buf = io.StringIO()
    if filas:
        w = csv.DictWriter(buf, fieldnames=list(filas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(filas)
    # BOM para que Excel reconozca UTF-8 (tildes y eñes)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


def _parse_fecha(valor: Optional[str], defecto: date) -> date:
    if not valor:
        return defecto
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Fecha inválida: {valor!r} (use AAAA-MM-DD)")


# ═══════════════════════════════════════════════════════════════════════════
# Top 40, olvidados y detalle
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/stats/top40", summary="Top 40 de emisiones")
def top40(period: str = "week", limit: int = 40, conn: sqlite3.Connection = Depends(get_db)):
    dias = PERIODOS.get(period)
    if dias is None:
        raise HTTPException(status_code=422, detail="period debe ser day, week o month")
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """
        SELECT a.id, a.titulo, a.artista, a.tipo_audio, a.duracion,
               COALESCE(a.subgenero, '') AS categoria, COUNT(*) AS toques,
               MAX(h.iniciado_en) AS ultima_emision
        FROM historial_emision h
        JOIN audios a ON a.id = h.audio_id
        WHERE h.iniciado_en >= ? AND h.tipo IN ('musica', 'pauta')
        GROUP BY a.id
        ORDER BY toques DESC, ultima_emision DESC
        LIMIT ?
        """,
        (desde, max(1, min(limit, 200))),
    ).fetchall()
    return [{
        "id": r["id"], "tipo": _tipo_reporte(r["tipo_audio"]), "titulo": r["titulo"],
        "artista": r["artista"], "categoria": r["categoria"], "toques": r["toques"],
        "duracion": r["duracion"] or 0, "ultima_emision": r["ultima_emision"],
        "audio_url": f"/api/v1/audios/{r['id']}/stream",
    } for r in rows]


@router.get("/api/v1/stats/top40/forgotten", summary="Música que no suena hace tiempo")
def olvidados(dias: int = 30, limit: int = 100, conn: sqlite3.Connection = Depends(get_db)):
    """Música activa sin emitirse en `dias` días (o nunca emitida)."""
    rows = conn.execute(
        """
        SELECT id, titulo, artista, tipo_audio, ultima_reproduccion, created_at,
               CAST(julianday('now', 'localtime')
                    - julianday(COALESCE(ultima_reproduccion, created_at)) AS INTEGER) AS dias
        FROM audios
        WHERE activo = 1 AND tipo_audio = 'Musica'
          AND julianday('now', 'localtime')
              - julianday(COALESCE(ultima_reproduccion, created_at)) >= ?
        ORDER BY dias DESC
        LIMIT ?
        """,
        (max(0, dias), max(1, min(limit, 1000))),
    ).fetchall()
    return [{
        "id": r["id"], "tipo": _tipo_reporte(r["tipo_audio"]), "titulo": r["titulo"],
        "artista": r["artista"], "dias_sin_sonar": r["dias"] or 0,
        "ultima_emision": r["ultima_reproduccion"], "nunca_emitido": r["ultima_reproduccion"] is None,
        "audio_url": f"/api/v1/audios/{r['id']}/stream",
    } for r in rows]


@router.get("/api/v1/stats/detalle/{audio_id}", summary="Ficha de auditoría de un audio")
def detalle(audio_id: int, conn: sqlite3.Connection = Depends(get_db)):
    a = conn.execute("SELECT * FROM audios WHERE id = ?", (audio_id,)).fetchone()
    if not a:
        raise HTTPException(status_code=404, detail=f"Audio id={audio_id} no encontrado")
    emisiones = conn.execute(
        "SELECT id, iniciado_en, origen, tipo, pauta_id FROM historial_emision"
        " WHERE audio_id = ? ORDER BY id DESC LIMIT 500", (audio_id,)
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) FROM historial_emision WHERE audio_id = ?", (audio_id,)
    ).fetchone()[0]
    ult_30 = conn.execute(
        "SELECT COUNT(*) FROM historial_emision WHERE audio_id = ?"
        " AND iniciado_en >= datetime('now', 'localtime', '-30 days')", (audio_id,)
    ).fetchone()[0]

    cumplimiento = None
    if a["tipo_audio"] == "Cuña":
        hoy = date.today()
        filas = [_cumplimiento_pauta(conn, p, hoy - timedelta(days=29), hoy)
                 for p in conn.execute("SELECT * FROM pautas WHERE audio_id = ?", (audio_id,))]
        prog = sum(f["programadas"] for f in filas)
        emit = sum(min(f["emitidas"], f["programadas"]) for f in filas)
        cumplimiento = round(100 * emit / prog) if prog else None

    return {
        "id": a["id"], "tipo": _tipo_reporte(a["tipo_audio"]), "tipo_audio": a["tipo_audio"],
        "titulo": a["titulo"], "artista": a["artista"], "categoria": a["subgenero"] or "",
        "duracion": a["duracion"] or 0, "audio_url": f"/api/v1/audios/{a['id']}/stream",
        "total_toques": total,
        "ultima_emision": emisiones[0]["iniciado_en"] if emisiones else None,
        "promedio_dia": round(ult_30 / 30, 1),
        "cumplimiento_pct": cumplimiento,
        "emisiones": [{
            "id": e["id"], "fecha_hora": e["iniciado_en"].replace(" ", "T"),
            "canal": e["origen"], "tipo": e["tipo"], "pauta_id": e["pauta_id"],
        } for e in emisiones],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Historial con filtros
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/historial/buscar", summary="Historial de emisión con filtros")
def historial_buscar(
    desde: Optional[str] = None, hasta: Optional[str] = None,
    tipo: Optional[str] = Query(None, description="musica | pauta | efecto"),
    origen: Optional[str] = Query(None, description="auto | manual | cola | pauta | respaldo"),
    q: Optional[str] = None, pauta_id: Optional[int] = None,
    limit: int = 200, offset: int = 0, formato: str = "json",
    conn: sqlite3.Connection = Depends(get_db),
):
    hoy = date.today()
    d_desde = _parse_fecha(desde, hoy - timedelta(days=6))
    d_hasta = _parse_fecha(hasta, hoy)
    where = ["h.iniciado_en >= ?", "h.iniciado_en < ?"]
    params: list = [d_desde.isoformat(), (d_hasta + timedelta(days=1)).isoformat()]
    if tipo:
        where.append("h.tipo = ?")
        params.append(tipo)
    if origen:
        where.append("h.origen = ?")
        params.append(origen)
    if pauta_id is not None:
        where.append("h.pauta_id = ?")
        params.append(pauta_id)
    if q:
        where.append("(h.titulo LIKE ? OR h.artista LIKE ? OR p.cliente LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    base = (" FROM historial_emision h LEFT JOIN pautas p ON p.id = h.pauta_id"
            " WHERE " + " AND ".join(where))
    total = conn.execute("SELECT COUNT(*)" + base, params).fetchone()[0]
    sql = ("SELECT h.id, h.iniciado_en, h.audio_id, h.titulo, h.artista, h.tipo, h.origen,"
           " h.pauta_id, p.cliente" + base + " ORDER BY h.id DESC")
    if formato == "csv":
        filas = [dict(r) for r in conn.execute(sql + " LIMIT 100000", params)]
        return _csv(f"historial_{d_desde}_{d_hasta}.csv", filas)
    rows = conn.execute(sql + " LIMIT ? OFFSET ?",
                        params + [max(1, min(limit, 1000)), max(0, offset)]).fetchall()
    return {"total": total, "desde": d_desde.isoformat(), "hasta": d_hasta.isoformat(),
            "data": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════════════
# Reporte de pautas por cliente
# ═══════════════════════════════════════════════════════════════════════════

def _horas_marcadas(matriz: dict, dia: date) -> list[int]:
    dow = (dia.weekday() + 1) % 7     # 0=Dom … 6=Sáb (clave de la matriz)
    return [h for h in range(24) if matriz.get(f"{dow}-{h}")]


def _cumplimiento_pauta(conn, p, desde: date, hasta: date, ahora: datetime = None) -> dict:
    """Programadas vs emitidas de una pauta entre dos fechas (inclusive)."""
    ahora = ahora or datetime.now()
    try:
        matriz = json.loads(p["matriz"] or "{}")
    except (TypeError, ValueError):
        matriz = {}
    if not isinstance(matriz, dict):
        matriz = {}
    ini = max(desde, _parse_fecha(p["fecha_inicio"], desde)) if p["fecha_inicio"] else desde
    fin = min(hasta, _parse_fecha(p["fecha_fin"], hasta)) if p["fecha_fin"] else hasta
    fin = min(fin, ahora.date())
    tope = p["menciones_diarias"] or None

    emitidas_dia = dict(conn.execute(
        "SELECT substr(slot, 1, 10) AS dia, COUNT(*) FROM historial_emision"
        " WHERE pauta_id = ? AND slot >= ? AND slot < ? GROUP BY dia",
        (p["id"], ini.isoformat(), (fin + timedelta(days=1)).isoformat()),
    ).fetchall()) if ini <= fin else {}

    dias, prog_total, emit_total = [], 0, 0
    d = ini
    while d <= fin:
        horas = _horas_marcadas(matriz, d)
        emit = emitidas_dia.get(d.isoformat(), 0)
        if d == ahora.date():
            # Del día en curso solo cuentan las horas ya terminadas (o ya emitidas)
            pasadas = [h for h in horas if h < ahora.hour]
            prog = max(len(pasadas), min(emit, len(horas)))
        else:
            prog = len(horas)
        if tope:
            prog = min(prog, tope)
        if prog or emit:
            dias.append({"fecha": d.isoformat(), "programadas": prog, "emitidas": emit,
                         "faltantes": max(0, prog - emit)})
        prog_total += prog
        emit_total += emit
        d += timedelta(days=1)

    return {
        "pauta_id": p["id"], "cliente": p["cliente"], "rubro": p["rubro"] or "",
        "contrato_id": p["contrato_id"] or "", "audio_id": p["audio_id"],
        "fecha_inicio": p["fecha_inicio"] or "", "fecha_fin": p["fecha_fin"] or "",
        "programadas": prog_total, "emitidas": emit_total,
        "faltantes": max(0, prog_total - emit_total),
        "cumplimiento_pct": round(min(100.0, 100 * emit_total / prog_total), 1) if prog_total else None,
        "dias": dias,
    }


@router.get("/api/v1/reportes/pautas", summary="Cumplimiento de pautas por cliente")
def reporte_pautas(desde: Optional[str] = None, hasta: Optional[str] = None,
                   cliente: Optional[str] = None, formato: str = "json",
                   conn: sqlite3.Connection = Depends(get_db)):
    hoy = date.today()
    d_desde = _parse_fecha(desde, hoy.replace(day=1))
    d_hasta = _parse_fecha(hasta, hoy)
    sql, params = "SELECT * FROM pautas", []
    if cliente:
        sql += " WHERE cliente LIKE ?"
        params.append(f"%{cliente.upper()}%")
    filas = []
    for p in conn.execute(sql + " ORDER BY cliente, id", params).fetchall():
        r = _cumplimiento_pauta(conn, p, d_desde, d_hasta)
        if r["programadas"] or r["emitidas"]:
            r.pop("dias")
            filas.append(r)
    if formato == "csv":
        return _csv(f"pautas_{d_desde}_{d_hasta}.csv", filas)
    return {"desde": d_desde.isoformat(), "hasta": d_hasta.isoformat(), "data": filas}


@router.get("/api/v1/reportes/pautas/{pauta_id}", summary="Detalle diario de una pauta")
def reporte_pauta(pauta_id: int, desde: Optional[str] = None, hasta: Optional[str] = None,
                  formato: str = "json", conn: sqlite3.Connection = Depends(get_db)):
    p = conn.execute("SELECT * FROM pautas WHERE id = ?", (pauta_id,)).fetchone()
    if not p:
        raise HTTPException(status_code=404, detail=f"Pauta id={pauta_id} no encontrada")
    hoy = date.today()
    d_desde = _parse_fecha(desde, hoy.replace(day=1))
    d_hasta = _parse_fecha(hasta, hoy)
    r = _cumplimiento_pauta(conn, p, d_desde, d_hasta)
    r["emisiones"] = [dict(e) for e in conn.execute(
        "SELECT iniciado_en, titulo, origen FROM historial_emision"
        " WHERE pauta_id = ? AND iniciado_en >= ? AND iniciado_en < ? ORDER BY id",
        (pauta_id, d_desde.isoformat(), (d_hasta + timedelta(days=1)).isoformat()),
    )]
    if formato == "csv":
        return _csv(f"pauta_{pauta_id}_{r['cliente']}_{d_desde}_{d_hasta}.csv".replace(" ", "_"),
                    [{"cliente": r["cliente"], **e} for e in r["emisiones"]])
    r.update({"desde": d_desde.isoformat(), "hasta": d_hasta.isoformat()})
    return r
