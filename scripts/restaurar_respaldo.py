"""
restaurar_respaldo.py — Ónix FM
Reemplaza la base de datos por una copia de respaldo. ONIX debe estar
CERRADO (lo cierra scripts\\restaurar_respaldo.ps1 antes de llamar a esto).

    python scripts/restaurar_respaldo.py                 → lista las copias
    python scripts/restaurar_respaldo.py onix-2026-10-12_0300.db
    python scripts/restaurar_respaldo.py D:\\otra\\carpeta\\copia.db

Antes de reemplazar:
  · verifica que la copia sea una base de ONIX sana (integrity_check y tablas);
  · guarda la base actual como radio_core.antes-de-restaurar-AAAAMMDD-HHMMSS.db
    junto a ella (por si la copia elegida no era la correcta).
"""

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.paths import DB_PATH  # noqa: E402

TABLAS = {"audios", "config", "pautas"}


def verificar(copia: Path) -> None:
    """Lanza ValueError si la copia no sirve."""
    if not copia.is_file():
        raise ValueError(f"No existe: {copia}")
    try:
        conn = sqlite3.connect(f"file:{copia}?mode=ro", uri=True)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"No es una base de datos válida: {exc}")
    if ok != "ok":
        raise ValueError(f"La copia está dañada: {ok}")
    if not TABLAS <= tablas:
        raise ValueError("No parece una base de ONIX (faltan tablas: "
                         f"{', '.join(sorted(TABLAS - tablas))})")


def restaurar(copia: Path, db: Path = DB_PATH) -> Path:
    """Reemplaza `db` por `copia`. Devuelve dónde quedó guardada la base anterior."""
    verificar(copia)
    db = Path(db)
    anterior = db.with_name(f"{db.stem}.antes-de-restaurar-{datetime.now():%Y%m%d-%H%M%S}.db")
    if db.exists():
        # Copia consistente de la actual (incluye lo que esté en el -wal)
        origen = sqlite3.connect(str(db))
        try:
            destino = sqlite3.connect(str(anterior))
            try:
                origen.backup(destino)
            finally:
                destino.close()
        finally:
            origen.close()
    for extra in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        extra.unlink(missing_ok=True)           # si no, SQLite mezclaría el diario viejo
    shutil.copy2(copia, db)
    return anterior


def main(argv: list[str]) -> int:
    from backend.api.respaldos import ajustes, listar
    carpeta = ajustes()["carpeta"]
    if not argv:
        copias = listar(carpeta)
        print(f"Copias en {carpeta}:")
        for c in copias:
            print(f"  {c['archivo']}   {c['fecha'].replace('T', ' ')}   {c['tamano'] // 1024} KB")
        if not copias:
            print("  (ninguna)")
        return 0
    copia = Path(argv[0])
    if not copia.is_absolute():
        copia = carpeta / copia
    try:
        anterior = restaurar(copia)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"NO se restauró: {exc}")
        return 1
    print(f"Restaurada: {copia.name}")
    print(f"La base anterior quedó guardada en: {anterior}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
