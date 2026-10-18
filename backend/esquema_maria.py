"""
esquema_maria.py — Ónix FM
Crea el esquema de ONIX en una base MariaDB VACÍA (instalación nueva) y la deja al día.

    from backend.esquema_maria import asegurar_esquema
    asegurar_esquema()        # si la base no tiene las tablas, las crea; luego aplica las migraciones pendientes

Los archivos `esquema_maria.sql` y `semilla_maria.json` los genera `scripts/generar_esquema_maria.py` a partir de la base
de pruebas. Una base que ya tiene datos NUNCA se toca aquí: solo se aplican las migraciones (idempotentes).
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("onix.esquema")

RAIZ = Path(__file__).resolve().parent
ARCHIVO_SQL = RAIZ / "esquema_maria.sql"
ARCHIVO_SEMILLA = RAIZ / "semilla_maria.json"


def _tiene_esquema(cur) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'audios'")
    return cur.fetchone() is not None


def crear_esquema() -> dict:
    """Aplica las tablas, los triggers y las filas de fábrica. Falla si la base ya tiene el esquema."""
    from backend.database import get_raw_mariadb_connection
    from backend.respaldo_maria import SEP
    raw = get_raw_mariadb_connection()._conn
    try:
        raw.autocommit(True)
        cur = raw.cursor()
        if _tiene_esquema(cur):
            raise RuntimeError("La base ya tiene tablas de ONIX: no se crea el esquema encima")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        sentencias = [s.strip() for s in ARCHIVO_SQL.read_text(encoding="utf-8").split(SEP)]
        for s in sentencias:
            if s and not s.startswith("--"):
                cur.execute(s)
        semilla = json.loads(ARCHIVO_SEMILLA.read_text(encoding="utf-8"))
        for tabla, filas in semilla.items():
            cols = list(filas[0].keys())
            sql = (f"INSERT INTO `{tabla}` ({', '.join('`' + c + '`' for c in cols)}) "
                   f"VALUES ({', '.join(['%s'] * len(cols))})")
            cur.executemany(sql, [tuple(f[c] for c in cols) for f in filas])
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        log.info("[Esquema] Base nueva creada: %d sentencias, %d tablas con filas de fábrica", len(sentencias), len(semilla))
        return {"sentencias": len(sentencias), "tablas_con_semilla": len(semilla)}
    finally:
        raw.close()


def asegurar_esquema() -> bool:
    """Crea el esquema si la base está vacía y aplica las migraciones. Devuelve True si tuvo que crearlo."""
    from backend.database import get_raw_mariadb_connection
    from backend.migraciones_maria import migrar
    raw = get_raw_mariadb_connection()._conn
    try:
        nueva = not _tiene_esquema(raw.cursor())
    finally:
        raw.close()
    if nueva:
        crear_esquema()
    migrar()
    return nueva
