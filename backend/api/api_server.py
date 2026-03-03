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
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status, Depends, WebSocket, WebSocketDisconnect, Request, Form, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field, field_validator

import sys
from pathlib import Path

# Añadimos la carpeta scripts al sistema para que encuentre init_db
root_path = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(root_path / "scripts"))

try:
    from init_db import initialize_database
except ImportError:
    # Si falla, intentamos importación relativa
    from scripts.init_db import initialize_database

# Importar el motor de audio (singleton)
try:
    from backend.audio.audio_engine import engine
except ImportError:
    # Ajuste de path si es necesario
    sys.path.append(str(root_path))
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

DB_PATH      = os.getenv("RADIO_DB_PATH", "radio_core.db")
MEDIA_ROOT   = Path(os.getenv("RADIO_MEDIA_ROOT", "/media/onix"))
STAGING_DIR  = MEDIA_ROOT / "staging"
LIBRARY_DIR  = MEDIA_ROOT / "library"
MUSICA_DIR   = LIBRARY_DIR / "musica"   # destino para archivos subidos via /upload

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

    # Suscribir la API a los eventos del motor de audio (EventBus -> WebSocket)
    def _on_engine_event(event: str, data: dict):
        """Puente síncrono (EventBus) -> asíncrono (WebSocket Broadcast)"""
        # track_started ya viene con los cue points desde audio_engine.py
        payload = json.dumps({
            "module": "engine",
            "cmd":    event,
            "ts":     int(time.time() * 1000),
            "data":   data
        })
        # Como el EventBus corre en hilos del motor, usamos call_soon_threadsafe
        # para programar el broadcast en el event loop de FastAPI/Uvicorn.
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_manager.broadcast(payload))

    engine.bus.subscribe("track_started", _on_engine_event)
    engine.bus.subscribe("playback_error", _on_engine_event)

    logger.info("API lista.")
    yield
    # ── SHUTDOWN (cleanup si fuera necesario) ────────────────────────────
    logger.info("Apagando Ónix FM API.")


app = FastAPI(
    title="Ónix FM — Radio Core API",
    description="Backend del sistema de gestión de audio para Ónix FM.",
    version="2.0.0",
    lifespan=lifespan,          # FIX: patrón lifespan moderno
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Montar la carpeta de biblioteca interactiva para streaming
app.mount("/stream", StaticFiles(directory=LIBRARY_DIR), name="stream")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Debug Validation: Imprime exactamente qué campo falló para evitar 422 silenciosos.
    """
    logger.error(f"422 Validation Error en {request.url}: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


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
            SELECT id, valor, orden
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
                {"id": v["id"], "valor": v["valor"], "orden": v["orden"]}
                for v in valores
            ],
        })

    logger.info(f"  → Retornando {len(resultado)} categorías")
    return {"categorias": resultado}


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

    try:
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
    summary="Subir archivo de audio a la Biblioteca",
    tags=["Audios"],
)
async def upload_audio_file(
    file: UploadFile = File(...),
):
    """
    Recibe un archivo de audio, normaliza su nombre (sin tildes ni espacios)
    y lo guarda en MUSICA_DIR (library/musica/).

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
    dest = MUSICA_DIR / filename_normalizado

    # Evitar colisiones: si existe, añadir sufijo numérico
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
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

    return {
        "mensaje":    "Archivo subido correctamente",
        "filename":   filename_normalizado,
        "archivo_path": str(dest),
        "size_bytes": dest.stat().st_size,
    }


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

    # PASO 3: Insertar en DB
    try:
        cursor = conn.execute("""
            INSERT INTO audios (
                titulo, artista, album, duracion, archivo_path,
                subgenero, categoria, cat3, genero_vocal,
                bpm, intro, outro, hook, fecha_lanzamiento,
                cue_inicio, cue_intro, cue_inicio_coro, cue_final_coro,
                cue_mezcla, fade_in, fade_out
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            titulo,
            artista,
            album,
            duracion or 0,
            str(dest),
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

    # Broadcast WebSocket — notificar a todos los módulos
    await _manager.broadcast(json.dumps({
        "module": "api", "cmd": "library_updated",
        "ts": int(time.time() * 1000),
        "data": {"action": "create", "id": nuevo_id, "titulo": titulo, "artista": artista}
    }))

    return {
        "mensaje": "Audio creado exitosamente",
        "data": {
            "id": nuevo_id,
            "titulo": titulo,
            "artista": artista,
            "archivo_path": str(dest),
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

    # Broadcast WebSocket — notificar a todos los módulos
    await _manager.broadcast(json.dumps({
        "module": "api", "cmd": "library_updated",
        "ts": int(__import__('time').time() * 1000),
        "data": {"action": "update", "id": audio_id, "campos": list(campos_update.keys())}
    }))

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
    cat1: Optional[str] = None,
    cat2: Optional[str] = None,
    cat3: Optional[str] = None,
    voz: Optional[str]  = None,
    busqueda: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
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
               cue_final_coro, cue_mezcla, fade_in, fade_out
        FROM audios
        WHERE activo = 1
    """
    params = []

    if cat1:
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
    if busqueda:
        query += " AND (titulo LIKE ? OR artista LIKE ?)"
        params.extend([f"%{busqueda}%", f"%{busqueda}%"])

    # Contar total
    count_query = query.replace(
        "SELECT id, titulo, artista, album, duracion, archivo_path,\n               subgenero as cat1, categoria as cat2, cat3, genero_vocal as voz,\n               bpm, fecha_lanzamiento, activo, created_at, updated_at",
        "SELECT COUNT(*) as total"
    )
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

    Flujo de cada mensaje:
        1. El frontend llama a wsCmd(cmd, data) → JSON.stringify → ws.send()
        2. Este endpoint recibe el texto, lo deserializa para loguear,
           y hace broadcast a todos los clientes conectados (incluido
           el emisor), de forma que el Shell y otros módulos puedan
           reaccionar al evento.
    """
    await _manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()

            # Parsear solo para logging; si el JSON está malformado
            # lo registramos como advertencia pero no cortamos la conexión
            try:
                msg    = json.loads(raw)
                cmd    = msg.get("cmd",    "?")
                module = msg.get("module", "?")
                logger.info(
                    f"[WS] ← {module}/{cmd} | "
                    f"data={str(msg.get('data', {}))[:120]}"
                )
            except json.JSONDecodeError:
                logger.warning(f"[WS] Mensaje no-JSON recibido: {raw[:120]}")

            # Reemitir el mensaje a todos los clientes (broadcast)
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
    """Sirve la consola de emisión (Studio) en la raíz."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html no encontrado en frontend/")
    return FileResponse(index_path)

# Montar carpetas estáticas
# /static -> frontend/ (para main.css, app.js, etc.)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# /admin -> frontend/admin/ (para la administración)
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")

if __name__ == "__main__":
    import uvicorn
    # Puerto 8000 por defecto para Ónix FM Core
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)
