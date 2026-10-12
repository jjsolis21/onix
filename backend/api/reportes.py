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
                  sus fechas de vigencia; sin fecha de inicio, desde que se creó),
                  como máximo menciones_diarias por día.
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
def top40(period: str = "week", limit: int = 40, tipo: str = "musica",
          conn: sqlite3.Connection = Depends(get_db)):
    """
    Más emitidos del período. tipo: musica (por defecto) · pauta · todo.
    `previos` = toques en el período anterior de igual largo (para la tendencia).
    """
    dias = PERIODOS.get(period)
    if dias is None:
        raise HTTPException(status_code=422, detail="period debe ser day, week o month")
    tipos = {"musica": ("musica",), "pauta": ("pauta",), "todo": ("musica", "pauta")}.get(tipo)
    if tipos is None:
        raise HTTPException(status_code=422, detail="tipo debe ser musica, pauta o todo")
    ahora = datetime.now()
    desde = (ahora - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    antes = (ahora - timedelta(days=2 * dias)).strftime("%Y-%m-%d %H:%M:%S")
    ph = ",".join("?" * len(tipos))
    rows = conn.execute(
        f"""
        SELECT a.id, a.titulo, a.artista, a.tipo_audio, a.duracion,
               COALESCE(a.subgenero, '') AS categoria,
               COUNT(*) AS toques, MAX(h.iniciado_en) AS ultima_emision
        FROM historial_emision h
        JOIN audios a ON a.id = h.audio_id
        WHERE h.iniciado_en >= ? AND h.tipo IN ({ph})
        GROUP BY a.id
        ORDER BY toques DESC, ultima_emision DESC
        LIMIT ?
        """,
        (desde, *tipos, max(1, min(limit, 200))),
    ).fetchall()
    ids = [r["id"] for r in rows]
    previos = dict(conn.execute(
        f"SELECT audio_id, COUNT(*) FROM historial_emision WHERE iniciado_en >= ? AND iniciado_en < ?"
        f" AND audio_id IN ({','.join('?' * len(ids))}) GROUP BY audio_id",
        (antes, desde, *ids)).fetchall()) if ids else {}
    return [{
        "id": r["id"], "tipo": _tipo_reporte(r["tipo_audio"]), "titulo": r["titulo"],
        "artista": r["artista"], "categoria": r["categoria"], "toques": r["toques"],
        "previos": previos.get(r["id"], 0),
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
    # Sin fecha de inicio, la pauta rige desde el día en que se creó
    inicio_pauta = p["fecha_inicio"] or (p["created_at"] or "")[:10]
    ini = max(desde, _parse_fecha(inicio_pauta, desde)) if inicio_pauta else desde
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
    # Solo pautas comerciales: ID y cortinas son de la emisora, no de un cliente
    sql = ("SELECT p.* FROM pautas p LEFT JOIN tanda_secciones s ON s.numero = COALESCE(p.seccion, 3)"
           " WHERE COALESCE(s.comercial, 1) = 1")
    params = []
    if cliente:
        sql += " AND p.cliente LIKE ?"
        params.append(f"%{cliente.upper()}%")
    filas = []
    for p in conn.execute(sql + " ORDER BY p.cliente, p.id", params).fetchall():
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


# ═══════════════════════════════════════════════════════════════════════════
# Certificado de emisión imprimible (lo que se entrega al cliente)
# ═══════════════════════════════════════════════════════════════════════════

_DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _e(v) -> str:
    from html import escape
    return escape(str(v if v is not None else ""))


@router.get("/api/v1/reportes/certificado", summary="Certificado de emisión imprimible por cliente")
def certificado(cliente: str = Query(..., min_length=1), desde: Optional[str] = None,
                hasta: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db)):
    """
    Página lista para imprimir o guardar como PDF: por cada spot del cliente,
    contratadas vs emitidas y la lista de cada salida REAL al aire (fecha y
    hora del historial de emisión, no lo programado).
    """
    from fastapi.responses import HTMLResponse
    hoy = date.today()
    d_desde = _parse_fecha(desde, hoy.replace(day=1))
    d_hasta = _parse_fecha(hasta, hoy)
    pautas = conn.execute(
        "SELECT p.* FROM pautas p LEFT JOIN tanda_secciones s ON s.numero = COALESCE(p.seccion, 3)"
        " WHERE COALESCE(s.comercial, 1) = 1 AND p.cliente = ? ORDER BY p.id",
        (cliente.strip().upper(),)).fetchall()
    if not pautas:
        raise HTTPException(status_code=404, detail=f"El cliente {cliente} no tiene spots")
    try:
        emisora = conn.execute("SELECT valor FROM config WHERE clave = 'emisora_nombre'").fetchone()
        emisora = emisora[0] if emisora and emisora[0] else "ÓNIX FM"
    except sqlite3.Error:
        emisora = "ÓNIX FM"

    bloques, tot_prog, tot_emit = [], 0, 0
    for p in pautas:
        r = _cumplimiento_pauta(conn, p, d_desde, d_hasta)
        emis = conn.execute(
            "SELECT iniciado_en FROM historial_emision"
            " WHERE pauta_id = ? AND iniciado_en >= ? AND iniciado_en < ? ORDER BY iniciado_en",
            (p["id"], d_desde.isoformat(), (d_hasta + timedelta(days=1)).isoformat())).fetchall()
        if not (r["programadas"] or emis):
            continue
        tot_prog += r["programadas"]
        tot_emit += len(emis)
        por_dia: dict[str, list[str]] = {}
        for (t,) in emis:
            por_dia.setdefault(t[:10], []).append(t[11:16])
        filas = "".join(
            f"<tr><td>{_DIAS[date.fromisoformat(d).weekday()]} {d[8:10]}/{d[5:7]}/{d[:4]}</td>"
            f"<td class='n'>{len(hs)}</td><td>{' · '.join(hs)}</td></tr>"
            for d, hs in sorted(por_dia.items()))
        # Mismo conteo que el total: salidas reales del período frente a lo contratado
        pct = f"{min(100, 100 * len(emis) / r['programadas']):.0f} %" if r["programadas"] else "—"
        bloques.append(f"""
        <section>
          <h2>{_e(p['audio_nombre'] or 'Spot')}</h2>
          <p class="meta">Contrato: {_e(p['contrato_id'] or '—')} · Vigencia: {_e(p['fecha_inicio'] or '—')} a {_e(p['fecha_fin'] or 'sin fin')}
             · {_e(p['menciones_diarias'] or '—')} menciones por día</p>
          <div class="kpis"><div><b>{r['programadas']}</b>Contratadas en el período</div>
            <div><b>{len(emis)}</b>Emitidas</div><div><b>{pct}</b>Cumplimiento</div></div>
          {f'<table><thead><tr><th>Día</th><th>Salidas</th><th>Horas al aire</th></tr></thead><tbody>{filas}</tbody></table>'
           if filas else '<p class="vacio">Sin emisiones registradas en el período.</p>'}
        </section>""")

    pct_total = f"{min(100, 100 * tot_emit / tot_prog):.0f} %" if tot_prog else "—"
    html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Certificado de emisión — {_e(pautas[0]['cliente'])}</title>
<style>
  body {{ font: 13px/1.45 "Segoe UI", Arial, sans-serif; color: #111; background: #fff; margin: 0; padding: 28px; }}
  .hoja {{ max-width: 820px; margin: 0 auto; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 3px solid #111; padding-bottom: 10px; }}
  header h1 {{ margin: 0; font-size: 22px; letter-spacing: .02em; }}
  header .emisora {{ font-size: 18px; font-weight: 800; }}
  .datos {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 20px; margin: 14px 0 6px; }}
  .datos b {{ display: inline-block; min-width: 70px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 8px 0; }}
  .kpis div {{ border: 1px solid #bbb; border-radius: 6px; padding: 6px 10px; font-size: 11px; color: #555; }}
  .kpis b {{ display: block; font-size: 20px; color: #111; }}
  .total {{ background: #f2f2f2; border-radius: 8px; padding: 6px 12px; margin: 10px 0 16px; }}
  section {{ margin: 18px 0; page-break-inside: avoid; }}
  h2 {{ font-size: 15px; margin: 0 0 2px; }}
  .meta {{ margin: 0; color: #555; font-size: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ border-bottom: 1px solid #ddd; padding: 4px 6px; text-align: left; vertical-align: top; }}
  th {{ background: #f2f2f2; }}
  td.n {{ text-align: center; font-weight: 700; width: 60px; }}
  .vacio {{ color: #a00; }}
  .firma {{ display: flex; justify-content: space-between; margin-top: 60px; gap: 40px; }}
  .firma div {{ flex: 1; border-top: 1px solid #111; padding-top: 4px; text-align: center; font-size: 12px; }}
  .pie {{ margin-top: 18px; font-size: 11px; color: #777; }}
  .acciones {{ position: fixed; top: 12px; right: 12px; }}
  .acciones button {{ font: 600 14px "Segoe UI", Arial; padding: 9px 16px; border-radius: 6px; border: 0; background: #111; color: #fff; cursor: pointer; }}
  #emisora:hover {{ outline: 1px dashed #999; }}
  @media print {{ .acciones {{ display: none; }} body {{ padding: 0; }} }}
</style></head><body>
<div class="acciones"><button onclick="window.print()">🖨 Imprimir / Guardar PDF</button></div>
<div class="hoja">
  <header><div><div class="emisora" id="emisora" contenteditable="true" title="Clic para escribir el nombre de tu emisora">{_e(emisora)}</div><h1>Certificado de emisión</h1></div>
    <div>Emitido el {hoy.strftime('%d/%m/%Y')}</div></header>
  <div class="datos">
    <div><b>Cliente:</b> {_e(pautas[0]['cliente'])}</div>
    <div><b>Período:</b> {d_desde.strftime('%d/%m/%Y')} al {d_hasta.strftime('%d/%m/%Y')}</div>
    <div><b>Agencia:</b> {_e(pautas[0]['agencia'] or '—') if 'agencia' in pautas[0].keys() else '—'}</div>
    <div><b>Rubro:</b> {_e(pautas[0]['rubro'] or '—')}</div>
  </div>
  <div class="kpis total"><div><b>{tot_prog}</b>Contratadas en el período</div>
    <div><b>{tot_emit}</b>Emitidas al aire</div><div><b>{pct_total}</b>Cumplimiento total</div></div>
  {''.join(bloques) or '<p class="vacio">Este cliente no tiene salidas en el período.</p>'}
  <div class="firma"><div>Responsable de programación</div><div>Recibido por el cliente</div></div>
  <p class="pie">Las horas corresponden al registro automático de emisión del sistema (lo que realmente salió al aire).</p>
</div>
<script>
  // El nombre de la emisora se escribe una vez y queda recordado en esta PC
  const el = document.getElementById('emisora');
  try {{ const v = localStorage.getItem('onix_emisora'); if (v) el.textContent = v; }} catch (e) {{}}
  el.addEventListener('input', () => {{ try {{ localStorage.setItem('onix_emisora', el.textContent.trim()); }} catch (e) {{}} }});
</script></body></html>"""
    return HTMLResponse(html)


# ═══════════════════════════════════════════════════════════════════════════
# Cuñas de hoy que no alcanzan a salir (aviso antes del reporte de mañana)
# ═══════════════════════════════════════════════════════════════════════════

def pautas_en_riesgo(conn, ahora: datetime = None) -> list[dict]:
    """
    Por cada comercial vigente que sale hoy: cuántas menciones le tocan hoy,
    cuántas salieron y cuántas horas marcadas le quedan. Si lo que falta no
    entra en las horas que quedan, está en riesgo (o ya incumplida).
    La hora en curso cuenta como disponible si todavía no salió en ella y
    quedan al menos 5 minutos.
    """
    ahora = ahora or datetime.now()
    hoy = ahora.date()
    dia = hoy.isoformat()
    filas = conn.execute(
        "SELECT p.* FROM pautas p LEFT JOIN tanda_secciones s ON s.numero = COALESCE(p.seccion, 3)"
        " WHERE COALESCE(s.comercial, 1) = 1 AND p.activo = 1"
        "   AND (COALESCE(p.fecha_inicio,'') = '' OR p.fecha_inicio <= ?)"
        "   AND (COALESCE(p.fecha_fin,'')    = '' OR p.fecha_fin    >= ?)", (dia, dia)).fetchall()
    emit = {}
    for pid, slot in conn.execute(
            "SELECT pauta_id, slot FROM historial_emision WHERE pauta_id IS NOT NULL AND slot LIKE ?",
            (dia + "-%",)):
        emit.setdefault(pid, []).append(slot)
    slot_actual = ahora.strftime("%Y-%m-%d-%H")
    out = []
    for p in filas:
        try:
            matriz = json.loads(p["matriz"] or "{}")
        except (TypeError, ValueError):
            continue
        horas = _horas_marcadas(matriz if isinstance(matriz, dict) else {}, hoy)
        if not horas:
            continue
        objetivo = min(len(horas), p["menciones_diarias"] or len(horas))
        salidas = emit.get(p["id"], [])
        restantes = sum(1 for h in horas if h > ahora.hour)
        if ahora.hour in horas and slot_actual not in salidas and ahora.minute < 55:
            restantes += 1
        faltan = max(0, objetivo - len(salidas))
        if faltan > restantes:
            out.append({"pauta_id": p["id"], "cliente": p["cliente"], "spot": p["audio_nombre"] or "",
                        "objetivo": objetivo, "emitidas": len(salidas), "restantes": restantes,
                        "perdidas": faltan - restantes,
                        "estado": "incumplida" if restantes == 0 else "en_riesgo"})
    return out


@router.get("/api/v1/pautas-riesgo", summary="Cuñas de hoy que no alcanzan a salir")
def riesgo(conn: sqlite3.Connection = Depends(get_db)):
    return {"data": pautas_en_riesgo(conn)}


# ═══════════════════════════════════════════════════════════════════════════
# Análisis: música por hora, artistas más sonados, pautas por mes
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/stats/analisis", summary="Música por hora, artistas y cumplimiento de pautas")
def analisis(period: str = "week", conn: sqlite3.Connection = Depends(get_db)):
    dias = PERIODOS.get(period)
    if dias is None:
        raise HTTPException(status_code=422, detail="period debe ser day, week o month")
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    horas = [0] * 24
    for h, n in conn.execute(
            "SELECT CAST(substr(iniciado_en, 12, 2) AS INTEGER), COUNT(*) FROM historial_emision"
            " WHERE iniciado_en >= ? AND tipo = 'musica' GROUP BY 1", (desde,)):
        if h is not None and 0 <= h < 24:
            horas[h] = n
    artistas = [{"artista": r[0], "toques": r[1], "canciones": r[2]} for r in conn.execute(
        "SELECT artista, COUNT(*) AS n, COUNT(DISTINCT audio_id) FROM historial_emision"
        " WHERE iniciado_en >= ? AND tipo = 'musica' AND COALESCE(artista, '') <> ''"
        " GROUP BY UPPER(artista) ORDER BY n DESC LIMIT 10", (desde,))]

    # Cumplimiento de pautas comerciales: últimos 6 meses (el actual hasta hoy)
    hoy = date.today()
    pautas = conn.execute(
        "SELECT p.* FROM pautas p LEFT JOIN tanda_secciones s ON s.numero = COALESCE(p.seccion, 3)"
        " WHERE COALESCE(s.comercial, 1) = 1").fetchall()
    meses = []
    y, m = hoy.year, hoy.month
    for _ in range(6):
        ini = date(y, m, 1)
        fin = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        prog = emit = 0
        for p in pautas:
            r = _cumplimiento_pauta(conn, p, ini, min(fin, hoy))
            prog += r["programadas"]
            emit += min(r["emitidas"], r["programadas"]) if r["programadas"] else 0
        meses.append({"mes": ini.strftime("%Y-%m"), "programadas": prog, "emitidas": emit,
                      "pct": round(100 * emit / prog, 1) if prog else None})
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return {"horas": horas, "artistas": artistas, "pautas_mes": list(reversed(meses))}


# ═══════════════════════════════════════════════════════════════════════════
# Rotación real vs planeada · canciones quemadas
# ═══════════════════════════════════════════════════════════════════════════

def _peso_planeado(conn) -> dict[str, float]:
    """
    Qué parte de la música piden los relojes para cada rotación (cat2):
    filas de música con ese filtro × horas de la semana en que su reloj está
    en la grilla. Las filas sin filtro de rotación van a "(cualquiera)".
    """
    horas_reloj = dict(conn.execute("SELECT reloj_id, COUNT(*) FROM reloj_grilla GROUP BY reloj_id").fetchall())
    filas_reloj: dict[int, list[str]] = {}
    for rid, filtros in conn.execute("SELECT reloj_id, filtros FROM reloj_filas WHERE tipo_audio = 'Musica'"):
        try:
            f = json.loads(filtros or "{}")
        except ValueError:
            f = {}
        filas_reloj.setdefault(rid, []).append(((f.get("cat2") or "").strip().upper()) or "(cualquiera)")
    peso: dict[str, float] = {}
    for rid, filas in filas_reloj.items():
        horas = horas_reloj.get(rid, 0)
        for v in filas:
            peso[v] = peso.get(v, 0) + horas / len(filas)
    total = sum(peso.values()) or 1
    return {k: v / total for k, v in peso.items()}


@router.get("/api/v1/stats/rotacion", summary="Rotación real frente a la planeada por los relojes")
def rotacion(period: str = "week", conn: sqlite3.Connection = Depends(get_db)):
    dias = PERIODOS.get(period)
    if dias is None:
        raise HTTPException(status_code=422, detail="period debe ser day, week o month")
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    toques = dict(conn.execute(
        "SELECT audio_id, COUNT(*) FROM historial_emision WHERE iniciado_en >= ? AND tipo = 'musica'"
        " GROUP BY audio_id", (desde,)).fetchall())
    grupos: dict[str, list[int]] = {}
    for aid, cat in conn.execute(
            "SELECT id, UPPER(COALESCE(NULLIF(TRIM(categoria), ''), '(sin rotación)')) FROM audios"
            " WHERE activo = 1 AND tipo_audio = 'Musica' AND COALESCE(aprobada, 1) = 1"):
        grupos.setdefault(cat, []).append(toques.get(aid, 0))
    total = sum(sum(v) for v in grupos.values())
    planeado = _peso_planeado(conn)
    filas = []
    for cat, lista in grupos.items():
        n, t = len(lista), sum(lista)
        filas.append({
            "rotacion": cat, "canciones": n, "toques": t,
            "promedio": round(t / n, 1) if n else 0, "maximo": max(lista) if lista else 0,
            "sin_sonar": sum(1 for x in lista if x == 0),
            "pct_real": round(100 * t / total, 1) if total else 0,
            "pct_planeado": round(100 * planeado[cat], 1) if cat in planeado else None,
        })
    filas.sort(key=lambda f: -f["toques"])
    return {"total": total, "libre_pct": round(100 * planeado.get("(cualquiera)", 0), 1), "data": filas}


@router.get("/api/v1/stats/quemadas", summary="Canciones que suenan mucho más que su rotación")
def quemadas(dias: int = 7, factor: float = 2.0, minimo: int = 5, conn: sqlite3.Connection = Depends(get_db)):
    """
    Quemada = sonó al menos `minimo` veces en `dias` días y más de `factor`
    veces el promedio de su rotación en ese lapso.
    """
    dias = max(1, min(dias, 90))
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """
        SELECT a.id, a.titulo, a.artista,
               UPPER(COALESCE(NULLIF(TRIM(a.categoria), ''), '(sin rotación)')) AS rot,
               (SELECT COUNT(*) FROM historial_emision h
                 WHERE h.audio_id = a.id AND h.iniciado_en >= ? AND h.tipo = 'musica') AS n
        FROM audios a WHERE a.activo = 1 AND a.tipo_audio = 'Musica'
        """, (desde,)).fetchall()
    por_rot: dict[str, list[int]] = {}
    for r in rows:
        por_rot.setdefault(r["rot"], []).append(r["n"])
    prom = {k: sum(v) / len(v) for k, v in por_rot.items()}
    out = [{"id": r["id"], "titulo": r["titulo"], "artista": r["artista"], "rotacion": r["rot"],
            "toques": r["n"], "promedio_rotacion": round(prom[r["rot"]], 1),
            "veces_promedio": round(r["n"] / prom[r["rot"]], 1) if prom[r["rot"]] else None}
           for r in rows if r["n"] >= minimo and r["n"] > factor * prom[r["rot"]]]
    out.sort(key=lambda x: -(x["veces_promedio"] or 0))
    return {"dias": dias, "data": out[:100]}


# ═══════════════════════════════════════════════════════════════════════════
# Reloj vivo de un día: qué sonó, hora por hora
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/stats/dia", summary="Lo que salió al aire en un día, hora por hora")
def dia_al_aire(fecha: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db)):
    d = _parse_fecha(fecha, date.today())
    ini, fin = d.isoformat(), (d + timedelta(days=1)).isoformat()
    filas = conn.execute(
        """
        SELECT h.iniciado_en, h.titulo, h.artista, h.tipo, h.origen, h.pauta_id, h.tanda,
               a.duracion, COALESCE(a.categoria, '') AS rotacion, p.cliente
        FROM historial_emision h
        LEFT JOIN audios a ON a.id = h.audio_id
        LEFT JOIN pautas p ON p.id = h.pauta_id
        WHERE h.iniciado_en >= ? AND h.iniciado_en < ?
        ORDER BY h.iniciado_en
        """, (ini, fin)).fetchall()
    horas = [{"hora": h, "items": [], "musica": 0, "cunas": 0, "segundos": 0.0} for h in range(24)]
    for r in filas:
        try:
            h = int(r["iniciado_en"][11:13])
        except (TypeError, ValueError):
            continue
        bloque = horas[h]
        es_cuna = r["pauta_id"] is not None or r["tipo"] == "pauta"
        bloque["items"].append({
            "hora": r["iniciado_en"][11:19], "titulo": r["titulo"], "artista": r["artista"] or "",
            "tipo": "cuña" if es_cuna else (r["tipo"] or "musica"), "origen": r["origen"],
            "cliente": r["cliente"] or "", "tanda": r["tanda"], "rotacion": r["rotacion"],
            "duracion": r["duracion"] or 0,
        })
        bloque["cunas" if es_cuna else "musica"] += 1
        bloque["segundos"] += float(r["duracion"] or 0)
    return {"fecha": ini, "total": len(filas), "horas": horas}
