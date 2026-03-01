"""
api_server.py — Ónix FM Radio Core API
FastAPI REST API Server — Versión 2.0
Sistema dinámico de categorías para Biblioteca.
"""

import logging
import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

import sys
from pathlib import Path

# Agregar la carpeta scripts al camino de búsqueda de Python
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR / "scripts"))

# Ahora sí funcionará el import
from init_db import initialize_database, get_connection

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
logger = logging.getLogger("onix.api")

DB_PATH      = os.getenv("RADIO_DB_PATH", "radio_core.db")
MEDIA_ROOT   = Path(os.getenv("RADIO_MEDIA_ROOT", "/media/onix"))
STAGING_DIR  = MEDIA_ROOT / "staging"
LIBRARY_DIR  = MEDIA_ROOT / "library"

app = FastAPI(
    title="Ónix FM — Radio Core API",
    description="Backend del sistema de gestión de audio para Ónix FM.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    logger.info("Iniciando Ónix FM API v2.0...")
    initialize_database(DB_PATH)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("API lista.")


# ===========================================================================
# SECCIÓN: CONFIG BIBLIOTECA — Sistema Dinámico de Categorías
# ===========================================================================

# ---- Schemas Pydantic ----

class ValorCreateRequest(BaseModel):
    nombre_interno: str = Field(..., description="nombre_interno de la categoría destino (ej: 'voz', 'cat1')")
    valor: str          = Field(..., min_length=1, max_length=100, description="Nuevo valor a insertar")
    orden: Optional[int] = Field(default=None, description="Posición de orden (auto si no se especifica)")

    @validator("valor")
    def valor_strip(cls, v):
        return v.strip()

    @validator("nombre_interno")
    def nombre_strip(cls, v):
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
    Valida que 'valor' exista en la categoría 'nombre_interno'.
    Lanza HTTPException 422 si no es válido.
    Permite None/vacío (campo opcional en audio).
    """
    if not valor:
        return
    validos = _get_all_valid_values(conn, nombre_interno)
    if not validos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"La categoría '{nombre_interno}' no existe o no tiene valores activos. "
                   f"Verifica la configuración en /api/v1/config/biblioteca/schema"
        )
    if valor not in validos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "campo": campo_api,
                "valor_enviado": valor,
                "valores_permitidos": sorted(validos),
                "mensaje": f"El valor '{valor}' no es válido para el campo '{campo_api}'. "
                           f"Usa POST /api/v1/config/biblioteca/valores para añadirlo."
            }
        )


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
    """Valida los 4 campos de categoría contra las tablas dinámicas."""
    _validate_categoria_valor(conn, "cat1", cat1, "cat1 (Género)")
    _validate_categoria_valor(conn, "cat2", cat2, "cat2 (Rotación)")
    _validate_categoria_valor(conn, "cat3", cat3, "cat3 (Subgénero)")
    _validate_categoria_valor(conn, "voz",  voz,  "voz (Voz/Tipo)")


# ---- POST /api/v1/audios ----

@app.post(
    "/api/v1/audios",
    status_code=status.HTTP_201_CREATED,
    summary="Crear nuevo audio en Biblioteca",
    tags=["Audios"],
)
def create_audio(
    body: AudioCreateRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """
    Registra un nuevo audio en la biblioteca.

    Flujo:
    1. Valida que cat1/cat2/cat3/voz existan en config dinámica.
    2. Mueve el archivo de staging → library (Copy-and-Delete).
    3. Inserta registro en DB mapeando campos Jazler → columnas DB.

    Mapeo de campos:
        cat1 → subgenero  (Género musical)
        cat2 → categoria  (Rotación/estado)
        cat3 → cat3       (Subgénero extra)
        voz  → genero_vocal
    """
    logger.info(
        f"POST /api/v1/audios → titulo='{body.titulo}', "
        f"artista='{body.artista}', cat1={body.cat1}, cat2={body.cat2}, "
        f"cat3={body.cat3}, voz={body.voz}"
    )

    # PASO 1: Validación dinámica de categorías
    _validate_all_categorias(conn, body.cat1, body.cat2, body.cat3, body.voz)
    logger.info("  → Validación de categorías: PASS")

    # PASO 2: Mover archivo (Copy-and-Delete)
    ruta_final = _move_to_library(body.archivo_path, body.titulo, body.artista)
    logger.info(f"  → Archivo en library: {ruta_final}")

    # PASO 3: Insertar en DB
    try:
        cursor = conn.execute("""
            INSERT INTO audios (
                titulo, artista, album, duracion, archivo_path,
                subgenero, categoria, cat3, genero_vocal,
                bpm, fecha_lanzamiento
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            body.titulo,
            body.artista,
            body.album,
            body.duracion or 0,
            ruta_final,
            body.cat1,        # subgenero
            body.cat2,        # categoria
            body.cat3,        # cat3
            body.voz,         # genero_vocal
            body.bpm,
            body.fecha_lanzamiento,
        ))
        nuevo_id = cursor.lastrowid

    except sqlite3.IntegrityError as e:
        # Revertir el movimiento de archivo si la DB falla
        logger.error(f"  → DB IntegrityError: {e}. Revirtiendo archivo...")
        _revert_file(ruta_final, body.archivo_path)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un audio con esa ruta de archivo: {ruta_final}"
        )

    logger.info(f"  → Audio creado con id={nuevo_id}")
    return {
        "mensaje": "Audio creado exitosamente",
        "data": {
            "id": nuevo_id,
            "titulo": body.titulo,
            "artista": body.artista,
            "archivo_path": ruta_final,
            "cat1": body.cat1,
            "cat2": body.cat2,
            "cat3": body.cat3,
            "voz": body.voz,
        }
    }


# ---- PUT /api/v1/audios/{audio_id} ----

@app.put(
    "/api/v1/audios/{audio_id}",
    summary="Actualizar audio existente",
    tags=["Audios"],
)
def update_audio(
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
    if body.titulo   is not None: campos_update["titulo"]          = body.titulo
    if body.artista  is not None: campos_update["artista"]         = body.artista
    if body.album    is not None: campos_update["album"]           = body.album
    if body.duracion is not None: campos_update["duracion"]        = body.duracion
    if body.bpm      is not None: campos_update["bpm"]             = body.bpm
    if body.fecha_lanzamiento is not None:
        campos_update["fecha_lanzamiento"] = body.fecha_lanzamiento

    # Mapeo Jazler → DB
    if body.cat1 is not None: campos_update["subgenero"]    = body.cat1
    if body.cat2 is not None: campos_update["categoria"]    = body.cat2
    if body.cat3 is not None: campos_update["cat3"]         = body.cat3
    if body.voz  is not None: campos_update["genero_vocal"] = body.voz

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
               bpm, fecha_lanzamiento, activo, created_at, updated_at
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
# ===========================================================================
# ESTADÍSTICAS DE INVENTARIO
# ===========================================================================
@app.get("/api/v1/stats", tags=["Audios"])
def get_stats(conn: sqlite3.Connection = Depends(get_db)):
    """Retorna contadores de inventario por tipo de audio."""
    # Mapeo de columnas de tu base de datos actual
    # cat2 se usa para la categoría y genero_vocal para el tipo en este nuevo esquema
    rows = conn.execute("""
        SELECT genero_vocal as tipo, COUNT(*) as cnt 
        FROM audios 
        WHERE activo = 1 
        GROUP BY genero_vocal
    """).fetchall()
    
    stats = {r["tipo"]: r["cnt"] for r in rows}
    
    return {
        "Musica": stats.get("Musica", 0),
        "Efecto": stats.get("Efecto", 0),
        "Cuña": stats.get("Cuña", 0),
        "total": sum(stats.values())
    }
# ===========================================================================
# WEBSOCKETS — Comunicación en tiempo real
# ===========================================================================
from fastapi import WebSocket, WebSocketDisconnect
import json

class ConnectionManager:
    def __init__(self):
        self.active = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast_async(self, event: str, payload: dict):
        message = json.dumps({"event": event, "data": payload})
        dead = set()
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self.active -= dead

ws_manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    # Enviamos un estado inicial simulado para que la UI no se quede colgada
    await ws.send_text(json.dumps({"event": "status", "data": {"modo": "manual"}}))
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            cmd = msg.get("cmd")
            
            # Responder al ping para mantener viva la conexión
            if cmd == "ping":
                await ws.send_text(json.dumps({"event": "pong"}))
                
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
# ===========================================================================
# FRONTEND ESTÁTICO
# ===========================================================================
FRONTEND_DIR = BASE_DIR / "frontend"
ADMIN_DIR = FRONTEND_DIR / "admin"

@app.get("/")
def serve_studio():
    f = FRONTEND_DIR / "index.html"
    return FileResponse(str(f)) if f.exists() else {"error": "index.html no encontrado"}

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="admin")
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)
