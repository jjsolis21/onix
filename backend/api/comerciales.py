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
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.paths import DB_PATH, resolve_media

router = APIRouter(tags=["Comerciales"])

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
