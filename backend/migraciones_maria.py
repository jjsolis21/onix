"""
migraciones_maria.py — Ónix FM
Migraciones idempotentes para MariaDB (la base real). `scripts/init_db.py` solo migra el archivo SQLite
histórico; todo cambio de esquema nuevo debe agregarse AQUÍ para que llegue a `onix_db` al arrancar.

Cada paso se puede correr las veces que sea: si ya está aplicado, no hace nada.
"""

import logging

logger = logging.getLogger("onix.migraciones")


import unicodedata

# Columna de `audios` donde cada categoría guarda su valor
_COLUMNA_DE_CATEGORIA = {"cat1": "subgenero", "cat2": "categoria", "cat3": "cat3", "voz": "genero_vocal"}
# Las voces oficiales: Hombre, Mujer, Dúo, Grupo y FT (para la música) y Genérico (para las cuñas)
_VALORES_DE_SISTEMA = {"voz": ("Hombre", "Mujer", "Dúo", "Grupo", "FT", "Genérico")}
# Valores que ya no son oficiales (MALE es «Hombre»; Instrumental no se usa): se retiran de la lista SOLO si ninguna canción los usa
_RETIRAR_SI_SIN_USO = {"voz": ("MALE", "Instrumental")}


def _clave_categoria(valor: str) -> str:
    """Sin mayúsculas ni tildes: «Pop» = «POP», «Dúo» = «DUO»."""
    base = unicodedata.normalize("NFKD", " ".join(str(valor).split()))
    return "".join(ch for ch in base if not unicodedata.combining(ch)).casefold()


def _unificar_categorias(c) -> int:
    """
    Un valor de la lista no puede estar dos veces solo por mayúsculas o tildes (Pop/POP, Balada/BALADA, Dúo/DUO).
    Se queda UNA escritura —la que no está toda en mayúsculas, o la primera por orden— y las canciones que usaban la otra
    pasan a ella. Devuelve cuántos valores repetidos se eliminaron. Idempotente.
    """
    eliminados = 0
    for cat_id, nombre in [tuple(r) for r in c.execute("SELECT id, nombre_interno FROM config_categorias_def").fetchall()]:
        columna = _COLUMNA_DE_CATEGORIA.get(nombre)
        grupos: dict = {}
        for fila in c.execute("SELECT id, valor, activo FROM config_categorias_valores WHERE cat_id = ? ORDER BY orden, id",
                              (cat_id,)).fetchall():
            grupos.setdefault(_clave_categoria(fila[1]), []).append(tuple(fila))
        for miembros in grupos.values():
            if len(miembros) < 2:
                continue
            # Se conserva la escritura normal («Pop») aunque estuviera inactiva; si todas están en mayúsculas, la primera por orden
            elegido = next((m for m in miembros if m[1] != m[1].upper()), None) or next((m for m in miembros if m[2]), miembros[0])
            for vid, valor, _activo in miembros:
                if vid == elegido[0]:
                    continue
                if columna:
                    c.execute(f"UPDATE audios SET `{columna}` = ? WHERE `{columna}` = ? COLLATE utf8mb4_bin", (elegido[1], valor))
                c.execute("DELETE FROM config_categorias_valores WHERE id = ?", (vid,))
                eliminados += 1
            if any(m[2] for m in miembros):
                c.execute("UPDATE config_categorias_valores SET activo = 1 WHERE id = ?", (elegido[0],))
    return eliminados


def _retirar_valores_sin_uso(c) -> None:
    for nombre, valores in _RETIRAR_SI_SIN_USO.items():
        columna = _COLUMNA_DE_CATEGORIA[nombre]
        for valor in valores:
            c.execute(f"DELETE v FROM config_categorias_valores v JOIN config_categorias_def d ON d.id = v.cat_id"
                      f" WHERE d.nombre_interno = ? AND UPPER(v.valor) = UPPER(?)"
                      f" AND NOT EXISTS (SELECT 1 FROM audios a WHERE UPPER(a.`{columna}`) = UPPER(?))", (nombre, valor, valor))


def _asegurar_valores_de_sistema(c) -> None:
    for nombre, valores in _VALORES_DE_SISTEMA.items():
        fila = c.execute("SELECT id FROM config_categorias_def WHERE nombre_interno = ?", (nombre,)).fetchone()
        if not fila:
            continue
        cat_id = fila[0]
        for valor in valores:
            existente = c.execute("SELECT id, activo FROM config_categorias_valores WHERE cat_id = ? AND UPPER(valor) = UPPER(?)",
                                  (cat_id, valor)).fetchone()
            if existente and existente[1]:
                continue
            if existente:
                c.execute("UPDATE config_categorias_valores SET activo = 1 WHERE id = ?", (existente[0],))
            else:
                orden = c.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM config_categorias_valores WHERE cat_id = ?",
                                  (cat_id,)).fetchone()[0]
                c.execute("INSERT INTO config_categorias_valores (cat_id, valor, orden) VALUES (?, ?, ?)", (cat_id, valor, orden))


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
        # 50 — Tips sobre la música (estilo Jazler SoHo): la fila de Tips del reloj puede sonar al inicio o al final del intro
        #      de la canción siguiente, o de puente entre dos canciones. '' = suena sola, como hasta ahora.
        if "tip_modo" not in _columnas(c, "reloj_filas"):
            c.execute("ALTER TABLE reloj_filas ADD COLUMN `tip_modo` VARCHAR(20) NOT NULL DEFAULT ''")
            logger.info("  → reloj_filas.tip_modo agregada")
        c.commit()

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
        # 49 — Géneros y categorías: una sola escritura por valor (Pop/POP, Balada/BALADA, Dúo/DUO…) y solo los de la lista.
        #      Se unifican los repetidos, se retira el valor de prueba «TEMP_TEST» si nadie lo usa, y la columna pasa a no distinguir
        #      mayúsculas para que el UNIQUE (categoría, valor) impida volver a crear un repetido.
        unificados = _unificar_categorias(c)
        if unificados:
            logger.info("  → %d valores de categoría repetidos unificados", unificados)
        c.execute("DELETE v FROM config_categorias_valores v WHERE v.valor = 'TEMP_TEST'"
                  " AND NOT EXISTS (SELECT 1 FROM audios a WHERE a.subgenero = 'TEMP_TEST' OR a.categoria = 'TEMP_TEST'"
                  " OR a.cat3 = 'TEMP_TEST' OR a.genero_vocal = 'TEMP_TEST')")
        coleccion = c.execute("SELECT collation_name, column_type, is_nullable FROM information_schema.columns"
                              " WHERE table_schema = DATABASE() AND table_name = 'config_categorias_valores' AND column_name = 'valor'").fetchone()
        if coleccion and coleccion[0] != "utf8mb4_unicode_ci":
            c.execute(f"ALTER TABLE config_categorias_valores MODIFY `valor` {coleccion[1]} CHARACTER SET utf8mb4"
                      f" COLLATE utf8mb4_unicode_ci {'NULL' if coleccion[2] == 'YES' else 'NOT NULL'}")
        _retirar_valores_sin_uso(c)
        _asegurar_valores_de_sistema(c)
        c.commit()

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
