"""
locutor.py — Ónix FM
API del Locutor IA: configuración, aprobación de lo que va a decir y estado del servicio.

    GET    /api/v1/locutor/estado
    POST   /api/v1/locutor/activar                      {activo}
    GET    /api/v1/locutor/config        PUT  /api/v1/locutor/config   {emisora?, persona?, reglas?, aprobacion?}
    GET|POST /api/v1/locutor/turnos      DELETE .../turnos/{id}
    GET|POST /api/v1/locutor/patrocinios PATCH|DELETE .../patrocinios/{id}
    GET|POST /api/v1/locutor/fuentes     DELETE .../fuentes/{id}     POST .../fuentes/revisar
    GET|POST /api/v1/locutor/mensajes    POST .../mensajes/{id}/aprobar | rechazar
    GET    /api/v1/locutor/intervenciones?estado=
    POST   /api/v1/locutor/intervenciones/probar        {tipo, anterior_id?, siguiente_id?} → borrador
    POST   /api/v1/locutor/intervenciones/{id}/aprobar  {texto?} → voz + insertar en el programa
    POST   /api/v1/locutor/intervenciones/{id}/descartar
    POST   /api/v1/locutor/importar                     {origen, reemplazar?} (copia antes la base)
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.locutor import almacen, moderacion, noticias, servicio as svc
from backend.paths import DB_PATH, RESPALDOS_BD_DIR

router = APIRouter(tags=["Locutor IA"])
engine: Any = None                      # lo asigna api_server
servicio: Optional[svc.Servicio] = None


def _c() -> sqlite3.Connection:
    return almacen.conectar(DB_PATH)


def _filas(cur) -> list:
    return [dict(r) for r in cur]


# ── Estado ──────────────────────────────────────────────────────────────────

@router.get("/api/v1/locutor/estado", summary="Estado del Locutor IA")
def estado():
    c = _c()
    try:
        cuenta = {r[0]: r[1] for r in c.execute(
            "SELECT estado, COUNT(*) FROM locutor_intervenciones WHERE creado_en >= datetime('now','-1 day')"
            " GROUP BY estado")}
        claves = c.execute("SELECT COUNT(*) FROM ia_claves WHERE activo = 1").fetchone()[0] \
            if c.execute("SELECT 1 FROM sqlite_master WHERE name = 'ia_claves'").fetchone() else 0
        return {"activo": bool(almacen.leer(c, "activo", False)), "ultimas_24h": cuenta,
                "por_aprobar": c.execute(
                    "SELECT COUNT(*) FROM locutor_intervenciones WHERE estado = 'borrador'").fetchone()[0],
                "claves_ia": claves, "ultimo_ciclo": servicio.ultimo if servicio else None,
                "aprobacion": almacen.aprobacion(c)}
    finally:
        c.close()


class ActivarIn(BaseModel):
    activo: bool


@router.post("/api/v1/locutor/activar", summary="Encender o apagar al Locutor IA")
def activar(b: ActivarIn):
    c = _c()
    try:
        almacen.guardar(c, "activo", b.activo)
        c.commit()
        return {"activo": b.activo}
    finally:
        c.close()


# ── Configuración ───────────────────────────────────────────────────────────

@router.get("/api/v1/locutor/config", summary="Emisora, locutor, reglas y aprobación")
def get_config():
    c = _c()
    try:
        return {"emisora": almacen.emisora(c), "persona": almacen.persona(c), "reglas": almacen.reglas(c),
                "aprobacion": almacen.aprobacion(c), "tipos": list(almacen.TIPOS)}
    finally:
        c.close()


class ConfigIn(BaseModel):
    emisora: Optional[dict] = None
    persona: Optional[dict] = None
    reglas: Optional[dict] = None
    aprobacion: Optional[dict] = None


@router.put("/api/v1/locutor/config", summary="Guardar la configuración del Locutor IA")
def put_config(b: ConfigIn):
    c = _c()
    try:
        if b.emisora is not None:
            almacen.guardar(c, "emisora", {**almacen.emisora(c), **b.emisora})
        if b.persona is not None:
            almacen.guardar(c, "persona", {**almacen.persona(c), **b.persona})
        if b.reglas is not None:
            almacen.guardar(c, "reglas", {**almacen.reglas(c), **b.reglas})
        if b.aprobacion is not None:
            malos = [k for k, v in b.aprobacion.items() if k not in almacen.TIPOS or v not in ("auto", "manual")]
            if malos:
                raise HTTPException(status_code=422, detail=f"Aprobación inválida para: {', '.join(malos)}")
            almacen.guardar(c, "aprobacion", {**almacen.aprobacion(c), **b.aprobacion})
        c.commit()
        return get_config()
    finally:
        c.close()


# ── Turnos, patrocinios, fuentes ────────────────────────────────────────────

class TurnoIn(BaseModel):
    desde: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    hasta: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    nombre: str = Field(..., min_length=1, max_length=60)
    voz: str = Field(..., min_length=1, max_length=80)
    personalidad: str = Field(default="", max_length=600)


@router.get("/api/v1/locutor/turnos")
def turnos():
    c = _c()
    try:
        return _filas(c.execute("SELECT * FROM locutor_turnos ORDER BY desde, id"))
    finally:
        c.close()


@router.post("/api/v1/locutor/turnos", status_code=201)
def crear_turno(b: TurnoIn):
    c = _c()
    try:
        cur = c.execute("INSERT INTO locutor_turnos (desde, hasta, nombre, voz, personalidad) VALUES (?,?,?,?,?)",
                        (b.desde, b.hasta, b.nombre, b.voz, b.personalidad))
        c.commit()
        return {"id": cur.lastrowid}
    finally:
        c.close()


def _borrar(tabla: str, rid: int) -> dict:
    c = _c()
    try:
        if not c.execute(f"DELETE FROM {tabla} WHERE id = ?", (rid,)).rowcount:
            raise HTTPException(status_code=404, detail="No existe")
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/api/v1/locutor/turnos/{rid}")
def quitar_turno(rid: int):
    return _borrar("locutor_turnos", rid)


class PatrocinioIn(BaseModel):
    nombre: str = Field(default="", max_length=80)
    texto: str = Field(..., min_length=5, max_length=1500)
    desde: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    hasta: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    frecuencia: int = Field(default=1, ge=1, le=48)


@router.get("/api/v1/locutor/patrocinios")
def patrocinios():
    c = _c()
    try:
        return _filas(c.execute("SELECT * FROM locutor_patrocinios ORDER BY id DESC"))
    finally:
        c.close()


@router.post("/api/v1/locutor/patrocinios", status_code=201)
def crear_patrocinio(b: PatrocinioIn):
    c = _c()
    try:
        cur = c.execute("INSERT INTO locutor_patrocinios (nombre, texto, desde, hasta, frecuencia) VALUES (?,?,?,?,?)",
                        (b.nombre, b.texto, b.desde, b.hasta, b.frecuencia))
        c.commit()
        return {"id": cur.lastrowid}
    finally:
        c.close()


class ActivoIn(BaseModel):
    activo: bool


@router.patch("/api/v1/locutor/patrocinios/{rid}")
def activar_patrocinio(rid: int, b: ActivoIn):
    c = _c()
    try:
        if not c.execute("UPDATE locutor_patrocinios SET activo = ? WHERE id = ?", (int(b.activo), rid)).rowcount:
            raise HTTPException(status_code=404, detail="No existe")
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/api/v1/locutor/patrocinios/{rid}")
def quitar_patrocinio(rid: int):
    return _borrar("locutor_patrocinios", rid)


class FuenteIn(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=80)
    url: str = Field(..., min_length=8, max_length=500)
    cierre: str = Field(default="", max_length=200)


@router.get("/api/v1/locutor/fuentes")
def fuentes():
    c = _c()
    try:
        return _filas(c.execute("SELECT * FROM locutor_fuentes ORDER BY id"))
    finally:
        c.close()


@router.post("/api/v1/locutor/fuentes", status_code=201)
def crear_fuente(b: FuenteIn):
    if not b.url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="La dirección debe empezar por http:// o https://")
    c = _c()
    try:
        try:
            cur = c.execute("INSERT INTO locutor_fuentes (nombre, url, cierre) VALUES (?,?,?)",
                            (b.nombre, b.url.strip(), b.cierre))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Esa fuente ya está registrada")
        c.commit()
        return {"id": cur.lastrowid}
    finally:
        c.close()


@router.post("/api/v1/locutor/fuentes/revisar", summary="Leer ahora todas las fuentes")
def revisar_fuentes():
    c = _c()
    try:
        nuevas = noticias.actualizar(c, almacen.reglas(c)["noticias_vetos"], forzar=True)
        return {"nuevas": nuevas, "fuentes": _filas(c.execute(
            "SELECT id, nombre, revisada_en, error FROM locutor_fuentes ORDER BY id"))}
    finally:
        c.close()


@router.delete("/api/v1/locutor/fuentes/{rid}")
def quitar_fuente(rid: int):
    return _borrar("locutor_fuentes", rid)


# ── Mensajes de oyentes ─────────────────────────────────────────────────────

class MensajeIn(BaseModel):
    de: str = Field(..., min_length=1, max_length=60)
    para: str = Field(default="", max_length=60)
    lugar: str = Field(default="", max_length=60)
    texto: str = Field(default="", max_length=500)
    cancion: str = Field(default="", max_length=120)


@router.get("/api/v1/locutor/mensajes")
def mensajes(limit: int = 50):
    c = _c()
    try:
        return _filas(c.execute("SELECT * FROM locutor_mensajes ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)))
    finally:
        c.close()


@router.post("/api/v1/locutor/mensajes", status_code=201, summary="Cargar un saludo (queda pendiente de aprobación)")
def crear_mensaje(b: MensajeIn):
    c = _c()
    try:
        motivo = moderacion.revisar([b.de, b.para, b.lugar, b.texto, b.cancion], almacen.persona(c)["prohibidos"])
        cur = c.execute("INSERT INTO locutor_mensajes (de, para, lugar, texto, cancion, estado, motivo)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (b.de, b.para, b.lugar, b.texto, b.cancion, "rechazado" if motivo else "pendiente", motivo))
        c.commit()
        return {"id": cur.lastrowid, "estado": "rechazado" if motivo else "pendiente", "motivo": motivo}
    finally:
        c.close()


def _estado_mensaje(mid: int, estado: str, prioridad: int = 0):
    c = _c()
    try:
        if not c.execute("UPDATE locutor_mensajes SET estado = ?, prioridad = ? WHERE id = ?",
                         (estado, prioridad, mid)).rowcount:
            raise HTTPException(status_code=404, detail="Mensaje no encontrado")
        c.commit()
        return {"ok": True, "estado": estado}
    finally:
        c.close()


@router.post("/api/v1/locutor/mensajes/{mid}/aprobar")
def aprobar_mensaje(mid: int):
    return _estado_mensaje(mid, "aprobado")


@router.post("/api/v1/locutor/mensajes/{mid}/rechazar")
def rechazar_mensaje(mid: int):
    return _estado_mensaje(mid, "rechazado")


# ── Intervenciones ──────────────────────────────────────────────────────────

@router.get("/api/v1/locutor/intervenciones", summary="Lo que el Locutor va a decir o ya dijo")
def intervenciones(estado: str = "", limit: int = 60):
    c = _c()
    try:
        if estado and estado not in almacen.ESTADOS:
            raise HTTPException(status_code=422, detail="Estado desconocido")
        sql = ("SELECT i.*, a.titulo AS anterior_titulo FROM locutor_intervenciones i"
               " LEFT JOIN audios a ON a.id = i.anterior_id")
        args: list = []
        if estado:
            sql += " WHERE i.estado = ?"
            args.append(estado)
        sql += " ORDER BY i.id DESC LIMIT ?"
        return _filas(c.execute(sql, args + [max(1, min(limit, 200))]))
    finally:
        c.close()


class ProbarIn(BaseModel):
    tipo: str
    anterior_id: Optional[int] = None
    siguiente_id: Optional[int] = None


@router.post("/api/v1/locutor/intervenciones/probar", status_code=201,
             summary="Escribir una intervención ya (queda en borrador para que la oigas antes de aprobarla)")
def probar(b: ProbarIn):
    if b.tipo not in almacen.TIPOS:
        raise HTTPException(status_code=422, detail=f"Tipo desconocido: {b.tipo}")
    from backend.locutor.metadatos import limpiar_pista
    c = _c()
    try:
        def pista(aid):
            r = c.execute("SELECT titulo, artista FROM audios WHERE id = ?", (aid,)).fetchone() if aid else None
            return limpiar_pista(r["artista"], r["titulo"]) if r else None
        ahora = datetime.now(almacen.zona(almacen.emisora(c)["zona_horaria"]))
        return svc.preparar(c, b.tipo, pista(b.anterior_id), pista(b.siguiente_id), ahora,
                            anterior_id=b.anterior_id, siguiente_id=b.siguiente_id, forzar_manual=True)
    finally:
        c.close()


class AprobarIn(BaseModel):
    texto: Optional[str] = Field(default=None, max_length=1500)


@router.post("/api/v1/locutor/intervenciones/{iid}/aprobar", summary="Aprobar: genera la voz y la mete en el programa")
def aprobar(iid: int, b: AprobarIn = AprobarIn()):
    c = _c()
    try:
        r = c.execute("SELECT * FROM locutor_intervenciones WHERE id = ?", (iid,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="No existe")
        if r["estado"] not in ("borrador", "aprobada", "fallo") or not (b.texto or r["texto"]):
            raise HTTPException(status_code=409, detail=f"Está en «{r['estado']}»: no se puede aprobar")
        texto = " ".join((b.texto or r["texto"]).split())
        c.execute("UPDATE locutor_intervenciones SET texto = ?, estado = 'aprobada', modo = 'manual',"
                  " decidido_en = datetime('now'), error = NULL WHERE id = ?", (texto, iid))
        c.commit()
        res = svc.locutar(c, iid)
        if res["estado"] != "lista":
            raise HTTPException(status_code=503, detail=res.get("error", "No se pudo generar la voz"))
        colocada = bool(r["anterior_id"]) and engine is not None and svc.insertar(c, iid, engine)
        return {**res, "en_programa": colocada,
                "aviso": None if colocada else "La voz quedó lista; si no estaba su canción en el programa, "
                                               "arrástrala desde la pestaña VOZ."}
    finally:
        c.close()


@router.post("/api/v1/locutor/intervenciones/{iid}/descartar")
def descartar(iid: int):
    c = _c()
    try:
        r = c.execute("SELECT estado FROM locutor_intervenciones WHERE id = ?", (iid,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="No existe")
        if r["estado"] in ("al_aire", "descartada"):
            raise HTTPException(status_code=409, detail=f"Está en «{r['estado']}»")
        almacen.cambiar_estado(c, iid, "descartada", motivo="Descartada por una persona")
        return {"ok": True}
    finally:
        c.close()


# ── Importar la configuración del Locutor IA de AzuraCast ───────────────────

class ImportarIn(BaseModel):
    origen: str = Field(..., min_length=3, max_length=500)
    slug: str = "solisfm"
    reemplazar: bool = False


@router.post("/api/v1/locutor/importar", summary="Traer turnos, voces, patrocinios y fuentes del Locutor anterior")
def importar(b: ImportarIn):
    origen = Path(b.origen)
    if not origen.is_file():
        raise HTTPException(status_code=404, detail=f"No existe el archivo {origen}")
    RESPALDOS_BD_DIR.mkdir(parents=True, exist_ok=True)
    copia = RESPALDOS_BD_DIR / f"radio_core.antes-locutor-{datetime.now():%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(copia)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    c = _c()
    try:
        try:
            r = almacen.importar_de_azuracast(c, origen, b.slug, b.reemplazar)
        except (ValueError, sqlite3.Error) as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {**r, "copia_previa": str(copia)}
    finally:
        c.close()
