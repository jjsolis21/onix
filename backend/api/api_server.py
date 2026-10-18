"""
api_server.py — Ónix FM Radio Core API
FastAPI REST API Server — Versión 2.0
Sistema dinámico de categorías para Biblioteca.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
from backend.database import DualError, DualIntegrityError, MariaDBConnection
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status, Depends, WebSocket, WebSocketDisconnect, Request, Form, File, UploadFile, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

import sys
from pathlib import Path

# Añadimos la carpeta scripts al sistema para que encuentre init_db
root_path = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(root_path / "scripts"))
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))   # para "from backend.… import" al ejecutar como script

# Rutas compartidas con el motor (misma BD, misma carpeta de medios)
from backend.paths import DB_PATH as _DB_PATH, MEDIA_ROOT, resolve_media, to_stored, normalize_stored

# Schemas para el módulo Pautas (Sección 03)
# Archivo: backend/api/schemas_pautas.py
try:
    from schemas_pautas import PautaIn, PautaOut
except ImportError:
    from backend.api.schemas_pautas import PautaIn, PautaOut

from backend.audio.loudness import NormalizadorWorker
from backend.version import VERSION
from backend.api.reportes import router as reportes_router
from backend.api import relojes as relojes_api
from backend.api import playlists as playlists_api
from backend.api import identificadores as identificadores_api
from backend.api import ia as ia_api
from backend.api import voicetrack as voicetrack_api
from backend.api import locuciones as locuciones_api
from backend.api import locutor as locutor_api
from backend.api import cartuchera as cartuchera_api
from backend.api import comerciales as comerciales_api
from backend.locutor.servicio import Servicio as LocutorServicio
from backend.api import problemas as problemas_api
from backend.api import respaldos as respaldos_api
from backend.api.red import router as red_router
from backend.api import biblioteca as biblioteca_api
from backend.api import auth as auth_api
from backend.api.utils_musica import calcular_epoca

# Motor de audio:
#   ONIX_ENGINE=local  (por defecto) → el motor corre dentro de este proceso.
#   ONIX_ENGINE=remoto → el motor es otro proceso (backend/audio/engine_service.py)
#                        y reiniciar la API no corta el aire.
ENGINE_MODE = os.getenv("ONIX_ENGINE", "local").strip().lower()
if ENGINE_MODE == "remoto":
    from backend.audio.engine_client import RemoteEngine
    engine = RemoteEngine(os.getenv("ONIX_ENGINE_HOST", "127.0.0.1"),
                          int(os.getenv("ONIX_ENGINE_PORT", "8766")))
else:
    from backend.audio.audio_engine import engine

# ---------------------------------------------------------------------------
# Conexión a Base de Datos (MariaDB / SQLAlchemy)
# ---------------------------------------------------------------------------
from backend.database import (
    get_connection,
    db_session,
    get_db,
    engine as db_engine,
    SessionLocal,
    Base,
    MariaDBConnection,
    IntegrityError,
    OperationalError,
    DatabaseError,
    Error,
)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
logger = logging.getLogger("onix.api")

# DB_PATH y MEDIA_ROOT vienen de backend/paths.py (RADIO_DB_PATH / RADIO_MEDIA_ROOT).
# En la BD, audios.archivo_path se guarda relativo a MEDIA_ROOT.
DB_PATH      = str(_DB_PATH)
STAGING_DIR  = MEDIA_ROOT / "staging"
LIBRARY_DIR  = MEDIA_ROOT / "library"
MUSICA_DIR   = LIBRARY_DIR / "musica"   # destino para archivos subidos via /upload

# Medición de sonoridad (LUFS) para la normalización de volumen
_normalizador = NormalizadorWorker(DB_PATH, resolve_media)
_locutor = LocutorServicio(lambda: engine, DB_PATH)

# Eventos del motor que se retransmiten a los clientes WebSocket
ENGINE_EVENTS = (
    "track_started", "track_ended", "crossfade_start", "playback_stopped",
    "playback_error", "mode_changed", "effect_fired", "effect_stopped", "queue_updated",
    "pauta_scheduled", "pauta_missed", "tanda_scheduled", "respaldo_activado",
    "engine_stopped", "engine_connected", "cue_started", "cue_stopped",
    "programa_actualizado", "tanda_excedida", "tanda_inmediata", "evento_inmediato", "detener_al_terminar", "seek", "pausa",
    "aire_muerto",
    "vu",
    "watchdog_recovery", "watchdog_stall", "watchdog_recovered",
)


def _ws_message(event: str, data: dict = None, module: str = "api") -> str:
    """
    Sobre único para todo lo que el servidor envía por WebSocket.
    Studio y Shell leen `event`; `cmd` se mantiene por compatibilidad.
    """
    return json.dumps({
        "event":  event,
        "module": module,
        "cmd":    event,
        "ts":     int(time.time() * 1000),
        "data":   data or {},
    }, default=str)


# ---------------------------------------------------------------------------
# Lifespan — sustituto moderno de @app.on_event("startup") / "shutdown"
# A partir de FastAPI 0.93+ el decorador on_event está deprecado.
# El patrón lifespan usa un async context manager: el código antes del
# "yield" corre al arrancar, el código después al apagar.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──────────────────────────────────────────────────────────
    logger.info("Iniciando Ónix FM API v2.0...")
    from backend.esquema_maria import asegurar_esquema
    if asegurar_esquema():    # base vacía (instalación nueva): crea las tablas; luego aplica las migraciones pendientes
        logger.info("Base de datos nueva: esquema creado")
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    MUSICA_DIR.mkdir(parents=True, exist_ok=True)
    _normalize_media_paths()
    _backfill_durations()
    _normalizador.start()     # mide en segundo plano los audios sin LUFS
    _respaldos.start()        # copia diaria de la base de datos
    _locutor.start()          # Locutor IA: solo actúa si está encendido en Admin

    # Puente EventBus (hilos del motor) -> WebSocket (event loop de Uvicorn).
    # Los callbacks del EventBus corren en hilos del motor, así que el
    # broadcast se programa en el loop con run_coroutine_threadsafe.
    loop = asyncio.get_running_loop()

    def _on_engine_event(event: str, data: dict):
        problemas_api.registrar(event, data)     # panel de problemas del Studio
        asyncio.run_coroutine_threadsafe(
            _manager.broadcast(_ws_message(event, data, module="engine")), loop
        )

    for ev in ENGINE_EVENTS:
        engine.bus.subscribe(ev, _on_engine_event)

    # Arrancar el motor. Si el audio falla (sin tarjeta, DLL ausente) la API
    # sigue viva para administrar la biblioteca; /api/v1/status reporta el error.
    try:
        engine.start()
    except Exception as exc:
        engine.last_error = str(exc)
        logger.error(f"El motor de audio NO arrancó: {exc}")

    logger.info("API lista.")
    yield
    # ── SHUTDOWN ─────────────────────────────────────────────────────────
    logger.info("Apagando Ónix FM API.")
    engine.stop()
    _normalizador.stop()
    _respaldos.stop()
    _locutor.stop()
    for ev in ENGINE_EVENTS:
        engine.bus.unsubscribe(ev, _on_engine_event)


app = FastAPI(
    title="Ónix FM — Radio Core API",
    description="Backend del sistema de gestión de audio para Ónix FM.",
    version="2.0.0",
    lifespan=lifespan,          # FIX: patrón lifespan moderno
)

app.include_router(reportes_router)
# La grilla (/relojes/grilla) se declara antes que /relojes/{id} dentro del router
relojes_api.engine = engine
app.include_router(relojes_api.router)
playlists_api.engine = engine
app.include_router(playlists_api.router)
app.include_router(identificadores_api.router)
app.include_router(ia_api.router)
locutor_api.engine = engine
locutor_api.servicio = _locutor
app.include_router(locutor_api.router)
cartuchera_api.normalizar_nombre = lambda nombre: _normalize_filename(nombre)
cartuchera_api.leer_duracion = lambda ruta: _probe_duration(ruta)
cartuchera_api.medir_volumen = _normalizador.avisar
comerciales_api.recargar_motor = lambda: engine.reload_config()
comerciales_api.replanificar = lambda: engine.plan_regenerar()
cartuchera_api.difundir = lambda evento, datos: _manager.broadcast(_ws_message(evento, datos))
voicetrack_api.medir_volumen = _normalizador.avisar
app.include_router(cartuchera_api.router)
app.include_router(comerciales_api.router)
app.include_router(voicetrack_api.router)
app.include_router(locuciones_api.router)
problemas_api.engine = engine
app.include_router(problemas_api.router)
app.include_router(respaldos_api.router)
app.include_router(red_router)
biblioteca_api.engine = engine
app.include_router(biblioteca_api.router)
app.include_router(auth_api.router)
_respaldos = respaldos_api.RespaldoWorker()

# Sin CORS abierto: solo las páginas de ONIX (mismo origen) usan la API. Con
# "*" cualquier web abierta en la PC del estudio (que entra sin contraseña)
# podía mandarle órdenes a ONIX.


@app.middleware("http")
async def control_de_acceso(request: Request, call_next):
    """
    Usuarios y roles (backend/api/auth.py). La PC del estudio pasa sin
    contraseña; las demás PCs necesitan sesión y permiso para la ruta.
    """
    ruta, metodo = request.url.path, request.method
    # Cambios solo desde páginas de ONIX: un Origin ajeno = otra web intentándolo
    origen = request.headers.get("origin")
    if metodo not in ("GET", "HEAD", "OPTIONS") and origen:
        if origen.split("://", 1)[-1] != request.headers.get("host", ""):
            return JSONResponse(status_code=403, content={"detail": "Origen no permitido"})
    if ruta.startswith(auth_api.LIBRES) or ruta == "/ws":
        return await call_next(request)          # el WebSocket se controla al conectar
    u = auth_api.usuario_de(request)
    if u is None:
        if ruta.startswith("/api/") or ruta.startswith("/stream"):
            return JSONResponse(status_code=401, content={"detail": "Inicia sesión"})
        return RedirectResponse(f"/login?volver={ruta}", status_code=303)
    if not auth_api.permiso(u["rol"], metodo, ruta):
        if ruta.startswith("/api/"):
            return JSONResponse(status_code=403, content={"detail": "Tu usuario no tiene permiso para esto"})
        return RedirectResponse("/admin/" if u["rol"] == "programacion" else "/", status_code=303)
    request.state.usuario = u
    return await call_next(request)


# Montar la carpeta de biblioteca interactiva para streaming.
# StaticFiles exige que el directorio exista al importar el módulo.
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/stream", StaticFiles(directory=LIBRARY_DIR), name="stream")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Debug Validation: Imprime exactamente qué campo falló para evitar 422 silenciosos.
    """
    logger.error(f"422 Validation Error en {request.url}: {exc.errors()}")
    # jsonable_encoder: los errores de validadores propios traen la excepción
    # en ctx y json.dumps no la sabe serializar (daba 500 en vez de 422).
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


# db_session y get_db provienen de backend.database (MariaDB / SQLAlchemy)






# ===========================================================================
# SECCIÓN: CONFIG BIBLIOTECA — Sistema Dinámico de Categorías
# ===========================================================================

# ---- Schemas Pydantic ----

class ValorCreateRequest(BaseModel):
    nombre_interno: str = Field(..., description="nombre_interno de la categoría destino (ej: 'voz', 'cat1')")
    valor: str          = Field(..., min_length=1, max_length=100, description="Nuevo valor a insertar")
    orden: Optional[int] = Field(default=None, description="Posición de orden (auto si no se especifica)")

    # FIX: @field_validator reemplaza al deprecado @validator de Pydantic v1.
    # La diferencia clave: field_validator recibe (cls, v) sin mode kwarg por
    # defecto, y debe decorarse indicando el nombre del campo como string.
    @field_validator("valor")
    @classmethod
    def valor_strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("nombre_interno")
    @classmethod
    def nombre_strip(cls, v: str) -> str:
        return v.strip().lower()


class ValorUpdateOrdenRequest(BaseModel):
    orden: int = Field(..., ge=0)


# ---- Helper interno ----

def _get_all_valid_values(conn: MariaDBConnection, nombre_interno: str) -> set[str]:
    """Retorna el set de valores activos para una categoría dado su nombre_interno."""
    row = conn.execute(
        "SELECT id FROM config_categorias_def WHERE nombre_interno = ? AND activo = 1",
        (nombre_interno,)
    ).fetchone()
    if not row:
        return set()
    rows = conn.execute(
        "SELECT valor FROM config_categorias_valores WHERE cat_id = ? AND activo = 1",
        (row["id"],)
    ).fetchall()
    return {r["valor"] for r in rows}


def _valores_activos_por_categoria(conn: MariaDBConnection) -> dict[str, dict[str, str]]:
    """Devuelve los mapeos {valor_upper: valor_oficial} activos para cada categoría."""
    rows = conn.execute("""
        SELECT cd.nombre_interno, cv.valor
        FROM config_categorias_valores cv
        JOIN config_categorias_def cd ON cd.id = cv.cat_id
        WHERE cv.activo = 1
    """).fetchall()
    cats = {"cat1": {}, "cat2": {}, "cat3": {}, "voz": {}}
    for r in rows:
        ni = r["nombre_interno"]
        if ni in cats and r["valor"]:
            val = r["valor"].strip()
            cats[ni][val.upper()] = val
    return cats


def _sanitizar_cat(val: Optional[str], cat_map: dict[str, str]) -> Optional[str]:
    """Retorna el valor oficial registrado o None si no existe en la categoría activa."""
    if not val or not str(val).strip():
        return None
    if not cat_map:
        return str(val).strip()
    return cat_map.get(str(val).strip().upper(), None)


def _validate_categoria_valor(
    conn: MariaDBConnection,
    nombre_interno: str,
    valor: Optional[str],
    campo_api: str,
) -> None:
    """
    El valor tiene que estar en la lista de la categoría (creada por el usuario). El sistema NO inventa géneros ni categorías:
    uno que no esté (o que se haya borrado) se rechaza con un mensaje claro.
    """
    if not valor or not valor.strip():
        return

    cat_def = conn.execute(
        "SELECT id, etiqueta_visible FROM config_categorias_def WHERE nombre_interno = ? AND activo = 1",
        (nombre_interno,)
    ).fetchone()

    if not cat_def:
        return

    existe = conn.execute(
        "SELECT 1 FROM config_categorias_valores WHERE cat_id = ? AND UPPER(valor) = UPPER(?) AND activo = 1",
        (cat_def["id"], valor.strip())
    ).fetchone()
    if not existe:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"«{valor.strip()}» no está en la lista de {cat_def['etiqueta_visible']}. Elige uno de la lista "
                   f"o créalo antes en Configuración › Biblioteca.")


# ---- Endpoints de Configuración ----

@app.get(
    "/api/v1/config/biblioteca/schema",
    summary="Schema de categorías de Biblioteca",
    tags=["Config Biblioteca"],
)
def get_biblioteca_schema(conn: MariaDBConnection = Depends(get_db)):
    """
    Retorna el schema completo de categorías activas con sus valores.
    El Frontend usa este endpoint para poblar todos los <select> dinámicamente.

    Ejemplo de respuesta:
    {
      "categorias": [
        {
          "id": 1,
          "nombre_interno": "cat1",
          "etiqueta_visible": "Género",
          "orden": 1,
          "valores": [
            {"id": 1, "valor": "Romántica", "orden": 1},
            ...
          ]
        },
        ...
      ]
    }
    """
    logger.info("GET /api/v1/config/biblioteca/schema → solicitando schema completo")

    categorias = conn.execute("""
        SELECT id, nombre_interno, etiqueta_visible, orden
        FROM config_categorias_def
        WHERE activo = 1
        ORDER BY orden ASC, nombre_interno ASC
    """).fetchall()

    resultado = []
    for cat in categorias:
        valores = conn.execute("""
            SELECT id, valor, orden, color
            FROM config_categorias_valores
            WHERE cat_id = ? AND activo = 1
            ORDER BY orden ASC, valor ASC
        """, (cat["id"],)).fetchall()

        resultado.append({
            "id": cat["id"],
            "nombre_interno": cat["nombre_interno"],
            "etiqueta_visible": cat["etiqueta_visible"],
            "orden": cat["orden"],
            "valores": [
                {"id": v["id"], "valor": v["valor"], "orden": v["orden"], "color": v["color"]}
                for v in valores
            ],
        })

    logger.info(f"  → Retornando {len(resultado)} categorías")
    return {"categorias": resultado}


# Columna de audios que guarda cada categoría
_COLUMNA_CATEGORIA = {"cat1": "subgenero", "cat2": "categoria", "cat3": "cat3", "voz": "genero_vocal"}


class ValorUpdateRequest(BaseModel):
    valor: Optional[str] = Field(None, min_length=1, max_length=100)
    orden: Optional[int] = None
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


@app.put("/api/v1/config/biblioteca/valores/{valor_id}", tags=["Config Biblioteca"],
         summary="Renombrar, reordenar o colorear un valor de categoría")
def update_categoria_valor(valor_id: int, body: ValorUpdateRequest, conn: MariaDBConnection = Depends(get_db)):
    """
    Renombrar también actualiza los audios que tenían el nombre viejo (si no,
    las canciones quedarían con una categoría que ya no existe).
    """
    v = conn.execute(
        "SELECT cv.id, cv.valor, cd.nombre_interno FROM config_categorias_valores cv"
        " JOIN config_categorias_def cd ON cd.id = cv.cat_id WHERE cv.id = ? AND cv.activo = 1",
        (valor_id,)).fetchone()
    if not v:
        raise HTTPException(status_code=404, detail="Valor no encontrado")
    actualizados = 0
    if body.valor is not None and body.valor.strip() != v["valor"]:
        nuevo = body.valor.strip()
        if conn.execute(
                "SELECT 1 FROM config_categorias_valores WHERE cat_id = (SELECT cat_id FROM config_categorias_valores"
                " WHERE id = ?) AND valor = ? AND activo = 1 AND id != ?", (valor_id, nuevo, valor_id)).fetchone():
            raise HTTPException(status_code=409, detail=f"Ya existe '{nuevo}' en esa categoría")
        conn.execute("UPDATE config_categorias_valores SET valor = ? WHERE id = ?", (nuevo, valor_id))
        col = _COLUMNA_CATEGORIA.get(v["nombre_interno"])
        if col:
            actualizados = conn.execute(f"UPDATE audios SET {col} = ? WHERE UPPER({col}) = UPPER(?)",
                                        (nuevo, v["valor"])).rowcount
    if body.orden is not None:
        conn.execute("UPDATE config_categorias_valores SET orden = ? WHERE id = ?", (body.orden, valor_id))
    if body.color is not None:
        conn.execute("UPDATE config_categorias_valores SET color = ? WHERE id = ?", (body.color, valor_id))
    conn.commit()
    return {"id": valor_id, "audios_actualizados": actualizados}


class CategoriaUpdateRequest(BaseModel):
    etiqueta_visible: str = Field(..., min_length=1, max_length=60)


@app.put("/api/v1/config/biblioteca/categorias/{nombre_interno}", tags=["Config Biblioteca"],
         summary="Renombrar una categoría (p. ej. 'Categoría 1' → 'Género')")
def update_categoria(nombre_interno: str, body: CategoriaUpdateRequest, conn: MariaDBConnection = Depends(get_db)):
    if not conn.execute("UPDATE config_categorias_def SET etiqueta_visible = ? WHERE nombre_interno = ? AND activo = 1",
                        (body.etiqueta_visible.strip(), nombre_interno)).rowcount:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")
    conn.commit()
    return {"nombre_interno": nombre_interno, "etiqueta_visible": body.etiqueta_visible.strip()}


@app.post(
    "/api/v1/config/biblioteca/valores",
    status_code=status.HTTP_201_CREATED,
    summary="Agregar valor a una categoría",
    tags=["Config Biblioteca"],
)
def add_categoria_valor(
    body: ValorCreateRequest,
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Agrega una nueva opción a una categoría existente.
    Ejemplo: añadir 'Trío' a la categoría 'voz'.

    Body:
        { "nombre_interno": "voz", "valor": "Trío" }
    """
    logger.info(
        f"POST /api/v1/config/biblioteca/valores → "
        f"cat='{body.nombre_interno}', valor='{body.valor}'"
    )

    cat = conn.execute(
        "SELECT id, etiqueta_visible FROM config_categorias_def "
        "WHERE nombre_interno = ? AND activo = 1",
        (body.nombre_interno,)
    ).fetchone()

    if not cat:
        logger.warning(f"  → Categoría '{body.nombre_interno}' no encontrada")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"La categoría '{body.nombre_interno}' no existe. "
                   f"Categorías disponibles: cat1, cat2, cat3, voz"
        )

    # Calcular orden automático si no se especifica
    if body.orden is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(orden), 0) + 1 as next_orden "
            "FROM config_categorias_valores WHERE cat_id = ?",
            (cat["id"],)
        ).fetchone()
        orden = row["next_orden"]
    else:
        orden = body.orden

    # «Pop» y «POP» (o «Dúo» y «DUO») son el MISMO valor: no se crea uno repetido distinto solo en mayúsculas o tildes
    valor_nuevo = " ".join(body.valor.split())
    body.valor = valor_nuevo
    repetido = conn.execute(
        "SELECT valor, activo FROM config_categorias_valores WHERE cat_id = ? AND UPPER(valor) = UPPER(?)",
        (cat["id"], valor_nuevo)).fetchone()
    if repetido and repetido["activo"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe «{repetido['valor']}» en {cat['etiqueta_visible']}: no se crea otro igual")
    # Un valor borrado antes (soft-delete) se reactiva en vez de chocar con él
    borrado = conn.execute(
        "SELECT id FROM config_categorias_valores WHERE cat_id = ? AND UPPER(valor) = UPPER(?) AND activo = 0",
        (cat["id"], valor_nuevo)).fetchone()
    try:
        if borrado:
            conn.execute("UPDATE config_categorias_valores SET activo = 1, orden = ? WHERE id = ?",
                         (orden, borrado["id"]))
            nuevo_id = borrado["id"]
        else:
            cursor = conn.execute("""
                INSERT INTO config_categorias_valores (cat_id, valor, orden)
                VALUES (?, ?, ?)
            """, (cat["id"], body.valor, orden))
            nuevo_id = cursor.lastrowid
        logger.info(
            f"  → Valor '{body.valor}' añadido a '{body.nombre_interno}' "
            f"(id={nuevo_id}, orden={orden})"
        )
    except DualIntegrityError:
        logger.warning(
            f"  → Conflicto: valor '{body.valor}' ya existe en '{body.nombre_interno}'"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"El valor '{body.valor}' ya existe en la categoría '{body.nombre_interno}'"
        )

    return {
        "mensaje": f"Valor '{body.valor}' añadido correctamente a '{cat['etiqueta_visible']}'",
        "data": {
            "id": nuevo_id,
            "cat_id": cat["id"],
            "nombre_interno": body.nombre_interno,
            "etiqueta_visible": cat["etiqueta_visible"],
            "valor": body.valor,
            "orden": orden,
        }
    }


@app.get(
    "/api/v1/config/biblioteca/valores/{valor_id}/uso",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Consultar el uso de un valor de categoría antes de eliminar",
    tags=["Config Biblioteca"],
)
def get_categoria_valor_uso(
    valor_id: int,
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Retorna cuántas canciones activas tienen asignado este valor y las alternativas
    disponibles para reasignación en el Modal PRO 2026.
    """
    valor = conn.execute("""
        SELECT cv.id, cv.valor, cv.cat_id, cd.nombre_interno, cd.etiqueta_visible
        FROM config_categorias_valores cv
        JOIN config_categorias_def cd ON cd.id = cv.cat_id
        WHERE cv.id = ? AND cv.activo = 1
    """, (valor_id,)).fetchone()

    if not valor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró ningún valor activo con id={valor_id}"
        )

    campo_map = {
        "cat1": "subgenero",
        "cat2": "categoria",
        "cat3": "cat3",
        "voz":  "genero_vocal",
    }
    campo_db = campo_map.get(valor["nombre_interno"])
    total_audios = 0
    ejemplos = []

    if campo_db:
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM audios WHERE {campo_db} = ? AND activo = 1",
            (valor["valor"],)
        ).fetchone()
        total_audios = row["cnt"] if row else 0

        if total_audios > 0:
            filas_ej = conn.execute(
                f"SELECT titulo, artista FROM audios WHERE {campo_db} = ? AND activo = 1 ORDER BY id DESC LIMIT 5",
                (valor["valor"],)
            ).fetchall()
            ejemplos = [f"{r['titulo']} - {r['artista']}" if r['artista'] else r['titulo'] for r in filas_ej]

    # Alternativas para reasignar (otros valores activos de la misma categoría)
    alternativas = conn.execute("""
        SELECT id, valor FROM config_categorias_valores
        WHERE cat_id = ? AND activo = 1 AND id != ?
        ORDER BY orden ASC, valor ASC
    """, (valor["cat_id"], valor_id)).fetchall()

    return {
        "id": valor_id,
        "valor": valor["valor"],
        "nombre_interno": valor["nombre_interno"],
        "etiqueta_visible": valor["etiqueta_visible"],
        "total_audios": total_audios,
        "ejemplos": ejemplos,
        "alternativas": [{"id": a["id"], "valor": a["valor"]} for a in alternativas],
    }


@app.delete(
    "/api/v1/config/biblioteca/valores/{valor_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Eliminar valor de una categoría con resolución inteligente",
    tags=["Config Biblioteca"],
)
def delete_categoria_valor(
    valor_id: int,
    accion: Optional[str] = "desasignar",
    reasignar_a_id: Optional[int] = None,
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Elimina (soft-delete) un valor de categoría resolviendo los audios asignados:
      - accion='reasignar': traslada las canciones a `reasignar_a_id`.
      - accion='desasignar': deja las canciones sin categoría (campo en NULL).
    """
    logger.info(f"DELETE /api/v1/config/biblioteca/valores/{valor_id}?accion={accion}&reasignar={reasignar_a_id}")

    valor = conn.execute("""
        SELECT cv.id, cv.valor, cv.cat_id, cd.nombre_interno, cd.etiqueta_visible
        FROM config_categorias_valores cv
        JOIN config_categorias_def cd ON cd.id = cv.cat_id
        WHERE cv.id = ? AND cv.activo = 1
    """, (valor_id,)).fetchone()

    if not valor:
        logger.warning(f"  → Valor id={valor_id} no encontrado o ya inactivo")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró ningún valor activo con id={valor_id}"
        )

    campo_map = {
        "cat1": "subgenero",
        "cat2": "categoria",
        "cat3": "cat3",
        "voz":  "genero_vocal",
    }
    campo_db = campo_map.get(valor["nombre_interno"])
    audios_afectados = 0
    if campo_db:
        try:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM audios WHERE {campo_db} = ? AND activo = 1",
                (valor["valor"],)
            ).fetchone()
            audios_afectados = row["cnt"] if row else 0
        except Exception:
            pass

    nuevo_valor_str = None
    if audios_afectados > 0 and campo_db:
        if accion == "reasignar":
            if not reasignar_a_id:
                raise HTTPException(status_code=400, detail="Debe indicar 'reasignar_a_id' para reasignar.")
            dest = conn.execute(
                "SELECT valor FROM config_categorias_valores WHERE id = ? AND cat_id = ? AND activo = 1",
                (reasignar_a_id, valor["cat_id"])
            ).fetchone()
            if not dest:
                raise HTTPException(status_code=400, detail="La categoría de destino no es válida o está inactiva.")
            nuevo_valor_str = dest["valor"]
            conn.execute(
                f"UPDATE audios SET {campo_db} = ? WHERE {campo_db} = ? AND activo = 1",
                (nuevo_valor_str, valor["valor"])
            )
            logger.info(f"  → Reasignados {audios_afectados} audios de '{valor['valor']}' a '{nuevo_valor_str}'.")
        else:
            # desasignar: poner a NULL para que no muestren categorías borradas
            conn.execute(
                f"UPDATE audios SET {campo_db} = NULL WHERE {campo_db} = ? AND activo = 1",
                (valor["valor"],)
            )
            logger.info(f"  → Desasignados {audios_afectados} audios de '{valor['valor']}'.")

    # Soft-delete en catálogo
    conn.execute(
        "UPDATE config_categorias_valores SET activo = 0 WHERE id = ?",
        (valor_id,)
    )
    conn.commit()

    if audios_afectados > 0:
        if accion == "reasignar":
            msg = f"Categoría '{valor['valor']}' eliminada. {audios_afectados} canción(es) reasignada(s) a '{nuevo_valor_str}'."
        else:
            msg = f"Categoría '{valor['valor']}' eliminada. {audios_afectados} canción(es) quedaron sin categoría."
    else:
        msg = f"Categoría '{valor['valor']}' eliminada de '{valor['etiqueta_visible']}'."

    return {
        "mensaje": msg,
        "audios_afectados": audios_afectados,
        "accion": accion if audios_afectados > 0 else "directa",
        "nuevo_valor": nuevo_valor_str,
        "data": {
            "id": valor_id,
            "valor": valor["valor"],
            "nombre_interno": valor["nombre_interno"],
        }
    }



# ===========================================================================
# SECCIÓN: AUDIOS — Endpoints refactorizados con validación dinámica
# ===========================================================================

# ---- Schemas Pydantic ----

class AudioTrack(BaseModel):
    """Modelo completo de audio para el Editor de Biblioteca Musical (modal).
    Incluye todos los cue points del editor de forma Jazler."""
    titulo:            str           = Field(..., min_length=1, max_length=200)
    artista:           str           = Field(..., min_length=1, max_length=200)
    album:             Optional[str] = Field(default=None, max_length=200)
    archivo_path:      Optional[str] = Field(default=None, description="Ruta existente en library")
    duracion:          Optional[int] = Field(default=0, ge=0)
    bpm:               Optional[int] = Field(default=None, ge=40, le=300)
    fecha_lanzamiento: Optional[str] = Field(default=None)
    epoca:             Optional[str] = Field(default=None)
    # Categorías dinámicas
    cat1:  Optional[str] = Field(default=None, description="Género")
    cat2:  Optional[str] = Field(default=None, description="Rotación")
    cat3:  Optional[str] = Field(default=None, description="Subgénero")
    voz:   Optional[str] = Field(default=None, description="Voz/Tipo")
    # Cue points retrocompatibles
    intro: Optional[float] = Field(default=None)
    outro: Optional[float] = Field(default=None)
    hook:  Optional[float] = Field(default=None)
    # Cue points extendidos (6 marcadores Jazler)
    cue_inicio:      Optional[float] = Field(default=None)
    cue_intro:       Optional[float] = Field(default=None)
    cue_inicio_coro: Optional[float] = Field(default=None)
    cue_final_coro:  Optional[float] = Field(default=None)
    cue_mezcla:      Optional[float] = Field(default=None)
    fade_in:         Optional[float] = Field(default=None)
    fade_out:        Optional[float] = Field(default=None)


class AudioCreateRequest(BaseModel):
    titulo:             str           = Field(..., min_length=1, max_length=200)
    artista:            str           = Field(..., min_length=1, max_length=200)
    album:              Optional[str] = Field(default=None, max_length=200)
    archivo_path:       str           = Field(..., description="Ruta en STAGING_DIR del archivo subido")
    duracion:           Optional[int] = Field(default=0, ge=0, description="Duración en segundos")
    bpm:                Optional[int] = Field(default=None, ge=40, le=300)
    fecha_lanzamiento:  Optional[str] = Field(default=None, description="YYYY o YYYY-MM-DD")
    epoca:              Optional[str] = Field(default=None)
    # Campos de categoría dinámica (nombres del frontend Jazler)
    cat1:  Optional[str] = Field(default=None, description="Género → config cat1")
    cat2:  Optional[str] = Field(default=None, description="Rotación → config cat2")
    cat3:  Optional[str] = Field(default=None, description="Subgénero → config cat3")
    voz:   Optional[str] = Field(default=None, description="Voz/Tipo → config voz")


# Tope de la regla "volver a sonar cada…": 3 años en horas (el editor la expresa en horas/días/semanas/meses)
MAX_REPETICION_HORAS = 24 * 365 * 3


class AudioUpdateRequest(BaseModel):
    titulo:            Optional[str] = Field(default=None, min_length=1, max_length=200)
    artista:           Optional[str] = Field(default=None, min_length=1, max_length=200)
    album:             Optional[str] = Field(default=None, max_length=200)
    duracion:          Optional[int] = Field(default=None, ge=0)
    bpm:               Optional[int] = Field(default=None, ge=40, le=300)
    fecha_lanzamiento: Optional[str] = Field(default=None)
    epoca:             Optional[str] = Field(default=None)
    cat1:  Optional[str] = Field(default=None)
    cat2:  Optional[str] = Field(default=None)
    cat3:  Optional[str] = Field(default=None)
    voz:   Optional[str] = Field(default=None)
    tipo_audio: Optional[str] = Field(default=None, description="Musica | Cuña | Efecto | Sweeper | Programa | Tips")
    ganancia_db: Optional[float] = Field(default=None, ge=-20, le=6,
                                         description="Ajuste manual de volumen sobre la normalización (dB)")
    origen:  Optional[str] = Field(default=None, description="NACIONAL | INTERNACIONAL")
    energia: Optional[int] = Field(default=None, ge=1, le=5)
    # Cue points retrocompatibles
    intro: Optional[float] = Field(default=None)
    outro: Optional[float] = Field(default=None)
    hook:  Optional[float] = Field(default=None)
    # Cue points extendidos (6 marcadores Jazler)
    cue_inicio:      Optional[float] = Field(default=None)
    cue_intro:       Optional[float] = Field(default=None)
    cue_inicio_coro: Optional[float] = Field(default=None)
    cue_final_coro:  Optional[float] = Field(default=None)
    cue_mezcla:      Optional[float] = Field(default=None)
    fade_in:         Optional[float] = Field(default=None)
    fade_out:        Optional[float] = Field(default=None)
    cue_outro:       Optional[float] = Field(default=None, description="Fin de la voz (no es la mezcla)")
    prioridad:       Optional[int]   = Field(default=None, ge=1, le=3)
    # «Publicar metadata»: '' = según el tipo, 'actualizar' o 'no_actualizar'; título/artista vacío = AUTO
    meta_modo:       Optional[str]   = Field(default=None, pattern="^(|actualizar|no_actualizar)$")
    meta_titulo:     Optional[str]   = Field(default=None, max_length=200)
    meta_artista:    Optional[str]   = Field(default=None, max_length=200)
    meta_extra1:     Optional[str]   = Field(default=None, max_length=200)
    meta_extra2:     Optional[str]   = Field(default=None, max_length=200)
    repeticion_horas: Optional[int]  = Field(default=None, ge=0, le=MAX_REPETICION_HORAS,
                                              description="No repetir hasta N horas después (0 = sin regla)")


# Tipos de audio válidos (audios.tipo_audio). Solo 'Musica' entra en rotación.
TIPOS_AUDIO = ("Musica", "Cuña", "Efecto", "Sweeper", "Programa", "Tips", "Locucion")   # Locucion = voice track


def _validate_tipo_audio(valor: Optional[str]) -> Optional[str]:
    """Devuelve el tipo con su escritura canónica; 422 si no existe."""
    if valor is None or not valor.strip():
        return None
    for tipo in TIPOS_AUDIO:
        if tipo.lower() == valor.strip().lower():
            return tipo
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"tipo_audio inválido: '{valor}'. Valores: {', '.join(TIPOS_AUDIO)}",
    )


ORIGENES = ("NACIONAL", "INTERNACIONAL")


def _validate_origen(valor: Optional[str]) -> Optional[str]:
    if valor is None or not valor.strip():
        return None
    v = valor.strip().upper()
    if v not in ORIGENES:
        raise HTTPException(status_code=422, detail=f"origen inválido: '{valor}'. Valores: {', '.join(ORIGENES)}")
    return v


# ===========================================================================
# SECCIÓN: UPLOAD — Subida de archivos de audio con nombre normalizado
# ===========================================================================

def _normalize_filename(name: str) -> str:
    """
    Normaliza el nombre de un archivo de audio para que sea seguro en disco
    y no genere errores en el motor de audio BASS:

      1. NFKD descompone caracteres compuestos (á → a + ́, ñ → n + ~)
      2. encode('ascii', 'ignore') elimina los acentos descompuestos
      3. Regex reemplaza cualquier caracter no alfanumérico (excepto ._-) por '_'
      4. Se colapsan underscores consecutivos y se eliminan al inicio/final

    Ejemplos:
      "Amor Etérneo  (Live).mp3"  →  "Amor_Eterno_Live.mp3"
      "Año Nuevo - ¡Fiesta!.mp3"  →  "Ano_Nuevo_Fiesta.mp3"
      "track 01   .mp3"           →  "track_01.mp3"
    """
    # Separar nombre y extensión
    p = Path(name)
    stem      = p.stem
    extension = p.suffix  # incluye el punto, ej: ".mp3"

    # NFKD + eliminar caracteres no-ASCII (tildes, diacríticos)
    stem_nfkd = unicodedata.normalize("NFKD", stem)
    stem_ascii = stem_nfkd.encode("ascii", "ignore").decode("ascii")

    # Reemplazar caracteres no seguros por '_'
    stem_safe = re.sub(r"[^\w\-.]", "_", stem_ascii)

    # Colapsar múltiples '_' consecutivos
    stem_clean = re.sub(r"_+", "_", stem_safe).strip("_")

    # Si quedó vacío, usar fallback
    if not stem_clean:
        stem_clean = "audio"

    return stem_clean + extension.lower()


@app.post(
    "/api/v1/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Subir archivo de audio a la Biblioteca o Comercial",
    tags=["Audios"],
)
async def upload_audio_file(
    file: UploadFile = File(...),
    tipo: Optional[str] = Form(None),
    subdir: Optional[str] = Form(None),
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Recibe un archivo de audio, normaliza su nombre (sin tildes ni espacios).
    Si tipo == 'pauta', lo guarda en MEDIA_ROOT/COMERCIALES/YYYY/.
    De lo contrario, lo guarda en MUSICA_DIR (library/musica/).

    Retorna la ruta del archivo lista para guardar en el campo `archivo_path`
    de la tabla `audios` al crear/actualizar un registro vía POST /api/v1/audios.

    Regla de Oro: el nombre normalizado evita errores del motor BASS con rutas
    que contengan espacios, tildes o caracteres especiales.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El archivo no tiene nombre."
        )

    # Validación básica de tipo MIME
    content_type = file.content_type or ""
    if not (content_type.startswith("audio/") or file.filename.lower().endswith(
            (".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma"))):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Tipo de archivo no soportado: '{file.content_type}'. "
                   f"Se aceptan archivos de audio (mp3, wav, ogg, flac, aac, m4a, wma)."
        )

    # Normalizar nombre
    filename_normalizado = _normalize_filename(file.filename)

    # pautas-controller.js marca los comerciales con subdir=MEDIA/COMERCIALES/{año}
    if subdir and "COMERCIALES" in subdir.upper():
        tipo = "pauta"

    if tipo == "pauta":
        year = str(datetime.now().year)
        base_dest_dir = MEDIA_ROOT / "COMERCIALES" / year
        base_dest_dir.mkdir(parents=True, exist_ok=True)
        dest = base_dest_dir / filename_normalizado
    else:
        dest = MUSICA_DIR / filename_normalizado

    # Evitar colisiones: si existe, añadir sufijo numérico
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            if tipo == "pauta":
                dest = base_dest_dir / f"{stem}_{counter}{suffix}"
            else:
                dest = MUSICA_DIR / f"{stem}_{counter}{suffix}"
            counter += 1

    # Guardar en disco
    try:
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as exc:
        logger.error(f"[Upload] Error guardando archivo: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo guardar el archivo: {exc}"
        )

    logger.info(f"[Upload] Archivo guardado: {dest} (original: {file.filename!r})")

    # Un comercial se registra de una vez en la Biblioteca como 'Cuña', para
    # que la pauta pueda referenciarlo por audio_id sin pasos manuales.
    audio_id = None
    if tipo == "pauta":
        titulo = Path(file.filename).stem.strip().upper() or dest.stem.upper()
        # Las cuñas se identifican por su TIPO («Cuña»), no por un género: no entran a la lista de géneros de la Biblioteca Musical
        cursor = conn.execute(
            "INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio)"
            " VALUES (?, 'PUBLICIDAD', ?, ?, 'Cuña')",
            (titulo, _probe_duration(dest), to_stored(dest)),
        )
        audio_id = cursor.lastrowid
        conn.commit()
        _normalizador.avisar()
        await _manager.broadcast(_ws_message(
            "library_updated", {"action": "create", "id": audio_id, "titulo": titulo},
        ))

    return {
        "mensaje":    "Archivo subido correctamente",
        "filename":   filename_normalizado,
        "archivo_path": to_stored(dest),
        "audio_id":   audio_id,
        "size_bytes": dest.stat().st_size,
    }


def _probe_duration(path: Path) -> int:
    """Duración en segundos leída de las cabeceras del audio (0 si no se puede)."""
    try:
        from mutagen import File as MutagenFile
        info = MutagenFile(str(path))
        if info is not None and info.info.length > 0:
            return int(round(info.info.length))
    except Exception as exc:
        logger.warning(f"  → No se pudo leer la duración de {path}: {exc}")
    return 0


def _normalize_media_paths() -> None:
    """
    Convierte al formato actual (relativo a MEDIA_ROOT, con "/") las rutas
    guardadas por versiones anteriores, p. ej. "\\media\\onix\\library\\X\\a.mp3".
    """
    with db_session() as conn:
        rows = conn.execute("SELECT id, archivo_path FROM audios").fetchall()
        for row in rows:
            nuevo = normalize_stored(row["archivo_path"])
            if nuevo != row["archivo_path"]:
                try:
                    conn.execute("UPDATE audios SET archivo_path = ? WHERE id = ?", (nuevo, row["id"]))
                    logger.info(f"  → Ruta normalizada: audio id={row['id']} → {nuevo}")
                except DualIntegrityError:
                    logger.warning(f"  → Ruta duplicada, no se normaliza: audio id={row['id']}")


def _backfill_durations() -> None:
    """Completa la duración de audios registrados con 0 cuyo archivo existe."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT id, archivo_path FROM audios WHERE COALESCE(duracion, 0) = 0 AND activo = 1"
        ).fetchall()
        for row in rows:
            path = resolve_media(row["archivo_path"])
            duracion = _probe_duration(path) if path.is_file() else 0
            if duracion:
                conn.execute("UPDATE audios SET duracion = ? WHERE id = ?", (duracion, row["id"]))
                logger.info(f"  → Duración completada: audio id={row['id']} = {duracion}s")


# ---- Helpers de archivos (Copy-and-Delete) ----

def _move_to_library(staging_path: str, titulo: str, artista: str) -> str:
    """
    Estrategia Copy-and-Delete:
    1. Copia el archivo de staging → library/<artista>/
    2. Elimina el original en staging
    3. Retorna la ruta final en library
    """
    src = Path(staging_path)
    if not src.exists():
        # Intentar relativo a STAGING_DIR
        src = STAGING_DIR / staging_path

    if not src.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Archivo no encontrado en staging: '{staging_path}'"
        )

    # Construir destino
    artista_slug = "".join(c if c.isalnum() or c in " _-" else "_" for c in artista).strip()
    dest_dir = LIBRARY_DIR / artista_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    # Evitar sobrescribir
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    logger.info(f"  [Copy-and-Delete] COPY: {src} → {dest}")
    shutil.copy2(src, dest)

    logger.info(f"  [Copy-and-Delete] DELETE: {src}")
    src.unlink()

    return str(dest)


def _categoria_canonica(conn: MariaDBConnection, nombre_interno: str, valor: Optional[str]) -> Optional[str]:
    """La escritura ya registrada ("Pop") en vez de otra ("POP"): una sola categoría, no dos."""
    if not valor or not str(valor).strip():
        return None
    fila = conn.execute(
        "SELECT cv.valor FROM config_categorias_valores cv JOIN config_categorias_def cd ON cd.id = cv.cat_id"
        " WHERE cd.nombre_interno = ? AND UPPER(cv.valor) = UPPER(?) AND cv.activo = 1",
        (nombre_interno, str(valor).strip())).fetchone()
    return fila[0] if fila else str(valor).strip()


def _categoria_de_la_lista(conn: MariaDBConnection, nombre_interno: str, valor: Optional[str],
                           etiqueta: str, avisos: list) -> Optional[str]:
    """El valor con su escritura oficial si está en la lista; si no, None y un aviso (nunca se inventa uno)."""
    canon = _categoria_canonica(conn, nombre_interno, valor)
    if not canon:
        return None
    try:
        _validate_categoria_valor(conn, nombre_interno, canon, etiqueta)
    except HTTPException:
        avisos.append(f"{etiqueta.capitalize()} «{canon}» no está en la lista: la canción queda sin {etiqueta}.")
        return None
    return canon


def _validate_all_categorias(
    conn: MariaDBConnection,
    cat1: Optional[str],
    cat2: Optional[str],
    cat3: Optional[str],
    voz: Optional[str],
) -> None:
    """Acepta y auto-registra los 4 campos de categoría en las tablas dinámicas."""
    _validate_categoria_valor(conn, "cat1", cat1, "cat1 (Género)")
    _validate_categoria_valor(conn, "cat2", cat2, "cat2 (Rotación)")
    _validate_categoria_valor(conn, "cat3", cat3, "cat3 (Subgénero)")
    _validate_categoria_valor(conn, "voz",  voz,  "voz (Voz/Tipo)")


# ---- POST /api/v1/audios ----

@app.post(
    "/api/v1/audios",
    status_code=status.HTTP_201_CREATED,
    summary="Crear nuevo audio en Biblioteca con archivo subido directamente",
    tags=["Audios"],
)
async def create_audio(
    titulo: str = Form(..., min_length=1, max_length=200),
    artista: str = Form(..., min_length=1, max_length=200),
    album: Optional[str] = Form(None, max_length=200),
    duracion: Optional[int] = Form(0, ge=0),
    bpm: Optional[int] = Form(None, ge=40, le=300),
    fecha_lanzamiento: Optional[str] = Form(None),
    cat1: Optional[str] = Form(None),
    cat2: Optional[str] = Form(None),
    cat3: Optional[str] = Form(None),
    voz: Optional[str] = Form(None),
    tipo_audio: Optional[str] = Form(None),
    origen: Optional[str] = Form(None),
    energia: Optional[int] = Form(None, ge=1, le=5),
    intro: Optional[float] = Form(None),
    outro: Optional[float] = Form(None),
    hook: Optional[float] = Form(None),
    # Cue points extendidos (6 marcadores Jazler)
    cue_inicio:      Optional[float] = Form(None),
    cue_intro:       Optional[float] = Form(None),
    cue_inicio_coro: Optional[float] = Form(None),
    cue_final_coro:  Optional[float] = Form(None),
    cue_mezcla:      Optional[float] = Form(None),
    fade_in:         Optional[float] = Form(None),
    fade_out:        Optional[float] = Form(None),
    file: UploadFile = File(...),
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Registra un nuevo audio recibiendo directamente el UploadFile y FormData.
    """
    logger.info(
        f"POST /api/v1/audios (UploadFile) → titulo='{titulo}', "
        f"artista='{artista}', archivo='{file.filename}'"
    )

    # REGLA CRÍTICA: Estandarización a MAYÚSCULAS
    titulo  = titulo.upper()
    artista = artista.upper()
    if album: album = album.upper()
    # Solo valores de la lista, con su escritura oficial ("Pop", no "POP"). Lo que venga de las etiquetas del archivo y no esté
    # en la lista se descarta con un aviso: el sistema no inventa géneros (la canción queda «Por completar»).
    avisos: list[str] = []
    cat1 = _categoria_de_la_lista(conn, "cat1", cat1, "género", avisos)
    cat2 = _categoria_de_la_lista(conn, "cat2", cat2, "rotación", avisos)
    cat3 = _categoria_de_la_lista(conn, "cat3", cat3, "subgénero", avisos)
    voz  = _categoria_de_la_lista(conn, "voz", voz, "voz", avisos)

    # PASO 1: Validación dinámica de categorías
    _validate_all_categorias(conn, cat1, cat2, cat3, voz)
    tipo_audio = _validate_tipo_audio(tipo_audio) or "Musica"
    origen = _validate_origen(origen)
    logger.info("  → Validación de categorías: PASS")

    # PASO 2: Guardar el archivo subido en LIBRARY_DIR directamente
    artista_slug = "".join(c if c.isalnum() or c in " _-" else "_" for c in artista).strip()
    dest_dir = LIBRARY_DIR / artista_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # REGLA DE ORO: Normalizar nombre para evitar errores de motor BASS y flatear sub-carpetas
    nombre_seguro = _normalize_filename(file.filename)
    dest = dest_dir / nombre_seguro

    # Manejar posibles colisiones de nombre
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    # Guardar en disco
    try:
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"  → Archivo guardado guardado en: {dest}")
    except Exception as e:
        logger.error(f"  → Error guardando archivo: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo al guardar el archivo: {e}"
        )

    # Duración real leída del archivo (el frontend suele enviar 0)
    if not duracion:
        duracion = _probe_duration(dest)

    # Época calculada automáticamente a partir del año de lanzamiento
    epoca = calcular_epoca(fecha_lanzamiento)

    # PASO 3: Insertar en DB
    try:
        cursor = conn.execute("""
            INSERT INTO audios (
                titulo, artista, album, duracion, archivo_path,
                subgenero, categoria, cat3, genero_vocal,
                bpm, intro, outro, hook, fecha_lanzamiento, epoca,
                cue_inicio, cue_intro, cue_inicio_coro, cue_final_coro,
                cue_mezcla, fade_in, fade_out, tipo_audio, origen, energia, aprobada
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            titulo,
            artista,
            album,
            duracion or 0,
            to_stored(dest),
            cat1,             # subgenero
            cat2,             # categoria
            cat3,             # cat3
            voz,              # genero_vocal
            bpm,
            intro,
            outro,
            hook,
            fecha_lanzamiento,
            epoca,
            cue_inicio,
            cue_intro,
            cue_inicio_coro,
            cue_final_coro,
            cue_mezcla,
            fade_in,
            fade_out,
            tipo_audio,
            origen,
            energia,
            # La música nueva entra a la Bandeja y no suena en automático hasta aprobarla, salvo que ya venga
            # clasificada (género y rotación): sin eso el reloj no sabría dónde ponerla. Lo que no es música
            # (cuñas, efectos, locuciones) no pasa por la Bandeja.
            1 if (tipo_audio != "Musica" or (cat1 and cat2)) else 0,
        ))
        nuevo_id = cursor.lastrowid

    except DualIntegrityError as e:
        logger.error(f"  → DB IntegrityError: {e}. Revirtiendo archivo...")
        if dest.exists():
            dest.unlink()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un registro con ese archivo_path: {dest}"
        )

    logger.info(f"  → Audio creado con id={nuevo_id}")

    # Confirmar ANTES de avisar: los clientes recargan la biblioteca al recibir
    # el evento y la dependencia get_db hace commit después de responder.
    conn.commit()
    _normalizador.avisar()

    # Broadcast WebSocket — notificar a todos los módulos
    await _manager.broadcast(_ws_message(
        "library_updated",
        {"action": "create", "id": nuevo_id, "titulo": titulo, "artista": artista},
    ))

    return {
        "mensaje": "Audio creado exitosamente",
        # ¿Suena en automático ya? Si falta género o rotación queda «Por completar» y aquí se dice por qué
        "lista_para_sonar": bool(tipo_audio != "Musica" or (cat1 and cat2)),
        "faltan": [] if tipo_audio != "Musica" else ([] if cat1 else ["género"]) + ([] if cat2 else ["rotación"]),
        "avisos": avisos,
        "data": {
            "id": nuevo_id,
            "titulo": titulo,
            "artista": artista,
            "archivo_path": to_stored(dest),
            "tipo_audio": tipo_audio,
            "cat1": cat1,
            "cat2": cat2,
            "cat3": cat3,
            "voz": voz,
            "intro": intro,
            "outro": outro,
            "hook": hook,
            "cue_inicio":      cue_inicio,
            "cue_intro":       cue_intro,
            "cue_inicio_coro": cue_inicio_coro,
            "cue_final_coro":  cue_final_coro,
            "cue_mezcla":      cue_mezcla,
            "fade_in":         fade_in,
            "fade_out":        fade_out,
            "fecha_lanzamiento": fecha_lanzamiento,
            "epoca":           epoca,
        }
    }


# ---- PUT /api/v1/audios/{audio_id} ----

@app.put(
    "/api/v1/audios/{audio_id}",
    summary="Actualizar audio existente",
    tags=["Audios"],
)
async def update_audio(
    audio_id: int,
    body: AudioUpdateRequest,
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Actualiza los metadatos de un audio existente.
    Solo actualiza los campos enviados (PATCH parcial sobre PUT).
    Valida categorías dinámicamente antes de persistir.
    """
    logger.info(f"PUT /api/v1/audios/{audio_id} → payload={body.dict(exclude_none=True)}")

    # Verificar existencia
    audio = conn.execute(
        "SELECT * FROM audios WHERE id = ? AND activo = 1", (audio_id,)
    ).fetchone()

    if not audio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio con id={audio_id} no encontrado o inactivo"
        )

    # Determinar valores finales (nuevo o el existente si no se envía)
    if body.cat1 is not None: body.cat1 = _categoria_canonica(conn, "cat1", body.cat1)
    if body.cat2 is not None: body.cat2 = _categoria_canonica(conn, "cat2", body.cat2)
    if body.cat3 is not None: body.cat3 = _categoria_canonica(conn, "cat3", body.cat3)
    if body.voz  is not None: body.voz  = _categoria_canonica(conn, "voz", body.voz)
    cat1_final = body.cat1 if body.cat1 is not None else audio["subgenero"]
    cat2_final = body.cat2 if body.cat2 is not None else audio["categoria"]
    cat3_final = body.cat3 if body.cat3 is not None else audio["cat3"]
    voz_final  = body.voz  if body.voz  is not None else audio["genero_vocal"]

    # Solo se validan los campos que vienen en la petición: una canción antigua con un valor que hoy no está en la lista
    # sigue pudiendo editar su título; pero NO se puede poner un género que no exista.
    _validate_all_categorias(conn, body.cat1, body.cat2, body.cat3, body.voz)
    logger.info("  → Validación de categorías: PASS")

    # Construir SET dinámico con los campos enviados
    campos_update = {}
    # Mapeo Jazler → DB (categorías)
    # REGLA CRÍTICA: Estandarización a MAYÚSCULAS
    if body.titulo   is not None: campos_update["titulo"]          = body.titulo.upper()
    if body.artista  is not None: campos_update["artista"]         = body.artista.upper()
    if body.album    is not None: campos_update["album"]           = body.album.upper()
    if body.duracion is not None: campos_update["duracion"]        = body.duracion
    if body.bpm      is not None: campos_update["bpm"]             = body.bpm
    if body.intro    is not None: campos_update["intro"]           = body.intro
    if body.outro    is not None: campos_update["outro"]           = body.outro
    if body.hook     is not None: campos_update["hook"]            = body.hook
    if body.fecha_lanzamiento is not None:
        campos_update["fecha_lanzamiento"] = body.fecha_lanzamiento.upper() if body.fecha_lanzamiento else None
        campos_update["epoca"] = calcular_epoca(campos_update["fecha_lanzamiento"])
    
    # Categorías con la escritura ya registrada (antes se forzaban a MAYÚSCULAS y
    # "Pop" y "POP" quedaban como dos categorías)
    if body.cat1 is not None: campos_update["subgenero"]    = body.cat1
    if body.cat2 is not None: campos_update["categoria"]    = body.cat2
    if body.cat3 is not None: campos_update["cat3"]         = body.cat3
    if body.voz  is not None: campos_update["genero_vocal"] = body.voz
    tipo_final = _validate_tipo_audio(body.tipo_audio)
    if tipo_final is not None: campos_update["tipo_audio"] = tipo_final
    if body.ganancia_db is not None: campos_update["ganancia_db"] = body.ganancia_db
    if body.origen  is not None: campos_update["origen"]  = _validate_origen(body.origen)
    if body.energia is not None: campos_update["energia"] = body.energia
    # Cue points extendidos (6 marcadores Jazler)
    if body.cue_inicio      is not None: campos_update["cue_inicio"]      = body.cue_inicio
    if body.cue_intro       is not None: campos_update["cue_intro"]       = body.cue_intro
    if body.cue_inicio_coro is not None: campos_update["cue_inicio_coro"] = body.cue_inicio_coro
    if body.cue_final_coro  is not None: campos_update["cue_final_coro"]  = body.cue_final_coro
    if body.cue_mezcla      is not None: campos_update["cue_mezcla"]      = body.cue_mezcla
    if body.fade_in         is not None: campos_update["fade_in"]         = body.fade_in
    if body.fade_out        is not None: campos_update["fade_out"]        = body.fade_out
    if body.cue_outro       is not None: campos_update["cue_outro"]       = body.cue_outro or None
    if body.prioridad       is not None: campos_update["prioridad"]       = body.prioridad
    if body.repeticion_horas is not None: campos_update["repeticion_horas"] = body.repeticion_horas or None
    for _m in ("meta_modo", "meta_titulo", "meta_artista", "meta_extra1", "meta_extra2"):
        if getattr(body, _m) is not None:
            campos_update[_m] = " ".join(getattr(body, _m).split())      # tal como se escribe: no se fuerza a mayúsculas

    if not campos_update:
        return {"mensaje": "Sin cambios que aplicar", "data": {"id": audio_id}}

    # Si el usuario movió el inicio o la mezcla, son marcas a mano: la
    # detección automática no las vuelve a tocar.
    if any(k in campos_update and abs(float(campos_update[k] or 0) - float(audio[k] or 0)) > 0.05
           for k in ("cue_inicio", "cue_mezcla")):
        campos_update["marcas_auto"] = 0

    set_clause = ", ".join([f"{k} = ?" for k in campos_update] + ["updated_at = NOW()"])
    values = list(campos_update.values())
    values.append(audio_id)

    conn.execute(
        f"UPDATE audios SET {set_clause} WHERE id = ?",
        values
    )
    aprobada_sola = False
    if (audio["tipo_audio"] == "Musica" and not audio["aprobada"]
            and (not (audio["subgenero"] or "").strip() or not (audio["categoria"] or "").strip())
            and (cat1_final or "").strip() and (cat2_final or "").strip()):
        # La edición completó el género y la rotación que faltaban: ya está lista, sin otro paso de aprobación.
        # (Una canción completa que alguien devolvió a propósito a la Bandeja no se vuelve a aprobar sola.)
        conn.execute("UPDATE audios SET aprobada = 1 WHERE id = ?", (audio_id,))
        aprobada_sola = True

    logger.info(
        f"  → Audio id={audio_id} actualizado. Campos: {list(campos_update.keys())}"
    )

    # Confirmar ANTES de avisar: los clientes recargan la biblioteca al recibir
    # el evento y la dependencia get_db hace commit después de responder.
    conn.commit()

    # Broadcast WebSocket — notificar a todos los módulos
    await _manager.broadcast(_ws_message(
        "library_updated",
        {"action": "update", "id": audio_id, "campos": list(campos_update.keys())},
    ))

    return {
        "mensaje": "Audio actualizado exitosamente",
        "data": {
            "id": audio_id,
            "campos_actualizados": list(campos_update.keys()),
            "aprobada_sola": aprobada_sola,
            "cat1": cat1_final,
            "cat2": cat2_final,
            "cat3": cat3_final,
            "voz": voz_final,
            "fecha_lanzamiento": campos_update.get("fecha_lanzamiento", audio["fecha_lanzamiento"]),
            "epoca": campos_update.get("epoca", audio["epoca"] if "epoca" in audio.keys() else None),
        }
    }


# ---- POST /api/v1/audios/{audio_id}/archivo ----

@app.post(
    "/api/v1/audios/{audio_id}/archivo",
    summary="Reemplazar el archivo físico de audio de una pista existente",
    tags=["Audios"],
)
async def update_audio_file(
    audio_id: int,
    file: UploadFile = File(...),
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Reemplaza el archivo físico de audio de una pista existente.
    Conserva la ID, metadatos, rotación y estadísticas de emisión,
    actualizando el archivo físico, archivo_path y recalcula la duración real.
    """
    logger.info(f"POST /api/v1/audios/{audio_id}/archivo → archivo='{file.filename}'")

    audio = conn.execute(
        "SELECT * FROM audios WHERE id = ? AND activo = 1", (audio_id,)
    ).fetchone()

    if not audio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio con id={audio_id} no encontrado o inactivo"
        )

    artista = audio["artista"] or "VARIOS"
    artista_slug = "".join(c if c.isalnum() or c in " _-" else "_" for c in artista).strip()
    dest_dir = LIBRARY_DIR / artista_slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    nombre_seguro = _normalize_filename(file.filename)
    dest = dest_dir / nombre_seguro

    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"  → Archivo reemplazado guardado en: {dest}")
    except Exception as e:
        logger.error(f"  → Error guardando archivo: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fallo al guardar el archivo: {e}"
        )

    nueva_duracion = _probe_duration(dest)
    if not nueva_duracion and audio["duracion"]:
        nueva_duracion = audio["duracion"]

    nuevo_path = to_stored(dest)

    conn.execute(
        "UPDATE audios SET archivo_path = ?, duracion = ?, updated_at = NOW() WHERE id = ?",
        (nuevo_path, nueva_duracion or 0, audio_id)
    )
    conn.commit()

    # Broadcast WebSocket
    await _manager.broadcast(_ws_message(
        "library_updated",
        {"action": "update", "id": audio_id, "campos": ["archivo_path", "duracion"]},
    ))

    return {
        "status": "ok",
        "audio_id": audio_id,
        "archivo_path": nuevo_path,
        "duracion": nueva_duracion,
        "nombre": file.filename
    }


# ---- GET /api/v1/audios ----

@app.get(
    "/api/v1/audios",
    summary="Listar audios de Biblioteca",
    tags=["Audios"],
)
def list_audios(
    # ── Filtro multi-valor para pautas-controller.js ──────────────────────
    # ?categoria_1=COMERCIAL&categoria_1=PUBLICIDAD  → subgenero IN (…)
    # Retrocompatible: si no se envía, cat1 (valor único) sigue funcionando.
    categoria_1: Optional[List[str]] = None,
    # ── Filtros originales (Biblioteca Musical) ───────────────────────────
    cat1: Optional[str] = None,
    cat2: Optional[str] = None,
    cat3: Optional[str] = None,
    voz: Optional[str]  = None,
    tipo: Optional[str] = None,   # Musica | Cuña | Efecto | Sweeper | Programa | Tips
    sin_tipo: Optional[str] = None,   # excluir un tipo (la Biblioteca musical no muestra cuñas)
    busqueda: Optional[str] = None,
    # Búsqueda del Studio: sin tildes ni mayúsculas, todas las palabras, en título/artista/categorías
    q: Optional[str] = None,
    ids: Optional[str] = None,     # "3,17,42": esos audios exactos (para mostrar nombres ya guardados)
    ambito: Optional[str] = None,  # 'cartuchera' = solo lo cargado desde la cartuchera; sin esto, esos no salen en las listas
    limit: int = Query(500, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Lista audios con filtros opcionales por categoría.
    Soporta búsqueda por título/artista.
    """
    query = """
        SELECT id, titulo, artista, album, duracion, archivo_path,
               subgenero as cat1, categoria as cat2, cat3, genero_vocal as voz,
               bpm, fecha_lanzamiento, epoca, activo, created_at, updated_at,
               intro, outro, hook,
               cue_inicio, cue_intro, cue_inicio_coro,
               cue_final_coro, cue_mezcla, fade_in, fade_out,
               tipo_audio, veces_reproducido, ultima_reproduccion,
               loudness_lufs, pico_db, loudness_estado, ganancia_db, origen, energia,
               marcas_auto, aprobada, restr_desde, restr_hasta, restr_dias, max_por_dia, prioridad,
               cue_outro, repeticion_horas, categoria_ident
        FROM audios
        WHERE activo = 1
    """
    params = []
    # Lo que se carga desde la cartuchera es SOLO de la cartuchera: no aparece en las demás listas
    if ambito == "cartuchera":
        query += " AND ambito = 'cartuchera'"
    elif not ids:
        query += " AND COALESCE(ambito, '') != 'cartuchera'"
    # Las locuciones (voces del Locutor IA y voice tracks) tienen su propia sección: no entran en las listas de audios
    if not ids and "Locucion" not in (tipo or ""):
        query += " AND tipo_audio != 'Locucion'"

    # categoria_1 (multi-valor, pautas-controller): tiene precedencia sobre cat1
    if categoria_1:
        ph = ", ".join("?" * len(categoria_1))
        query += f" AND subgenero IN ({ph})"
        params.extend(categoria_1)
    elif cat1:
        # cat1 (valor único, biblioteca-musical): retrocompatibilidad
        query += " AND subgenero = ?"
        params.append(cat1)
    if cat2:
        query += " AND categoria = ?"
        params.append(cat2)
    if cat3:
        query += " AND cat3 = ?"
        params.append(cat3)
    if voz:
        query += " AND genero_vocal = ?"
        params.append(voz)
    if tipo:
        # Uno o varios separados por coma (el Studio junta jingles, sweepers y tips)
        tipos = [_validate_tipo_audio(t) for t in tipo.split(",") if t.strip()]
        if tipos:
            query += f" AND tipo_audio IN ({', '.join('?' * len(tipos))})"
            params.extend(tipos)
    if sin_tipo:
        query += " AND tipo_audio != ?"
        params.append(_validate_tipo_audio(sin_tipo))
    if busqueda:
        query += " AND (titulo LIKE ? OR artista LIKE ?)"
        params.extend([f"%{busqueda}%", f"%{busqueda}%"])
    if ids:
        lista_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()][:500]
        query += f" AND id IN ({', '.join('?' * len(lista_ids))})" if lista_ids else " AND 0"
        params.extend(lista_ids)
    # Búsqueda del Studio sobre audios.busq (indexada por triggers): cada palabra debe aparecer
    for palabra in (_sin_acentos(q).split() if q else []):
        query += " AND busq LIKE ? ESCAPE '\\\\'"          # en MariaDB la barra invertida se escribe doble
        params.append("%" + palabra.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")

    # Contar total
    count_query = "SELECT COUNT(*) AS total " + query[query.index("FROM audios"):]
    total = conn.execute(count_query, params).fetchone()["total"]

    query += " ORDER BY artista ASC, titulo ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    audios = conn.execute(query, params).fetchall()
    active_cats = _valores_activos_por_categoria(conn)
    data = []
    for a in audios:
        d = dict(a)
        if d.get("tipo_audio") == "Musica":
            d["cat1"] = _sanitizar_cat(d.get("cat1"), active_cats["cat1"])
            d["cat2"] = _sanitizar_cat(d.get("cat2"), active_cats["cat2"])
            d["cat3"] = _sanitizar_cat(d.get("cat3"), active_cats["cat3"])
            d["voz"]  = _sanitizar_cat(d.get("voz"),  active_cats["voz"])
        data.append(d)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": data,
    }


_SIN_TILDES = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _sin_acentos(texto: Optional[str]) -> str:
    """Minúsculas y sin tildes, igual que audios.busq (migración 33): «cancion» encuentra «Canción»."""
    return (texto or "").translate(_SIN_TILDES).lower()


# Columnas que usa la tabla de la Biblioteca musical del Admin (SQL, nombre que ve la pantalla).
# La ficha y el editor piden su fila completa aparte (/api/v1/biblioteca/{id}/ficha).
_CAMPOS_LISTA = [
    ("id", "id"), ("titulo", "titulo"), ("artista", "artista"), ("album", "album"), ("duracion", "duracion"),
    ("archivo_path", "archivo_path"), ("subgenero", "cat1"), ("categoria", "cat2"), ("cat3", "cat3"),
    ("genero_vocal", "voz"), ("bpm", "bpm"), ("fecha_lanzamiento", "fecha_lanzamiento"), ("epoca", "epoca"),
    ("intro", "intro"),
    ("cue_intro", "cue_intro"), ("cue_inicio_coro", "cue_inicio_coro"), ("cue_mezcla", "cue_mezcla"),
    ("tipo_audio", "tipo_audio"), ("veces_reproducido", "veces_reproducido"),
    ("ultima_reproduccion", "ultima_reproduccion"), ("loudness_estado", "loudness_estado"), ("origen", "origen"),
    ("energia", "energia"), ("marcas_auto", "marcas_auto"), ("aprobada", "aprobada"), ("prioridad", "prioridad"),
    ("restr_desde", "restr_desde"), ("restr_hasta", "restr_hasta"), ("restr_dias", "restr_dias"),
    ("max_por_dia", "max_por_dia"),
    # Los usa el módulo clásico (admin-app.js): marcas y fundidos
    ("cue_inicio", "cue_inicio"), ("cue_final_coro", "cue_final_coro"), ("cue_outro", "cue_outro"),
    ("fade_in", "fade_in"), ("fade_out", "fade_out"), ("outro", "outro"),
]


@app.get("/api/v1/biblioteca/lista", tags=["Audios"], summary="Lista ligera y comprimida para la tabla del Admin")
def lista_biblioteca(request: Request, sin_tipo: Optional[str] = None, tipo: Optional[str] = None,
                     conn: MariaDBConnection = Depends(get_db)):
    """
    Con ~20 000 audios la lista completa de /api/v1/audios pesa unos 15 MB y tarda ~2 s.
    Aquí van solo las columnas de la tabla, como filas sin nombres de campo repetidos
    (`campos` + `filas`), y comprimida con gzip si el navegador la acepta.
    """
    import gzip
    sql = f"SELECT {', '.join(c for c, _ in _CAMPOS_LISTA)} FROM audios WHERE activo = 1"
    params: list = []
    # Lo cargado desde la cartuchera es solo de la cartuchera, y las voces (Locutor IA / voice tracks) tienen su sección:
    # ninguno de los dos entra en la Biblioteca Musical
    sql += " AND COALESCE(ambito, '') != 'cartuchera'"
    if tipo != "Locucion":
        sql += " AND tipo_audio != 'Locucion'"
    if tipo:
        sql += " AND tipo_audio = ?"; params.append(_validate_tipo_audio(tipo))
    if sin_tipo:                      # uno o varios separados por coma
        excluir = [_validate_tipo_audio(t) for t in sin_tipo.split(",") if t.strip()]
        if excluir:
            sql += f" AND tipo_audio NOT IN ({', '.join('?' * len(excluir))})"; params.extend(excluir)

    active_cats = _valores_activos_por_categoria(conn)
    col_idx = {alias: idx for idx, (_, alias) in enumerate(_CAMPOS_LISTA)}
    tipo_idx = col_idx.get("tipo_audio")
    filas = []
    for r in conn.execute(sql, params):
        fila = list(r.values()) if hasattr(r, "values") else list(r)
        if tipo_idx is None or fila[tipo_idx] == "Musica":
            for cat_key in ("cat1", "cat2", "cat3", "voz"):
                idx = col_idx.get(cat_key)
                if idx is not None and fila[idx]:
                    sanitized = _sanitizar_cat(fila[idx], active_cats[cat_key])
                    fila[idx] = sanitized if sanitized is not None else ""
        filas.append(fila)

    cuerpo = json.dumps({"campos": [a for _, a in _CAMPOS_LISTA], "filas": filas},
                        separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    cabeceras = {}
    if "gzip" in request.headers.get("accept-encoding", "") and len(cuerpo) > 2000:
        cuerpo = gzip.compress(cuerpo, compresslevel=4)
        cabeceras["Content-Encoding"] = "gzip"
    return Response(content=cuerpo, media_type="application/json", headers=cabeceras)


@app.get("/api/v1/biblioteca/conteos", tags=["Audios"], summary="Cuántos audios hay de cada tipo")
def conteos_biblioteca(conn: MariaDBConnection = Depends(get_db)):
    filas = conn.execute("SELECT tipo_audio, COUNT(*) AS n FROM audios WHERE activo = 1"
                         " AND COALESCE(ambito, '') != 'cartuchera' AND tipo_audio != 'Locucion' GROUP BY tipo_audio")
    return {r["tipo_audio"]: r["n"] for r in filas}


@app.get("/api/v1/biblioteca/filtros", tags=["Audios"], summary="Valores para los filtros (género, rotación, voz)")
def filtros_biblioteca(tipo: str = "Musica", conn: MariaDBConnection = Depends(get_db)):
    t = _validate_tipo_audio(tipo)
    def distintos(col: str) -> list[str]:
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {col} FROM audios WHERE activo = 1 AND tipo_audio = ? AND COALESCE(ambito, '') != 'cartuchera' "
            f"AND COALESCE({col}, '') != '' ORDER BY {col}", (t,))]
    return {"cat1": distintos("subgenero"), "cat2": distintos("categoria"), "voz": distintos("genero_vocal")}


@app.get("/api/v1/artistas", tags=["Audios"], summary="Artistas ya cargados (para autocompletar)")
def listar_artistas(tipo: Optional[str] = None, conn: MariaDBConnection = Depends(get_db)):
    """Nombres distintos de artista (o anunciante, con tipo=Cuña), con cuántos audios tiene cada uno."""
    sql = "SELECT artista, COUNT(*) AS n FROM audios WHERE activo = 1 AND COALESCE(artista, '') != ''"
    params = []
    if tipo:
        sql += " AND tipo_audio = ?"
        params.append(_validate_tipo_audio(tipo))
    sql += " GROUP BY artista ORDER BY artista"
    return [{"artista": r["artista"], "audios": r["n"]} for r in conn.execute(sql, params)]


class LoteAudiosRequest(BaseModel):
    ids:     List[int] = Field(..., min_length=1, max_length=20000)
    # Campos a cambiar en todas: cat1, cat2, cat3, voz, origen, energia, tipo_audio, aprobada
    cambios: dict = Field(default_factory=dict)
    # Acción extra: "redetectar_marcas" | "desactivar"
    accion:  Optional[str] = None


_LOTE_COLUMNAS = {"cat1": "subgenero", "cat2": "categoria", "cat3": "cat3", "voz": "genero_vocal",
                  "origen": "origen", "energia": "energia", "tipo_audio": "tipo_audio",
                  "restr_desde": "restr_desde", "restr_hasta": "restr_hasta",
                  "restr_dias": "restr_dias", "max_por_dia": "max_por_dia",
                  "prioridad": "prioridad", "bpm": "bpm", "repeticion_horas": "repeticion_horas"}


@app.get("/api/v1/audios/{audio_id}/repeticion", tags=["Audios"],
         summary="¿Esta canción ya sonó dentro de su regla de repetición?")
def estado_repeticion(audio_id: int, conn: MariaDBConnection = Depends(get_db)):
    """Para que el Studio avise en modo manual: `bloqueada` = sonó hace menos de `horas`."""
    r = conn.execute("SELECT titulo, repeticion_horas, ultima_reproduccion FROM audios WHERE id = ?",
                     (audio_id,)).fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Audio no encontrado")
    horas = r["repeticion_horas"] or 0
    out = {"titulo": r["titulo"], "horas": horas, "ultima": r["ultima_reproduccion"],
           "bloqueada": False, "faltan_min": 0, "disponible": None}
    if horas and r["ultima_reproduccion"]:
        try:
            ult = datetime.fromisoformat(str(r["ultima_reproduccion"]).replace(" ", "T"))
        except ValueError:
            return out
        libre = ult + timedelta(hours=horas)
        faltan = (libre - datetime.now()).total_seconds() / 60
        if faltan > 0:
            out.update(bloqueada=True, faltan_min=int(faltan) + 1, disponible=libre.isoformat(timespec="minutes"))
    return out


@app.post("/api/v1/biblioteca/lote", tags=["Audios"], summary="Edición masiva de la Biblioteca")
def editar_lote(body: LoteAudiosRequest, conn: MariaDBConnection = Depends(get_db)):
    """
    Aplica los mismos cambios a muchas canciones (como "cambiar categoría a las
    seleccionadas" de Jazler). Aprobar exige que tengan género y rotación: las
    que no los tienen quedan en la Bandeja y se informan en `sin_clasificar`.
    """
    ids = sorted(set(body.ids))
    ph = ",".join("?" * len(ids))
    sets, valores = [], []
    c = body.cambios or {}
    for clave, col in _LOTE_COLUMNAS.items():
        if clave not in c:
            continue
        v = c[clave]
        if clave in ("cat1", "cat2", "cat3", "voz"):
            v = (str(v).strip() or None) if v is not None else None
            v = _categoria_canonica(conn, clave, v)
            _validate_categoria_valor(conn, clave, v, clave)
        elif clave == "origen":
            v = _validate_origen(v) if v else None
        elif clave == "energia":
            v = int(v) if v not in (None, "") else None
            if v is not None and not 1 <= v <= 5:
                raise HTTPException(status_code=422, detail="La energía va de 1 a 5")
        elif clave == "tipo_audio":
            v = _validate_tipo_audio(v)
        elif clave in ("restr_desde", "restr_hasta"):
            v = int(v) if v not in (None, "") else None
            if v is not None and not 0 <= v <= 24:
                raise HTTPException(status_code=422, detail="Las horas van de 0 a 24")
        elif clave == "prioridad":
            v = int(v) if v not in (None, "") else None
            if v is not None and v not in (1, 2, 3):
                raise HTTPException(status_code=422, detail="Prioridad: 1 Alta, 2 Normal o 3 Baja")
        elif clave == "repeticion_horas":
            v = int(v) if v not in (None, "", 0, "0") else None
            if v is not None and not 1 <= v <= MAX_REPETICION_HORAS:
                raise HTTPException(status_code=422, detail="La repetición va de 1 hora a 3 años")
        elif clave == "bpm":
            v = int(float(v)) if v not in (None, "") else None
            if v is not None and not 40 <= v <= 300:
                raise HTTPException(status_code=422, detail="BPM entre 40 y 300")
        elif clave == "max_por_dia":
            v = int(v) if v not in (None, "", 0, "0") else None
            if v is not None and not 1 <= v <= 48:
                raise HTTPException(status_code=422, detail="Máximo por día: de 1 a 48")
        elif clave == "restr_dias":
            dias = sorted({int(d) for d in (v if isinstance(v, list) else str(v or "").split(",")) if str(d).strip() != ""})
            if any(not 0 <= d <= 6 for d in dias):
                raise HTTPException(status_code=422, detail="Días: 0 (Dom) a 6 (Sáb)")
            v = ",".join(map(str, dias)) if dias and len(dias) < 7 else None   # los 7 = sin restricción
        sets.append(f"{col} = ?")
        valores.append(v)
    # Cuáles estaban sin género o sin rotación ANTES del cambio (solo a esas las completa y aprueba sola esta edición)
    incompletas_antes: list[int] = []
    if ("cat1" in c or "cat2" in c) and "aprobada" not in c:
        incompletas_antes = [r[0] for r in conn.execute(
            f"SELECT id FROM audios WHERE id IN ({ph}) AND activo = 1 AND tipo_audio = 'Musica' AND COALESCE(aprobada, 0) = 0"
            " AND (COALESCE(subgenero, '') = '' OR COALESCE(categoria, '') = '')", ids)]
    hechos = 0
    if sets:
        hechos = conn.execute(f"UPDATE audios SET {', '.join(sets)}, updated_at = NOW()"
                              f" WHERE id IN ({ph}) AND activo = 1", valores + ids).rowcount
    aprobadas_solas = 0
    if incompletas_antes:
        # Las que el cambio deja con género y rotación pasan a «lista para sonar» solas
        aprobadas_solas = conn.execute(
            f"UPDATE audios SET aprobada = 1 WHERE id IN ({','.join('?' * len(incompletas_antes))})"
            " AND COALESCE(subgenero, '') <> '' AND COALESCE(categoria, '') <> ''", incompletas_antes).rowcount
        hechos = max(hechos, aprobadas_solas)
    sin_clasificar = []
    if "aprobada" in c:
        if c["aprobada"]:
            sin_clasificar = [r[0] for r in conn.execute(
                f"SELECT id FROM audios WHERE id IN ({ph}) AND activo = 1 AND"
                " (COALESCE(subgenero, '') = '' OR COALESCE(categoria, '') = '')", ids)]
            ok = [i for i in ids if i not in set(sin_clasificar)]
            if ok:
                hechos = max(hechos, conn.execute(
                    f"UPDATE audios SET aprobada = 1 WHERE id IN ({','.join('?' * len(ok))})", ok).rowcount)
        else:
            hechos = max(hechos, conn.execute(f"UPDATE audios SET aprobada = 0 WHERE id IN ({ph})", ids).rowcount)
    if body.accion == "redetectar_marcas":
        # Solo las automáticas o con error: las marcadas a mano no se pisan
        hechos = conn.execute(
            f"UPDATE audios SET cue_inicio = 0, cue_mezcla = 0, marcas_auto = NULL"
            f" WHERE id IN ({ph}) AND COALESCE(marcas_auto, 1) != 0", ids).rowcount
        _normalizador.avisar()
    elif body.accion == "desactivar":
        en_pauta = [r[0] for r in conn.execute(
            f"SELECT DISTINCT audio_id FROM pautas WHERE activo = 1 AND audio_id IN ({ph})", ids)]
        if en_pauta:
            raise HTTPException(status_code=409, detail=f"{len(en_pauta)} audio(s) están en pautas activas")
        hechos = conn.execute(f"UPDATE audios SET activo = 0 WHERE id IN ({ph})", ids).rowcount
    elif body.accion:
        raise HTTPException(status_code=422, detail=f"Acción desconocida: {body.accion}")
    conn.commit()
    if engine is not None:
        try:
            engine.plan_regenerar()          # el automático ya ve los cambios
        except Exception:
            pass
    return {"actualizados": hechos, "sin_clasificar": sin_clasificar, "aprobadas_solas": aprobadas_solas}


# ---- DELETE (soft) /api/v1/audios/{audio_id} ----

@app.delete(
    "/api/v1/audios/{audio_id}",
    summary="Eliminar audio (soft-delete)",
    tags=["Audios"],
)
def delete_audio(
    audio_id: int,
    conn: MariaDBConnection = Depends(get_db),
):
    audio = conn.execute(
        "SELECT id, titulo, archivo_path FROM audios WHERE id = ? AND activo = 1",
        (audio_id,)
    ).fetchone()

    if not audio:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio id={audio_id} no encontrado"
        )

    conn.execute(
        "UPDATE audios SET activo = 0, updated_at = NOW() WHERE id = ?",
        (audio_id,)
    )
    logger.info(f"Audio id={audio_id} ('{audio['titulo']}') marcado como inactivo")

    return {"mensaje": f"Audio '{audio['titulo']}' eliminado correctamente"}

# ===========================================================================
# SECCIÓN: PAUTAS — CRUD completo (Sección 03 — Programación de Pautas)
#
# Endpoints:
#   GET    /api/v1/pautas          → lista pautas (matriz parseada como dict)
#   POST   /api/v1/pautas          → crea pauta (matriz dict → JSON str en DB)
#   PUT    /api/v1/pautas/{id}     → actualiza pauta
#   DELETE /api/v1/pautas/{id}     → elimina pauta de la DB
#
# Invariante de marca: cliente siempre en MAYÚSCULAS (gestionado en PautaIn).
# ===========================================================================


def _guardar_versiones(conn: MariaDBConnection, pauta_id: int, body: PautaIn) -> None:
    """Versiones que rotan: la principal (audio_id) siempre va primera."""
    if body.versiones is None:
        return
    ids = list(dict.fromkeys(([body.audio_id] if body.audio_id else []) + body.versiones))
    existentes = {r[0] for r in conn.execute(
        f"SELECT id FROM audios WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    conn.execute("DELETE FROM pauta_audios WHERE pauta_id = ?", (pauta_id,))
    if len(ids) > 1:
        conn.executemany("INSERT INTO pauta_audios (pauta_id, audio_id, orden) VALUES (?, ?, ?)",
                         [(pauta_id, a, i) for i, a in enumerate(ids) if a in existentes])


def _con_versiones(conn: MariaDBConnection, pauta: dict) -> dict:
    """Agrega las versiones (con cuántas veces salió cada una)."""
    pauta["versiones"] = [dict(r) for r in conn.execute(
        """SELECT a.id, a.titulo, a.duracion,
                  (SELECT COUNT(*) FROM historial_emision h
                    WHERE h.pauta_id = pa.pauta_id AND h.audio_id = a.id) AS emitidas
           FROM pauta_audios pa JOIN audios a ON a.id = pa.audio_id
           WHERE pa.pauta_id = ? ORDER BY pa.orden""", (pauta["id"],))]
    return pauta


class OrdenBloque(BaseModel):
    ids: list[int] = Field(..., max_length=500, description="Pautas en el orden en que deben sonar")


@app.put("/api/v1/pautas/orden", tags=["Pautas"], summary="Orden manual de las cuñas en el bloque")
def ordenar_bloque(body: OrdenBloque, conn: MariaDBConnection = Depends(get_db)):
    """Guarda el orden armado por programación. Lista vacía = el sistema decide."""
    conn.execute("UPDATE pautas SET orden = NULL")
    conn.executemany("UPDATE pautas SET orden = ? WHERE id = ?",
                     [(i, pid) for i, pid in enumerate(body.ids)])
    conn.commit()
    return {"ok": True, "ordenadas": len(body.ids)}


@app.get(
    "/api/v1/pautas/notificaciones",
    summary="Obtener notificaciones de pautas",
    tags=["Pautas"],
)
def get_pautas_notificaciones(
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Retorna pautas vencidas (fecha_fin < hoy) y pautas que vencen pronto
    (fecha_fin <= hoy + 3 días). Útil para la campana de notificaciones.
    """
    from datetime import date, timedelta
    
    hoy = date.today().isoformat()
    pronto = (date.today() + timedelta(days=3)).isoformat()

    # Vencidas: fecha_fin < hoy
    sql_vencilas = "SELECT * FROM pautas WHERE fecha_fin < ? AND fecha_fin != '' AND activo = 1 ORDER BY fecha_fin ASC"
    rows_vencidas = conn.execute(sql_vencilas, (hoy,)).fetchall()
    vencidas = [PautaOut.from_row(r).model_dump() for r in rows_vencidas]

    # Vencen pronto: hoy <= fecha_fin <= pronto
    sql_pronto = "SELECT * FROM pautas WHERE fecha_fin >= ? AND fecha_fin <= ? AND fecha_fin != '' AND activo = 1 ORDER BY fecha_fin ASC"
    rows_pronto = conn.execute(sql_pronto, (hoy, pronto)).fetchall()
    vencen_pronto = [PautaOut.from_row(r).model_dump() for r in rows_pronto]

    campana_count = len(vencidas) + len(vencen_pronto)

    return {
        "vencidas": vencidas,
        "vencen_pronto": vencen_pronto,
        "campana_count": campana_count
    }


@app.get(
    "/api/v1/pautas",
    summary="Listar pautas",
    tags=["Pautas"],
)
def list_pautas(
    activo: Optional[int] = None,
    limit:  int = 200,
    offset: int = 0,
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Devuelve la lista de pautas.

    - **activo=1** → solo las activas  |  **activo=0** → solo las vencidas/inactivas
    - Sin parámetro `activo`: devuelve todas.
    - El campo `matriz` se devuelve como objeto JSON (no string), listo para
      consumir en pautas-controller.js sin procesamiento adicional.
    """
    logger.info(f"GET /api/v1/pautas → activo={activo}, limit={limit}, offset={offset}")

    conditions: list = []
    params:     list = []

    if activo is not None:
        conditions.append("activo = ?")
        params.append(activo)

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql    = f"SELECT * FROM pautas {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    data = [_con_versiones(conn, PautaOut.from_row(r).model_dump()) for r in rows]

    logger.info(f"  → {len(data)} pauta(s) encontradas")
    return data


@app.post(
    "/api/v1/pautas",
    status_code=status.HTTP_201_CREATED,
    summary="Crear pauta",
    tags=["Pautas"],
)
def create_pauta(
    body: PautaIn,
    conn: MariaDBConnection = Depends(get_db),
):
    """
    Crea una nueva pauta publicitaria.

    - `cliente` se normaliza a MAYÚSCULAS automáticamente.
    - `matriz` (dict) se serializa a JSON string para almacenamiento.
    - Devuelve la pauta creada con el `id` asignado por la base de datos.
    """
    logger.info(f"POST /api/v1/pautas → cliente='{body.cliente}', audio_id={body.audio_id}")

    db_data = body.to_db_dict()

    try:
        cur = conn.execute(
            """
            INSERT INTO pautas
                (cliente, audio_id, audio_nombre, matriz,
                 fecha_inicio, fecha_fin, notas,
                 cue_intro, cue_outro, menciones_diarias, rubro, prioridad, contrato_id,
                 agencia, posicion, seccion, hora_inicio_permitida, hora_fin_permitida,
                 activo, created_at, updated_at)
            VALUES
                (:cliente, :audio_id, :audio_nombre, :matriz,
                 :fecha_inicio, :fecha_fin, :notas,
                 :cue_intro, :cue_outro, :menciones_diarias, :rubro, :prioridad, :contrato_id,
                 :agencia, :posicion, :seccion, :hora_inicio_permitida, :hora_fin_permitida,
                 1, NOW(), NOW())
            """,
            db_data,
        )
    except DualIntegrityError as exc:
        logger.error(f"  → IntegrityError al crear pauta: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No se pudo crear la pauta: {exc}. "
                   f"Verifica que audio_id={body.audio_id} exista en la Biblioteca.",
        )

    new_id = cur.lastrowid
    _guardar_versiones(conn, new_id, body)
    row    = conn.execute("SELECT * FROM pautas WHERE id = ?", (new_id,)).fetchone()

    logger.info(f"  → Pauta creada con id={new_id}")
    return _con_versiones(conn, PautaOut.from_row(row).model_dump())


@app.put(
    "/api/v1/pautas/{pauta_id}",
    summary="Actualizar pauta",
    tags=["Pautas"],
)
def update_pauta(
    pauta_id: int,
    body:     PautaIn,
    conn:     MariaDBConnection = Depends(get_db),
):
    """
    Actualiza todos los campos de una pauta existente.

    - Devuelve la pauta actualizada completa.
    - 404 si el `pauta_id` no existe.
    """
    logger.info(f"PUT /api/v1/pautas/{pauta_id} → cliente='{body.cliente}'")

    existing = conn.execute(
        "SELECT id FROM pautas WHERE id = ?", (pauta_id,)
    ).fetchone()

    if not existing:
        logger.warning(f"  → Pauta id={pauta_id} no encontrada")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pauta con id={pauta_id} no encontrada.",
        )

    db_data = body.to_db_dict()

    conn.execute(
        """
        UPDATE pautas SET
            cliente      = :cliente,
            audio_id     = :audio_id,
            audio_nombre = :audio_nombre,
            matriz       = :matriz,
            fecha_inicio = :fecha_inicio,
            fecha_fin    = :fecha_fin,
            notas        = :notas,
            cue_intro    = :cue_intro,
            cue_outro    = :cue_outro,
            menciones_diarias = :menciones_diarias,
            rubro        = :rubro,
            prioridad    = :prioridad,
            contrato_id  = :contrato_id,
            agencia      = :agencia,
            posicion     = :posicion,
            seccion      = :seccion,
            hora_inicio_permitida = :hora_inicio_permitida,
            hora_fin_permitida    = :hora_fin_permitida,
            updated_at   = NOW()
        WHERE id = :id
        """,
        {**db_data, "id": pauta_id},
    )

    _guardar_versiones(conn, pauta_id, body)
    row = conn.execute("SELECT * FROM pautas WHERE id = ?", (pauta_id,)).fetchone()
    logger.info(f"  → Pauta id={pauta_id} actualizada")
    return _con_versiones(conn, PautaOut.from_row(row).model_dump())


@app.delete(
    "/api/v1/pautas/{pauta_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar pauta",
    tags=["Pautas"],
)
def delete_pauta(
    pauta_id: int,
    physical: bool = False,
    conn:     MariaDBConnection = Depends(get_db),
):
    """
    Elimina permanentemente una pauta de la base de datos.
    Si physical=True, también localiza el archivo vinculado
    en la tabla audios y lo borra físicamente.

    - 404 si el `pauta_id` no existe.
    - 204 No Content si la eliminación fue exitosa (sin cuerpo de respuesta).
    """
    logger.info(f"DELETE /api/v1/pautas/{pauta_id}?physical={physical}")

    existing = conn.execute(
        "SELECT id, cliente, audio_id FROM pautas WHERE id = ?", (pauta_id,)
    ).fetchone()

    if not existing:
        logger.warning(f"  → Pauta id={pauta_id} no encontrada")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pauta con id={pauta_id} no encontrada.",
        )

    # Borrar archivo físico si se requiere y existe el vínculo
    if physical and existing["audio_id"]:
        audio = conn.execute(
            "SELECT id, archivo_path FROM audios WHERE id = ?", (existing["audio_id"],)
        ).fetchone()
        
        if audio and audio["archivo_path"]:
            path = resolve_media(audio["archivo_path"])
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"  → Archivo físico {path} eliminado")
                except Exception as e:
                    logger.error(f"  → Error al eliminar archivo físico {path}: {e}")
            
            # Borrar también de la tabla audios para no dejar registros huérfanos
            conn.execute("DELETE FROM audios WHERE id = ?", (audio["id"],))
            logger.info(f"  → Audio id={audio['id']} eliminado también")

    # Borrar la pauta
    conn.execute("DELETE FROM pautas WHERE id = ?", (pauta_id,))
    logger.info(f"  → Pauta id={pauta_id} ('{existing['cliente']}') eliminada")
    # FastAPI devuelve 204 sin cuerpo automáticamente cuando el handler retorna None


@app.get(
    "/api/v1/pautas/preview_tanda",
    tags=["Pautas"],
    summary="Simular y obtener vista previa de las tandas de una hora sin afectar el aire",
)
def preview_tanda(
    fecha: str = Query(..., description="Fecha en formato YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    hora: int = Query(..., ge=0, le=23, description="Hora del día (0-23)"),
    minuto: Optional[int] = Query(None, description="Minuto específico de la tanda (opcional)"),
    rubro: Optional[str] = Query(None, description="Filtrar cuñas de un rubro específico (opcional)"),
):

    """
    Calcula una simulación en memoria de los bloques comerciales planificados para
    una fecha y hora determinadas, usando el motor del Planner (Dayparting, Spacing,
    Pautas en Deuda, Separación de rubros y voces).

    **Completamente seguro e inocuo:**
    No altera el estado del motor ni registra nada en el historial ni en la base de datos.
    """
    try:
        from backend.audio.planner import Planner
        planner = Planner(DB_PATH)
        resultado = planner.simular_tanda(hora=hora, fecha=fecha, minuto=minuto, rubro_filtro=rubro)
        return resultado

    except Exception as exc:
        logger.error(f"[API] Error en preview_tanda: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al simular la tanda: {exc}"
        )



# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _revert_file(library_path: str, original_staging_name: str) -> None:
    """Intenta revertir el movimiento de archivo en caso de error de DB."""
    try:
        src = Path(library_path)
        dest = STAGING_DIR / src.name
        if src.exists():
            shutil.move(str(src), str(dest))
            logger.warning(f"  [Revert] Archivo devuelto a staging: {dest}")
    except Exception as e:
        logger.error(f"  [Revert FALLIDO] No se pudo revertir archivo: {e}")


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", tags=["Sistema"])
def health_check():
    try:
        with db_session() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "connected", "version": "2.0.0"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB no disponible: {e}")


@app.get("/api/v1/nowplaying", tags=["Sistema"], summary="Qué texto se publica ahora (metadata al aire)")
def now_playing(conn: MariaDBConnection = Depends(get_db)):
    """
    Aplica «Publicar metadata» de cada audio: si lo que suena actualiza, se publica; si no, se mantiene el texto
    del último audio que sí actualizó. Los consumidores (RDS, encoder, pantallas) leen este resultado.
    """
    from backend.audio.publicar import texto_publicado
    st = dict(engine.status)
    ahora = st.get("current_track") or {}
    fila = conn.execute("SELECT * FROM audios WHERE id = ?", (ahora.get("id"),)).fetchone() if ahora.get("id") else None
    actual = texto_publicado(fila) if fila else None
    publicado, motivo = actual, "suena"
    if actual is None or not actual["actualizar"]:
        publicado, motivo = None, "mantiene el anterior"
        for r in conn.execute(
                "SELECT a.* FROM historial_emision h JOIN audios a ON a.id = h.audio_id"
                " WHERE h.audio_id IS NOT NULL ORDER BY h.id DESC LIMIT 60"):
            t = texto_publicado(r)
            if t["actualizar"]:
                publicado = t
                break
    return {"suena": {"id": ahora.get("id"), "titulo": ahora.get("titulo"), "artista": ahora.get("artista")} if ahora else None,
            "actualiza": bool(actual and actual["actualizar"]), "publicado": publicado, "motivo": motivo}


@app.get("/api/v1/status", tags=["Sistema"])
def get_status():
    """
    Estado en vivo del motor de audio (backend, modo, pista al aire, cola).
    `version` es la del motor y `version_api` la de este servidor: si no
    coinciden, el motor sigue con código viejo y hay que reiniciarlo.
    """
    st = dict(engine.status)
    st["version_api"] = VERSION
    st.setdefault("version", None)       # un motor muy viejo no la informa
    return st


# ===========================================================================
# SECCIÓN: PLAYER — Control del motor de audio
# Los mismos comandos están disponibles por WebSocket (ver /ws).
# ===========================================================================

PLAYER_COMMANDS = {"play", "queue", "fire_effect", "set_mode", "skip", "stop", "clear_queue",
                   "lanzar_tanda", "cue", "cue_stop", "detener_al_terminar", "pausar",
                   "plan_insertar", "plan_mover", "plan_quitar", "seek",
                   "cola_insertar", "cola_mover", "cola_quitar", "plan_a_cola"}


def _opciones_boton(msg: dict) -> dict:
    """Marcas y volumen del botón de la cartuchera que se pulsó (si el comando dice cuál fue)."""
    banco, slot = msg.get("banco"), msg.get("slot")
    if not banco or slot is None:
        return {}
    try:
        with db_session() as conn:
            r = conn.execute("SELECT audio_id, mark_in, mark_out, volumen FROM cartuchera WHERE banco = ? AND slot = ?",
                             (str(banco), int(slot))).fetchone()
    except (DualError, TypeError, ValueError):
        return {}
    if not r or r["audio_id"] != msg.get("audio_id"):
        return {"boton": f"{banco}/{slot}"}
    return {"boton": f"{banco}/{slot}", "mark_in": r["mark_in"], "mark_out": r["mark_out"], "volumen": r["volumen"]}


def _player_command(cmd: str, msg: dict) -> dict:
    """
    Ejecuta un comando de reproducción. Lanza ValueError con un mensaje
    para el operador si el comando o sus datos no son válidos.
    """
    st = engine.status
    if not st["running"]:
        raise ValueError(f"Motor de audio detenido: {st.get('last_error') or engine.last_error or 'no iniciado'}")
    try:
        return _run_player_command(cmd, msg)
    except (ConnectionError, RuntimeError) as exc:   # motor remoto caído o sin respuesta
        raise ValueError(str(exc))


def _run_player_command(cmd: str, msg: dict) -> dict:

    def _audio_id() -> int:
        try:
            return int(msg["audio_id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Falta audio_id")

    if cmd == "play":
        if msg.get("audio_id") is None:
            engine.play_next()
        elif not engine.play_track(_audio_id()):
            raise ValueError(f"Audio id={msg['audio_id']} no existe o está inactivo")
    elif cmd == "queue":
        if not engine.queue_track(_audio_id()):
            raise ValueError(f"Audio id={msg['audio_id']} no existe o está inactivo")
    elif cmd == "cola_insertar":
        # Como "queue" pero en un lugar de la cola: el Studio inserta tras la canción elegida
        if msg.get("posicion") is None:
            raise ValueError("Falta posicion")
        if not engine.queue_track(_audio_id(), "", int(msg["posicion"])):
            raise ValueError(f"Audio id={msg['audio_id']} no existe o está inactivo")
    elif cmd in ("cola_mover", "cola_quitar", "plan_a_cola"):
        try:
            uid = int(msg["uid"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Falta uid")
        if cmd == "cola_quitar":
            engine.cola_quitar(uid)
        elif cmd == "cola_mover":
            engine.cola_mover(uid, int(msg.get("posicion") or 0))
        else:
            engine.plan_a_cola(uid, int(msg.get("posicion") or 0))
    elif cmd == "fire_effect":
        if not engine.fire_effect(_audio_id(), int(msg.get("canal_offset") or 0), _opciones_boton(msg)):
            raise ValueError(f"Audio id={msg['audio_id']} no existe o está inactivo")
    elif cmd == "set_mode":
        engine.set_mode(str(msg.get("mode") or msg.get("modo") or ""))
    elif cmd == "skip":
        engine.skip()
    elif cmd == "stop":
        engine.stop_playback()
    elif cmd == "clear_queue":
        engine.clear_queue()
    elif cmd == "lanzar_tanda":
        n = engine.lanzar_tanda(bool(msg.get("ahora")))
        if not n:
            raise ValueError("No hay ninguna tanda en espera")
        return {"ok": True, "cmd": cmd, "cuñas": n}
    elif cmd == "cue":
        if not engine.cue_play(_audio_id(), float(msg.get("desde") or 0)):
            raise ValueError(f"Audio id={msg['audio_id']} no existe o está inactivo")
    elif cmd == "cue_stop":
        engine.cue_stop()
    elif cmd == "detener_al_terminar":
        engine.detener_al_terminar(bool(msg.get("activo", True)))
    elif cmd == "pausar":
        engine.pausar(bool(msg.get("activo", True)))
    elif cmd == "seek":
        try:
            engine.seek(float(msg["posicion"]))
        except (KeyError, TypeError):
            raise ValueError("Falta posicion (segundos)")
    elif cmd == "plan_insertar":
        if not engine.plan_insertar(_audio_id(), int(msg.get("posicion") or 0)):
            raise ValueError(f"Audio id={msg['audio_id']} no existe o está inactivo")
    elif cmd in ("plan_mover", "plan_quitar"):
        try:
            uid = int(msg["uid"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Falta uid")
        if cmd == "plan_mover":
            engine.plan_mover(uid, int(msg.get("posicion") or 0))
        else:
            engine.plan_quitar(uid)
    else:
        raise ValueError(f"Comando desconocido: {cmd}")
    return {"ok": True, "cmd": cmd}


class PlayerCommand(BaseModel):
    audio_id:     Optional[int] = None
    canal_offset: Optional[int] = 0
    mode:         Optional[str] = None
    ahora:        Optional[bool] = False    # lanzar_tanda: ya, sin esperar el cambio de pista
    desde:        Optional[float] = 0.0     # cue: segundo desde el que se preescucha
    activo:       Optional[bool] = True     # detener_al_terminar / pausar
    posicion:     Optional[float] = 0       # plan_insertar / plan_mover (índice) · seek (segundos)
    uid:          Optional[int] = None      # plan_mover / plan_quitar


@app.post("/api/v1/player/{cmd}", tags=["Player"], summary="Enviar comando al motor de audio")
def player_command(cmd: str, body: Optional[PlayerCommand] = None):
    """
    Comandos: play (con audio_id = al aire ya; sin audio_id = siguiente de la cola),
    queue, fire_effect, set_mode (mode: manual|automatico), skip, stop, clear_queue,
    lanzar_tanda (ahora: bool; modo manual), cue (audio_id, desde) y cue_stop
    (preescucha por la salida interna).
    """
    if cmd not in PLAYER_COMMANDS:
        raise HTTPException(status_code=404, detail=f"Comando desconocido: {cmd}")
    try:
        return _player_command(cmd, body.model_dump() if body else {})
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


# ===========================================================================
# SECCIÓN: RE-PLANIFICACIÓN DINÁMICA — /api/v1/programa/recalcular
# Permite al Studio o al motor solicitar un recálculo de la cola sin cortar
# el audio que está sonando en ese momento.
# ===========================================================================

class RecalcularBody(BaseModel):
    motivo: Optional[str] = Field(
        default="manual",
        description="Razón del recálculo: 'manual' (operador), 'skip', 'aire_muerto', 'archivo_corrupto'."
    )
    uid_eliminado: Optional[int] = Field(
        default=None,
        description="UID del ítem que se acaba de eliminar del programa (para anotarlo en el log)."
    )


@app.post(
    "/api/v1/programa/recalcular",
    tags=["Player"],
    summary="Recalcular cola del programa manteniendo las tandas en su hora",
)
def recalcular_programa(body: Optional[RecalcularBody] = None):
    """
    Solicita una re-planificación dinámica del programa.

    El motor vacía su cola pendiente y la reconstruye desde el instante en que
    termina la pista que suena ahora, respetando estrictamente las horas de las
    tandas comerciales configuradas.

    **Cuándo llamarlo desde el Studio:**
    - Al eliminar una pista de la cola (`plan_quitar`).
    - Al mover una pista a otra posición (`plan_mover`).
    - Al hacer skip manual (opcional, el motor lo hace automáticamente).
    - Cuando el operador detecta un desfase y quiere cuadrar el reloj.

    **No interrumpe el audio que está sonando en este momento.**

    Responde con la cola actualizada para que el Studio la pinte de inmediato.
    """
    st = engine.status
    if not st.get("running"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Motor de audio detenido. Inicia el motor antes de recalcular.",
        )
    if st.get("modo") == "manual":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El recálculo solo aplica en modo automático. "
                   "En modo manual, el operador arma la cola.",
        )

    motivo = (body.motivo if body else None) or "manual"
    uid_el = body.uid_eliminado if body else None
    log_extra = f" (uid eliminado: {uid_el})" if uid_el else ""
    logger.info(
        "[API] Recálculo dinámico solicitado — motivo: %s%s", motivo, log_extra
    )

    # Encolar el comando en el hilo del motor (no bloquea la respuesta HTTP)
    engine.plan_recalcular()

    # Devolver el programa actualizado para que el Studio lo pinte.
    # Como el recálculo es asíncrono (se ejecuta en el hilo del motor),
    # esperamos hasta 500 ms a que se complete antes de responder.
    import time as _time
    for _ in range(10):
        _time.sleep(0.05)
        prog = engine.programa
        # El recálculo habrá terminado cuando el programa tenga ítems
        if prog.get("items") or prog.get("cola"):
            break

    prog = engine.programa
    return {
        "ok": True,
        "motivo": motivo,
        "modo": prog.get("modo"),
        "items": prog.get("items", []),
        "cola": prog.get("cola", []),
        "fin_estimado": prog.get("fin"),
        "total_items": len(prog.get("items", [])),
        "total_audios": sum(
            1 for i in prog.get("items", []) if i.get("tipo") == "audio"
        ),
        "total_tandas": sum(
            1 for i in prog.get("items", []) if i.get("tipo") == "tanda"
        ),
    }




# Paletas de la cartuchera (como las de Jazler): cada una con nombre y nº de botones propios.
# Se administran en Admin › Cartuchera; el operador carga sus botones desde el Studio.
class CartucheraSlotIn(BaseModel):
    audio_id: int
    etiqueta: str = Field("", max_length=40)


class PaletaIn(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=30)
    slots: Optional[int] = Field(None, ge=9, le=63)       # de 9 en 9: 1 a 7 páginas, tope 63 botones

    @field_validator("slots")
    @classmethod
    def _de_nueve_en_nueve(cls, v):
        if v is not None and v % 9:
            raise ValueError("Los botones de una paleta van de 9 en 9 (9, 18… hasta 63)")
        return v


def _paletas(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT clave, nombre, slots FROM paletas ORDER BY orden, clave")]


def _check_slot(conn, banco: str, slot: int) -> None:
    r = conn.execute("SELECT slots FROM paletas WHERE clave = ?", (banco,)).fetchone()
    if not r or not (1 <= slot <= r["slots"]):
        raise HTTPException(status_code=404, detail=f"Botón inexistente: {banco}/{slot}")


@app.get("/api/v1/cartuchera", tags=["Cartuchera"], summary="Paletas y botones asignados")
def get_cartuchera(conn: MariaDBConnection = Depends(get_db)):
    """Devuelve las paletas (en orden) y, por paleta, los botones que tienen audio asignado."""
    rows = conn.execute(
        """
        SELECT c.banco, c.slot, c.audio_id, c.etiqueta, c.color, c.mark_in, c.mark_out, c.volumen,
               a.titulo, a.artista, a.duracion, a.tipo_audio, a.archivo_path
        FROM cartuchera c
        JOIN audios a ON a.id = c.audio_id AND a.activo = 1
        ORDER BY c.banco, c.slot
        """
    ).fetchall()
    paletas = _paletas(conn)
    data = {p["clave"]: [] for p in paletas}
    for r in rows:
        if r["banco"] in data:
            data[r["banco"]].append(dict(r))
    return {"paletas": paletas, "bancos": {p["clave"]: p["slots"] for p in paletas},
            "nombres": {p["clave"]: p["nombre"] for p in paletas}, "slots": data}


@app.post("/api/v1/paletas", tags=["Cartuchera"], status_code=201, summary="Crear paleta")
async def crear_paleta(body: PaletaIn, conn: MariaDBConnection = Depends(get_db)):
    nombre = (body.nombre or "").strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="Escribe un nombre para la paleta")
    if conn.execute("SELECT 1 FROM paletas WHERE lower(nombre) = lower(?)", (nombre,)).fetchone():
        raise HTTPException(status_code=409, detail=f"Ya existe una paleta llamada «{nombre}»")
    clave = "p" + secrets.token_hex(3)
    orden = conn.execute("SELECT COALESCE(MAX(orden), -1) + 1 FROM paletas").fetchone()[0]
    conn.execute("INSERT INTO paletas (clave, nombre, slots, orden) VALUES (?, ?, ?, ?)",
                 (clave, nombre, body.slots or 9, orden))
    conn.commit()
    await _manager.broadcast(_ws_message("cartuchera_updated", {"banco": clave}))
    return {"clave": clave, "nombre": nombre, "slots": body.slots or 9}


@app.put("/api/v1/paletas/{clave}", tags=["Cartuchera"], summary="Renombrar o cambiar el nº de botones")
async def editar_paleta(clave: str, body: PaletaIn, conn: MariaDBConnection = Depends(get_db)):
    p = conn.execute("SELECT clave, nombre, slots FROM paletas WHERE clave = ?", (clave,)).fetchone()
    if not p:
        raise HTTPException(status_code=404, detail="Paleta no encontrada")
    nombre = (body.nombre or p["nombre"]).strip()
    if conn.execute("SELECT 1 FROM paletas WHERE lower(nombre) = lower(?) AND clave != ?", (nombre, clave)).fetchone():
        raise HTTPException(status_code=409, detail=f"Ya existe una paleta llamada «{nombre}»")
    slots = body.slots or p["slots"]
    conn.execute("UPDATE paletas SET nombre = ?, slots = ? WHERE clave = ?", (nombre, slots, clave))
    conn.execute("DELETE FROM cartuchera WHERE banco = ? AND slot > ?", (clave, slots))   # al achicar
    conn.commit()
    await _manager.broadcast(_ws_message("cartuchera_updated", {"banco": clave}))
    return {"clave": clave, "nombre": nombre, "slots": slots}


@app.delete("/api/v1/paletas/{clave}", tags=["Cartuchera"], summary="Borrar paleta y sus botones")
async def borrar_paleta(clave: str, conn: MariaDBConnection = Depends(get_db)):
    if not conn.execute("SELECT 1 FROM paletas WHERE clave = ?", (clave,)).fetchone():
        raise HTTPException(status_code=404, detail="Paleta no encontrada")
    if conn.execute("SELECT COUNT(*) FROM paletas").fetchone()[0] <= 1:
        raise HTTPException(status_code=409, detail="Debe quedar al menos una paleta")
    conn.execute("DELETE FROM cartuchera WHERE banco = ?", (clave,))
    conn.execute("DELETE FROM paletas WHERE clave = ?", (clave,))
    conn.commit()
    await _manager.broadcast(_ws_message("cartuchera_updated", {"banco": clave}))
    return {"ok": True}


@app.put("/api/v1/cartuchera/{banco}/{slot}", tags=["Cartuchera"], summary="Asignar audio a un botón")
async def set_cartuchera_slot(
    banco: str, slot: int, body: CartucheraSlotIn,
    conn: MariaDBConnection = Depends(get_db),
):
    _check_slot(conn, banco, slot)
    audio = conn.execute(
        "SELECT id, titulo FROM audios WHERE id = ? AND activo = 1", (body.audio_id,)
    ).fetchone()
    if not audio:
        raise HTTPException(status_code=404, detail=f"Audio id={body.audio_id} no encontrado")
    conn.execute(
        """
        INSERT INTO cartuchera (banco, slot, audio_id, etiqueta) VALUES (?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE
            audio_id = VALUES(audio_id), etiqueta = VALUES(etiqueta),
            mark_in = NULL, mark_out = NULL, updated_at = NOW()
        """,
        (banco, slot, body.audio_id, body.etiqueta.strip()),
    )
    conn.commit()
    await _manager.broadcast(_ws_message("cartuchera_updated", {"banco": banco, "slot": slot}))
    return {"banco": banco, "slot": slot, "audio_id": body.audio_id, "titulo": audio["titulo"]}


@app.delete("/api/v1/cartuchera/{banco}/{slot}", tags=["Cartuchera"], summary="Vaciar un botón")
async def clear_cartuchera_slot(banco: str, slot: int, conn: MariaDBConnection = Depends(get_db)):
    _check_slot(conn, banco, slot)
    conn.execute("DELETE FROM cartuchera WHERE banco = ? AND slot = ?", (banco, slot))
    conn.commit()
    await _manager.broadcast(_ws_message("cartuchera_updated", {"banco": banco, "slot": slot}))
    return {"banco": banco, "slot": slot, "audio_id": None}


# ===========================================================================
# SECCIÓN: CONFIG MOTOR — Parámetros de emisión (tabla config)
# ===========================================================================

class MotorConfig(BaseModel):
    crossfade_seg:       Optional[float] = Field(None, ge=0, le=20)
    ventana_artista_min: Optional[int]   = Field(None, ge=0, le=1440)
    max_mismo_genero:    Optional[int]   = Field(None, ge=1, le=10)
    ducking_db:          Optional[float] = Field(None, ge=-40, le=0)
    voz_fondo_pct:       Optional[int]   = Field(None, ge=0, le=100)   # música bajo la voz del locutor
    fade_corte_seg:      Optional[float] = Field(None, ge=0.2, le=15,
                                                 description="Fade de la canción cuando una tanda o evento inmediato la corta")
    regla_voces_mujer:   Optional[bool]  = Field(None, description="No sonar dos voces de mujer seguidas en una tanda")
    fade_corte_curva:    Optional[str]   = Field(None, max_length=80, pattern=r"^\s*\d{1,3}(\s*[,;]\s*\d{1,3}){1,11}\s*$",
                                                 description="Volumen % repartido en partes iguales del fade, p. ej. '100,70,40,30,16'")
    normalizacion_activa: Optional[bool] = None
    horas_planificadas:  Optional[float] = Field(None, ge=0.5, le=24)
    aleatoriedad:        Optional[int]   = Field(None, ge=0, le=50)
    tolerancia_tanda_seg: Optional[int]  = Field(None, ge=0, le=600)
    separacion_cancion_dias: Optional[int] = Field(None, ge=0, le=14)
    separacion_cancion_ventana_min: Optional[int] = Field(None, ge=0, le=720)
    normalizacion_lufs:  Optional[float] = Field(None, ge=-30, le=-5)
    tandas_inmediatas:   Optional[str]   = Field(None, max_length=200,
                                                 description="Minutos cuya tanda corta la canción, p. ej. '0' (vacío = ninguna)")
    tanda_max_seg:       Optional[int]   = Field(None, ge=0, le=1800)
    marcas_auto:         Optional[bool]  = Field(None, description="Detectar inicio y mezcla de la música al cargarla")
    silencio_seg:        Optional[int]   = Field(None, ge=0, le=120,
                                                 description="Aire muerto: segundos de silencio antes de actuar (0 = apagado)")
    tandas_minutos:      Optional[str]   = Field(None, max_length=200,
                                                 description="Minutos de las tandas, p. ej. '15,45'")
    tandas_cierre:       Optional[str]   = Field(None, max_length=200,
                                                 description="Tandas que TERMINAN en su minuto (0 = en punto), p. ej. '0'")

    @field_validator("tandas_minutos")
    @classmethod
    def _minutos_validos(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        partes = [x for x in re.split(r"[,;\s]+", v) if x]
        if not partes or any(not x.isdigit() or int(x) > 59 for x in partes):
            raise ValueError("tandas_minutos: minutos entre 0 y 59 separados por coma, p. ej. '15,45'")
        return ",".join(str(m) for m in sorted({int(x) for x in partes}))

    @field_validator("tandas_cierre")
    @classmethod
    def _cierre_valido(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None if v is None else ""
        partes = [x for x in re.split(r"[,;\s]+", v) if x]
        if any(not x.isdigit() or int(x) > 59 for x in partes):
            raise ValueError("tandas_cierre: minutos entre 0 y 59 separados por coma")
        return ",".join(str(m) for m in sorted({int(x) for x in partes}))

    @field_validator("tandas_inmediatas")
    @classmethod
    def _inmediatas_validas(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None if v is None else ""
        partes = [x for x in re.split(r"[,;\s]+", v) if x]
        if any(not x.isdigit() or int(x) > 59 for x in partes):
            raise ValueError("tandas_inmediatas: minutos entre 0 y 59 separados por coma")
        return ",".join(str(m) for m in sorted({int(x) for x in partes}))


def _read_motor_config(conn: MariaDBConnection) -> dict:
    valores = {r["clave"]: r["valor"] for r in conn.execute("SELECT clave, valor FROM config")}
    return {
        "modo":                valores.get("modo", "manual"),
        "crossfade_seg":       float(valores.get("crossfade_seg", 4)),
        "ventana_artista_min": int(float(valores.get("ventana_artista_min", 60))),
        "max_mismo_genero":    int(float(valores.get("max_mismo_genero", 2))),
        "ducking_db":          float(valores.get("ducking_db", -12)),
        "voz_fondo_pct":       int(float(valores.get("voz_fondo_pct", 20))),
        "fade_corte_seg":      float(valores.get("fade_corte_seg", 5) or 5),
        "fade_corte_curva":    valores.get("fade_corte_curva") or "100,70,40,30,16",
        "regla_voces_mujer":   valores.get("regla_voces_mujer", "1") not in ("0", "false", ""),
        "tandas_minutos":      valores.get("tandas_minutos", "15,45"),
        "tandas_inmediatas":   valores.get("tandas_inmediatas", ""),
        "tandas_cierre":       valores.get("tandas_cierre", ""),
        "tanda_max_seg":       int(float(valores.get("tanda_max_seg", 240) or 0)),
        "silencio_seg":        int(float(valores.get("silencio_seg", 10) or 0)),
        "marcas_auto":         valores.get("marcas_auto", "1") not in ("0", "false", ""),
        "normalizacion_activa": valores.get("normalizacion_activa", "1") not in ("0", "false", ""),
        "normalizacion_lufs":  float(valores.get("normalizacion_lufs", -16)),
        "horas_planificadas":  float(valores.get("horas_planificadas", 2)),
        "aleatoriedad":        int(float(valores.get("aleatoriedad", 3))),
        "tolerancia_tanda_seg": int(float(valores.get("tolerancia_tanda_seg", 90))),
        "separacion_cancion_dias": int(float(valores.get("separacion_cancion_dias", 1))),
        "separacion_cancion_ventana_min": int(float(valores.get("separacion_cancion_ventana_min", 60))),
    }


@app.get("/api/v1/config/motor", tags=["Config"], summary="Parámetros del motor de audio")
def get_motor_config(conn: MariaDBConnection = Depends(get_db)):
    return _read_motor_config(conn)


@app.put("/api/v1/config/motor", tags=["Config"], summary="Guardar parámetros del motor")
def update_motor_config(body: MotorConfig, conn: MariaDBConnection = Depends(get_db)):
    """
    Guarda solo los campos enviados y los aplica al motor en caliente.
    El modo (manual/automático) se cambia con el comando set_mode, no aquí.
    """
    datos = body.model_dump(exclude_none=True)
    if body.tandas_inmediatas == "":
        datos["tandas_inmediatas"] = ""
    for clave, valor in datos.items():
        if isinstance(valor, bool):
            valor = "1" if valor else "0"
        conn.execute(
            "INSERT INTO config (clave, valor) VALUES (?, ?)"
            " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
            (clave, str(valor)),
        )
    conn.commit()
    engine.reload_config()
    return _read_motor_config(conn)


def _minutos_texto(v, campo: str) -> str:
    partes = [x for x in re.split(r"[,;\s]+", str(v or "")) if x]
    if any(not x.isdigit() or int(x) > 59 for x in partes):
        raise HTTPException(status_code=422, detail=f"{campo}: minutos entre 0 y 59 separados por coma")
    return ",".join(str(m) for m in sorted({int(x) for x in partes}))


class GrillaTandas(BaseModel):
    grilla: dict[str, dict] = Field(default_factory=dict)


@app.get("/api/v1/comerciales/tandas-grilla", tags=["Comerciales"], summary="Tandas por día y hora")
def get_grilla_tandas(conn: MariaDBConnection = Depends(get_db)):
    """Grilla {"dia-hora": {...}} (dia 0=Dom…6=Sab). Las horas que no aparecen usan los minutos generales."""
    from backend.audio.audio_engine import cargar_grilla
    fila = conn.execute("SELECT valor FROM config WHERE clave = 'tandas_grilla'").fetchone()
    cfg = _read_motor_config(conn)
    return {"grilla": cargar_grilla(fila["valor"] if fila else ""),
            "general": {k: cfg[k] for k in ("tandas_minutos", "tandas_inmediatas", "tandas_cierre")}}


@app.put("/api/v1/comerciales/tandas-grilla", tags=["Comerciales"], summary="Guardar tandas por día y hora")
def put_grilla_tandas(body: GrillaTandas, conn: MariaDBConnection = Depends(get_db)):
    limpia = {}
    for clave, e in body.grilla.items():
        m = re.fullmatch(r"([0-6])-(\d{1,2})", clave)
        if not m or int(m.group(2)) > 23:
            raise HTTPException(status_code=422, detail=f"Clave inválida: {clave} (dia 0-6, hora 0-23)")
        if e.get("off"):
            limpia[clave] = {"off": True}
            continue
        d = {"minutos": _minutos_texto(e.get("minutos"), "minutos")}
        if not d["minutos"]:
            raise HTTPException(status_code=422, detail=f"{clave}: escribe los minutos o marca la hora sin tandas")
        for k in ("inmediatas", "cierre"):
            if k in e:
                d[k] = _minutos_texto(e.get(k), k)
        limpia[clave] = d
    conn.execute("INSERT INTO config (clave, valor) VALUES ('tandas_grilla', ?)"
                 " ON DUPLICATE KEY UPDATE valor = VALUES(valor)", (json.dumps(limpia),))
    conn.commit()
    engine.reload_config()
    return get_grilla_tandas(conn)


@app.get("/api/v1/audios/{audio_id}/stream", tags=["Audios"], summary="Preescucha del audio")
def stream_audio(audio_id: int, conn: MariaDBConnection = Depends(get_db)):
    """Sirve el archivo de audio (con soporte de Range) para preescucha en el navegador."""
    row = conn.execute(
        "SELECT archivo_path FROM audios WHERE id = ? AND activo = 1", (audio_id,)
    ).fetchone()
    path = resolve_media(row["archivo_path"]) if row else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Archivo del audio id={audio_id} no encontrado")
    return FileResponse(path)


# ===========================================================================
# SECCIÓN: SALIDAS DE AUDIO — Tarjetas de sonido y roles (aire / preescucha)
# ===========================================================================

ROLES_SALIDA = ("aire", "preescucha", "cartuchera")


class SalidasIn(BaseModel):
    aire:       Optional[int] = Field(None, ge=-1, description="-1 = predeterminada del sistema")
    preescucha: Optional[int] = Field(None, ge=-1)
    cartuchera: Optional[int] = Field(None, ge=-2, description="-2 = la misma tarjeta que el aire")


def _read_salidas(conn: MariaDBConnection) -> dict:
    valores = {r["clave"]: r["valor"] for r in conn.execute(
        "SELECT clave, valor FROM config WHERE clave LIKE 'dispositivo_%'")}
    return {rol: int(float(valores.get(f"dispositivo_{rol}", -2 if rol == "cartuchera" else -1)))
            for rol in ROLES_SALIDA}


def _listar_dispositivos() -> list:
    try:
        return engine.list_devices()
    except Exception as exc:          # motor remoto caído
        logger.warning(f"No se pudieron listar las tarjetas: {exc}")
        return []


@app.get("/api/v1/audio/dispositivos", tags=["Config"], summary="Tarjetas de sonido y salidas")
def get_dispositivos(conn: MariaDBConnection = Depends(get_db)):
    """
    Tarjetas que ve el motor (índice 0 = sin sonido) y qué tarjeta usa cada
    salida: `aire` (lo que sale a la emisora) y `preescucha` (escucha interna).
    """
    return {"dispositivos": _listar_dispositivos(), "salidas": _read_salidas(conn)}


@app.put("/api/v1/audio/salidas", tags=["Config"], summary="Asignar tarjetas a las salidas")
def put_salidas(body: SalidasIn, conn: MariaDBConnection = Depends(get_db)):
    """
    Se aplica en caliente: la próxima pista (o preescucha) ya sale por la
    tarjeta nueva; lo que está sonando termina en la anterior.
    """
    disponibles = {d["index"] for d in _listar_dispositivos()}
    for rol, idx in body.model_dump(exclude_none=True).items():
        if idx >= 0 and disponibles and idx not in disponibles:
            raise HTTPException(status_code=422, detail=f"La tarjeta {idx} no existe en esta PC")
        conn.execute(
            "INSERT INTO config (clave, valor) VALUES (?, ?)"
            " ON DUPLICATE KEY UPDATE valor = VALUES(valor)",
            (f"dispositivo_{rol}", str(idx)),
        )
    conn.commit()
    engine.reload_config()
    return {"dispositivos": _listar_dispositivos(), "salidas": _read_salidas(conn)}


# ===========================================================================
# SECCIÓN: TANDAS — Secciones del corte (como las prioridades de Jazler)
# ===========================================================================

class SeccionIn(BaseModel):
    numero:    int  = Field(..., ge=0, le=99)
    nombre:    str  = Field(..., min_length=1, max_length=60)
    opcional:  bool = False
    comercial: bool = False


@app.get("/api/v1/tandas/secciones", tags=["Pautas"], summary="Secciones de la tanda")
def get_secciones(conn: MariaDBConnection = Depends(get_db)):
    """Orden dentro de la tanda. Cada pauta pertenece a una sección."""
    usos = dict(conn.execute("SELECT COALESCE(seccion, 3), COUNT(*) FROM pautas GROUP BY 1").fetchall())
    return [{"numero": r["numero"], "nombre": r["nombre"], "opcional": bool(r["opcional"]),
             "comercial": bool(r["comercial"]), "pautas": usos.get(r["numero"], 0)}
            for r in conn.execute("SELECT * FROM tanda_secciones ORDER BY numero")]


@app.put("/api/v1/tandas/secciones", tags=["Pautas"], summary="Guardar secciones de la tanda")
def put_secciones(body: List[SeccionIn], conn: MariaDBConnection = Depends(get_db)):
    """
    Reemplaza la lista completa. Debe quedar al menos una sección comercial.
    Una pauta cuya sección se borra se trata como comercial (nunca se pierde).
    """
    numeros = [s.numero for s in body]
    if len(numeros) != len(set(numeros)):
        raise HTTPException(status_code=422, detail="Hay números de sección repetidos")
    if not any(s.comercial for s in body):
        raise HTTPException(status_code=422, detail="Debe haber al menos una sección comercial")
    conn.execute("DELETE FROM tanda_secciones")
    conn.executemany("INSERT INTO tanda_secciones (numero, nombre, opcional, comercial) VALUES (?, ?, ?, ?)",
                     [(s.numero, s.nombre.strip(), int(s.opcional), int(s.comercial)) for s in body])
    conn.commit()
    try:
        engine.plan_regenerar()
    except Exception:
        pass
    return get_secciones(conn)


@app.get("/api/v1/normalizacion", tags=["Config"], summary="Estado de la normalización de volumen")
def get_normalizacion(conn: MariaDBConnection = Depends(get_db)):
    """Cuántos audios están medidos, pendientes o con error, y si hay ffmpeg."""
    cuenta = {"ok": 0, "error": 0, "pendiente": 0}
    for r in conn.execute(
        "SELECT COALESCE(loudness_estado, 'pendiente') AS e, COUNT(*) AS n"
        " FROM audios WHERE activo = 1 GROUP BY e"
    ):
        cuenta[r["e"]] = r["n"]
    marcas = {"auto": 0, "manual": 0, "pendiente": 0, "error": 0}
    nombres = {1: "auto", 0: "manual", -1: "error", None: "pendiente"}
    for r in conn.execute("SELECT marcas_auto AS m, COUNT(*) AS n FROM audios"
                          " WHERE activo = 1 AND tipo_audio = 'Musica' GROUP BY m"):
        marcas[nombres.get(r["m"], "pendiente")] += r["n"]
    return {
        **cuenta,
        "marcas": marcas,
        "ffmpeg": _normalizador.ffmpeg,
        "config": {k: v for k, v in _read_motor_config(conn).items() if k.startswith("normalizacion")},
    }


@app.post("/api/v1/normalizacion/remedir", tags=["Config"], summary="Volver a medir audios")
def remedir_normalizacion(solo_errores: bool = True, conn: MariaDBConnection = Depends(get_db)):
    """Marca como pendientes los audios con error (o todos) y despierta al medidor."""
    sql = "UPDATE audios SET loudness_estado = NULL WHERE activo = 1"
    if solo_errores:
        sql += " AND loudness_estado = 'error'"
    n = conn.execute(sql).rowcount
    conn.commit()
    _normalizador.avisar()
    return {"pendientes": n}


@app.get("/api/v1/historial", tags=["Sistema"], summary="Historial de emisión")
def get_historial(limit: int = 100, conn: MariaDBConnection = Depends(get_db)):
    """Últimos elementos que salieron al aire (música, pautas y efectos)."""
    rows = conn.execute(
        "SELECT * FROM historial_emision ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/v1/stats", tags=["Sistema"])
def get_stats(conn: MariaDBConnection = Depends(get_db)):
    """
    Retorna estadísticas generales de la Biblioteca Musical.

    Consulta directamente la tabla `audios` para producir métricas
    útiles en dashboards y widgets del Shell de Ónix FM.

    Ejemplo de respuesta:
    {
      "total_audios": 245,
      "total_activos": 240,
      "duracion_total_segundos": 58320,
      "por_cat1": {"Romántica": 45, "Pop": 38, ...},
      "por_cat2": {"Éxito": 80, "Clásico": 55, ...},
      "por_voz": {"Hombre": 110, "Mujer": 90, ...}
    }
    """
    logger.info("GET /api/v1/stats → consultando métricas de audios")

    # ── Totales generales ─────────────────────────────────────────────────
    totales = conn.execute("""
        SELECT
            COUNT(*)                          AS total_audios,
            SUM(CASE WHEN activo = 1 THEN 1 ELSE 0 END) AS total_activos,
            SUM(CASE WHEN activo = 0 THEN 1 ELSE 0 END) AS total_inactivos,
            COALESCE(SUM(duracion), 0)        AS duracion_total_segundos,
            COALESCE(AVG(bpm), 0)             AS bpm_promedio,
            COUNT(DISTINCT artista)           AS total_artistas
        FROM audios
    """).fetchone()

    # ── Distribución por Género (cat1 ↔ subgenero) ────────────────────────
    rows_cat1 = conn.execute("""
        SELECT subgenero AS valor, COUNT(*) AS cantidad
        FROM audios
        WHERE activo = 1 AND subgenero IS NOT NULL
        GROUP BY subgenero
        ORDER BY cantidad DESC
    """).fetchall()

    # ── Distribución por Rotación (cat2 ↔ categoria) ──────────────────────
    rows_cat2 = conn.execute("""
        SELECT categoria AS valor, COUNT(*) AS cantidad
        FROM audios
        WHERE activo = 1 AND categoria IS NOT NULL
        GROUP BY categoria
        ORDER BY cantidad DESC
    """).fetchall()

    # ── Distribución por Voz (voz ↔ genero_vocal) ─────────────────────────
    rows_voz = conn.execute("""
        SELECT genero_vocal AS valor, COUNT(*) AS cantidad
        FROM audios
        WHERE activo = 1 AND genero_vocal IS NOT NULL
        GROUP BY genero_vocal
        ORDER BY cantidad DESC
    """).fetchall()

    # ── Distribución por Subgénero (cat3) ─────────────────────────────────
    rows_cat3 = conn.execute("""
        SELECT cat3 AS valor, COUNT(*) AS cantidad
        FROM audios
        WHERE activo = 1 AND cat3 IS NOT NULL
        GROUP BY cat3
        ORDER BY cantidad DESC
    """).fetchall()

    # ── Inventario por tipo de audio (contadores del Studio y del Shell) ──
    por_tipo = {t: 0 for t in TIPOS_AUDIO}
    for r in conn.execute(
        "SELECT tipo_audio, COUNT(*) AS cantidad FROM audios WHERE activo = 1 GROUP BY tipo_audio"
    ):
        por_tipo[r["tipo_audio"]] = r["cantidad"]

    duracion_total = totales["duracion_total_segundos"]

    logger.info(
        f"  → Stats: {totales['total_activos']} activos / "
        f"{totales['total_artistas']} artistas / "
        f"{duracion_total}s duración total"
    )

    return {
        "total_audios":             totales["total_audios"],
        "total_activos":            totales["total_activos"],
        "total_inactivos":          totales["total_inactivos"],
        "total_artistas":           totales["total_artistas"],
        "duracion_total_segundos":  duracion_total,
        "duracion_total_horas":     round(duracion_total / 3600, 2),
        "bpm_promedio":             round(totales["bpm_promedio"] or 0, 1),
        "por_cat1_genero":   {r["valor"]: r["cantidad"] for r in rows_cat1},
        "por_cat2_rotacion": {r["valor"]: r["cantidad"] for r in rows_cat2},
        "por_cat3_subgenero":{r["valor"]: r["cantidad"] for r in rows_cat3},
        "por_voz":           {r["valor"]: r["cantidad"] for r in rows_voz},
        "por_tipo":          por_tipo,
        "total":             totales["total_activos"] or 0,
        **por_tipo,          # claves planas: Musica, Cuña, Efecto, …
    }


# ---------------------------------------------------------------------------
# WebSocket — Canal de comandos en tiempo real para el Shell de Ónix FM
#
# POR QUÉ EL 403 OCURRE SIN ESTE ENDPOINT:
#   Cuando el navegador envía una petición de upgrade WebSocket
#   ("GET /ws" con "Upgrade: websocket"), Starlette busca un handler
#   registrado para esa ruta. Si no encuentra ninguno, devuelve 403
#   Forbidden — no un 404 como haría para HTTP normal. Es un
#   comportamiento específico del handshake WS en Starlette.
#
# POR QUÉ CORS NO ES EL PROBLEMA:
#   CORSMiddleware de Starlette SOLO procesa peticiones HTTP; no
#   interviene en el handshake WebSocket. El navegador sí envía el
#   header "Origin" durante el upgrade, pero ni FastAPI ni Starlette
#   lo validan por defecto. Por eso no añadimos ninguna comprobación
#   de origen aquí — aceptamos conexiones de cualquier origen.
# ---------------------------------------------------------------------------

class _ConnectionManager:
    """
    Gestiona el conjunto de conexiones WebSocket activas y permite
    hacer broadcast de mensajes a todos los clientes a la vez.

    El patrón es simple a propósito: una lista en memoria es suficiente
    para el caso de uso de Ónix FM (un único proceso uvicorn, pocos
    clientes simultáneos). Si en el futuro se necesita escalar a varios
    workers, habría que sustituir esto por un broker como Redis Pub/Sub.
    """

    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        """Acepta el handshake y registra la conexión."""
        await ws.accept()   # ← responde al upgrade con HTTP 101 Switching Protocols
        self._active.append(ws)
        logger.info(f"[WS] Cliente conectado. Total activos: {len(self._active)}")

    def disconnect(self, ws: WebSocket) -> None:
        """Elimina la conexión del registro (la llamada no es async porque no hay I/O)."""
        if ws in self._active:
            self._active.remove(ws)
        logger.info(f"[WS] Cliente desconectado. Total activos: {len(self._active)}")

    async def broadcast(self, message: str) -> None:
        """
        Retransmite el mensaje a todos los clientes registrados.

        Si al enviar un mensaje se descubre que una conexión está rota
        (excepción al hacer send_text), esa conexión se acumula en la
        lista 'muertos' y se elimina al final del ciclo. Esto evita
        modificar la lista mientras se itera sobre ella.
        """
        muertos: list[WebSocket] = []
        for ws in self._active:
            try:
                await ws.send_text(message)
            except Exception:
                muertos.append(ws)
        for ws in muertos:
            self.disconnect(ws)


# Instancia global — un único gestor para todo el proceso
_manager = _ConnectionManager()
# Quién tiene el control del aire (una sola PC a la vez). `ws` = su conexión; None = libre.
_control: dict = {"ws": None, "usuario": ""}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Canal WebSocket principal del Shell de Ónix FM.

    Protocolo de mensajes (JSON, texto):
        { "module": str, "cmd": str, "ts": int, "data": object }

    Comandos conocidos enviados por el frontend (biblioteca-musical.html):
        UPLOAD        — nuevo audio añadido a la biblioteca
        UPDATE        — metadatos de audio modificados
        DELETE        — audio eliminado (soft-delete)
        FILE_SELECTED — archivo seleccionado en el dropzone del modal

    Comandos del Studio para el motor de audio (app.js), sin "module":
        play        { cmd, audio_id? }   — sin audio_id: siguiente de la cola
        queue       { cmd, audio_id }
        fire_effect { cmd, audio_id, canal_offset }
        set_mode    { cmd, mode: "manual"|"automatico" }
        skip / stop / clear_queue / ping

    Flujo de cada mensaje:
        1. El frontend llama a wsCmd(cmd, data) → JSON.stringify → ws.send()
        2. Este endpoint recibe el texto, lo deserializa para loguear,
           y hace broadcast a todos los clientes conectados (incluido
           el emisor), de forma que el Shell y otros módulos puedan
           reaccionar al evento.
    """
    # Mismo control que las páginas: sesión (o PC del estudio) y origen propio
    usuario = auth_api.usuario_de(ws)
    origen = ws.headers.get("origin")
    if usuario is None or (origen and origen.split("://", 1)[-1] != ws.headers.get("host", "")):
        await ws.close(code=1008)
        return
    opera_aire = auth_api.puede_operar_aire(usuario["rol"])
    await _manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()

            # Si el JSON está malformado lo registramos como advertencia
            # pero no cortamos la conexión
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[WS] Mensaje no-JSON recibido: {raw[:120]}")
                continue
            if not isinstance(msg, dict):
                continue

            cmd    = msg.get("cmd",    "?")
            module = msg.get("module", "?")

            # Keep-alive
            if cmd == "ping":
                await ws.send_text(_ws_message("pong"))
                continue

            # Comandos del Studio para el motor de audio. No se reemiten:
            # el motor responde con sus propios eventos (track_started, …).
            if cmd in PLAYER_COMMANDS and not opera_aire:
                await ws.send_text(_ws_message(
                    "playback_error", {"reason": "sin_permiso", "cmd": cmd,
                                       "msg": "Tu usuario no puede operar el aire"}))
                continue
            # Control del aire: una sola PC opera a la vez. Quien lo tiene lo suelta al cerrar, o lo toma otra con «tomar_control».
            if cmd == "tomar_control" and opera_aire:
                previo = _control["ws"]
                _control.update(ws=ws, usuario=usuario["nombre"])
                if previo is not None and previo is not ws:
                    await previo.send_text(_ws_message("playback_error", {
                        "reason": "control_perdido", "msg": f"{usuario['nombre']} tomó el control del aire"}))
                await _manager.broadcast(_ws_message("control_aire", {"usuario": usuario["nombre"]}))
                continue
            if cmd in PLAYER_COMMANDS and _control["ws"] is None:
                _control.update(ws=ws, usuario=usuario["nombre"])
                await _manager.broadcast(_ws_message("control_aire", {"usuario": usuario["nombre"]}))
            if cmd in PLAYER_COMMANDS and _control["ws"] is not ws:
                await ws.send_text(_ws_message(
                    "playback_error", {"reason": "sin_control", "cmd": cmd,
                                       "msg": f"El control del aire lo tiene {_control['usuario']}. Toma el control para operar."}))
                continue
            if cmd in PLAYER_COMMANDS:
                logger.info(f"[WS] ← player/{cmd} | {str(msg)[:120]}")
                try:
                    # El motor consulta SQLite: fuera del event loop.
                    await asyncio.to_thread(_player_command, cmd, msg)
                except ValueError as exc:
                    await ws.send_text(_ws_message(
                        "playback_error",
                        {"reason": "comando_rechazado", "msg": str(exc), "cmd": cmd},
                    ))
                continue

            logger.info(
                f"[WS] ← {module}/{cmd} | "
                f"data={str(msg.get('data', {}))[:120]}"
            )

            # Resto de mensajes (UPLOAD, UPDATE, …): reemitir a todos los clientes
            await _manager.broadcast(raw)

    except WebSocketDisconnect:
        pass                                              # desconexión normal del cliente
    except Exception as exc:
        # Error inesperado (red caída, PC apagada…) — logueamos
        logger.error(f"[WS] Error inesperado: {exc}")
    finally:
        # Por cualquier vía que se cierre la conexión, esa PC suelta el control del aire: si no, ninguna otra podría operar
        if _control["ws"] is ws:
            _control.update(ws=None, usuario="")
            try:
                await _manager.broadcast(_ws_message("control_aire", {"usuario": ""}))
            except Exception:
                pass
        _manager.disconnect(ws)

# ===========================================================================
# SECCIÓN: FRONTEND — Servir la aplicación SPA
# ===========================================================================

# Rutas absolutas para evitar fallos de carpeta
BASE_DIR   = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ADMIN_DIR    = FRONTEND_DIR / "admin"

@app.get("/")
async def read_index():
    """Studio: la consola del operador (estilo Jazler SoHo)."""
    return FileResponse(FRONTEND_DIR / "studio" / "index.html")

@app.get("/login")
async def pagina_login():
    """Inicio de sesión para entrar desde otras PCs de la red."""
    return FileResponse(FRONTEND_DIR / "login" / "index.html")

@app.get("/studio")
async def read_studio():
    """Dirección anterior del Studio: se mantiene para accesos guardados."""
    return RedirectResponse("/", status_code=307)

# Montar carpetas estáticas
# /static -> frontend/ (studio/studio.js, studio/studio.css)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# /admin -> frontend/admin/ (para la administración)
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")

if __name__ == "__main__":
    import uvicorn
    # Puerto 8000 por defecto para Ónix FM Core
    # reload reinicia el proceso (y corta el audio al aire) con cada cambio de
    # código: solo se activa a pedido, con ONIX_RELOAD=1.
    uvicorn.run(
        "api_server:app", host="0.0.0.0",
        port=int(os.getenv("ONIX_PORT", "8000")),
        reload=os.getenv("ONIX_RELOAD") == "1",
    )
