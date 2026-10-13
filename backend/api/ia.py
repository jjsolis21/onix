"""
ia.py — Ónix FM
Claves de IA (Gemini y compatibles) con orden de respaldo. Solo el administrador las ve o cambia.
La clave se guarda en la base de datos de la emisora y NUNCA se devuelve: la lista muestra solo
los últimos 4 caracteres.

    GET    /api/v1/ia/proveedores               → modelos y direcciones sugeridas
    GET    /api/v1/ia/claves                    → lista en orden de uso
    POST   /api/v1/ia/claves                    → {nombre, proveedor, modelo, base_url, clave, activo}
    PUT    /api/v1/ia/claves/{id}               → igual; clave vacía = conservar la actual
    DELETE /api/v1/ia/claves/{id}
    POST   /api/v1/ia/claves/{id}/mover         → {dir: -1 | 1}
    POST   /api/v1/ia/claves/{id}/probar        → {ok, ms | error}
"""

import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.ia import proveedores
from backend.paths import DB_PATH

router = APIRouter(tags=["IA"])


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


class ClaveIn(BaseModel):
    nombre:    str = Field(..., min_length=1, max_length=60)
    proveedor: str
    modelo:    str = Field(default="", max_length=120)
    base_url:  str = Field(default="", max_length=300)
    clave:     str = Field(default="", max_length=400)
    activo:    bool = True


def _publica(r) -> dict:
    k = r["clave"] or ""
    return {"id": r["id"], "nombre": r["nombre"], "proveedor": r["proveedor"], "modelo": r["modelo"],
            "base_url": r["base_url"], "activo": bool(r["activo"]), "orden": r["orden"],
            "clave_fin": ("•••• " + k[-4:]) if len(k) >= 8 else "••••",
            "ultimo_ok": r["ultimo_ok"], "ultimo_error": r["ultimo_error"], "ultimo_error_en": r["ultimo_error_en"]}


def _validar(b: ClaveIn):
    if b.proveedor not in proveedores.PROVEEDORES:
        raise HTTPException(status_code=422, detail="Proveedor no válido")
    if b.proveedor not in ("gemini",) and b.base_url and not b.base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="La dirección base debe empezar con https://")


@router.get("/api/v1/ia/proveedores", summary="Proveedores de IA y valores sugeridos")
def listar_proveedores():
    return [{"clave": k, **v} for k, v in proveedores.PROVEEDORES.items()]


@router.get("/api/v1/ia/claves", summary="Claves de IA en orden de uso (sin mostrar la clave)")
def listar():
    c = _conn()
    try:
        return [_publica(r) for r in c.execute("SELECT * FROM ia_claves ORDER BY orden, id")]
    finally:
        c.close()


@router.post("/api/v1/ia/claves", status_code=201, summary="Agregar una clave de IA (queda al final del orden)")
def agregar(b: ClaveIn):
    _validar(b)
    if not b.clave.strip():
        raise HTTPException(status_code=422, detail="Pega la clave")
    c = _conn()
    try:
        orden = (c.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM ia_claves").fetchone()[0])
        cur = c.execute("INSERT INTO ia_claves (nombre, proveedor, modelo, base_url, clave, orden, activo)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (b.nombre.strip(), b.proveedor, b.modelo.strip(), b.base_url.strip(), b.clave.strip(), orden, int(b.activo)))
        c.commit()
        return _publica(c.execute("SELECT * FROM ia_claves WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        c.close()


@router.put("/api/v1/ia/claves/{cid}", summary="Editar una clave de IA (clave vacía = conservar)")
def editar(cid: int, b: ClaveIn):
    _validar(b)
    c = _conn()
    try:
        if not c.execute("SELECT 1 FROM ia_claves WHERE id = ?", (cid,)).fetchone():
            raise HTTPException(status_code=404, detail="Clave no encontrada")
        campos = "nombre = ?, proveedor = ?, modelo = ?, base_url = ?, activo = ?"
        vals = [b.nombre.strip(), b.proveedor, b.modelo.strip(), b.base_url.strip(), int(b.activo)]
        if b.clave.strip():
            campos += ", clave = ?, ultimo_error = NULL, ultimo_error_en = NULL"
            vals.append(b.clave.strip())
        c.execute(f"UPDATE ia_claves SET {campos} WHERE id = ?", vals + [cid])
        c.commit()
        proveedores._fallos.pop(cid, None)
        return _publica(c.execute("SELECT * FROM ia_claves WHERE id = ?", (cid,)).fetchone())
    finally:
        c.close()


@router.delete("/api/v1/ia/claves/{cid}", summary="Quitar una clave de IA")
def quitar(cid: int):
    c = _conn()
    try:
        if not c.execute("DELETE FROM ia_claves WHERE id = ?", (cid,)).rowcount:
            raise HTTPException(status_code=404, detail="Clave no encontrada")
        c.commit()
        proveedores._fallos.pop(cid, None)
        return {"ok": True}
    finally:
        c.close()


class MoverIn(BaseModel):
    dir: int = Field(..., ge=-1, le=1)


@router.post("/api/v1/ia/claves/{cid}/mover", summary="Subir o bajar en el orden de respaldo")
def mover(cid: int, b: MoverIn):
    c = _conn()
    try:
        ids = [r[0] for r in c.execute("SELECT id FROM ia_claves ORDER BY orden, id")]
        if cid not in ids:
            raise HTTPException(status_code=404, detail="Clave no encontrada")
        i, j = ids.index(cid), ids.index(cid) + b.dir
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        for n, x in enumerate(ids):
            c.execute("UPDATE ia_claves SET orden = ? WHERE id = ?", (n, x))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.post("/api/v1/ia/claves/{cid}/probar", summary="Probar esta clave con una pregunta mínima")
def probar(cid: int):
    c = _conn()
    try:
        r = c.execute("SELECT * FROM ia_claves WHERE id = ?", (cid,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Clave no encontrada")
        k = dict(r)
        res = proveedores.probar(k)
        proveedores._marcar(c, k, None if res["ok"] else res["error"])
        return res
    finally:
        c.close()
