"""
respaldo_maria.py — Ónix FM
Respaldo y restauración de la base MariaDB (onix_db), sin depender de `mysqldump`.

FORMATO  onix-AAAA-MM-DD_HHMM.sql.gz  (texto SQL comprimido)
    · Una FOTO consistente de toda la base (transacción con snapshot): el motor puede seguir escribiendo el historial
      mientras se respalda y la copia no queda a medias entre tablas.
    · Para cada tabla: DROP, CREATE y los datos en bloques de INSERT; los triggers van al final (si no, dispararían al
      insertar los datos).
    · Cada sentencia termina con una línea separadora, y el archivo cierra con «-- ONIX-FIN tablas=N filas=M».
      Una copia sin ese cierre (cortada, disco lleno, apagón) se rechaza: nunca se acepta como buena una copia a medias.

LO QUE NO RESPALDA: los audios (carpeta media/); son archivos grandes que hay que copiar aparte.
"""

import gzip
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("onix.respaldo_maria")

SEP = "\n-- ;;ONIX;;\n"
CABECERA = "-- ONIX-DUMP v1"
TABLAS_OBLIGATORIAS = {"audios", "config", "pautas"}     # sin estas no es una base de ONIX
LOTE = 200                                               # filas por INSERT
# Tablas de apoyo que dejan las migraciones (copias de seguridad de tablas): no van en el respaldo diario
_AUXILIARES = re.compile(r"_(respaldo|viejo|nueva)_aid$")


def _abrir_foto():
    """Conexión propia con una foto consistente de la base (REPEATABLE READ + snapshot)."""
    from backend.database import get_raw_mariadb_connection
    raw = get_raw_mariadb_connection()._conn
    cur = raw.cursor()
    cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
    return raw


def volcar(destino: Path) -> dict:
    """Escribe el respaldo en `destino` (.sql.gz). Devuelve {'tablas': N, 'filas': M}."""
    import pymysql.cursors
    raw = _abrir_foto()
    try:
        cur = raw.cursor()
        cur.execute("SELECT DATABASE()")
        base = cur.fetchone()[0]
        cur.execute("SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name")
        tablas = [t for (t,) in cur.fetchall() if not _AUXILIARES.search(t)]
        total_filas = 0
        with gzip.open(destino, "wt", encoding="utf-8", newline="\n") as f:
            def escribir(sentencia: str):
                f.write(sentencia + SEP)

            escribir(f"{CABECERA} base={base} fecha={datetime.now().isoformat(timespec='seconds')}")
            escribir("SET FOREIGN_KEY_CHECKS=0")
            for t in tablas:
                cur.execute(f"SHOW CREATE TABLE `{t}`")
                ddl = cur.fetchone()[1]
                escribir(f"DROP TABLE IF EXISTS `{t}`")
                escribir(ddl)
                datos = raw.cursor(pymysql.cursors.SSCursor)          # lee por partes: no carga toda la tabla en memoria
                datos.execute(f"SELECT * FROM `{t}`")
                columnas = ", ".join(f"`{d[0]}`" for d in datos.description)
                filas = 0
                while True:
                    bloque = datos.fetchmany(LOTE)
                    if not bloque:
                        break
                    valores = ",".join("(" + ",".join(raw.escape(v) for v in fila) + ")" for fila in bloque)
                    escribir(f"INSERT INTO `{t}` ({columnas}) VALUES {valores}")
                    filas += len(bloque)
                datos.close()
                escribir(f"-- TABLA {t} {filas}")
                total_filas += filas
            cur.execute("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = DATABASE()"
                        " ORDER BY trigger_name")
            for (trg,) in cur.fetchall():
                cur.execute(f"SHOW CREATE TRIGGER `{trg}`")
                escribir(f"DROP TRIGGER IF EXISTS `{trg}`")
                escribir(cur.fetchone()[2])
            escribir("SET FOREIGN_KEY_CHECKS=1")
            f.write(f"-- ONIX-FIN tablas={len(tablas)} filas={total_filas}\n")
        return {"tablas": len(tablas), "filas": total_filas}
    finally:
        try:
            raw.rollback()
        finally:
            raw.close()


def _leer(ruta: Path) -> str:
    try:
        with gzip.open(ruta, "rt", encoding="utf-8") as f:
            return f.read()
    except (OSError, EOFError, UnicodeDecodeError) as exc:         # no es gzip, está cortado o dañado
        raise ValueError(f"No es un respaldo de ONIX válido o está dañado: {exc}")


def verificar(ruta: Path) -> dict:
    """Comprueba que el respaldo esté entero. Lanza ValueError si no sirve; devuelve {'tablas': N, 'filas': M}."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ValueError(f"No existe: {ruta}")
    if ruta.suffix == ".db":
        raise ValueError("Es una copia vieja de SQLite (.db), de antes de MariaDB: no se puede restaurar en esta base")
    texto = _leer(ruta)
    if not texto.startswith(CABECERA):
        raise ValueError("No es un respaldo de ONIX")
    m = re.search(r"^-- ONIX-FIN tablas=(\d+) filas=(\d+)\s*$", texto, re.MULTILINE)
    if not m:
        raise ValueError("La copia está incompleta (le falta el cierre): se cortó al copiarse")
    tablas = {n: int(k) for n, k in re.findall(r"^-- TABLA (\S+) (\d+)$", texto, re.MULTILINE)}
    if len(tablas) != int(m.group(1)) or sum(tablas.values()) != int(m.group(2)):
        raise ValueError("La copia está dañada: los conteos no coinciden")
    faltan = TABLAS_OBLIGATORIAS - set(tablas)
    if faltan:
        raise ValueError(f"No parece una base de ONIX (faltan tablas: {', '.join(sorted(faltan))})")
    return {"tablas": len(tablas), "filas": sum(tablas.values())}


def restaurar(ruta: Path, carpeta_seguridad: Optional[Path] = None) -> Path:
    """
    Reemplaza el contenido de la base por el del respaldo. ONIX debe estar CERRADO.
    Antes guarda la base actual en `antes-de-restaurar-AAAAMMDD-HHMMSS.sql.gz` (por si la copia elegida no era la correcta).
    Devuelve la ruta de ese respaldo de seguridad.
    """
    ruta = Path(ruta)
    verificar(ruta)                                           # nada se toca si la copia no sirve
    carpeta = Path(carpeta_seguridad) if carpeta_seguridad else ruta.parent
    carpeta.mkdir(parents=True, exist_ok=True)
    anterior = carpeta / f"antes-de-restaurar-{datetime.now():%Y%m%d-%H%M%S}.sql.gz"
    volcar(anterior)
    verificar(anterior)                                       # y se comprueba que SÍ quedó guardada antes de seguir

    from backend.database import get_raw_mariadb_connection
    raw = get_raw_mariadb_connection()._conn
    try:
        raw.autocommit(True)
        cur = raw.cursor()
        cur.execute("SET SESSION lock_wait_timeout = 60")
        for sentencia in _leer(ruta).split(SEP):
            sentencia = sentencia.strip()
            if not sentencia or sentencia.startswith("--"):
                continue
            cur.execute(sentencia)
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
    finally:
        raw.close()
    log.info("[Respaldo] Restaurado %s (la base anterior quedó en %s)", ruta.name, anterior.name)
    return anterior
