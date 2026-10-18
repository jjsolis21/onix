"""
migrar_epocas.py — Script de migración para calcular la época de las canciones existentes en Ónix FM.
Ejecuta la migración idempotente agregando la columna `epoca` y calculando su valor según `fecha_lanzamiento`.
"""
import os
import sys
import sqlite3
from pathlib import Path

# Asegurar path de importación
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.api.utils_musica import calcular_epoca

DB_PATH = os.getenv("RADIO_DB_PATH", str(BASE_DIR / "backend" / "api" / "radio_core.db"))


def migrar():
    print(f"[*] Conectando a base de datos: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Agregar columna si no existe
    cursor.execute("PRAGMA table_info(audios)")
    columnas = {r["name"] for r in cursor.fetchall()}
    if "epoca" not in columnas:
        print("[+] Agregando columna 'epoca' a la tabla 'audios'...")
        cursor.execute("ALTER TABLE audios ADD COLUMN epoca TEXT DEFAULT NULL")
    else:
        print("[i] La columna 'epoca' ya existe en 'audios'.")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audios_epoca ON audios (epoca)")

    # 2. Recalcular para todos los audios con fecha_lanzamiento
    cursor.execute("SELECT id, titulo, artista, fecha_lanzamiento, epoca FROM audios WHERE fecha_lanzamiento IS NOT NULL AND TRIM(fecha_lanzamiento) != ''")
    audios = cursor.fetchall()
    actualizados = 0

    print(f"[*] Procesando {len(audios)} audios con fecha de lanzamiento...")
    for a in audios:
        epoca_calculada = calcular_epoca(a["fecha_lanzamiento"])
        if epoca_calculada != a["epoca"]:
            cursor.execute("UPDATE audios SET epoca = ? WHERE id = ?", (epoca_calculada, a["id"]))
            actualizados += 1

    # Registrar en config si existe la tabla
    try:
        cursor.execute("INSERT OR REPLACE INTO config (clave, valor) VALUES ('migracion_epoca_audios', '1')")
    except Exception:
        pass

    conn.commit()
    conn.close()
    print(f"[OK] Migracion completada exitosamente. Audios actualizados con su epoca: {actualizados}/{len(audios)}")


if __name__ == "__main__":
    migrar()
