"""
migrate.py — Ónix FM Radio Core
Migración: añade columnas intro, outro, hook a la tabla audios.

Ejecutar UNA SOLA VEZ desde la raíz del proyecto:
    python backend/api/migrate.py

O desde la carpeta backend/api/:
    python migrate.py

El script es idempotente: si las columnas ya existen no hace nada.
"""

import sqlite3
import os
from pathlib import Path

# ── Ruta a la base de datos ───────────────────────────────────────────────────
# Busca radio_core.db subiendo desde este archivo hasta la raíz del proyecto.
_THIS = Path(__file__).resolve()
DB_PATH = os.getenv("RADIO_DB_PATH", None)

if not DB_PATH:
    # Intentar encontrar radio_core.db subiendo directorios
    for parent in [_THIS.parent, _THIS.parent.parent, _THIS.parent.parent.parent]:
        candidate = parent / "radio_core.db"
        if candidate.exists():
            DB_PATH = str(candidate)
            break

if not DB_PATH:
    # Fallback: en el directorio de trabajo actual
    DB_PATH = "radio_core.db"

print(f"[migrate] Usando base de datos: {DB_PATH}")


def run_migration():
    if not Path(DB_PATH).exists():
        print(f"[migrate] ✗ No se encontró radio_core.db en: {DB_PATH}")
        print("[migrate]   Ejecuta el script desde la raíz del proyecto o")
        print("            define la variable de entorno RADIO_DB_PATH.")
        return False

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # Leer las columnas existentes en la tabla audios
        cols_info = conn.execute("PRAGMA table_info(audios)").fetchall()
        existing_cols = {row["name"] for row in cols_info}
        print(f"[migrate] Columnas existentes en 'audios': {sorted(existing_cols)}")

        # Columnas a añadir: nombre → definición SQL
        new_columns = {
            "intro": "REAL DEFAULT 0.0",   # segundos — punto de entrada de voz
            "outro": "REAL DEFAULT 0.0",   # segundos — punto de mezcla de salida
            "hook":  "REAL DEFAULT 0.0",   # segundos — punto del estribillo/gancho
        }

        added = []
        skipped = []

        for col_name, col_def in new_columns.items():
            if col_name in existing_cols:
                skipped.append(col_name)
                print(f"[migrate]   → '{col_name}' ya existe. Omitiendo.")
            else:
                conn.execute(f"ALTER TABLE audios ADD COLUMN {col_name} {col_def}")
                added.append(col_name)
                print(f"[migrate]   ✓ Columna '{col_name} {col_def}' añadida.")

        conn.commit()

        print()
        if added:
            print(f"[migrate] ✅ Migración completada. Columnas añadidas: {added}")
        else:
            print("[migrate] ✅ Sin cambios — todas las columnas ya existían.")

        if skipped:
            print(f"[migrate]    Omitidas (ya existían): {skipped}")

        return True

    except Exception as e:
        conn.rollback()
        print(f"[migrate] ✗ Error durante la migración: {e}")
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    success = run_migration()
    exit(0 if success else 1)
