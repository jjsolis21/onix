"""
generar_esquema_maria.py — Ónix FM
Regenera `backend/esquema_maria.sql` (tablas y triggers) y `backend/semilla_maria.json` (filas de fábrica) a partir de la
base de PRUEBAS (`onix_test`, siempre con el esquema al día). Se corre a mano cuando cambia el esquema:

    python scripts/generar_esquema_maria.py

Las instalaciones nuevas aplican esos dos archivos con `backend/esquema_maria.py`.
No lee ni copia ningún dato de `onix_db`.
"""

import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

os.environ["DB_NAME"] = "onix_test"            # nunca la base real

from backend.database import get_raw_mariadb_connection  # noqa: E402
from backend.respaldo_maria import SEP, _AUXILIARES  # noqa: E402

DESTINO_SQL = RAIZ / "backend" / "esquema_maria.sql"
DESTINO_SEMILLA = RAIZ / "backend" / "semilla_maria.json"


def esquema() -> tuple[str, int, int]:
    raw = get_raw_mariadb_connection()._conn
    cur = raw.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() "
                "AND table_type = 'BASE TABLE' ORDER BY table_name")
    tablas = [t for (t,) in cur.fetchall() if not _AUXILIARES.search(t)]
    partes = ["-- ONIX-ESQUEMA v1 (generado por scripts/generar_esquema_maria.py; no editar a mano)"]
    for t in tablas:
        cur.execute(f"SHOW CREATE TABLE `{t}`")
        ddl = re.sub(r" AUTO_INCREMENT=\d+", "", cur.fetchone()[1])       # el contador de ids no es parte del esquema
        partes.append(ddl)
    cur.execute("SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = DATABASE() ORDER BY trigger_name")
    triggers = [t for (t,) in cur.fetchall()]
    for trg in triggers:
        cur.execute(f"SHOW CREATE TRIGGER `{trg}`")
        ddl = re.sub(r"DEFINER=`[^`]*`@`[^`]*` ", "", cur.fetchone()[2])  # el usuario definidor puede no existir en el otro servidor
        partes.append(ddl)
    raw.close()
    return SEP.join(partes) + SEP, len(tablas), len(triggers)


def semilla() -> dict:
    """Filas con las que nace una instalación nueva: las que crea init_db en una base vacía (categorías, secciones…)."""
    import sqlite3
    import tempfile
    from init_db import initialize_database
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "semilla.db"
        initialize_database(str(ruta))
        lit = sqlite3.connect(str(ruta))
        lit.row_factory = sqlite3.Row
        raw = get_raw_mariadb_connection()._conn
        cur = raw.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        existentes = {t for (t,) in cur.fetchall()}
        raw.close()
        out = {}
        try:
            for (t,) in lit.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                if t not in existentes or t in ("sesiones", "usuarios", "historial_emision"):
                    continue
                filas = [dict(f) for f in lit.execute(f"SELECT * FROM `{t}`").fetchall()]
                if filas and len(filas) <= 500:
                    out[t] = filas
        finally:
            lit.close()
    return out


def main() -> None:
    sql, n_tablas, n_triggers = esquema()
    DESTINO_SQL.write_text(sql, encoding="utf-8", newline="\n")
    datos = semilla()
    DESTINO_SEMILLA.write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8", newline="\n")
    print(f"Esquema: {n_tablas} tablas y {n_triggers} triggers -> {DESTINO_SQL.relative_to(RAIZ)}")
    print(f"Semilla: {sum(len(v) for v in datos.values())} filas en {len(datos)} tablas -> {DESTINO_SEMILLA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
