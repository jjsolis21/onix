"""
comerciales.py — Ónix FM
Apoyo de la sección Comerciales (como «Config. de comerciales» y «Chequeo de integridad» de Jazler SoHo):

    GET    /api/v1/comerciales/catalogo/{tipo}              clientes | agencias, con cuántos spots usa cada uno
    POST   /api/v1/comerciales/catalogo/{tipo}              crear
    PUT    /api/v1/comerciales/catalogo/{tipo}/{id}         editar (si cambia el nombre, se actualizan sus spots)
    DELETE /api/v1/comerciales/catalogo/{tipo}/{id}         quitar del catálogo (los spots conservan el texto)
    POST   /api/v1/comerciales/catalogo/{tipo}/importar     traer los nombres que ya usan los spots
    GET    /api/v1/comerciales/chequeo                      spots con problemas (audio perdido, sin horarios, vencidos…)
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.paths import DB_PATH, resolve_media

router = APIRouter(tags=["Comerciales"])
recargar_motor = None      # api_server lo conecta: el motor rehace el reparto de la hora
replanificar = None        # api_server lo conecta: el motor rehace su plan (engine.plan_regenerar)

TIPOS = {"clientes": ("comerciales_clientes", "cliente"), "agencias": ("comerciales_agencias", "agencia")}

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS comerciales_clientes (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
    contacto TEXT NOT NULL DEFAULT '', telefono TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
    notas TEXT NOT NULL DEFAULT '', creado_en TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS comerciales_agencias (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
    contacto TEXT NOT NULL DEFAULT '', telefono TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
    notas TEXT NOT NULL DEFAULT '', creado_en TEXT NOT NULL DEFAULT (datetime('now')));
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_ESQUEMA)
    return c


def _tipo(tipo: str):
    if tipo not in TIPOS:
        raise HTTPException(status_code=404, detail="Tipo desconocido: usa clientes o agencias")
    return TIPOS[tipo]


class EntradaIn(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    contacto: str = Field(default="", max_length=120)
    telefono: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=120)
    notas: str = Field(default="", max_length=500)


def _limpia(b: EntradaIn) -> dict:
    d = {k: " ".join(str(v).split()) for k, v in b.model_dump().items()}
    d["nombre"] = d["nombre"].upper()            # los spots guardan cliente y agencia en mayúsculas
    if not d["nombre"]:
        raise HTTPException(status_code=422, detail="Escribe un nombre")
    return d


@router.get("/api/v1/comerciales/catalogo/{tipo}", summary="Catálogo de clientes o agencias")
def listar(tipo: str):
    tabla, col = _tipo(tipo)
    c = _conn()
    try:
        usos = {(r[0] or "").upper(): r[1] for r in c.execute(
            f"SELECT {col}, COUNT(*) FROM pautas WHERE activo = 1 AND COALESCE({col}, '') != '' GROUP BY UPPER({col})")}
        filas = [dict(r) for r in c.execute(f"SELECT * FROM {tabla} ORDER BY nombre")]
        for f in filas:
            f["spots"] = usos.get(f["nombre"].upper(), 0)
        enlazados = {f["nombre"].upper() for f in filas}
        sin_catalogo = sorted(n for n in usos if n not in enlazados)
        return {"items": filas, "sin_catalogo": sin_catalogo}
    finally:
        c.close()


@router.post("/api/v1/comerciales/catalogo/{tipo}", status_code=201, summary="Agregar al catálogo")
def crear(tipo: str, b: EntradaIn):
    tabla, _ = _tipo(tipo)
    d = _limpia(b)
    c = _conn()
    try:
        try:
            cur = c.execute(f"INSERT INTO {tabla} (nombre, contacto, telefono, email, notas) VALUES (?, ?, ?, ?, ?)",
                            (d["nombre"], d["contacto"], d["telefono"], d["email"], d["notas"]))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"Ya existe «{d['nombre']}»")
        c.commit()
        return {"id": cur.lastrowid, "nombre": d["nombre"]}
    finally:
        c.close()


@router.put("/api/v1/comerciales/catalogo/{tipo}/{rid}", summary="Editar; si cambia el nombre se actualizan sus spots")
def editar(tipo: str, rid: int, b: EntradaIn):
    tabla, col = _tipo(tipo)
    d = _limpia(b)
    c = _conn()
    try:
        antes = c.execute(f"SELECT nombre FROM {tabla} WHERE id = ?", (rid,)).fetchone()
        if not antes:
            raise HTTPException(status_code=404, detail="No existe")
        try:
            c.execute(f"UPDATE {tabla} SET nombre = ?, contacto = ?, telefono = ?, email = ?, notas = ? WHERE id = ?",
                      (d["nombre"], d["contacto"], d["telefono"], d["email"], d["notas"], rid))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"Ya existe «{d['nombre']}»")
        renombrados = 0
        if antes["nombre"].upper() != d["nombre"]:
            renombrados = c.execute(
                f"UPDATE pautas SET {col} = ?, updated_at = datetime('now') WHERE UPPER({col}) = UPPER(?)",
                (d["nombre"], antes["nombre"])).rowcount
        c.commit()
        return {"ok": True, "spots_actualizados": renombrados}
    finally:
        c.close()


@router.delete("/api/v1/comerciales/catalogo/{tipo}/{rid}", summary="Quitar del catálogo (los spots conservan el texto)")
def quitar(tipo: str, rid: int):
    tabla, _ = _tipo(tipo)
    c = _conn()
    try:
        if not c.execute(f"DELETE FROM {tabla} WHERE id = ?", (rid,)).rowcount:
            raise HTTPException(status_code=404, detail="No existe")
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.post("/api/v1/comerciales/catalogo/{tipo}/importar", summary="Traer los nombres que ya usan los spots")
def importar(tipo: str):
    tabla, col = _tipo(tipo)
    c = _conn()
    try:
        nombres = [r[0] for r in c.execute(
            f"SELECT DISTINCT UPPER(TRIM({col})) FROM pautas WHERE COALESCE(TRIM({col}), '') != ''")]
        n = 0
        for nombre in nombres:
            n += c.execute(f"INSERT OR IGNORE INTO {tabla} (nombre) VALUES (?)", (nombre,)).rowcount
        c.commit()
        return {"agregados": n, "total": len(nombres)}
    finally:
        c.close()


# ── Chequeo de integridad ────────────────────────────────────────────────────

def _horarios(matriz) -> int:
    if isinstance(matriz, str):
        try:
            matriz = json.loads(matriz or "{}")
        except ValueError:
            matriz = {}
    return sum(1 for k, v in (matriz or {}).items() if k != "__meta" and v)


@router.get("/api/v1/comerciales/chequeo", summary="Spots con problemas que impedirían que suenen bien")
def chequeo():
    hoy = date.today().isoformat()
    c = _conn()
    try:
        hay_versiones = c.execute("SELECT 1 FROM sqlite_master WHERE name = 'pauta_audios'").fetchone() is not None
        problemas = []

        def poner(p, nivel, codigo, texto):
            problemas.append({"pauta_id": p["id"], "cliente": p["cliente"] or "(sin cliente)",
                              "spot": p["titulo"] or p["audio_nombre"] or f"audio {p['audio_id']}",
                              "nivel": nivel, "codigo": codigo, "texto": texto})

        filas = c.execute(
            "SELECT p.*, a.titulo, a.archivo_path, a.activo AS audio_activo, a.duracion, a.tipo_audio"
            " FROM pautas p LEFT JOIN audios a ON a.id = p.audio_id WHERE p.activo = 1 ORDER BY p.cliente, p.id").fetchall()
        for p in filas:
            if p["archivo_path"] is None:
                poner(p, "error", "audio_perdido", "El audio del spot ya no existe en la Biblioteca.")
            else:
                if not p["audio_activo"]:
                    poner(p, "error", "audio_inactivo", "El audio está en la Papelera: el spot no sonará.")
                elif not resolve_media(p["archivo_path"]).is_file():
                    poner(p, "error", "archivo_perdido", "No se encuentra el archivo de audio en el disco.")
                if not (p["duracion"] or 0):
                    poner(p, "aviso", "sin_duracion", "El audio no tiene duración registrada: las tandas no pueden medirlo.")
                if p["tipo_audio"] and p["tipo_audio"] != "Cuña":
                    poner(p, "aviso", "tipo", f"El audio está marcado como «{p['tipo_audio']}», no como Cuña.")
            if not _horarios(p["matriz"]):
                poner(p, "error", "sin_horarios", "No tiene horarios marcados: nunca saldrá al aire.")
            ini, fin = (p["fecha_inicio"] or ""), (p["fecha_fin"] or "")
            if ini and fin and fin < ini:
                poner(p, "error", "fechas", f"La fecha de fin ({fin}) es anterior a la de inicio ({ini}).")
            elif fin and fin < hoy:
                poner(p, "aviso", "vencido", f"Venció el {fin} y sigue activo: ya no sale, pero ocupa un lugar.")
            if not (p["rubro"] or "").strip():
                poner(p, "aviso", "sin_rubro", "Sin rubro: no se puede separar de otros anunciantes del mismo rubro.")
            if hay_versiones:
                for v in c.execute(
                        "SELECT a.titulo, a.archivo_path, a.activo FROM pauta_audios pa JOIN audios a ON a.id = pa.audio_id"
                        " WHERE pa.pauta_id = ?", (p["id"],)):
                    if not v["activo"] or not resolve_media(v["archivo_path"]).is_file():
                        poner(p, "error", "version_perdida", f"La versión «{v['titulo']}» no tiene audio disponible.")
        errores = sum(1 for x in problemas if x["nivel"] == "error")
        return {"revisados": len(filas), "errores": errores, "avisos": len(problemas) - errores,
                "problemas": sorted(problemas, key=lambda x: (x["nivel"] != "error", x["cliente"], x["spot"]))}
    finally:
        c.close()


# ── Tandas del día (vista de solo lectura de lo que el motor va a sacar) ────

@router.get("/api/v1/comerciales/tandas-dia", summary="Qué sale en cada tanda de un día, bloque por bloque")
def tandas_dia(fecha: Optional[str] = None):
    from backend.audio.audio_engine import cargar_grilla, leer_ajustes, parse_minutos, tandas_de_hora
    from backend.audio.planner import Planner
    try:
        dia = date.fromisoformat(fecha) if fecha else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha inválida (AAAA-MM-DD)")
    pl = Planner(DB_PATH)
    c = _conn()
    try:
        cfg = {r["clave"]: r["valor"] for r in c.execute("SELECT clave, valor FROM config")}
        base = (parse_minutos(cfg.get("tandas_minutos", "15,45")),
                parse_minutos(cfg["tandas_inmediatas"]) if (cfg.get("tandas_inmediatas") or "").strip() else [],
                parse_minutos(cfg["tandas_cierre"]) if (cfg.get("tandas_cierre") or "").strip() else [])
        grilla = cargar_grilla(cfg.get("tandas_grilla", ""))
        maximo = int(float(cfg.get("tanda_max_seg") or 0))
        horas = []
        for h in range(24):
            ini = datetime(dia.year, dia.month, dia.day, h)
            minutos, inm, cie = tandas_de_hora(grilla, (dia.weekday() + 1) % 7, h, *base)
            bloques = []
            for m in minutos:
                cunas = pl.lista_tanda(c, ini, m, minutos)
                dur = round(sum(x["dur"] for x in cunas))
                bloques.append({
                    "minuto": m, "inmediata": m in inm, "cierre": m in cie, "duracion": dur,
                    "excede": bool(maximo and dur > maximo),
                    "cunas": [{"pauta_id": x["pauta_id"], "cliente": x["cliente"] or "", "spot": x["spot"] or "",
                               "rubro": x["rubro"] or "", "seccion": x["seccion"], "comercial": x["comercial"],
                               "fijada": bool(x.get("fijada")),
                               "duracion": round(x["dur"])} for x in cunas]})
            ajustes, quitadas = leer_ajustes(c, dia.isoformat(), h)
            nombres = {r[0]: r[1] for r in c.execute(
                f"SELECT p.id, COALESCE(NULLIF(p.cliente, ''), a.titulo) FROM pautas p LEFT JOIN audios a ON a.id = p.audio_id"
                f" WHERE p.id IN ({','.join('?' * len(quitadas))})", list(quitadas))} if quitadas else {}
            horas.append({"hora": h, "bloques": bloques, "sin_tandas": not minutos,
                          "ajustada": bool(ajustes or quitadas),
                          "quitadas": [{"pauta_id": q, "cliente": nombres.get(q, f"pauta {q}")} for q in sorted(quitadas)]})
        return {"fecha": dia.isoformat(), "maximo": maximo, "horas": horas}
    finally:
        c.close()


class AjusteIn(BaseModel):
    fecha: str
    hora: int = Field(..., ge=0, le=23)
    bloques: dict[int, list[int]] = Field(default_factory=dict)
    quitadas: list[int] = Field(default_factory=list)


def _fecha(txt: str) -> str:
    try:
        return date.fromisoformat(txt).isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail="Fecha inválida (AAAA-MM-DD)")


def _avisar_motor():
    if recargar_motor:
        try:
            recargar_motor()
        except Exception:
            pass


@router.put("/api/v1/comerciales/tandas-dia/ajuste", summary="Orden a mano de las tandas de una hora")
def guardar_ajuste(b: AjusteIn):
    """Reemplaza los ajustes de esa hora: qué cuñas van en cada bloque y en qué orden, y cuáles no salen ese día."""
    from backend.audio.audio_engine import leer_ajustes
    fecha = _fecha(b.fecha)
    if any(not 0 <= m <= 59 for m in b.bloques):
        raise HTTPException(status_code=422, detail="Minuto de tanda inválido")
    c = _conn()
    try:
        leer_ajustes(c, fecha, b.hora)          # crea las tablas si faltan
        c.execute("DELETE FROM tanda_ajustes WHERE fecha = ? AND hora = ?", (fecha, b.hora))
        c.execute("DELETE FROM tanda_quitadas WHERE fecha = ? AND hora = ?", (fecha, b.hora))
        quitadas = set(b.quitadas)
        for m, ids in b.bloques.items():
            ids = [i for i in dict.fromkeys(ids) if i not in quitadas]
            c.execute("INSERT INTO tanda_ajustes (fecha, hora, minuto, ids) VALUES (?, ?, ?, ?)",
                      (fecha, b.hora, m, json.dumps(ids)))
        for q in quitadas:
            c.execute("INSERT INTO tanda_quitadas (fecha, hora, pauta_id) VALUES (?, ?, ?)", (fecha, b.hora, q))
        c.commit()
    finally:
        c.close()
    _avisar_motor()
    return {"ok": True}


@router.delete("/api/v1/comerciales/tandas-dia/ajuste", summary="Volver al reparto automático en esa hora")
def borrar_ajuste(fecha: str, hora: int):
    from backend.audio.audio_engine import leer_ajustes
    fecha = _fecha(fecha)
    c = _conn()
    try:
        leer_ajustes(c, fecha, hora)
        c.execute("DELETE FROM tanda_ajustes WHERE fecha = ? AND hora = ?", (fecha, hora))
        c.execute("DELETE FROM tanda_quitadas WHERE fecha = ? AND hora = ?", (fecha, hora))
        c.commit()
    finally:
        c.close()
    _avisar_motor()
    return {"ok": True}


# ── Bloques publicitarios: una lista de cuñas elegidas, programada como evento de Relojes ──
# Se guardan como un reloj (filas con audio fijo) + un evento a hora fija (reloj_eventos), así que
# aparecen en Relojes › Eventos y el planificador los trata igual que cualquier otro evento.

COLOR_BLOQUE = "#e6a800"
PREFIJO = "Bloque: "


class BloqueIn(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=70)
    audios: list[int] = Field(..., min_length=1, max_length=40)       # ids de audios (cuñas), en orden
    modo: str = Field("empieza", pattern="^(empieza|inmediato|termina)$")
    hora: str = Field(..., pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    dias: list[int] = Field(default_factory=lambda: list(range(7)))   # 0=Dom … 6=Sáb
    fecha_inicio: str = Field("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    fecha_fin: str = Field("", pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    cortar: bool = False       # solo con «termina»: además corta la canción para terminar exacto


def _duracion_audios(c, ids: list[int]):
    filas = {r["id"]: r for r in c.execute(
        f"SELECT id, titulo, duracion, activo FROM audios WHERE id IN ({','.join('?' * len(ids))})", ids)}
    faltan = [i for i in ids if i not in filas or not filas[i]["activo"]]
    if faltan:
        raise HTTPException(status_code=422, detail=f"Estas cuñas no existen o están en la Papelera: {faltan}")
    return [filas[i] for i in ids], round(sum(float(filas[i]["duracion"] or 0) for i in ids))


def _hora_menos(hora: str, seg: int) -> str:
    h, m = (int(x) for x in hora.split(":"))
    t = (h * 3600 + m * 60 - seg) % 86400
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}"


def _ajustes(c, reloj_id: int, hora: str, inmediato: bool) -> dict:
    """Modo y hora tal como se eligieron (bloques viejos sin ese dato: empieza/inmediato a su hora)."""
    fila = c.execute("SELECT comentario FROM relojes WHERE id = ?", (reloj_id,)).fetchone()
    try:
        a = json.loads(fila[0]) if fila and (fila[0] or "").startswith("{") else {}
    except ValueError:
        a = {}
    return {"modo": a.get("modo") or ("inmediato" if inmediato else "empieza"),
            "hora_objetivo": a.get("hora") or hora, "cortar": bool(a.get("cortar"))}


def _replanificar():
    if replanificar:
        try:
            replanificar()
        except Exception:
            pass


@router.get("/api/v1/comerciales/cunas-activas", summary="Cuñas de la Biblioteca de comerciales, con su duración")
def cunas_activas():
    """Todo audio activo tipo Cuña (o guardado en COMERCIALES), tenga o no spot con contrato."""
    c = _conn()
    try:
        spots = {}
        for r in c.execute("SELECT audio_id, cliente, rubro FROM pautas WHERE activo = 1 ORDER BY id DESC"):
            spots.setdefault(r["audio_id"], r)
        out = []
        for r in c.execute(
                "SELECT id, titulo, archivo_path, duracion FROM audios WHERE activo = 1"
                " AND (tipo_audio = 'Cu\u00f1a' OR UPPER(COALESCE(archivo_path, '')) LIKE '%COMERCIALES%')"
                " ORDER BY titulo COLLATE NOCASE"):
            sp = spots.get(r["id"])
            out.append({"audio_id": r["id"], "spot": r["titulo"] or "", "cliente": sp["cliente"] if sp else "",
                        "rubro": sp["rubro"] if sp else "", "con_spot": sp is not None,
                        "duracion": round(float(r["duracion"] or 0))})
        return out
    finally:
        c.close()


@router.get("/api/v1/comerciales/bloques", summary="Bloques publicitarios programados")
def listar_bloques():
    c = _conn()
    try:
        out = []
        for e in c.execute("SELECT e.*, r.nombre AS reloj FROM reloj_eventos e JOIN relojes r ON r.id = e.reloj_id"
                           " WHERE r.nombre LIKE ? ORDER BY e.hora", (PREFIJO + "%",)).fetchall():
            filas = c.execute("SELECT f.audio_id, a.titulo, a.duracion, f.nota AS cliente FROM reloj_filas f"
                              " LEFT JOIN audios a ON a.id = f.audio_id"
                              " WHERE f.reloj_id = ? ORDER BY f.orden", (e["reloj_id"],)).fetchall()
            out.append({"evento_id": e["id"], "reloj_id": e["reloj_id"], "nombre": e["nombre"],
                        "hora": e["hora"], **_ajustes(c, e["reloj_id"], e["hora"], bool(e["inmediato"])), "dias": [int(d) for d in e["dias"].split(",") if d != ""],
                        "inmediato": bool(e["inmediato"]), "activo": bool(e["activo"]),
                        "fecha_inicio": e["fecha_inicio"], "fecha_fin": e["fecha_fin"],
                        "duracion": round(sum(float(f["duracion"] or 0) for f in filas)),
                        "cunas": [{"audio_id": f["audio_id"], "titulo": f["titulo"] or "(sin audio)", "cliente": f["cliente"] or "",
                                   "duracion": round(float(f["duracion"] or 0))} for f in filas]})
        return out
    finally:
        c.close()


def _guardar_bloque(c, b: BloqueIn, evento_id: Optional[int] = None) -> dict:
    """Crea (evento_id None) o reemplaza un bloque: su reloj con las cuñas en orden y su evento a hora fija."""
    dias = sorted({int(d) for d in b.dias})
    if not dias or any(d < 0 or d > 6 for d in dias):
        raise HTTPException(status_code=422, detail="Elige al menos un día")
    if b.fecha_inicio and b.fecha_fin and b.fecha_fin < b.fecha_inicio:
        raise HTTPException(status_code=422, detail="La fecha final es anterior a la inicial")
    filas, dur = _duracion_audios(c, b.audios)
    nombre = " ".join(b.nombre.split())
    # «termina a las HH:MM»: arranca antes según lo que dura (como las tandas al cierre)
    inicio = _hora_menos(b.hora, dur) if b.modo == "termina" else b.hora
    inmediato = b.modo == "inmediato" or (b.modo == "termina" and b.cortar)
    # lo que el operador eligió, para poder editarlo después tal cual
    ajustes = json.dumps({"bloque": True, "modo": b.modo, "hora": b.hora, "cortar": b.cortar})
    try:
        if evento_id is None:
            rid = c.execute("INSERT INTO relojes (nombre, color, comentario) VALUES (?, ?, ?)",
                            (PREFIJO + nombre, COLOR_BLOQUE, ajustes)).lastrowid
        else:
            e = c.execute("SELECT e.reloj_id FROM reloj_eventos e JOIN relojes r ON r.id = e.reloj_id"
                          " WHERE e.id = ? AND r.nombre LIKE ?", (evento_id, PREFIJO + "%")).fetchone()
            if not e:
                raise HTTPException(status_code=404, detail="No existe ese bloque")
            rid = e["reloj_id"]
            c.execute("UPDATE relojes SET nombre = ?, comentario = ?, updated_at = datetime('now') WHERE id = ?",
                      (PREFIJO + nombre, ajustes, rid))
            c.execute("DELETE FROM reloj_filas WHERE reloj_id = ?", (rid,))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ya existe un bloque llamado «{nombre}»")
    for i, f in enumerate(filas):
        c.execute("INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, filtros, audio_id, nota)"
                  " VALUES (?, ?, 'Cuña', '{}', ?, ?)", (rid, i, f["id"], f["titulo"] or ""))
    valores = (nombre, inicio, ",".join(map(str, dias)), b.fecha_inicio, b.fecha_fin, int(inmediato))
    if evento_id is None:
        evento_id = c.execute(
            "INSERT INTO reloj_eventos (nombre, hora, dias, fecha_inicio, fecha_fin, inmediato, reloj_id, activo, hasta)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1, '')", (*valores, rid)).lastrowid
    else:
        c.execute("UPDATE reloj_eventos SET nombre = ?, hora = ?, dias = ?, fecha_inicio = ?, fecha_fin = ?,"
                  " inmediato = ? WHERE id = ?", (*valores, evento_id))
    c.commit()
    return {"evento_id": evento_id, "reloj_id": rid, "duracion": dur, "empieza": inicio,
            "termina": _hora_menos(inicio, -dur), "inmediato": inmediato}


@router.post("/api/v1/comerciales/bloques", status_code=201, summary="Crear un bloque publicitario programado")
def crear_bloque(b: BloqueIn):
    c = _conn()
    try:
        r = _guardar_bloque(c, b)
    finally:
        c.close()
    _replanificar()
    return r


@router.put("/api/v1/comerciales/bloques/{evento_id}", summary="Editar un bloque: cuñas, orden, hora, modo y días")
def editar_bloque(evento_id: int, b: BloqueIn):
    c = _conn()
    try:
        r = _guardar_bloque(c, b, evento_id)
    finally:
        c.close()
    _replanificar()
    return r


@router.delete("/api/v1/comerciales/bloques/{evento_id}", summary="Quitar un bloque publicitario")
def quitar_bloque(evento_id: int):
    c = _conn()
    try:
        e = c.execute("SELECT e.reloj_id FROM reloj_eventos e JOIN relojes r ON r.id = e.reloj_id"
                      " WHERE e.id = ? AND r.nombre LIKE ?", (evento_id, PREFIJO + "%")).fetchone()
        if not e:
            raise HTTPException(status_code=404, detail="No existe ese bloque")
        c.execute("DELETE FROM reloj_eventos WHERE id = ?", (evento_id,))
        c.execute("DELETE FROM relojes WHERE id = ?", (e["reloj_id"],))     # sus filas se van en cascada
        c.commit()
    finally:
        c.close()
    _replanificar()
    return {"ok": True}
