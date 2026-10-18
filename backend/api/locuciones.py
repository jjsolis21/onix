"""
locuciones.py — Ónix FM
Sección aparte para las VOCES: las locuciones que genera el Locutor IA y los voice tracks del operador
(audios de tipo «Locucion»). No son música: no aparecen en la Biblioteca Musical ni en las demás listas.

    GET    /api/v1/locuciones?q=&origen=&limit=&offset=   → tabla (con total y resumen)
    DELETE /api/v1/locuciones/{id}                         → borra la locución (fila y archivo)
    POST   /api/v1/locuciones/limpiar                      → {dias}: borra las de más de N días
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.paths import MEDIA_ROOT, resolve_media

router = APIRouter(tags=["Locuciones"])

TIPO = "Locucion"
# Una locución que el locutor ya generó y espera salir al aire no se puede borrar
ESTADOS_EN_USO = ("lista",)


def _conn():
    from backend.database import get_connection
    return get_connection()


def _tiene_locutor(c) -> bool:
    try:
        c.execute("SELECT 1 FROM locutor_intervenciones LIMIT 1").fetchall()
        return True
    except Exception:
        return False


@router.get("/api/v1/locuciones", summary="Locuciones (voces del Locutor IA y voice tracks)")
def listar(q: Optional[str] = None, origen: Optional[str] = None,
           limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    c = _conn()
    try:
        con_ia = _tiene_locutor(c)
        union = (" LEFT JOIN locutor_intervenciones i ON i.audio_id = a.id" if con_ia else "")
        cols_ia = (", i.id AS interv_id, i.tipo AS interv_tipo, i.texto, i.persona, i.voz, i.proveedor_voz, i.estado, i.al_aire_en"
                   if con_ia else ", NULL AS interv_id, NULL AS interv_tipo, NULL AS texto, NULL AS persona, NULL AS voz,"
                                  " NULL AS proveedor_voz, NULL AS estado, NULL AS al_aire_en")
        # (sin filtrar por «activo»: las voces se gestionan aquí, y en algunas bases quedaron marcadas como inactivas)
        donde, params = ["a.tipo_audio = ?"], [TIPO]
        if origen == "ia":
            donde.append("i.id IS NOT NULL" if con_ia else "0")
        elif origen == "voicetrack":
            donde.append("i.id IS NULL" if con_ia else "1")
        for palabra in (q or "").lower().split():
            like = "%" + palabra.replace("%", "").replace("_", "") + "%"
            donde.append("(LOWER(a.titulo) LIKE ?" + (" OR LOWER(COALESCE(i.texto, '')) LIKE ?)" if con_ia else ")"))
            params.extend([like, like] if con_ia else [like])
        sql = f" FROM audios a{union} WHERE " + " AND ".join(donde)
        total = c.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
        filas = c.execute(
            "SELECT a.id, a.titulo, a.duracion, a.created_at, a.veces_reproducido, a.ultima_reproduccion, a.archivo_path" + cols_ia
            + sql + " ORDER BY a.id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        data = []
        for f in filas:
            d = dict(f)
            d["origen"] = "ia" if d.get("interv_id") else "voicetrack"
            d["en_uso"] = d.get("estado") in ESTADOS_EN_USO
            data.append(d)
        res = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN a.duracion BETWEEN 0 AND 600 THEN a.duracion ELSE 0 END), 0)" + (", COUNT(i.id)" if con_ia else ", 0")
            + f" FROM audios a{union} WHERE a.tipo_audio = ?", [TIPO]).fetchone()
        return {"total": total, "data": data,
                "resumen": {"total": res[0], "segundos": round(float(res[1] or 0)), "ia": res[2], "voicetrack": res[0] - res[2]}}
    finally:
        c.close()


def _borrar(c, fila) -> bool:
    """Borra la fila y el archivo de una locución. Devuelve True si el archivo se pudo quitar (o no existía)."""
    from backend.api.biblioteca import _borrar_archivo
    aid = fila["id"]
    ok = True
    try:
        ruta = resolve_media(fila["archivo_path"] or "").resolve()
        if MEDIA_ROOT.resolve() in ruta.parents and ruta.is_file():            # nunca fuera de la carpeta de medios
            ok = _borrar_archivo(ruta)
    except OSError:
        ok = False
    if _tiene_locutor(c):
        c.execute("UPDATE locutor_intervenciones SET audio_id = NULL WHERE audio_id = ?", (aid,))
    c.execute("DELETE FROM audios WHERE id = ?", (aid,))
    return ok


def _en_uso(c, aid: int) -> bool:
    if not _tiene_locutor(c):
        return False
    return c.execute("SELECT 1 FROM locutor_intervenciones WHERE audio_id = ? AND estado IN ({})".format(
        ",".join("?" * len(ESTADOS_EN_USO))), (aid, *ESTADOS_EN_USO)).fetchone() is not None


@router.delete("/api/v1/locuciones/{audio_id}", summary="Borrar una locución")
def borrar(audio_id: int):
    c = _conn()
    try:
        f = c.execute("SELECT id, archivo_path FROM audios WHERE id = ? AND tipo_audio = ?", (audio_id, TIPO)).fetchone()
        if not f:
            raise HTTPException(status_code=404, detail="No existe esa locución")
        if _en_uso(c, audio_id):
            raise HTTPException(status_code=409, detail="Está lista para salir al aire: espera a que suene o se descarte")
        ok = _borrar(c, f)
        c.commit()
        return {"ok": True, "archivo_borrado": ok}
    finally:
        c.close()


class LimpiarIn(BaseModel):
    dias: int = Field(..., ge=1, le=3650)


@router.post("/api/v1/locuciones/limpiar", summary="Borrar las locuciones de más de N días")
def limpiar(b: LimpiarIn):
    c = _conn()
    try:
        corte = (datetime.now() - timedelta(days=b.dias)).strftime("%Y-%m-%d %H:%M:%S")      # igual en MariaDB y SQLite
        antiguas = c.execute(
            "SELECT id, archivo_path FROM audios WHERE tipo_audio = ? AND created_at < ?", (TIPO, corte)).fetchall()
        borradas = pendientes = saltadas = 0
        for f in antiguas:
            if _en_uso(c, f["id"]):
                saltadas += 1
                continue
            if not _borrar(c, f):
                pendientes += 1
            borradas += 1
        c.commit()
        return {"borradas": borradas, "saltadas": saltadas, "archivos_pendientes": pendientes}
    finally:
        c.close()
