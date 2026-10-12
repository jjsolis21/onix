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
import shutil
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status, Depends, WebSocket, WebSocketDisconnect, Request, Form, File, UploadFile, Query
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

try:
    from init_db import initialize_database
except ImportError:
    # Si falla, intentamos importación relativa
    from scripts.init_db import initialize_database

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
from backend.api import problemas as problemas_api
from backend.api import respaldos as respaldos_api
from backend.api.red import router as red_router
from backend.api import biblioteca as biblioteca_api
from backend.api import auth as auth_api

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
# Conexión a DB con check_same_thread=False
# Esto es necesario porque FastAPI usa múltiples hilos y SQLite por defecto
# rechaza conexiones creadas en un hilo distinto al que las usa.
# ---------------------------------------------------------------------------
def get_connection(db_path: str = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False)  # FIX: multi-thread support
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

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

# Eventos del motor que se retransmiten a los clientes WebSocket
ENGINE_EVENTS = (
    "track_started", "track_ended", "crossfade_start", "playback_stopped",
    "playback_error", "mode_changed", "effect_fired", "queue_updated",
    "pauta_scheduled", "pauta_missed", "tanda_scheduled", "respaldo_activado",
    "engine_stopped", "engine_connected", "cue_started", "cue_stopped",
    "programa_actualizado", "tanda_excedida", "tanda_inmediata", "evento_inmediato", "detener_al_terminar", "seek",
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
    initialize_database(DB_PATH)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    MUSICA_DIR.mkdir(parents=True, exist_ok=True)
    _normalize_media_paths()
    _backfill_durations()
    _normalizador.start()     # mide en segundo plano los audios sin LUFS
    _respaldos.start()        # copia diaria de la base de datos

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


# ---------------------------------------------------------------------------
# DB Dependency
# ---------------------------------------------------------------------------
@contextmanager
def db_session():
    conn = get_connection(DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db():
    with db_session() as conn:
        yield conn





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

def _get_all_valid_values(conn: sqlite3.Connection, nombre_interno: str) -> set[str]:
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


def _validate_categoria_valor(
    conn: sqlite3.Connection,
    nombre_interno: str,
    valor: Optional[str],
    campo_api: str,
) -> None:
    """
    Acepta cualquier string no vacío para una categoría.
    Si el valor no existe en config_categorias_valores lo auto-registra
    (upsert) para mantener el catálogo actualizado.
    Permite None/vacío (campo opcional en audio).
    """
    if not valor:
        return

    # Buscar la definición de la categoría
    cat_def = conn.execute(
        "SELECT id FROM config_categorias_def WHERE nombre_interno = ? AND activo = 1",
        (nombre_interno,)
    ).fetchone()

    if not cat_def:
        # La categoría no existe en la configuración — aceptar igualmente
        logger.warning(
            f"Categoría '{nombre_interno}' no definida en config_categorias_def; "
            f"se acepta el valor '{valor}' sin registrar."
        )
        return

    cat_id = cat_def["id"]

    # Verificar si el valor ya existe (activo o inactivo)
    existing = conn.execute(
        "SELECT id, activo FROM config_categorias_valores WHERE cat_id = ? AND valor = ?",
        (cat_id, valor)
    ).fetchone()

    if existing is None:
        # Auto-registrar el valor nuevo
        max_orden = conn.execute(
            "SELECT COALESCE(MAX(orden), 0) FROM config_categorias_valores WHERE cat_id = ?",
            (cat_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO config_categorias_valores (cat_id, valor, orden, activo) VALUES (?, ?, ?, 1)",
            (cat_id, valor, max_orden + 1)
        )
        logger.info(f"Auto-registrado valor '{valor}' en categoría '{nombre_interno}'.")
    elif not existing["activo"]:
        # Reactivar si estaba inactivo
        conn.execute(
            "UPDATE config_categorias_valores SET activo = 1 WHERE id = ?",
            (existing["id"],)
        )
        logger.info(f"Reactivado valor '{valor}' en categoría '{nombre_interno}'.")


# ---- Endpoints de Configuración ----

@app.get(
    "/api/v1/config/biblioteca/schema",
    summary="Schema de categorías de Biblioteca",
    tags=["Config Biblioteca"],
)
def get_biblioteca_schema(conn: sqlite3.Connection = Depends(get_db)):
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
def update_categoria_valor(valor_id: int, body: ValorUpdateRequest, conn: sqlite3.Connection = Depends(get_db)):
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
def update_categoria(nombre_interno: str, body: CategoriaUpdateRequest, conn: sqlite3.Connection = Depends(get_db)):
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
    conn: sqlite3.Connection = Depends(get_db),
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

    # Un valor borrado antes (soft-delete) se reactiva en vez de chocar con él
    borrado = conn.execute(
        "SELECT id FROM config_categorias_valores WHERE cat_id = ? AND valor = ? AND activo = 0",
        (cat["id"], body.valor)).fetchone()
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
    except sqlite3.IntegrityError:
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


@app.delete(
    "/api/v1/config/biblioteca/valores/{valor_id}",
    status_code=status.HTTP_200_OK,
    summary="Eliminar valor de una categoría",
    tags=["Config Biblioteca"],
)
def delete_categoria_valor(
    valor_id: int,
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Elimina (soft-delete: marca como inactivo) un valor de categoría.
    Los audios que ya usaban este valor NO se ven afectados retroactivamente.
    """
    logger.info(f"DELETE /api/v1/config/biblioteca/valores/{valor_id}")

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

    # Verificar si algún audio usa este valor (solo informativo, no bloquea)
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
            pass  # campo puede no existir en versiones anteriores

    # Soft-delete
    conn.execute(
        "UPDATE config_categorias_valores SET activo = 0 WHERE id = ?",
        (valor_id,)
    )

    logger.info(
        f"  → Valor '{valor['valor']}' desactivado de '{valor['nombre_interno']}'. "
        f"Audios afectados: {audios_afectados}"
    )

    return {
        "mensaje": f"Valor '{valor['valor']}' eliminado de '{valor['etiqueta_visible']}'",
        "advertencia": (
            f"{audios_afectados} audio(s) aún usan este valor y quedarán sin categoría válida."
            if audios_afectados > 0 else None
        ),
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
    # Campos de categoría dinámica (nombres del frontend Jazler)
    cat1:  Optional[str] = Field(default=None, description="Género → config cat1")
    cat2:  Optional[str] = Field(default=None, description="Rotación → config cat2")
    cat3:  Optional[str] = Field(default=None, description="Subgénero → config cat3")
    voz:   Optional[str] = Field(default=None, description="Voz/Tipo → config voz")


class AudioUpdateRequest(BaseModel):
    titulo:            Optional[str] = Field(default=None, min_length=1, max_length=200)
    artista:           Optional[str] = Field(default=None, min_length=1, max_length=200)
    album:             Optional[str] = Field(default=None, max_length=200)
    duracion:          Optional[int] = Field(default=None, ge=0)
    bpm:               Optional[int] = Field(default=None, ge=40, le=300)
    fecha_lanzamiento: Optional[str] = Field(default=None)
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


# Tipos de audio válidos (audios.tipo_audio). Solo 'Musica' entra en rotación.
TIPOS_AUDIO = ("Musica", "Cuña", "Efecto", "Sweeper", "Programa", "Tips")


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
    conn: sqlite3.Connection = Depends(get_db),
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
        _validate_categoria_valor(conn, "cat1", "COMERCIAL", "cat1 (Género)")
        cursor = conn.execute(
            "INSERT INTO audios (titulo, artista, duracion, archivo_path, subgenero, tipo_audio)"
            " VALUES (?, 'PUBLICIDAD', ?, ?, 'COMERCIAL', 'Cuña')",
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
                except sqlite3.IntegrityError:
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


def _validate_all_categorias(
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection = Depends(get_db),
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
    if cat1:  cat1  = cat1.upper()
    if cat2:  cat2  = cat2.upper()
    if cat3:  cat3  = cat3.upper()
    if voz:   voz   = voz.upper()

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

    # PASO 3: Insertar en DB
    try:
        cursor = conn.execute("""
            INSERT INTO audios (
                titulo, artista, album, duracion, archivo_path,
                subgenero, categoria, cat3, genero_vocal,
                bpm, intro, outro, hook, fecha_lanzamiento,
                cue_inicio, cue_intro, cue_inicio_coro, cue_final_coro,
                cue_mezcla, fade_in, fade_out, tipo_audio, origen, energia, aprobada
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            # La música nueva entra a la Bandeja: no suena en automático hasta aprobarla
            0 if tipo_audio == "Musica" else 1,
        ))
        nuevo_id = cursor.lastrowid

    except sqlite3.IntegrityError as e:
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
    conn: sqlite3.Connection = Depends(get_db),
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
    cat1_final = body.cat1 if body.cat1 is not None else audio["subgenero"]
    cat2_final = body.cat2 if body.cat2 is not None else audio["categoria"]
    cat3_final = body.cat3 if body.cat3 is not None else audio["cat3"]
    voz_final  = body.voz  if body.voz  is not None else audio["genero_vocal"]

    # Validar solo si el campo viene en el payload o ya tenía valor
    _validate_all_categorias(conn, cat1_final, cat2_final, cat3_final, voz_final)
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
    
    if body.cat1 is not None: campos_update["subgenero"]    = body.cat1.upper()
    if body.cat2 is not None: campos_update["categoria"]    = body.cat2.upper()
    if body.cat3 is not None: campos_update["cat3"]         = body.cat3.upper()
    if body.voz  is not None: campos_update["genero_vocal"] = body.voz.upper()
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

    if not campos_update:
        return {"mensaje": "Sin cambios que aplicar", "data": {"id": audio_id}}

    # Si el usuario movió el inicio o la mezcla, son marcas a mano: la
    # detección automática no las vuelve a tocar.
    if any(k in campos_update and abs(float(campos_update[k] or 0) - float(audio[k] or 0)) > 0.05
           for k in ("cue_inicio", "cue_mezcla")):
        campos_update["marcas_auto"] = 0

    campos_update["updated_at"] = "datetime('now')"

    set_clause = ", ".join(
        f"{k} = datetime('now')" if v == "datetime('now')" else f"{k} = ?"
        for k, v in campos_update.items()
    )
    values = [
        v for v in campos_update.values() if v != "datetime('now')"
    ]
    values.append(audio_id)

    conn.execute(
        f"UPDATE audios SET {set_clause} WHERE id = ?",
        values
    )

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
            "cat1": cat1_final,
            "cat2": cat2_final,
            "cat3": cat3_final,
            "voz": voz_final,
        }
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
    limit: int = Query(500, ge=1, le=50000),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Lista audios con filtros opcionales por categoría.
    Soporta búsqueda por título/artista.
    """
    query = """
        SELECT id, titulo, artista, album, duracion, archivo_path,
               subgenero as cat1, categoria as cat2, cat3, genero_vocal as voz,
               bpm, fecha_lanzamiento, activo, created_at, updated_at,
               intro, outro, hook,
               cue_inicio, cue_intro, cue_inicio_coro,
               cue_final_coro, cue_mezcla, fade_in, fade_out,
               tipo_audio, veces_reproducido, ultima_reproduccion,
               loudness_lufs, pico_db, loudness_estado, ganancia_db, origen, energia,
               marcas_auto, aprobada
        FROM audios
        WHERE activo = 1
    """
    params = []

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
        query += " AND tipo_audio = ?"
        params.append(_validate_tipo_audio(tipo))
    if sin_tipo:
        query += " AND tipo_audio != ?"
        params.append(_validate_tipo_audio(sin_tipo))
    if busqueda:
        query += " AND (titulo LIKE ? OR artista LIKE ?)"
        params.extend([f"%{busqueda}%", f"%{busqueda}%"])

    # Contar total
    count_query = "SELECT COUNT(*) AS total " + query[query.index("FROM audios"):]
    total = conn.execute(count_query, params).fetchone()["total"]

    query += " ORDER BY artista ASC, titulo ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    audios = conn.execute(query, params).fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": [dict(a) for a in audios],
    }


@app.get("/api/v1/artistas", tags=["Audios"], summary="Artistas ya cargados (para autocompletar)")
def listar_artistas(tipo: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db)):
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
                  "origen": "origen", "energia": "energia", "tipo_audio": "tipo_audio"}


@app.post("/api/v1/biblioteca/lote", tags=["Audios"], summary="Edición masiva de la Biblioteca")
def editar_lote(body: LoteAudiosRequest, conn: sqlite3.Connection = Depends(get_db)):
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
            if v:
                # Usar la escritura que ya existe ("Pop", no "POP"): si no, la
                # misma categoría aparece dos veces
                existe = conn.execute(
                    "SELECT cv.valor FROM config_categorias_valores cv JOIN config_categorias_def cd"
                    " ON cd.id = cv.cat_id WHERE cd.nombre_interno = ? AND UPPER(cv.valor) = UPPER(?)"
                    " AND cv.activo = 1", (clave, v)).fetchone()
                v = existe[0] if existe else v
            _validate_categoria_valor(conn, clave, v, clave)
        elif clave == "origen":
            v = _validate_origen(v) if v else None
        elif clave == "energia":
            v = int(v) if v not in (None, "") else None
            if v is not None and not 1 <= v <= 5:
                raise HTTPException(status_code=422, detail="La energía va de 1 a 5")
        elif clave == "tipo_audio":
            v = _validate_tipo_audio(v)
        sets.append(f"{col} = ?")
        valores.append(v)
    hechos = 0
    if sets:
        hechos = conn.execute(f"UPDATE audios SET {', '.join(sets)}, updated_at = datetime('now')"
                              f" WHERE id IN ({ph}) AND activo = 1", valores + ids).rowcount
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
    return {"actualizados": hechos, "sin_clasificar": sin_clasificar}


# ---- DELETE (soft) /api/v1/audios/{audio_id} ----

@app.delete(
    "/api/v1/audios/{audio_id}",
    summary="Eliminar audio (soft-delete)",
    tags=["Audios"],
)
def delete_audio(
    audio_id: int,
    conn: sqlite3.Connection = Depends(get_db),
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
        "UPDATE audios SET activo = 0, updated_at = datetime('now') WHERE id = ?",
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


@app.get(
    "/api/v1/pautas/notificaciones",
    summary="Obtener notificaciones de pautas",
    tags=["Pautas"],
)
def get_pautas_notificaciones(
    conn: sqlite3.Connection = Depends(get_db),
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
    conn: sqlite3.Connection = Depends(get_db),
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
    data = [PautaOut.from_row(r).model_dump() for r in rows]

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
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Crea una nueva pauta publicitaria.

    - `cliente` se normaliza a MAYÚSCULAS automáticamente.
    - `matriz` (dict) se serializa a JSON string para almacenamiento.
    - Devuelve la pauta creada con el `id` asignado por la base de datos.
    """
    logger.info(f"POST /api/v1/pautas → cliente='{body.cliente}', audio_id={body.audio_id}")

    db_data = body.to_db_dict()
    now     = "datetime('now')"

    try:
        cur = conn.execute(
            """
            INSERT INTO pautas
                (cliente, audio_id, audio_nombre, matriz,
                 fecha_inicio, fecha_fin, notas,
                 cue_intro, cue_outro, menciones_diarias, rubro, prioridad, contrato_id,
                 seccion, activo, created_at, updated_at)
            VALUES
                (:cliente, :audio_id, :audio_nombre, :matriz,
                 :fecha_inicio, :fecha_fin, :notas,
                 :cue_intro, :cue_outro, :menciones_diarias, :rubro, :prioridad, :contrato_id,
                 :seccion, 1, datetime('now'), datetime('now'))
            """,
            db_data,
        )
    except sqlite3.IntegrityError as exc:
        logger.error(f"  → IntegrityError al crear pauta: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No se pudo crear la pauta: {exc}. "
                   f"Verifica que audio_id={body.audio_id} exista en la Biblioteca.",
        )

    new_id = cur.lastrowid
    row    = conn.execute("SELECT * FROM pautas WHERE id = ?", (new_id,)).fetchone()

    logger.info(f"  → Pauta creada con id={new_id}")
    return PautaOut.from_row(row).model_dump()


@app.put(
    "/api/v1/pautas/{pauta_id}",
    summary="Actualizar pauta",
    tags=["Pautas"],
)
def update_pauta(
    pauta_id: int,
    body:     PautaIn,
    conn:     sqlite3.Connection = Depends(get_db),
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
            seccion      = :seccion,
            updated_at   = datetime('now')
        WHERE id = :id
        """,
        {**db_data, "id": pauta_id},
    )

    row = conn.execute("SELECT * FROM pautas WHERE id = ?", (pauta_id,)).fetchone()
    logger.info(f"  → Pauta id={pauta_id} actualizada")
    return PautaOut.from_row(row).model_dump()


@app.delete(
    "/api/v1/pautas/{pauta_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar pauta",
    tags=["Pautas"],
)
def delete_pauta(
    pauta_id: int,
    physical: bool = False,
    conn:     sqlite3.Connection = Depends(get_db),
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
                   "lanzar_tanda", "cue", "cue_stop", "detener_al_terminar",
                   "plan_insertar", "plan_mover", "plan_quitar", "seek"}


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
    elif cmd == "fire_effect":
        if not engine.fire_effect(_audio_id(), int(msg.get("canal_offset") or 0)):
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
    activo:       Optional[bool] = True     # detener_al_terminar
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
# SECCIÓN: CARTUCHERA — Botones de disparo del Studio
# ===========================================================================

BANCOS_CARTUCHERA = {"jingles": 8, "publicidad": 6}   # banco → nº de botones


class CartucheraSlotIn(BaseModel):
    audio_id: int
    etiqueta: str = Field("", max_length=40)


def _check_slot(banco: str, slot: int) -> None:
    if banco not in BANCOS_CARTUCHERA or not (1 <= slot <= BANCOS_CARTUCHERA[banco]):
        raise HTTPException(status_code=404, detail=f"Botón inexistente: {banco}/{slot}")


@app.get("/api/v1/cartuchera", tags=["Cartuchera"], summary="Botones asignados")
def get_cartuchera(conn: sqlite3.Connection = Depends(get_db)):
    """Devuelve, por banco, los botones que tienen audio asignado."""
    rows = conn.execute(
        """
        SELECT c.banco, c.slot, c.audio_id, c.etiqueta,
               a.titulo, a.artista, a.duracion, a.tipo_audio
        FROM cartuchera c
        JOIN audios a ON a.id = c.audio_id AND a.activo = 1
        ORDER BY c.banco, c.slot
        """
    ).fetchall()
    data = {banco: [] for banco in BANCOS_CARTUCHERA}
    for r in rows:
        data.setdefault(r["banco"], []).append(dict(r))
    return {"bancos": BANCOS_CARTUCHERA, "slots": data}


@app.put("/api/v1/cartuchera/{banco}/{slot}", tags=["Cartuchera"], summary="Asignar audio a un botón")
async def set_cartuchera_slot(
    banco: str, slot: int, body: CartucheraSlotIn,
    conn: sqlite3.Connection = Depends(get_db),
):
    _check_slot(banco, slot)
    audio = conn.execute(
        "SELECT id, titulo FROM audios WHERE id = ? AND activo = 1", (body.audio_id,)
    ).fetchone()
    if not audio:
        raise HTTPException(status_code=404, detail=f"Audio id={body.audio_id} no encontrado")
    conn.execute(
        """
        INSERT INTO cartuchera (banco, slot, audio_id, etiqueta) VALUES (?, ?, ?, ?)
        ON CONFLICT(banco, slot) DO UPDATE SET
            audio_id = excluded.audio_id, etiqueta = excluded.etiqueta,
            updated_at = datetime('now')
        """,
        (banco, slot, body.audio_id, body.etiqueta.strip()),
    )
    conn.commit()
    await _manager.broadcast(_ws_message("cartuchera_updated", {"banco": banco, "slot": slot}))
    return {"banco": banco, "slot": slot, "audio_id": body.audio_id, "titulo": audio["titulo"]}


@app.delete("/api/v1/cartuchera/{banco}/{slot}", tags=["Cartuchera"], summary="Vaciar un botón")
async def clear_cartuchera_slot(banco: str, slot: int, conn: sqlite3.Connection = Depends(get_db)):
    _check_slot(banco, slot)
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

    @field_validator("tandas_minutos")
    @classmethod
    def _minutos_validos(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        partes = [x for x in re.split(r"[,;\s]+", v) if x]
        if not partes or any(not x.isdigit() or int(x) > 59 for x in partes):
            raise ValueError("tandas_minutos: minutos entre 0 y 59 separados por coma, p. ej. '15,45'")
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


def _read_motor_config(conn: sqlite3.Connection) -> dict:
    valores = {r["clave"]: r["valor"] for r in conn.execute("SELECT clave, valor FROM config")}
    return {
        "modo":                valores.get("modo", "manual"),
        "crossfade_seg":       float(valores.get("crossfade_seg", 4)),
        "ventana_artista_min": int(float(valores.get("ventana_artista_min", 60))),
        "max_mismo_genero":    int(float(valores.get("max_mismo_genero", 2))),
        "ducking_db":          float(valores.get("ducking_db", -12)),
        "tandas_minutos":      valores.get("tandas_minutos", "15,45"),
        "tandas_inmediatas":   valores.get("tandas_inmediatas", ""),
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
def get_motor_config(conn: sqlite3.Connection = Depends(get_db)):
    return _read_motor_config(conn)


@app.put("/api/v1/config/motor", tags=["Config"], summary="Guardar parámetros del motor")
def update_motor_config(body: MotorConfig, conn: sqlite3.Connection = Depends(get_db)):
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
            " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (clave, str(valor)),
        )
    conn.commit()
    engine.reload_config()
    return _read_motor_config(conn)


@app.get("/api/v1/audios/{audio_id}/stream", tags=["Audios"], summary="Preescucha del audio")
def stream_audio(audio_id: int, conn: sqlite3.Connection = Depends(get_db)):
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


def _read_salidas(conn: sqlite3.Connection) -> dict:
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
def get_dispositivos(conn: sqlite3.Connection = Depends(get_db)):
    """
    Tarjetas que ve el motor (índice 0 = sin sonido) y qué tarjeta usa cada
    salida: `aire` (lo que sale a la emisora) y `preescucha` (escucha interna).
    """
    return {"dispositivos": _listar_dispositivos(), "salidas": _read_salidas(conn)}


@app.put("/api/v1/audio/salidas", tags=["Config"], summary="Asignar tarjetas a las salidas")
def put_salidas(body: SalidasIn, conn: sqlite3.Connection = Depends(get_db)):
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
            " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
            (f"dispositivo_{rol}", str(idx)),
        )
    conn.commit()
    engine.reload_config()
    return {"dispositivos": _listar_dispositivos(), "salidas": _read_salidas(conn)}


# ===========================================================================
# SECCIÓN: TANDAS — Secciones del corte (como las prioridades de Jazler)
# ===========================================================================

class SeccionIn(BaseModel):
    numero:    int  = Field(..., ge=1, le=99)
    nombre:    str  = Field(..., min_length=1, max_length=60)
    opcional:  bool = False
    comercial: bool = False


@app.get("/api/v1/tandas/secciones", tags=["Pautas"], summary="Secciones de la tanda")
def get_secciones(conn: sqlite3.Connection = Depends(get_db)):
    """Orden dentro de la tanda. Cada pauta pertenece a una sección."""
    usos = dict(conn.execute("SELECT COALESCE(seccion, 3), COUNT(*) FROM pautas GROUP BY 1").fetchall())
    return [{"numero": r["numero"], "nombre": r["nombre"], "opcional": bool(r["opcional"]),
             "comercial": bool(r["comercial"]), "pautas": usos.get(r["numero"], 0)}
            for r in conn.execute("SELECT * FROM tanda_secciones ORDER BY numero")]


@app.put("/api/v1/tandas/secciones", tags=["Pautas"], summary="Guardar secciones de la tanda")
def put_secciones(body: List[SeccionIn], conn: sqlite3.Connection = Depends(get_db)):
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
def get_normalizacion(conn: sqlite3.Connection = Depends(get_db)):
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
def remedir_normalizacion(solo_errores: bool = True, conn: sqlite3.Connection = Depends(get_db)):
    """Marca como pendientes los audios con error (o todos) y despierta al medidor."""
    sql = "UPDATE audios SET loudness_estado = NULL WHERE activo = 1"
    if solo_errores:
        sql += " AND loudness_estado = 'error'"
    n = conn.execute(sql).rowcount
    conn.commit()
    _normalizador.avisar()
    return {"pendientes": n}


@app.get("/api/v1/historial", tags=["Sistema"], summary="Historial de emisión")
def get_historial(limit: int = 100, conn: sqlite3.Connection = Depends(get_db)):
    """Últimos elementos que salieron al aire (música, pautas y efectos)."""
    rows = conn.execute(
        "SELECT * FROM historial_emision ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/v1/stats", tags=["Sistema"])
def get_stats(conn: sqlite3.Connection = Depends(get_db)):
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
        # Desconexión limpia iniciada por el cliente (cierre normal)
        _manager.disconnect(ws)
    except Exception as exc:
        # Error inesperado — logueamos y limpiamos la conexión
        logger.error(f"[WS] Error inesperado: {exc}")
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
