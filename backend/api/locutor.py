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

import sqlite3                      # SOLO para atrapar errores al leer el archivo SQLite del Locutor anterior (importar)
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.locutor import almacen, datos as datos_mod, moderacion, noticias, servicio as svc
from backend.database import DualError, DualIntegrityError, DualOperationalError, MariaDBConnection
from backend.paths import DB_PATH, RESPALDOS_BD_DIR

router = APIRouter(tags=["Locutor IA"])
engine: Any = None                      # lo asigna api_server
servicio: Optional[svc.Servicio] = None


def _c() -> MariaDBConnection:
    return almacen.conectar(DB_PATH)


def _filas(cur) -> list:
    return [dict(r) for r in cur]


# ── Estado ──────────────────────────────────────────────────────────────────

@router.get("/api/v1/locutor/estado", summary="Estado del Locutor IA")
def estado():
    c = _c()
    try:
        cuenta = {r[0]: r[1] for r in c.execute(
            "SELECT estado, COUNT(*) FROM locutor_intervenciones WHERE creado_en >= DATE_SUB(NOW(), INTERVAL 1 DAY)"
            " GROUP BY estado")}
        claves = c.execute("SELECT COUNT(*) FROM ia_claves WHERE activo = 1").fetchone()[0] \
            if c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'ia_claves'").fetchone() else 0
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


@router.get("/api/v1/locutor/voces", summary="Voces que se pueden asignar a un locutor")
def voces():
    """
    Las del catálogo del Locutor (importadas o usadas en turnos) más las de las claves de Admin › IA.
    `disponible` dice si hoy hay una clave activa de ese proveedor: sin ella, esa voz no puede sonar.
    """
    from backend.ia import proveedores
    c = _c()
    try:
        try:
            claves = [dict(r) for r in c.execute("SELECT * FROM ia_claves WHERE activo = 1 ORDER BY orden, id")]
        except DualOperationalError:
            claves = []
        activos = {k["proveedor"] for k in claves}
        out, vistas = [], set()

        def poner(nombre, voz, proveedor, genero=""):
            if voz and voz not in vistas:
                vistas.add(voz)
                out.append({"nombre": nombre, "voz": voz, "proveedor": proveedor, "genero": genero,
                            "disponible": proveedor in activos})
        for v in almacen.leer(c, "voces", []) or []:
            poner(v.get("nombre") or v.get("voz"), v.get("voz"), v.get("proveedor") or "fish", v.get("genero") or "")
        for k in claves:
            if k["proveedor"] == "fish":
                for nombre, vid in proveedores.voces_de(k):
                    poner(nombre, f"fish:{vid}", "fish")
        for t in c.execute("SELECT nombre, voz FROM locutor_turnos"):
            poner(t["nombre"], t["voz"], "fish" if t["voz"].startswith("fish:") else "otro")
        for p in ("gemini", "openai"):
            for v in proveedores.VOCES[p]:
                poner(v, v, p)
        return {"voces": out, "proveedores_activos": sorted(activos)}
    finally:
        c.close()


@router.put("/api/v1/locutor/turnos/{rid}", summary="Cambiar un turno (horario, locutor o voz)")
def editar_turno(rid: int, b: TurnoIn):
    c = _c()
    try:
        if not c.execute("UPDATE locutor_turnos SET desde = ?, hasta = ?, nombre = ?, voz = ?, personalidad = ?"
                         " WHERE id = ?", (b.desde, b.hasta, b.nombre, b.voz, b.personalidad, rid)).rowcount:
            raise HTTPException(status_code=404, detail="No existe")
        c.commit()
        return {"ok": True}
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
        except DualIntegrityError:
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


# ── Datos verificados de artistas y canciones ───────────────────────────────

class DatoIn(BaseModel):
    ambito: str = Field(..., pattern=r"^(artista|cancion)$")
    artista: str = Field(..., min_length=1, max_length=120)
    titulo: str = Field(default="", max_length=160)
    dato: str = Field(..., min_length=3, max_length=400)


@router.get("/api/v1/locutor/datos", summary="Lo que el locutor puede contar de cada artista y canción")
def datos(q: str = "", limit: int = 80):
    c = _c()
    try:
        sql, args = "SELECT * FROM locutor_datos", []
        if q.strip():
            sql += " WHERE clave LIKE ? OR etiqueta LIKE ?"
            args += [f"%{datos_mod.norm(q)}%", f"%{q.strip()}%"]
        sql += " ORDER BY (estado = 'sin_datos'), revisado_en DESC LIMIT ?"
        return _filas(c.execute(sql, args + [max(1, min(limit, 300))]))
    finally:
        c.close()


@router.put("/api/v1/locutor/datos", summary="Escribir a mano el dato de un artista o de una canción")
def poner_dato(b: DatoIn):
    if b.ambito == "cancion" and not b.titulo.strip():
        raise HTTPException(status_code=422, detail="Para una canción hace falta el título")
    artista = datos_mod.artista_principal(b.artista)
    if b.ambito == "artista":
        clave, etiqueta = datos_mod.norm(artista), artista
    else:
        clave, etiqueta = f"{datos_mod.norm(artista)}|{datos_mod.norm(b.titulo)}", f"{artista} - {b.titulo.strip()}"
    c = _c()
    try:
        datos_mod.guardar(c, b.ambito, clave, etiqueta, " ".join(b.dato.split()), "manual")
        return {"ok": True, "clave": clave}
    finally:
        c.close()


@router.post("/api/v1/locutor/datos/{rid}/descartar", summary="Que el locutor no diga ese dato (ni lo vuelva a buscar)")
def descartar_dato(rid: int):
    c = _c()
    try:
        if not c.execute("UPDATE locutor_datos SET estado = 'descartado', revisado_en = NOW() WHERE id = ?",
                         (rid,)).rowcount:
            raise HTTPException(status_code=404, detail="No existe")
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@router.delete("/api/v1/locutor/datos/{rid}", summary="Borrarlo: la próxima vez se busca de nuevo")
def borrar_dato(rid: int):
    return _borrar("locutor_datos", rid)


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
                  " decidido_en = NOW(), error = NULL WHERE id = ?", (texto, iid))
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
    # Copia de seguridad de la base real (MariaDB) antes de reemplazar nada
    from backend.respaldo_maria import verificar, volcar
    copia = RESPALDOS_BD_DIR / f"antes-locutor-{datetime.now():%Y%m%d-%H%M%S}.sql.gz"
    try:
        volcar(copia)
        verificar(copia)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo hacer la copia previa; no se importó nada: {exc}")
    c = _c()
    try:
        try:
            r = almacen.importar_de_azuracast(c, origen, b.slug, b.reemplazar)
        except (ValueError, sqlite3.Error, DualError) as e:      # sqlite3.Error: el archivo de origen (AzuraCast) era SQLite
            raise HTTPException(status_code=422, detail=str(e))
        return {**r, "copia_previa": str(copia)}
    finally:
        c.close()
