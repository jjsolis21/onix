"""
migraciones_maria.py — Ónix FM
Migraciones idempotentes para MariaDB (la base real). `scripts/init_db.py` solo migra el archivo SQLite
histórico; todo cambio de esquema nuevo debe agregarse AQUÍ para que llegue a `onix_db` al arrancar.

Cada paso se puede correr las veces que sea: si ya está aplicado, no hace nada.
"""

import logging

logger = logging.getLogger("onix.migraciones")


def _columnas(c, tabla: str) -> set:
    return {r[0] for r in c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ?",
        (tabla,)).fetchall()}


def _indices(c, tabla: str) -> set:
    return {r[0] for r in c.execute(
        "SELECT index_name FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = ?",
        (tabla,)).fetchall()}


def _reconstruir_con_autoincrement(c, tabla: str, tipo: str) -> None:
    """Copia la tabla a una nueva con AUTO_INCREMENT y la intercambia. La vieja queda como `<tabla>_viejo_aid`."""
    nueva, vieja = f"{tabla}_nueva_aid", f"{tabla}_viejo_aid"
    c.execute(f"DROP TABLE IF EXISTS `{nueva}`")
    c.execute(f"CREATE TABLE `{nueva}` LIKE `{tabla}`")
    c.execute(f"ALTER TABLE `{nueva}` MODIFY `id` {tipo} NOT NULL AUTO_INCREMENT")
    c.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_AUTO_VALUE_ON_ZERO')")   # el id 0 se conserva tal cual
    c.execute(f"INSERT INTO `{nueva}` SELECT * FROM `{tabla}`")
    n_old = c.execute(f"SELECT COUNT(*) FROM `{tabla}`").fetchone()[0]
    n_new = c.execute(f"SELECT COUNT(*) FROM `{nueva}`").fetchone()[0]
    if n_old != n_new:
        c.execute(f"DROP TABLE `{nueva}`")
        raise RuntimeError(f"{tabla}: la copia no cuadra ({n_old} vs {n_new}); no se cambió nada")
    c.execute(f"DROP TABLE IF EXISTS `{vieja}`")
    c.execute(f"RENAME TABLE `{tabla}` TO `{vieja}`, `{nueva}` TO `{tabla}`")
    logger.info("  → %s reconstruida con AUTO_INCREMENT (%d filas; original en %s)", tabla, n_new, vieja)


def migrar() -> None:
    """Aplica los pasos pendientes (idempotente)."""
    from backend.database import get_connection
    c = get_connection()
    try:
        # 44 — Dayparting por spot (franja horaria permitida) y «deuda» de pautas no emitidas
        cols = _columnas(c, "pautas")
        for col in ("hora_inicio_permitida", "hora_fin_permitida"):
            if col not in cols:
                c.execute(f"ALTER TABLE pautas ADD COLUMN `{col}` INT NULL DEFAULT NULL")
                logger.info("  → pautas.%s agregada", col)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pautas_deuda (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                pauta_id      INT NOT NULL,
                slot_original VARCHAR(40) NOT NULL,
                fecha         VARCHAR(20) NOT NULL,
                motivo        VARCHAR(60) DEFAULT 'no_emitida',
                recuperada    INT DEFAULT 0,
                recuperada_en VARCHAR(30) DEFAULT NULL,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
        if "idx_pauta_fecha" not in _indices(c, "pautas_deuda"):
            c.execute("CREATE INDEX idx_pauta_fecha ON pautas_deuda (pauta_id, fecha, recuperada)")
        # 48 — Nombres sin distinguir mayúsculas: en SQLite eran COLLATE NOCASE y en MariaDB quedaron «utf8mb4_bin»
        #      (distingue «Navidad» de «NAVIDAD»), así que el UNIQUE dejaba pasar duplicados.
        for tabla, columna in (("comerciales_agencias", "nombre"), ("comerciales_clientes", "nombre"),
                               ("comerciales_rubros", "nombre"), ("ident_categorias", "nombre")):
            info = c.execute("SELECT column_type, is_nullable, collation_name FROM information_schema.columns"
                             " WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?",
                             (tabla, columna)).fetchone()
            if not info or info[2] == "utf8mb4_unicode_ci":
                continue
            duplicado = c.execute(f"SELECT 1 FROM `{tabla}` GROUP BY `{columna}` COLLATE utf8mb4_unicode_ci"
                                  f"{', tipo_audio' if tabla == 'ident_categorias' else ''} HAVING COUNT(*) > 1 LIMIT 1").fetchone()
            if duplicado:
                logger.warning("  → %s.%s: hay nombres repetidos que solo difieren en mayúsculas; revisar a mano", tabla, columna)
                continue
            nulo = "NULL" if info[1] == "YES" else "NOT NULL"
            c.execute(f"ALTER TABLE `{tabla}` MODIFY `{columna}` {info[0]} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci {nulo}")
            logger.info("  → %s.%s ahora no distingue mayúsculas", tabla, columna)
        c.commit()

        # 47 — audios.duracion con decimales: en SQLite (INTEGER) guardaba 61.5 s, pero en MariaDB un INT lo redondeaba a 62 y
        #      los redondeos se acumulan en las tandas que deben terminar en punto.
        tipo_dur = c.execute("SELECT data_type FROM information_schema.columns WHERE table_schema = DATABASE()"
                             " AND table_name = 'audios' AND column_name = 'duracion'").fetchone()
        if tipo_dur and tipo_dur[0] in ("int", "bigint", "smallint", "mediumint"):
            c.execute("ALTER TABLE audios MODIFY `duracion` DOUBLE NULL DEFAULT 0")
            logger.info("  → audios.duracion ahora admite decimales")
        c.commit()

        # 46 — Búsqueda rápida: audios.busq lo mantenía un trigger de SQLite que la migración no recreó, y las canciones
        #      nuevas o editadas quedaban sin texto de búsqueda (la biblioteca no las encontraba).
        pares = list(zip("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN"))

        def _busq(p: str) -> str:
            e = f"CONCAT(COALESCE({p}titulo, ''), ' ', COALESCE({p}artista, ''), ' ', COALESCE({p}subgenero, ''), ' ', COALESCE({p}categoria, ''))"
            for con, sin in pares:
                e = f"REPLACE({e}, '{con}', '{sin}')"
            return f"LOWER({e})"
        for nombre, evento in (("trg_audios_busq_ins", "INSERT"), ("trg_audios_busq_upd", "UPDATE")):
            c.execute(f"DROP TRIGGER IF EXISTS {nombre}")
            c.execute(f"CREATE TRIGGER {nombre} BEFORE {evento} ON audios FOR EACH ROW SET NEW.busq = {_busq('NEW.')}")
        c.execute(f"UPDATE audios SET busq = {_busq('')} WHERE busq IS NULL OR busq = '' OR busq <> {_busq('')}")
        c.commit()

        # 45 — `id` con AUTO_INCREMENT: las tablas creadas por esquema_extra (SQLite INTEGER PRIMARY KEY) no lo tenían,
        #      y MariaDB rechazaba cualquier INSERT sin id («Field id doesn't have a default value»).
        sin_auto = c.execute(
            "SELECT table_name, column_type FROM information_schema.columns WHERE table_schema = DATABASE()"
            " AND column_name = 'id' AND column_key = 'PRI' AND extra NOT LIKE '%auto_increment%'"
            " AND RIGHT(table_name, 4) <> '_aid'").fetchall()          # las copias de seguridad de esta migración no se tocan
        for t, tipo in sin_auto:
            try:
                c.execute(f"ALTER TABLE `{t}` MODIFY `id` {tipo} NOT NULL AUTO_INCREMENT")
            except Exception:
                # Si hay una fila con id = 0, MariaDB la renumera y choca: se reconstruye la tabla sin tocar el 0.
                _reconstruir_con_autoincrement(c, t, tipo)
            logger.info("  → %s.id con AUTO_INCREMENT", t)
        c.commit()
    finally:
        c.close()
