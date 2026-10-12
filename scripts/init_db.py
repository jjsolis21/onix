"""
init_db.py — Ónix FM Radio Core
Inicialización y migración del esquema de base de datos.
Versión 2.1: Agrega tabla pautas (Sección 03 — Programación de Pautas).
"""

import sqlite3
import logging
import os
from pathlib import Path

logger = logging.getLogger("onix.init_db")

# Ruta relativa al archivo actual (Sube de scripts/ a la raíz) → backend/api/radio_core.db
_DB_DEFAULT = str(Path(__file__).resolve().parent.parent / "backend" / "api" / "radio_core.db")
DB_PATH = os.getenv("RADIO_DB_PATH", _DB_DEFAULT)

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


# Parámetros por defecto del motor de audio (tabla config)
SEED_CONFIG = [
    ("modo",                "manual"),   # manual | automatico
    ("crossfade_seg",       "4"),
    ("ventana_artista_min", "60"),
    ("max_mismo_genero",    "2"),
    ("ducking_db",          "-12"),
    # Minutos de cada hora en que salen las tandas de pautas
    ("tandas_minutos",      "15,45"),
    # Normalización de volumen (EBU R128)
    ("normalizacion_activa", "1"),
    ("normalizacion_lufs",   "-16"),
    # Salidas de audio: índice de tarjeta BASS (-1 = predeterminada del sistema)
    ("dispositivo_aire",       "-1"),
    ("dispositivo_preescucha", "-1"),
    ("dispositivo_cartuchera", "-2"),   # -2 = la misma tarjeta que el aire
    # Planificador (relojes)
    ("horas_planificadas",   "2"),      # cuánto se ve y se carga por adelantado
    ("aleatoriedad",         "3"),      # 0 = estricto (la menos tocada); N = al azar entre N candidatas
    ("tolerancia_tanda_seg", "90"),     # margen para que la tanda caiga en su minuto
    ("separacion_cancion_dias", "1"),   # no repetir la canción N días seguidos…
    ("separacion_cancion_ventana_min", "60"),  # …en la misma franja (± minutos)
    # Tandas al estilo Jazler
    ("tandas_inmediatas",    ""),       # minutos cuya tanda corta la canción (p. ej. "0")
    ("tanda_max_seg",        "240"),    # aviso si una tanda dura más
    # Detector de aire muerto: segundos de silencio antes de actuar (0 = apagado)
    ("silencio_seg",         "10"),
    # Detectar inicio y punto de mezcla de la música al cargarla (backend/audio/marcas.py)
    ("marcas_auto",          "1"),
]

# Secciones de la tanda (orden dentro del corte). Como las "priorities" de Jazler.
#   (número, nombre, opcional, comercial)
#   opcional  → solo sale si la tanda tiene al menos una cuña comercial
#   comercial → cuñas de clientes: se reparten entre las tandas de la hora;
#               las demás (ID, cortinas) salen en CADA tanda de sus horas
SEED_SECCIONES = [
    (1, "Identificación",     0, 0),
    (2, "Cortina de entrada", 1, 0),
    (3, "Comerciales",        0, 1),
    (4, "Cortina de salida",  1, 0),
]


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

    # ------------------------------------------------------------------
    # MIGRACIÓN 6 — Columnas de Mix (intro, outro, hook) en tabla audios
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando columnas de cueing en audios...")
    if "intro" not in columnas_audios:
        cursor.execute("ALTER TABLE audios ADD COLUMN intro REAL DEFAULT NULL")
    if "outro" not in columnas_audios:
        cursor.execute("ALTER TABLE audios ADD COLUMN outro REAL DEFAULT NULL")
    if "hook" not in columnas_audios:
        cursor.execute("ALTER TABLE audios ADD COLUMN hook REAL DEFAULT NULL")
    logger.info("  → Columnas intro, outro, hook verificadas/añadidas: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 7 — Cue points extendidos (6 marcadores Jazler)
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando columnas de cue points extendidos en audios...")
    # Re-leer columnas después de posibles ALTER previos
    cursor.execute("PRAGMA table_info(audios)")
    columnas_audios_v2 = {row["name"] for row in cursor.fetchall()}

    nuevas_columnas_cue = [
        ("cue_inicio",      "REAL"),   # Punto de inicio de reproducción
        ("cue_intro",       "REAL"),   # Fin del intro / entrada de voz
        ("cue_inicio_coro", "REAL"),   # Inicio del coro
        ("cue_final_coro",  "REAL"),   # Final del coro
        ("cue_mezcla",      "REAL"),   # Punto de mezcla / mix out
        ("fade_in",         "REAL"),   # Duración del fade in
        ("fade_out",        "REAL"),   # Duración del fade out
    ]
    for col_name, col_type in nuevas_columnas_cue:
        if col_name not in columnas_audios_v2:
            cursor.execute(
                f"ALTER TABLE audios ADD COLUMN {col_name} {col_type} DEFAULT NULL"
            )
            logger.info(f"  → Columna {col_name} añadida a audios: OK")
        else:
            logger.info(f"  → Columna {col_name} ya existe: SKIP")
    logger.info("  → Cue points extendidos verificados/añadidos: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 8 — Tabla pautas (Sección 03 — Programación de Pautas)
    #
    # Diseño:
    #   · audio_id   → FK a audios(id). ON DELETE RESTRICT para no perder
    #                  el historial de pautas si se borra un audio.
    #   · audio_nombre → cache desnormalizado del nombre de archivo para
    #                    mostrar en el listado sin JOIN.
    #   · matriz     → JSON string con el mapa de horarios:
    #                  { "dia-hora": true }  ej: {"1-8": true, "5-18": true}
    #                  dia: 0=Dom…6=Sáb  |  hora: 0–23
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando tabla pautas...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pautas (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente        TEXT    NOT NULL,
            audio_id       INTEGER NOT NULL
                               REFERENCES audios(id)
                               ON DELETE RESTRICT
                               ON UPDATE CASCADE,
            audio_nombre   TEXT    NOT NULL DEFAULT '',
            matriz         TEXT    NOT NULL DEFAULT '{}',
            fecha_inicio   TEXT             DEFAULT '',
            fecha_fin      TEXT             DEFAULT '',
            notas          TEXT             DEFAULT '',
            activo         INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Índices de búsqueda más frecuente en pautas
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pautas_cliente
        ON pautas(cliente)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pautas_audio_id
        ON pautas(audio_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pautas_fecha_inicio
        ON pautas(fecha_inicio)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pautas_activo
        ON pautas(activo)
    """)
    logger.info("  → Tabla pautas e índices: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 9 — Columnas extendidas para Pautas (Jazler Soho)
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando columnas extendidas en pautas...")
    cursor.execute("PRAGMA table_info(pautas)")
    columnas_pautas = {row["name"] for row in cursor.fetchall()}

    nuevas_columnas_pautas = [
        ("cue_intro", "REAL"),
        ("cue_outro", "REAL"),
        ("menciones_diarias", "INTEGER"),
        ("rubro", "TEXT"),
        ("prioridad", "INTEGER"),
        ("contrato_id", "TEXT"),
    ]
    for col_name, col_type in nuevas_columnas_pautas:
        if col_name not in columnas_pautas:
            cursor.execute(f"ALTER TABLE pautas ADD COLUMN {col_name} {col_type} DEFAULT NULL")
            logger.info(f"  → Columna {col_name} añadida a pautas: OK")
        else:
            logger.info(f"  → Columna {col_name} ya existe: SKIP")
    logger.info("  → Columnas extendidas pautas verificadas/añadidas: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 10 — Soporte del motor de audio
    #   · config             → parámetros del motor (clave/valor)
    #   · audios             → ultima_reproduccion / veces_reproducido (rotación)
    #   · historial_emision  → registro de todo lo que sale al aire.
    #                          `slot` = 'YYYY-MM-DD-HH' para no repetir una
    #                          pauta dentro de la misma hora.
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando tablas del motor de audio...")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )
    """)
    for clave, valor in SEED_CONFIG:
        cursor.execute(
            "INSERT OR IGNORE INTO config (clave, valor) VALUES (?, ?)", (clave, valor)
        )

    cursor.execute("PRAGMA table_info(audios)")
    columnas_audios_v3 = {row["name"] for row in cursor.fetchall()}
    for col_name, col_def in (
        ("ultima_reproduccion", "TEXT DEFAULT NULL"),
        ("veces_reproducido",   "INTEGER NOT NULL DEFAULT 0"),
    ):
        if col_name not in columnas_audios_v3:
            cursor.execute(f"ALTER TABLE audios ADD COLUMN {col_name} {col_def}")
            logger.info(f"  → Columna {col_name} añadida a audios: OK")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS historial_emision (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            audio_id    INTEGER,
            titulo      TEXT    NOT NULL DEFAULT '',
            artista     TEXT    NOT NULL DEFAULT '',
            tipo        TEXT    NOT NULL DEFAULT 'musica',
            origen      TEXT    NOT NULL DEFAULT 'manual',
            pauta_id    INTEGER,
            slot        TEXT,
            iniciado_en TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_historial_iniciado
        ON historial_emision(iniciado_en)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_historial_pauta_slot
        ON historial_emision(pauta_id, slot)
    """)
    logger.info("  → config, columnas de rotación e historial_emision: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 11 — Tipo de audio explícito y cartuchera
    #   · audios.tipo_audio → Musica | Cuña | Efecto | Sweeper | Programa | Tips
    #     (antes se deducía de la categoría 1). Se rellena 'Cuña' para los
    #     audios comerciales ya existentes.
    #   · cartuchera → qué audio dispara cada botón del Studio.
    #     banco: 'jingles' | 'publicidad'   slot: 1..N
    # ------------------------------------------------------------------
    logger.info("MIGRACIÓN: Verificando tipo_audio y cartuchera...")
    cursor.execute("PRAGMA table_info(audios)")
    if "tipo_audio" not in {row["name"] for row in cursor.fetchall()}:
        cursor.execute("ALTER TABLE audios ADD COLUMN tipo_audio TEXT NOT NULL DEFAULT 'Musica'")
        cursor.execute("""
            UPDATE audios SET tipo_audio = 'Cuña'
            WHERE UPPER(COALESCE(subgenero, '')) IN ('COMERCIAL', 'PUBLICIDAD')
               OR id IN (SELECT audio_id FROM pautas WHERE audio_id IS NOT NULL)
        """)
        logger.info("  → Columna tipo_audio añadida a audios: OK")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audios_tipo ON audios(tipo_audio)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cartuchera (
            banco      TEXT    NOT NULL,
            slot       INTEGER NOT NULL,
            audio_id   INTEGER NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
            etiqueta   TEXT    NOT NULL DEFAULT '',
            updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (banco, slot)
        )
    """)
    logger.info("  → tipo_audio y cartuchera: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 12 — Normalización de volumen
    #   · loudness_lufs / pico_db → medidos con ffmpeg (backend/audio/loudness.py)
    #   · loudness_estado         → NULL pendiente | 'ok' | 'error'
    #   · ganancia_db             → ajuste manual extra del operador (dB)
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    columnas_audios_v4 = {row["name"] for row in cursor.fetchall()}
    for col_name, col_def in (
        ("loudness_lufs",   "REAL DEFAULT NULL"),
        ("pico_db",         "REAL DEFAULT NULL"),
        ("loudness_estado", "TEXT DEFAULT NULL"),
        ("ganancia_db",     "REAL DEFAULT NULL"),
    ):
        if col_name not in columnas_audios_v4:
            cursor.execute(f"ALTER TABLE audios ADD COLUMN {col_name} {col_def}")
            logger.info(f"  → Columna {col_name} añadida a audios: OK")
    logger.info("  → Columnas de normalización: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 13 — Origen y energía de la música (filtros del Studio)
    #   · origen  → 'NACIONAL' | 'INTERNACIONAL' (NULL = sin clasificar)
    #   · energia → 1 (suave) … 5 (muy movida)
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    columnas_audios_v5 = {row["name"] for row in cursor.fetchall()}
    for col_name, col_def in (("origen", "TEXT DEFAULT NULL"), ("energia", "INTEGER DEFAULT NULL")):
        if col_name not in columnas_audios_v5:
            cursor.execute(f"ALTER TABLE audios ADD COLUMN {col_name} {col_def}")
            logger.info(f"  → Columna {col_name} añadida a audios: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 14 — Relojes (modo automático estilo Jazler SoHo)
    #   · relojes      → patrón de la hora (nombre, color, filtro de compensación)
    #   · reloj_filas  → filas en orden: qué tipo de audio y con qué filtros
    #   · reloj_grilla → qué reloj rige cada hora de cada día (7 × 24)
    #   Ver backend/audio/planner.py
    # ------------------------------------------------------------------
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS relojes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT    NOT NULL UNIQUE,
            color       TEXT    NOT NULL DEFAULT '#0a9396',
            comentario  TEXT    NOT NULL DEFAULT '',
            -- Compensación: de dónde salen las canciones de relleno para
            -- llegar a la tanda a tiempo (JSON con los mismos filtros de una fila)
            compensacion TEXT   NOT NULL DEFAULT '{}',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS reloj_filas (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            reloj_id  INTEGER NOT NULL REFERENCES relojes(id) ON DELETE CASCADE,
            orden     INTEGER NOT NULL DEFAULT 0,
            -- Tipo de audio a elegir: Musica | Efecto | Sweeper | Tips | Programa
            tipo_audio TEXT   NOT NULL DEFAULT 'Musica',
            -- Filtros (JSON): {"cat1": "...", "cat2": "...", "cat3": "...",
            --  "voz": "...", "origen": "...", "energia_min": 1, "energia_max": 5}
            filtros   TEXT    NOT NULL DEFAULT '{}',
            -- Audio fijo (opcional): esta fila siempre pone este audio
            audio_id  INTEGER REFERENCES audios(id) ON DELETE SET NULL,
            nota      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_reloj_filas_reloj ON reloj_filas(reloj_id, orden);
        CREATE TABLE IF NOT EXISTS reloj_grilla (
            dia      INTEGER NOT NULL,          -- 0=Dom … 6=Sáb (igual que la matriz de pautas)
            hora     INTEGER NOT NULL,          -- 0–23
            reloj_id INTEGER NOT NULL REFERENCES relojes(id) ON DELETE CASCADE,
            PRIMARY KEY (dia, hora)
        );
    """)
    logger.info("  → relojes, reloj_filas y reloj_grilla: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 15 — Tandas al estilo Jazler (secciones)
    #   · tanda_secciones        → orden dentro de la tanda, opcional, comercial
    #   · pautas.seccion         → a qué sección pertenece (3 = Comerciales)
    #   · historial_emision.tanda→ minuto de la tanda en que salió
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tanda_secciones (
            numero    INTEGER PRIMARY KEY,
            nombre    TEXT    NOT NULL,
            opcional  INTEGER NOT NULL DEFAULT 0,
            comercial INTEGER NOT NULL DEFAULT 0
        )
    """)
    if cursor.execute("SELECT COUNT(*) FROM tanda_secciones").fetchone()[0] == 0:
        cursor.executemany("INSERT INTO tanda_secciones VALUES (?, ?, ?, ?)", SEED_SECCIONES)
    cursor.execute("PRAGMA table_info(pautas)")
    if "seccion" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE pautas ADD COLUMN seccion INTEGER NOT NULL DEFAULT 3")
    cursor.execute("PRAGMA table_info(historial_emision)")
    if "tanda" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE historial_emision ADD COLUMN tanda INTEGER DEFAULT NULL")
    logger.info("  → tanda_secciones, pautas.seccion e historial_emision.tanda: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 16 — Eventos a hora fija (Jazler "Force Clocks")
    #   Un reloj que suena UNA vez a una hora exacta (HH:MM) en los días
    #   elegidos, entre dos fechas. Sus filas se ponen una sola vez y luego
    #   sigue el reloj de la grilla. Ver backend/audio/planner.py
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reloj_eventos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre       TEXT    NOT NULL,
            reloj_id     INTEGER NOT NULL REFERENCES relojes(id) ON DELETE CASCADE,
            hora         TEXT    NOT NULL,              -- 'HH:MM'
            dias         TEXT    NOT NULL DEFAULT '0,1,2,3,4,5,6',  -- 0=Dom … 6=Sáb
            fecha_inicio TEXT    NOT NULL DEFAULT '',   -- 'YYYY-MM-DD' ('' = desde siempre)
            fecha_fin    TEXT    NOT NULL DEFAULT '',   -- 'YYYY-MM-DD' ('' = sin fin)
            -- 1 = corta lo que suene y entra en punto (como la tanda inmediata)
            inmediato    INTEGER NOT NULL DEFAULT 0,
            activo       INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    logger.info("  → reloj_eventos: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 17 — Usuarios y sesiones (entrar desde otras PCs de la red)
    #   La PC del estudio entra sin contraseña; ver backend/api/auth.py
    # ------------------------------------------------------------------
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario       TEXT    NOT NULL UNIQUE,
            nombre        TEXT    NOT NULL DEFAULT '',
            rol           TEXT    NOT NULL,          -- admin | programacion | operador
            clave_hash    TEXT    NOT NULL,          -- pbkdf2$vueltas$sal$hash
            activo        INTEGER NOT NULL DEFAULT 1,
            ultimo_acceso TEXT,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sesiones (
            token_hash TEXT PRIMARY KEY,             -- sha256 del token de la cookie
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            expira     TEXT    NOT NULL,
            ip         TEXT,
            creada     TEXT    NOT NULL DEFAULT (datetime('now'))
        );
    """)
    logger.info("  → usuarios y sesiones: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 18 — Marcas automáticas (inicio y mezcla detectados)
    #   audios.marcas_auto: NULL sin analizar · 1 automáticas · 0 a mano · -1 error
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    if "marcas_auto" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE audios ADD COLUMN marcas_auto INTEGER DEFAULT NULL")
    logger.info("  → audios.marcas_auto: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 19 — Color de los valores de categoría (antes vivía solo en
    # el navegador de cada PC; ahora lo comparten todas)
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(config_categorias_valores)")
    if "color" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE config_categorias_valores ADD COLUMN color TEXT DEFAULT NULL")
    logger.info("  → config_categorias_valores.color: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 20 — Bandeja de entrada de la Biblioteca
    #   audios.aprobada: la música recién cargada entra con 0 y NO la elige el
    #   automático hasta que se clasifica (género y rotación) y se aprueba.
    #   Lo que ya estaba cargado queda aprobado (DEFAULT 1).
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    if "aprobada" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE audios ADD COLUMN aprobada INTEGER NOT NULL DEFAULT 1")
    logger.info("  → audios.aprobada: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 21 — Restricciones por canción (como Jazler)
    #   restr_desde / restr_hasta: horas en que PUEDE sonar (0–23, hasta
    #     excluida; si desde > hasta cruza la medianoche: 20→6 = de noche)
    #   restr_dias: días en que puede sonar "0,1,…" (0=Dom); NULL = todos
    #   max_por_dia: tope de veces por día; NULL = sin tope
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    cols = {r["name"] for r in cursor.fetchall()}
    for col, tipo in (("restr_desde", "INTEGER"), ("restr_hasta", "INTEGER"),
                      ("restr_dias", "TEXT"), ("max_por_dia", "INTEGER")):
        if col not in cols:
            cursor.execute(f"ALTER TABLE audios ADD COLUMN {col} {tipo} DEFAULT NULL")
    logger.info("  → restricciones por canción: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 22 — Prioridad por canción (1 Alta · 2 Normal · 3 Baja)
    #   El automático elige antes las de prioridad alta (ver planner._elegir)
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    if "prioridad" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE audios ADD COLUMN prioridad INTEGER DEFAULT NULL")
    logger.info("  → audios.prioridad: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 23 — Editor de canción (como Jazler)
    #   cue_outro: dónde termina la voz (aviso al locutor). NO es el punto de
    #     mezcla: la columna vieja `outro` sigue siendo respaldo de la mezcla.
    #   repeticion_horas: no repetir esta canción hasta N horas después de sonar
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(audios)")
    cols = {r["name"] for r in cursor.fetchall()}
    if "cue_outro" not in cols:
        cursor.execute("ALTER TABLE audios ADD COLUMN cue_outro REAL DEFAULT NULL")
    if "repeticion_horas" not in cols:
        cursor.execute("ALTER TABLE audios ADD COLUMN repeticion_horas INTEGER DEFAULT NULL")
    logger.info("  → audios.cue_outro y repeticion_horas: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 24 — Comerciales al estilo Jazler
    #   agencia:  agencia de publicidad (aparte del cliente/anunciante)
    #   posicion: 0 libre · 1 siempre primero · 2 siempre último en la tanda
    #   prioridad pasa de sí/no a 3 niveles: 1 Alta · 0 Normal · -1 Baja
    #     (las "urgentes" viejas ya valen 1 = Alta)
    # ------------------------------------------------------------------
    cursor.execute("PRAGMA table_info(pautas)")
    cols = {r["name"] for r in cursor.fetchall()}
    if "agencia" not in cols:
        cursor.execute("ALTER TABLE pautas ADD COLUMN agencia TEXT DEFAULT ''")
    if "posicion" not in cols:
        cursor.execute("ALTER TABLE pautas ADD COLUMN posicion INTEGER DEFAULT 0")
    logger.info("  → pautas.agencia y posicion: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 25 — Spot con varias versiones y orden manual en el bloque
    #   pauta_audios: versiones que rotan (A, B, C…) de un mismo spot.
    #     Sin filas, la pauta usa su audio_id de siempre.
    #   pautas.orden: lugar fijo dentro del bloque (NULL = el sistema decide)
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pauta_audios (
            pauta_id INTEGER NOT NULL REFERENCES pautas(id) ON DELETE CASCADE,
            audio_id INTEGER NOT NULL REFERENCES audios(id) ON DELETE CASCADE,
            orden    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pauta_id, audio_id)
        )
    """)
    cursor.execute("PRAGMA table_info(pautas)")
    if "orden" not in {r["name"] for r in cursor.fetchall()}:
        cursor.execute("ALTER TABLE pautas ADD COLUMN orden INTEGER DEFAULT NULL")
    logger.info("  → pauta_audios y pautas.orden: OK")

    # ------------------------------------------------------------------
    # MIGRACIÓN 26 — Día específico (Jazler "Día Específico")
    #   Para una fecha concreta, qué reloj rige cada hora, por encima de la
    #   grilla semanal. reloj_id NULL = esa hora solo música.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reloj_dias (
            fecha    TEXT    NOT NULL,          -- YYYY-MM-DD
            hora     INTEGER NOT NULL,          -- 0–23
            reloj_id INTEGER REFERENCES relojes(id) ON DELETE CASCADE,
            PRIMARY KEY (fecha, hora)
        )
    """)
    logger.info("  → reloj_dias: OK")

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
            intro           REAL    DEFAULT NULL,
            outro           REAL    DEFAULT NULL,
            hook            REAL    DEFAULT NULL,
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
