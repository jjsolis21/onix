"""
restaurar_respaldo.py — Ónix FM
Reemplaza el contenido de la base MariaDB por una copia de respaldo. ONIX debe estar
CERRADO (lo cierra scripts\\restaurar_respaldo.ps1 antes de llamar a esto).

    python scripts/restaurar_respaldo.py                       → lista las copias
    python scripts/restaurar_respaldo.py onix-2026-10-12_0300.sql.gz
    python scripts/restaurar_respaldo.py D:\\otra\\carpeta\\copia.sql.gz

Antes de reemplazar:
  · verifica que la copia esté entera (cierre y conteos) y sea una base de ONIX;
  · guarda la base actual como antes-de-restaurar-AAAAMMDD-HHMMSS.sql.gz junto a las copias
    (por si la copia elegida no era la correcta) y comprueba que ese respaldo quedó bien.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.respaldo_maria import restaurar, verificar  # noqa: E402,F401


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
    except Exception as exc:                       # ValueError (copia mala), OSError, errores de MariaDB
        print(f"NO se restauró: {exc}")
        return 1
    print(f"Restaurada: {copia.name}")
    print(f"La base anterior quedó guardada en: {anterior}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
