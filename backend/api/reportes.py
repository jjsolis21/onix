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
from backend.database import DualError, MariaDBConnection
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response


router = APIRouter(tags=["Estadísticas"])

PERIODOS = {"day": 1, "week": 7, "month": 30}


from backend.database import get_db


def _tipo_reporte(tipo_audio: Optional[str]) -> str:
    """Etiqueta que usa el módulo de Estadísticas del Admin."""
    if tipo_audio == "Cuña":
        return "PAUTA"
    elif tipo_audio == "Efecto":
        return "JINGLE"
    elif tipo_audio == "Sweeper":
        return "SWEEPER"
    elif tipo_audio == "Tips":
        return "TIPS"
    elif tipo_audio == "Locucion":
        return "VOZ"
    return "TEMA"


def _dedup_case(items: list[str]) -> list[str]:
    vistos = set()
    res = []
    for item in items:
        if item and item.strip():
            norm = item.strip().lower()
            if norm not in vistos:
                vistos.add(norm)
                res.append(item.strip())
    return sorted(res, key=lambda s: s.lower())


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
# Top 40, filtros dinámicos, olvidados y detalle
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/stats/filtros-categorias", summary="Categorías disponibles para filtros en estadísticas")
def filtros_categorias(conn: MariaDBConnection = Depends(get_db)):
    """Devuelve las categorías de música, identificadores y pautas presentes en el sistema.
    SOLO incluye valores que existen en la biblioteca real (audios con archivos),
    nunca categorías vacías de la tabla de configuración.

    Nota sobre la DB:
      a.subgenero → Género musical  (SALSA, MERENGUE, URBANO, POP, BALADA …)
      a.categoria → Rotación        (Clásico, Reciente, Éxito, Nuevo …)
    """
    # ── Música: Géneros (a.subgenero) ────────────────────────────────────
    generos_raw = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT subgenero FROM audios"
            " WHERE tipo_audio = 'Musica'"
            "   AND subgenero IS NOT NULL AND TRIM(subgenero) != ''"
            " ORDER BY subgenero"
        ).fetchall() if r[0]
    ]

    # ── Música: Rotaciones (a.categoria) ─────────────────────────────────
    rotaciones_raw = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT categoria FROM audios"
            " WHERE tipo_audio = 'Musica'"
            "   AND categoria IS NOT NULL AND TRIM(categoria) != ''"
            " ORDER BY categoria"
        ).fetchall() if r[0]
    ]

    # ── Identificadores: tipos base con al menos 1 audio ─────────────────
    ident_tipos_base = [
        {"id": "Efecto",  "nombre": "Jingles / Efectos"},
        {"id": "Sweeper", "nombre": "Sweepers / Barridos"},
        {"id": "Tips",    "nombre": "Tips / Consejos"},
    ]
    tipos_con_audio = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT tipo_audio FROM audios"
            " WHERE tipo_audio IN ('Efecto','Sweeper','Tips')"
        ).fetchall()
    }
    ident_tipos = [t for t in ident_tipos_base if t["id"] in tipos_con_audio]

    # ── Identificadores: subcategorías con al menos 1 audio real ─────────
    ident_cats_raw = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT categoria_ident FROM audios"
            " WHERE tipo_audio IN ('Efecto','Sweeper','Tips')"
            "   AND categoria_ident IS NOT NULL AND TRIM(categoria_ident) != ''"
            " ORDER BY categoria_ident"
        ).fetchall() if r[0]
    ]

    # ── Pautas: clientes ─────────────────────────────────────────────────
    try:
        clientes_raw = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT cliente FROM pautas"
                " WHERE cliente IS NOT NULL AND TRIM(cliente) != ''"
                " UNION"
                " SELECT DISTINCT artista FROM audios"
                " WHERE tipo_audio = 'Cuña'"
                "   AND artista IS NOT NULL AND TRIM(artista) != ''"
                " ORDER BY 1"
            ).fetchall() if r[0]
        ]
    except Exception:
        clientes_raw = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT artista FROM audios"
                " WHERE tipo_audio = 'Cuña'"
                "   AND artista IS NOT NULL AND TRIM(artista) != ''"
            ).fetchall() if r[0]
        ]

    return {
        "musica": {
            "generos":    _dedup_case(generos_raw),
            "rotaciones": _dedup_case(rotaciones_raw),
        },
        "ident": {
            "tipos":      ident_tipos,
            "categorias": _dedup_case(ident_cats_raw),
        },
        "pauta": {
            "clientes": _dedup_case(clientes_raw),
        }
    }



@router.get("/api/v1/stats/top40", summary="Top de emisiones con filtros avanzados")
def top40(period: str = "week", limit: int = 40, tipo: str = "musica",
          categoria: Optional[str] = None, subtipo: Optional[str] = None,
          genero: Optional[str] = None, rotacion: Optional[str] = None,
          desde: Optional[str] = None, hasta: Optional[str] = None,
          formato: str = "json",
          conn: MariaDBConnection = Depends(get_db)):
    """
    Más emitidos del período.
    tipo: musica (por defecto) · ident · pauta · todo.
    genero: filtro por género musical (SALSA, MERENGUE, POP…).
    rotacion: filtro por rotación (Clásico, Reciente, Éxito, Nuevo…).
    categoria: compatibilidad con filtro único legado.
    subtipo: filtro secundario específico (ej. Efecto | Sweeper | Tips).
    period: day · yesterday · week · month · custom.
    """
    tipos_validos = ("musica", "pauta", "todo", "ident", "identificadores")
    if tipo not in tipos_validos:
        raise HTTPException(status_code=422, detail="tipo debe ser musica, ident, pauta o todo")

    periodos_validos = ("day", "yesterday", "week", "month", "custom")
    if period not in periodos_validos:
        raise HTTPException(status_code=422, detail="period debe ser day, yesterday, week, month o custom")

    ahora = datetime.now()
    if period == "yesterday":
        hoy_00 = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        desde_dt = hoy_00 - timedelta(days=1)
        hasta_dt = hoy_00 - timedelta(seconds=1)
        duracion = timedelta(days=1)
    elif period == "day":
        hasta_dt = ahora
        duracion = timedelta(days=1)
        desde_dt = ahora - duracion
    elif period == "week":
        hasta_dt = ahora
        duracion = timedelta(days=7)
        desde_dt = ahora - duracion
    elif period == "month":
        hasta_dt = ahora
        duracion = timedelta(days=30)
        desde_dt = ahora - duracion
    elif period == "custom":
        if not desde:
            raise HTTPException(status_code=422, detail="Para período personalizado debe indicar 'desde'")
        try:
            d_str = desde.strip()
            if len(d_str) == 10:
                d_str += " 00:00:00"
            desde_dt = datetime.fromisoformat(d_str)
        except ValueError:
            raise HTTPException(status_code=422, detail="Formato de 'desde' inválido (use AAAA-MM-DD)")

        if hasta:
            try:
                h_str = hasta.strip()
                if len(h_str) == 10:
                    h_str += " 23:59:59"
                hasta_dt = datetime.fromisoformat(h_str)
            except ValueError:
                raise HTTPException(status_code=422, detail="Formato de 'hasta' inválido (use AAAA-MM-DD)")
        else:
            hasta_dt = ahora
        duracion = max(timedelta(hours=1), hasta_dt - desde_dt)

    antes_dt = desde_dt - duracion
    desde_sql = desde_dt.strftime("%Y-%m-%d %H:%M:%S")
    hasta_sql = hasta_dt.strftime("%Y-%m-%d %H:%M:%S")
    antes_sql = antes_dt.strftime("%Y-%m-%d %H:%M:%S")

    condiciones = ["h.iniciado_en >= ?", "h.iniciado_en <= ?"]
    params: list = [desde_sql, hasta_sql]

    # Tipo de audio y filtros específicos
    if tipo == "musica":
        condiciones.append("(a.tipo_audio = 'Musica' OR (a.tipo_audio IS NULL AND h.tipo = 'musica'))")

        # Género musical (subgenero en DB)
        gen_val = (genero.strip() if genero and genero.strip() else None)
        if not gen_val and categoria and categoria.strip().startswith("genero:"):
            gen_val = categoria.strip()[7:].strip()

        if gen_val:
            condiciones.append("(LOWER(COALESCE(a.subgenero, '')) = LOWER(?) OR LOWER(COALESCE(a.categoria, '')) = LOWER(?))")
            params.extend([gen_val, gen_val])

        # Rotación (categoria en DB)
        rot_val = (rotacion.strip() if rotacion and rotacion.strip() else None)
        if not rot_val and categoria and categoria.strip().startswith("rotacion:"):
            rot_val = categoria.strip()[9:].strip()

        if rot_val:
            condiciones.append("(LOWER(COALESCE(a.categoria, '')) = LOWER(?) OR LOWER(COALESCE(a.subgenero, '')) = LOWER(?))")
            params.extend([rot_val, rot_val])

        # Búsqueda libre en categoría si se envía sin prefijo
        if not gen_val and not rot_val and categoria and categoria.strip():
            cat = categoria.strip()
            condiciones.append("(LOWER(COALESCE(a.categoria, '')) = LOWER(?) OR LOWER(COALESCE(a.subgenero, '')) = LOWER(?) OR LOWER(COALESCE(a.cat3, '')) = LOWER(?))")
            params.extend([cat, cat, cat])

    elif tipo in ("ident", "identificadores"):
        condiciones.append("(a.tipo_audio IN ('Efecto', 'Sweeper', 'Tips') OR h.tipo IN ('efecto', 'sweeper', 'tips'))")
        
        # Tipo base (Efecto / Sweeper / Tips)
        t_base = subtipo or genero
        if not t_base and categoria and categoria.strip().startswith("tipo:"):
            t_base = categoria.strip()[5:].strip()

        if t_base and t_base.strip() in ('Efecto', 'Sweeper', 'Tips'):
            condiciones.append("a.tipo_audio = ?")
            params.append(t_base.strip())

        # Categoría de estación
        cat_est = rotacion
        if not cat_est and categoria and not categoria.strip().startswith("tipo:"):
            cat_est = categoria.strip()

        if cat_est and cat_est.strip():
            condiciones.append("LOWER(COALESCE(a.categoria_ident, '')) = LOWER(?)")
            params.append(cat_est.strip())

    elif tipo == "pauta":
        condiciones.append("(a.tipo_audio = 'Cuña' OR h.tipo = 'pauta')")
        cliente_val = genero or categoria
        if cliente_val and cliente_val.strip():
            cat = cliente_val.strip()
            condiciones.append("(LOWER(COALESCE(a.artista, '')) = LOWER(?) OR LOWER(COALESCE(a.subgenero, '')) = LOWER(?))")
            params.extend([cat, cat])

    elif tipo == "todo":
        condiciones.append("COALESCE(a.ambito, '') != 'cartuchera'")

    limite_real = max(1, min(limit, 500))
    where_clausula = " AND ".join(condiciones)

    rows = conn.execute(
        f"""
        SELECT a.id, a.titulo, a.artista, a.tipo_audio, a.duracion,
               COALESCE(a.categoria, '') AS cat_genero,
               COALESCE(a.subgenero, '') AS cat_rotacion,
               COALESCE(a.categoria_ident, '') AS categoria_ident,
               COUNT(*) AS toques, MAX(h.iniciado_en) AS ultima_emision
        FROM historial_emision h
        JOIN audios a ON a.id = h.audio_id
        WHERE {where_clausula}
        GROUP BY a.id
        ORDER BY toques DESC, ultima_emision DESC
        LIMIT ?
        """,
        (*params, limite_real),
    ).fetchall()

    ids = [r["id"] for r in rows]
    previos = {}
    if ids:
        ph_ids = ",".join("?" * len(ids))
        previos = dict(conn.execute(
            f"SELECT audio_id, COUNT(*) FROM historial_emision WHERE iniciado_en >= ? AND iniciado_en < ?"
            f" AND audio_id IN ({ph_ids}) GROUP BY audio_id",
            (antes_sql, desde_sql, *ids)).fetchall())

    resultado = []
    for r in rows:
        t_audio = r["tipo_audio"] or "Musica"
        if t_audio in ("Efecto", "Sweeper", "Tips"):
            cat_display = r["categoria_ident"] or ("Jingle" if t_audio == "Efecto" else t_audio)
        elif t_audio == "Cuña":
            cat_display = r["cat_rotacion"] or "Comercial"
        else:
            cat_display = r["cat_genero"] or r["cat_rotacion"] or "Música"

        resultado.append({
            "id": r["id"],
            "tipo": _tipo_reporte(t_audio),
            "tipo_audio": t_audio,
            "titulo": r["titulo"],
            "artista": r["artista"],
            "categoria": cat_display,
            "toques": r["toques"],
            "previos": previos.get(r["id"], 0),
            "duracion": r["duracion"] or 0,
            "ultima_emision": r["ultima_emision"],
            "audio_url": f"/api/v1/audios/{r['id']}/stream",
        })

    if formato == "csv":
        filas_csv = [{
            "Posición": idx + 1,
            "Tipo": x["tipo"],
            "Título": x["titulo"],
            "Artista": x["artista"],
            "Categoría": x["categoria"],
            "Toques": x["toques"],
            "Período Anterior": x["previos"],
            "Duración": f"{x['duracion'] // 60}:{x['duracion'] % 60:02d}",
            "Última Emisión": x["ultima_emision"] or "",
        } for idx, x in enumerate(resultado)]
        nombre_arch = f"Top_Reproducidos_{tipo}_{period}.csv"
        return _csv(nombre_arch, filas_csv)

    return resultado


@router.get("/api/v1/stats/top40/forgotten", summary="Música que no suena hace tiempo")
def olvidados(dias: int = 30, limit: int = 100, conn: MariaDBConnection = Depends(get_db)):
    """Música activa sin emitirse en `dias` días (o nunca emitida)."""
    rows = conn.execute(
        """
        SELECT id, titulo, artista, tipo_audio, ultima_reproduccion, created_at,
               CAST(TIMESTAMPDIFF(SECOND, COALESCE(ultima_reproduccion, created_at), NOW()) / 86400 AS SIGNED) AS dias
        FROM audios
        WHERE activo = 1 AND tipo_audio = 'Musica'
          AND TIMESTAMPDIFF(SECOND, COALESCE(ultima_reproduccion, created_at), NOW()) / 86400.0 >= ?
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
def detalle(audio_id: int, conn: MariaDBConnection = Depends(get_db)):
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
        " AND iniciado_en >= DATE_SUB(NOW(), INTERVAL 30 DAY)", (audio_id,)
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
    conn: MariaDBConnection = Depends(get_db),
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
                   conn: MariaDBConnection = Depends(get_db)):
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
                  formato: str = "json", conn: MariaDBConnection = Depends(get_db)):
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
                hasta: Optional[str] = None, conn: MariaDBConnection = Depends(get_db)):
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
    except DualError:
        emisora = "ÓNIX FM"

    bloques, tot_prog, tot_emit = [], 0, 0
    for p in pautas:
        r = _cumplimiento_pauta(conn, p, d_desde, d_hasta)
        emis = conn.execute(
            "SELECT iniciado_en, audio_id FROM historial_emision"
            " WHERE pauta_id = ? AND iniciado_en >= ? AND iniciado_en < ? ORDER BY iniciado_en",
            (p["id"], d_desde.isoformat(), (d_hasta + timedelta(days=1)).isoformat())).fetchall()
        if not (r["programadas"] or emis):
            continue
        tot_prog += r["programadas"]
        tot_emit += len(emis)
        # Spot con varias versiones (A, B, C…): qué versión salió en cada hora
        versiones = conn.execute(
            "SELECT a.id, a.titulo FROM pauta_audios pa JOIN audios a ON a.id = pa.audio_id"
            " WHERE pa.pauta_id = ? ORDER BY pa.orden", (p["id"],)).fetchall()
        letra = {v["id"]: chr(65 + i) for i, v in enumerate(versiones)} if len(versiones) > 1 else {}
        por_dia: dict[str, list[str]] = {}
        cuenta: dict[str, int] = {}
        for t, aid in emis:
            marca = letra.get(aid, "?") if letra else ""
            if letra:
                cuenta[marca] = cuenta.get(marca, 0) + 1
            por_dia.setdefault(t[:10], []).append(f"{t[11:16]}<sup>{marca}</sup>" if marca else t[11:16])
        filas = "".join(
            f"<tr><td>{_DIAS[date.fromisoformat(d).weekday()]} {d[8:10]}/{d[5:7]}/{d[:4]}</td>"
            f"<td class='n'>{len(hs)}</td><td>{' · '.join(hs)}</td></tr>"
            for d, hs in sorted(por_dia.items()))
        por_version = ""
        if letra:
            por_version = ("<table class='ver'><thead><tr><th>Versión</th><th>Audio</th><th>Salidas</th></tr></thead><tbody>" +
                           "".join(f"<tr><td class='n'>{letra[v['id']]}</td><td>{_e(v['titulo'])}</td>"
                                   f"<td class='n'>{cuenta.get(letra[v['id']], 0)}</td></tr>" for v in versiones) +
                           (f"<tr><td class='n'>?</td><td>Otra versión (ya no está en el spot)</td><td class='n'>{cuenta['?']}</td></tr>"
                            if cuenta.get("?") else "") + "</tbody></table>")
        # Mismo conteo que el total: salidas reales del período frente a lo contratado
        pct = f"{min(100, 100 * len(emis) / r['programadas']):.0f} %" if r["programadas"] else "—"
        bloques.append(f"""
        <section>
          <h2>{_e(p['audio_nombre'] or 'Spot')}</h2>
          <p class="meta">Contrato: {_e(p['contrato_id'] or '—')} · Vigencia: {_e(p['fecha_inicio'] or '—')} a {_e(p['fecha_fin'] or 'sin fin')}
             · {_e(p['menciones_diarias'] or '—')} menciones por día</p>
          <div class="kpis"><div><b>{r['programadas']}</b>Contratadas en el período</div>
            <div><b>{len(emis)}</b>Emitidas</div><div><b>{pct}</b>Cumplimiento</div></div>
          {por_version}
          {f'<table><thead><tr><th>Día</th><th>Salidas</th><th>Horas al aire{" (la letra es la versión)" if letra else ""}</th></tr></thead><tbody>{filas}</tbody></table>'
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
  table.ver {{ width: auto; min-width: 60%; margin: 8px 0; }}
  sup {{ color: #555; font-weight: 700; margin-left: 1px; }}
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
def riesgo(conn: MariaDBConnection = Depends(get_db)):
    return {"data": pautas_en_riesgo(conn)}


# ═══════════════════════════════════════════════════════════════════════════
# Análisis: música por hora, artistas más sonados, pautas por mes
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/stats/analisis", summary="Música por hora, artistas y cumplimiento de pautas")
def analisis(period: str = "week", conn: MariaDBConnection = Depends(get_db)):
    dias = PERIODOS.get(period)
    if dias is None:
        raise HTTPException(status_code=422, detail="period debe ser day, week o month")
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    horas = [0] * 24
    for h, n in conn.execute(
            "SELECT CAST(SUBSTR(iniciado_en, 12, 2) AS SIGNED), COUNT(*) FROM historial_emision"
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
def rotacion(period: str = "week", conn: MariaDBConnection = Depends(get_db)):
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
def quemadas(dias: int = 7, factor: float = 2.0, minimo: int = 5, conn: MariaDBConnection = Depends(get_db)):
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
def dia_al_aire(fecha: Optional[str] = None, conn: MariaDBConnection = Depends(get_db)):
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


# ═══════════════════════════════════════════════════════════════════════════
# Exportar a Excel: un libro con todas las vistas de Estadísticas
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/v1/stats/exportar", summary="Libro de Excel con las estadísticas")
def exportar_excel(period: str = "week", fecha: Optional[str] = None,
                   conn: MariaDBConnection = Depends(get_db)):
    """
    Una hoja por vista: Top, Olvido, Rotación, Quemadas, Artistas, Música por
    hora, Cumplimiento por cliente y el reloj del día elegido.
    """
    from backend.api import xlsx
    nombre_periodo = {"day": "hoy", "week": "semana", "month": "mes"}.get(period)
    if nombre_periodo is None:
        raise HTTPException(status_code=422, detail="period debe ser day, week o month")
    hojas = []

    top = top40(period=period, limit=200, tipo="musica", conn=conn)
    hojas.append((f"Top música ({nombre_periodo})", ["#", "Título", "Artista", "Género", "Toques", "Período anterior", "Última emisión"],
                  [[i + 1, t["titulo"], t["artista"], t["categoria"], t["toques"], t["previos"],
                    (t["ultima_emision"] or "")[:16]] for i, t in enumerate(top)]))
    topc = top40(period=period, limit=200, tipo="pauta", conn=conn)
    hojas.append((f"Top cuñas ({nombre_periodo})", ["#", "Cuña", "Anunciante", "Toques", "Período anterior"],
                  [[i + 1, t["titulo"], t["artista"], t["toques"], t["previos"]] for i, t in enumerate(topc)]))

    olv = olvidados(dias=30, limit=1000, conn=conn)
    hojas.append(("Olvido (30 días)", ["Título", "Artista", "Días sin sonar", "Última emisión", "Nunca sonó"],
                  [[o["titulo"], o["artista"], o["dias_sin_sonar"], (o["ultima_emision"] or "")[:16], o["nunca_emitido"]] for o in olv]))

    rot = rotacion(period=period, conn=conn)
    hojas.append((f"Rotación ({nombre_periodo})", ["Rotación", "Canciones", "Toques", "Promedio por canción", "Máximo", "Sin sonar", "% real", "% planeado"],
                  [[f["rotacion"], f["canciones"], f["toques"], f["promedio"], f["maximo"], f["sin_sonar"], f["pct_real"],
                    f["pct_planeado"]] for f in rot["data"]]))
    qm = quemadas(dias=7, factor=2.0, minimo=5, conn=conn)["data"]
    hojas.append(("Quemadas (7 días)", ["Título", "Artista", "Rotación", "Toques", "Promedio de su rotación", "Veces el promedio"],
                  [[q["titulo"], q["artista"], q["rotacion"], q["toques"], q["promedio_rotacion"], q["veces_promedio"]] for q in qm]))

    an = analisis(period=period, conn=conn)
    hojas.append((f"Artistas ({nombre_periodo})", ["Artista", "Toques", "Canciones distintas"],
                  [[a["artista"], a["toques"], a["canciones"]] for a in an["artistas"]]))
    hojas.append((f"Por hora ({nombre_periodo})", ["Hora", "Canciones"], [[f"{h:02d}:00", n] for h, n in enumerate(an["horas"])]))
    hojas.append(("Pautas por mes", ["Mes", "Contratadas", "Emitidas", "% cumplimiento"],
                  [[m["mes"], m["programadas"], m["emitidas"], m["pct"]] for m in an["pautas_mes"]]))

    pautas = reporte_pautas(desde=None, hasta=None, cliente=None, formato="json", conn=conn)
    por: dict = {}
    for p in pautas["data"]:
        c = por.setdefault(p["cliente"], [0, 0])
        c[0] += p["programadas"]
        c[1] += min(p["emitidas"], p["programadas"])
    hojas.append((f"Clientes {pautas['desde'][:7]}", ["Cliente", "Contratadas", "Emitidas", "% cumplimiento"],
                  sorted([[k, v[0], v[1], round(100 * v[1] / v[0], 1) if v[0] else None] for k, v in por.items()],
                         key=lambda r: (r[3] is None, r[3]))))

    dia = dia_al_aire(fecha=fecha, conn=conn)
    hojas.append((f"Día {dia['fecha']}", ["Hora", "Título / cliente", "Artista / spot", "Tipo", "Rotación", "Origen", "Tanda", "Duración (s)"],
                  [[it["hora"], it["cliente"] or it["titulo"], it["artista"] if it["tipo"] != "cuña" else it["titulo"],
                    it["tipo"], it["rotacion"], it["origen"], it["tanda"], it["duracion"]]
                   for h in dia["horas"] for it in h["items"]]))

    datos = xlsx.libro(hojas)
    return Response(datos, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{xlsx.nombre_archivo("estadisticas_onix")}"'})
