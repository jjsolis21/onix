"""
init_db.py — Ónix FM Radio Core
Inicialización y migración del esquema de base de datos.
Versión 2.0: Sistema dinámico de categorías para Biblioteca.
"""

import sqlite3
import logging
import os

logger = logging.getLogger("onix.init_db")

DB_PATH = os.getenv("RADIO_DB_PATH", "radio_core.db")

# ---------------------------------------------------------------------------
# SEED DATA — Categorías y valores por defecto del sistema Jazler/Biblioteca
# ---------------------------------------------------------------------------
SEED_CATEGORIAS = [
    # (nombre_interno, etiqueta_visible, orden)
    ("cat1", "Género",    1),
    ("cat2", "Rotación",  2),
    ("cat3", "Subgénero", 3),
    ("voz",  "Voz/Tipo",  4),
]

SEED_VALORES = {
    "cat1": [
        "Romántica", "Pop", "Rock", "Balada", "Cumbia",
        "Tropical", "Ranchera", "Electrónica", "Reggaetón",
        "Salsa", "Merengue", "Bachata", "Jazz", "Clásica",
    ],
    "cat2": [
        "Nuevo", "Reciente", "Éxito", "Clásico", "Desconocido",
    ],
    "cat3": [
        "Acústico", "En Vivo", "Remix", "Original", "Cover",
    ],
    "voz": [
        "Hombre", "Mujer", "Dúo", "Grupo", "Instrumental",
    ],
}


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    """
    Ejecuta todas las migraciones de forma idempotente.
    Cada bloque verifica si la acción ya fue aplicada antes de ejecutarse.
    """
    cursor = conn.cursor()

    # ------------------------------------------------------------------
    # MIGRACIÓN 1 — Tabla de definición de categorías
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando tabla config_categorias_def...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config_categorias_def (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_interno  TEXT    NOT NULL UNIQUE,
            etiqueta_visible TEXT   NOT NULL,
            orden           INTEGER NOT NULL DEFAULT 0,
            activo          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    logger.info("  → config_categorias_def: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 2 — Tabla de valores de categorías
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando tabla config_categorias_valores...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config_categorias_valores (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            cat_id    INTEGER NOT NULL REFERENCES config_categorias_def(id) ON DELETE CASCADE,
            valor     TEXT    NOT NULL,
            orden     INTEGER NOT NULL DEFAULT 0,
            activo    INTEGER NOT NULL DEFAULT 1,
            created_at TEXT   NOT NULL DEFAULT (datetime('now')),
            UNIQUE(cat_id, valor)
        )
    """)
    logger.info("  → config_categorias_valores: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 3 — Columna cat3 en tabla audios (si no existe)
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando columna cat3 en audios...")
    cursor.execute("PRAGMA table_info(audios)")
    columnas_audios = {row["name"] for row in cursor.fetchall()}

    if "cat3" not in columnas_audios:
        cursor.execute("ALTER TABLE audios ADD COLUMN cat3 TEXT DEFAULT NULL")
        logger.info("  → Columna cat3 añadida a audios: OK")
    else:
        logger.info("  → Columna cat3 ya existe: SKIP")

    # ------------------------------------------------------------------
    # MIGRACIÓN 4 — Seed de categorías por defecto (solo si tabla vacía)
    # ------------------------------------------------------------------
    cursor.execute("SELECT COUNT(*) as cnt FROM config_categorias_def")
    if cursor.fetchone()["cnt"] == 0:
        logger.info("MIGRACIÓN: Insertando seed de categorías por defecto...")

        for nombre, etiqueta, orden in SEED_CATEGORIAS:
            cursor.execute("""
                INSERT OR IGNORE INTO config_categorias_def
                    (nombre_interno, etiqueta_visible, orden)
                VALUES (?, ?, ?)
            """, (nombre, etiqueta, orden))

        # Obtener los IDs recién insertados
        cursor.execute("SELECT id, nombre_interno FROM config_categorias_def")
        cat_map = {row["nombre_interno"]: row["id"] for row in cursor.fetchall()}

        for nombre_interno, valores in SEED_VALORES.items():
            cat_id = cat_map.get(nombre_interno)
            if not cat_id:
                logger.warning(f"  → Cat '{nombre_interno}' no encontrada en mapa, SKIP")
                continue
            for idx, valor in enumerate(valores):
                cursor.execute("""
                    INSERT OR IGNORE INTO config_categorias_valores
                        (cat_id, valor, orden)
                    VALUES (?, ?, ?)
                """, (cat_id, valor, idx + 1))
            logger.info(
                f"  → '{nombre_interno}': {len(valores)} valores insertados"
            )
    else:
        logger.info("MIGRACIÓN: Seed de categorías ya existe: SKIP")

    # ------------------------------------------------------------------
    # MIGRACIÓN 5 — Índices de rendimiento
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cat_valores_cat_id
        ON config_categorias_valores(cat_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cat_valores_valor
        ON config_categorias_valores(cat_id, valor)
    """)
    logger.info("MIGRACIÓN: Índices de rendimiento: OK")

    conn.commit()
    logger.info("MIGRACIÓN: Todas las migraciones completadas exitosamente.")


def initialize_database(db_path: str = DB_PATH) -> None:
    """Punto de entrada principal. Crear schema base + ejecutar migraciones."""
    logger.info(f"Inicializando base de datos: {db_path}")
    conn = get_connection(db_path)
    try:
        _create_base_schema(conn)
        run_migrations(conn)
    except Exception as e:
        conn.rollback()
        logger.critical(f"ERROR FATAL en inicialización de DB: {e}", exc_info=True)
        raise
    finally:
        conn.close()


def _create_base_schema(conn: sqlite3.Connection) -> None:
    """Crea las tablas originales del sistema si no existen."""
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS audios (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo          TEXT    NOT NULL,
            artista         TEXT    NOT NULL,
            album           TEXT    DEFAULT NULL,
            duracion        INTEGER DEFAULT 0,
            archivo_path    TEXT    NOT NULL UNIQUE,
            categoria       TEXT    DEFAULT NULL,
            subgenero       TEXT    DEFAULT NULL,
            genero_vocal    TEXT    DEFAULT NULL,
            bpm             INTEGER DEFAULT NULL,
            fecha_lanzamiento TEXT  DEFAULT NULL,
            activo          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS playlists (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT    NOT NULL UNIQUE,
            descripcion TEXT    DEFAULT NULL,
            activo      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS playlist_audios (
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            audio_id    INTEGER NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
            posicion    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (playlist_id, audio_id)
        );
    """)
    conn.commit()
    logger.info("Schema base verificado/creado.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    initialize_database()
    print("✅ Base de datos inicializada correctamente.")
