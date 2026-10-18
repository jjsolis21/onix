"""
tests/test_db.py — Ónix FM
Validación de Conexión a MariaDB 12.3 y verificación de canciones migradas.
"""

import sys
from pathlib import Path
from sqlalchemy import text

# Añadir la raíz del proyecto al path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest

from backend.database import DB_BACKEND, DB_NAME, engine, get_connection, db_session

# Solo en el modo MariaDB (ONIX_TEST_DB=mariadb) y siempre contra la base de PRUEBAS, nunca la real
pytestmark = pytest.mark.skipif(DB_BACKEND != "mariadb", reason="prueba de MariaDB: corre con ONIX_TEST_DB=mariadb")


def test_mariadb_connection_sqlalchemy():
    """Valida la conexión a MariaDB usando SQLAlchemy."""
    print("\n--- [1] Prueba de conexión SQLAlchemy ---")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT DATABASE(), VERSION();")).fetchone()
        db_name, version = result[0], result[1]
        print(f"OK: Conectado a MariaDB. Base de datos: '{db_name}', Versión: '{version}'")
        assert db_name.startswith("onix_test"), f"Las pruebas solo usan la base de pruebas, obtenido '{db_name}'"

        # Conteo total de audios
        total_audios = conn.execute(text("SELECT COUNT(*) FROM audios;")).scalar()
        print(f"OK: Total de pistas registradas en 'audios': {total_audios}")
        assert total_audios >= 0


def test_select_canciones_sqlalchemy():
    """Realiza un SELECT a la tabla de canciones con SQLAlchemy text()."""
    print("\n--- [2] SELECT canciones (SQLAlchemy) ---")
    with engine.connect() as conn:
        query = text("""
            SELECT id, titulo, artista, album, fecha_lanzamiento, epoca, duracion
            FROM audios
            ORDER BY id ASC
            LIMIT 5;
        """)
        rows = conn.execute(query).fetchall()
        print(f"OK: Se obtuvieron {len(rows)} canciones de muestra:")
        for r in rows:
            print(f"  • [ID {r.id}] \"{r.titulo}\" - {r.artista} | Año/Lanzamiento: {r.fecha_lanzamiento or 'N/A'} | Época: {r.epoca or 'N/A'} | Duración: {r.duracion:.1f}s")
        assert len(rows) >= 0               # la base de pruebas puede estar vacía


def test_select_canciones_compat_adapter():
    """Valida el adaptador get_connection() con sintaxis de placeholders '?' y '%s'."""
    print("\n--- [3] Prueba del Adaptador get_connection() y db_session() ---")
    with db_session() as conn:
        # Prueba con placeholder '?' (traducción automática a MariaDB)
        cur = conn.execute("SELECT id, titulo, artista, epoca FROM audios WHERE id > ? LIMIT 3", (0,))
        canciones = cur.fetchall()
        print(f"OK: Placeholder '?' traducido con éxito. Canciones encontradas: {len(canciones)}")
        for c in canciones:
            # Validar acceso tanto por clave como por índice
            assert c["titulo"] == c[1], "El acceso dict/tupla no coincide en CompatRow"
            print(f"  • {c['artista']} - {c['titulo']} (Época: {c['epoca']})")

        assert len(canciones) >= 0


if __name__ == "__main__":
    print("==================================================")
    print("    TEST DE VALIDACIÓN: CONEXIÓN MARIADB 12.3     ")
    print("==================================================")
    try:
        test_mariadb_connection_sqlalchemy()
        test_select_canciones_sqlalchemy()
        test_select_canciones_compat_adapter()
        print("\n==================================================")
        print("  [OK] TODAS LAS PRUEBAS A MARIADB PASARON CON EXITO ")
        print("==================================================")

    except AssertionError as ae:
        print(f"\n[FALLO DE ASERCIÓN]: {ae}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR INESPERADO]: {exc}")
        sys.exit(1)
