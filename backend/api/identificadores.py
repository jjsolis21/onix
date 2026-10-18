"""
identificadores.py — Ónix FM
Identificadores: jingles, sweepers y tips (Jazler SoHo los separa de la música).
Cada tipo tiene su lista de categorías (ID de emisora, Stinger, Entrada de programa…),
editable como "Editar categorías". El audio guarda la suya en audios.categoria_ident y
los relojes pueden filtrar por ella (filtro "ident").

    GET    /api/v1/ident/audios?tipo=&q=&categoria=&limit=&offset=   → tabla (con total)
    GET    /api/v1/ident/categorias                                  → por tipo, con cuántos audios usan cada una
    POST   /api/v1/ident/categorias                                  → {tipo_audio, nombre, comentario}
    PUT    /api/v1/ident/categorias/{id}                             → renombrar / comentario (el cambio llega a sus audios)
    POST   /api/v1/ident/categorias/{id}/mover                       → {dir: -1 | 1}
    DELETE /api/v1/ident/categorias/{id}?quitar=1                    → 409 si hay audios, salvo quitar=1
    PUT    /api/v1/ident/audios                                      → {ids, categoria}: asigna (o quita con null)
"""

import sqlite3
import unicodedata
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.paths import DB_PATH

router = APIRouter(tags=["Identificadores"])

TIPOS = ("Efecto", "Sweeper", "Tips")          # Efecto = Jingles


from backend.database import get_db


def _tipo(valor: str) -> str:
    for t in TIPOS:
        if t.lower() == (valor or "").strip().lower():
            return t
    raise HTTPException(status_code=422, detail=f"Tipo inválido: {valor!r}. Valores: {', '.join(TIPOS)}")


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


@router.get("/api/v1/ident/audios", summary="Tabla de identificadores")
def listar(tipo: str = "Efecto", q: Optional[str] = None, categoria: Optional[str] = None,
           limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
           conn: sqlite3.Connection = Depends(get_db)):
    sql = (" FROM audios WHERE activo = 1 AND tipo_audio = ? AND COALESCE(ambito, '') != 'cartuchera'")
    params: list = [_tipo(tipo)]
    for palabra in _plano(q or "").split():
        sql += " AND busq LIKE ? ESCAPE '\\'"
        params.append("%" + palabra.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if categoria == "__sin":
        sql += " AND COALESCE(categoria_ident, '') = ''"
    elif categoria:
        sql += " AND UPPER(COALESCE(categoria_ident, '')) = UPPER(?)"
        params.append(categoria)
    total = conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
    filas = conn.execute(
        "SELECT id, titulo, artista, duracion, cue_intro, intro, categoria_ident, tipo_audio,"
        " veces_reproducido, ultima_reproduccion" + sql + " ORDER BY artista COLLATE NOCASE, titulo COLLATE NOCASE LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    return {"total": total, "data": [dict(f) for f in filas]}


@router.get("/api/v1/ident/categorias", summary="Categorías por tipo")
def categorias(conn: sqlite3.Connection = Depends(get_db)):
    out = {t: [] for t in TIPOS}
    for r in conn.execute(
            "SELECT c.id, c.tipo_audio, c.nombre, c.comentario, c.orden,"
            " (SELECT COUNT(*) FROM audios a WHERE a.activo = 1 AND a.tipo_audio = c.tipo_audio AND COALESCE(a.ambito, '') != 'cartuchera'"
            "  AND UPPER(COALESCE(a.categoria_ident, '')) = UPPER(c.nombre)) AS usos"
            " FROM ident_categorias c ORDER BY c.tipo_audio, c.orden, c.id"):
        out.setdefault(r["tipo_audio"], []).append(dict(r))
    return out


class CategoriaIn(BaseModel):
    tipo_audio: Optional[str] = None
    nombre:     Optional[str] = Field(None, min_length=1, max_length=40)
    comentario: Optional[str] = Field(None, max_length=120)


@router.post("/api/v1/ident/categorias", status_code=201, summary="Agregar categoría")
def agregar(body: CategoriaIn, conn: sqlite3.Connection = Depends(get_db)):
    tipo, nombre = _tipo(body.tipo_audio or ""), (body.nombre or "").strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="Escribe un nombre para la categoría")
    if conn.execute("SELECT 1 FROM ident_categorias WHERE tipo_audio = ? AND nombre = ? COLLATE NOCASE", (tipo, nombre)).fetchone():
        raise HTTPException(status_code=409, detail=f"Ya existe «{nombre}» en este tipo")
    orden = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM ident_categorias WHERE tipo_audio = ?", (tipo,)).fetchone()[0]
    cur = conn.execute("INSERT INTO ident_categorias (tipo_audio, nombre, comentario, orden) VALUES (?, ?, ?, ?)",
                       (tipo, nombre, (body.comentario or "").strip(), orden))
    return {"id": cur.lastrowid, "tipo_audio": tipo, "nombre": nombre}


def _cat(conn, cid: int):
    r = conn.execute("SELECT * FROM ident_categorias WHERE id = ?", (cid,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")
    return r


@router.put("/api/v1/ident/categorias/{cid}", summary="Renombrar o comentar una categoría")
def editar(cid: int, body: CategoriaIn, conn: sqlite3.Connection = Depends(get_db)):
    c = _cat(conn, cid)
    nombre = (body.nombre if body.nombre is not None else c["nombre"]).strip()
    if conn.execute("SELECT 1 FROM ident_categorias WHERE tipo_audio = ? AND nombre = ? COLLATE NOCASE AND id != ?",
                    (c["tipo_audio"], nombre, cid)).fetchone():
        raise HTTPException(status_code=409, detail=f"Ya existe «{nombre}» en este tipo")
    comentario = (body.comentario if body.comentario is not None else c["comentario"]).strip()
    conn.execute("UPDATE ident_categorias SET nombre = ?, comentario = ? WHERE id = ?", (nombre, comentario, cid))
    if nombre != c["nombre"]:     # los audios (y los filtros de los relojes se leen por nombre) siguen el cambio
        conn.execute("UPDATE audios SET categoria_ident = ? WHERE tipo_audio = ? AND UPPER(COALESCE(categoria_ident, '')) = UPPER(?)",
                     (nombre, c["tipo_audio"], c["nombre"]))
        conn.execute("UPDATE reloj_filas SET filtros = json_set(filtros, '$.ident', UPPER(?)) "
                     "WHERE tipo_audio = ? AND UPPER(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(filtros, '$.ident')), '')) = UPPER(?)",
                     (nombre, c["tipo_audio"], c["nombre"]))
    return {"id": cid, "nombre": nombre, "comentario": comentario}


class MoverIn(BaseModel):
    dir: int = Field(..., ge=-1, le=1)


@router.post("/api/v1/ident/categorias/{cid}/mover", summary="Subir o bajar una categoría")
def mover(cid: int, body: MoverIn, conn: sqlite3.Connection = Depends(get_db)):
    c = _cat(conn, cid)
    lista = [r["id"] for r in conn.execute("SELECT id FROM ident_categorias WHERE tipo_audio = ? ORDER BY orden, id", (c["tipo_audio"],))]
    i = lista.index(cid)
    j = i + (1 if body.dir > 0 else -1) if body.dir else i
    if 0 <= j < len(lista):
        lista[i], lista[j] = lista[j], lista[i]
        for n, x in enumerate(lista):
            conn.execute("UPDATE ident_categorias SET orden = ? WHERE id = ?", (n, x))
    return {"ok": True}


@router.delete("/api/v1/ident/categorias/{cid}", summary="Eliminar una categoría")
def eliminar(cid: int, quitar: bool = False, conn: sqlite3.Connection = Depends(get_db)):
    c = _cat(conn, cid)
    usos = conn.execute("SELECT COUNT(*) FROM audios WHERE activo = 1 AND tipo_audio = ? AND COALESCE(ambito, '') != 'cartuchera' AND UPPER(COALESCE(categoria_ident, '')) = UPPER(?)",
                        (c["tipo_audio"], c["nombre"])).fetchone()[0]
    if usos and not quitar:
        raise HTTPException(status_code=409, detail=f"{usos} audio(s) usan «{c['nombre']}». Reasígnalos o elimina quitándola de ellos.")
    conn.execute("UPDATE audios SET categoria_ident = NULL WHERE tipo_audio = ? AND UPPER(COALESCE(categoria_ident, '')) = UPPER(?)",
                 (c["tipo_audio"], c["nombre"]))
    conn.execute("DELETE FROM ident_categorias WHERE id = ?", (cid,))
    return {"ok": True, "quitada_de": usos}


class AsignarIn(BaseModel):
    ids:       list[int] = Field(..., min_length=1, max_length=5000)
    categoria: Optional[str] = None


@router.put("/api/v1/ident/audios", summary="Asignar categoría a varios audios")
def asignar(body: AsignarIn, conn: sqlite3.Connection = Depends(get_db)):
    ids = sorted(set(body.ids))
    ph = ",".join("?" * len(ids))
    filas = conn.execute(f"SELECT id, tipo_audio FROM audios WHERE id IN ({ph}) AND activo = 1", ids).fetchall()
    if not filas:
        raise HTTPException(status_code=404, detail="Audios no encontrados")
    nombre = (body.categoria or "").strip() or None
    if nombre:
        for tipo in {f["tipo_audio"] for f in filas}:
            r = conn.execute("SELECT nombre FROM ident_categorias WHERE tipo_audio = ? AND nombre = ? COLLATE NOCASE", (tipo, nombre)).fetchone()
            if not r:
                raise HTTPException(status_code=422, detail=f"«{nombre}» no es una categoría de {tipo}")
            nombre = r["nombre"]                                   # la escritura canónica
    n = conn.execute(f"UPDATE audios SET categoria_ident = ?, updated_at = datetime('now') WHERE id IN ({ph}) AND activo = 1",
                     [nombre] + ids).rowcount
    return {"actualizados": n, "categoria": nombre}
