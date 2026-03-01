"""
init_db.py — Ónix FM Digital
Script de inicialización de la base de datos SQLite.

IMPORTANTE: Este script borra y recrea radio_core.db cada vez que se ejecuta.
Úsalo solo para setup inicial o cuando cambies el esquema de columnas.

Columnas canónicas de la tabla `audios` (fuente de verdad):
    id, titulo, artista, genero_vocal, energia, origen,
    tipo_audio, ruta_archivo, puntos_audio, ultima_reproduccion
"""

import sqlite3
import os
from pathlib import Path

# ── Ubicación de la base de datos ─────────────────────────────────────────────
# Usa Path.cwd() para que funcione correctamente en Windows y Linux por igual.
BASE_DIR = Path.cwd()
DB_PATH  = BASE_DIR / "radio_core.db"


def init_database():
    # ── Borrar la DB si ya existe ──────────────────────────────────────────────
    # Esto garantiza que los cambios de esquema se apliquen desde cero,
    # evitando errores de columnas obsoletas o faltantes.
    if DB_PATH.exists():
        os.remove(DB_PATH)
        print(f"[DB] Archivo anterior eliminado: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")   # soporta lecturas concurrentes
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    # ══════════════════════════════════════════════════════════════════════════
    # TABLA: audios
    # Fuente de verdad del esquema. Cualquier cambio aquí DEBE reflejarse
    # en api_server.py (INSERT, SELECT) y en el frontend.
    #
    # MÓDULOS FUTUROS (publicidad, logs, etc.) deben agregar sus propias
    # tablas aquí; nunca modificar las columnas de `audios` directamente.
    # ══════════════════════════════════════════════════════════════════════════
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audios (
            id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
            titulo              TEXT     NOT NULL,
            artista             TEXT     NOT NULL     DEFAULT 'Desconocido',
            genero_vocal        TEXT     NOT NULL     DEFAULT 'Instrumental'
                                         CHECK(genero_vocal IN ('Hombre','Mujer','Dueto','Instrumental')),
            energia             INTEGER  NOT NULL     DEFAULT 3
                                         CHECK(energia BETWEEN 1 AND 5),
            origen              TEXT     NOT NULL     DEFAULT 'Nacional'
                                         CHECK(origen IN ('Nacional','Internacional')),
            tipo_audio          TEXT     NOT NULL     DEFAULT 'Musica'
                                         CHECK(tipo_audio IN ('Musica','Efecto','Cuña')),
            ruta_archivo        TEXT     NOT NULL     UNIQUE,
            puntos_audio        TEXT     NOT NULL     DEFAULT '{"intro_sec":0,"mix_point_sec":0}',
            ultima_reproduccion DATETIME              DEFAULT NULL,

            -- Columnas auxiliares (no en el esquema mínimo, pero útiles)
            duracion_seg        REAL                  DEFAULT 0,
            veces_reproducido   INTEGER  NOT NULL     DEFAULT 0,
            activo              INTEGER  NOT NULL     DEFAULT 1,
            fecha_ingesta       DATETIME NOT NULL     DEFAULT (datetime('now','localtime')),
            notas               TEXT                  DEFAULT ''
        )
    """)

    # Índices para el algoritmo de rotación y los filtros de la API
    cur.execute("CREATE INDEX IF NOT EXISTS idx_artista       ON audios(artista)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tipo_audio    ON audios(tipo_audio)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_origen        ON audios(origen)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_genero_vocal  ON audios(genero_vocal)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ultima_reprod ON audios(ultima_reproduccion)")

    # ══════════════════════════════════════════════════════════════════════════
    # TABLA: historial_reproduccion
    # Log inmutable; usado por el algoritmo de rotación para calcular
    # cuándo se tocó cada artista por última vez.
    # ══════════════════════════════════════════════════════════════════════════
    cur.execute("""
        CREATE TABLE IF NOT EXISTS historial_reproduccion (
            id          INTEGER  PRIMARY KEY AUTOINCREMENT,
            audio_id    INTEGER  NOT NULL  REFERENCES audios(id),
            inicio_ts   DATETIME NOT NULL  DEFAULT (datetime('now','localtime')),
            fin_ts      DATETIME           DEFAULT NULL,
            completada  INTEGER            DEFAULT 0,
            operador    TEXT               DEFAULT 'AUTO'
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_audio  ON historial_reproduccion(audio_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_inicio ON historial_reproduccion(inicio_ts)")

    # ══════════════════════════════════════════════════════════════════════════
    # TABLA: config
    # Pares clave-valor para configuración en caliente (sin reiniciar el motor).
    # ══════════════════════════════════════════════════════════════════════════
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config (
            clave       TEXT PRIMARY KEY,
            valor       TEXT NOT NULL,
            descripcion TEXT DEFAULT ''
        )
    """)

    defaults = [
        ("modo",                 "manual",  "Modo de operación: 'manual' o 'automatico'"),
        ("crossfade_seg",        "4",       "Segundos de crossfade entre canciones"),
        ("ventana_artista_min",  "60",      "Minutos mínimos antes de repetir artista"),
        ("max_mismo_genero",     "2",       "Máximo consecutivo del mismo género vocal"),
        ("forzar_nacional",      "false",   "Fuerza origen=Nacional en modo automático"),
        ("ducking_db",           "-12",     "Atenuación en dB de música durante efectos"),
        ("version_schema",       "2.0.0",   "Versión del esquema — columnas canónicas Ónix FM"),
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO config(clave, valor, descripcion) VALUES (?, ?, ?)",
        defaults
    )

    conn.commit()
    conn.close()
    print(f"[DB] Base de datos creada correctamente en: {DB_PATH}")
    print("[DB] Tablas: audios, historial_reproduccion, config")


if __name__ == "__main__":
    init_database()
