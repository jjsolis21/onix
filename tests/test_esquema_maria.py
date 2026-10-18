"""Una instalación nueva: el creador de esquema deja una base vacía igual que la de pruebas."""

import os
import subprocess
import sys
from pathlib import Path

import pymysql
import pytest

from backend.database import DB_HOST, DB_PASSWORD, DB_PORT, DB_USER

RAIZ = Path(__file__).resolve().parent.parent
BASE_NUEVA = "onix_test_esquema_" + os.environ.get("PYTEST_XDIST_WORKER", "solo")


def _conectar(base=None):
    return pymysql.connect(host=DB_HOST, port=int(DB_PORT), user=DB_USER, password=DB_PASSWORD, database=base, autocommit=True)


def _estructura(base):
    c = _conectar()
    try:
        cur = c.cursor()
        cur.execute("SELECT table_name, column_name, column_type, is_nullable, collation_name FROM information_schema.columns"
                    " WHERE table_schema = %s ORDER BY 1, ordinal_position", (base,))
        columnas = cur.fetchall()
        cur.execute("SELECT table_name, index_name, non_unique, GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics"
                    " WHERE table_schema = %s GROUP BY table_name, index_name, non_unique ORDER BY 1, 2", (base,))
        indices = cur.fetchall()
        cur.execute("SELECT trigger_name, event_manipulation, action_timing FROM information_schema.triggers"
                    " WHERE trigger_schema = %s ORDER BY 1", (base,))
        triggers = cur.fetchall()
        return columnas, indices, triggers
    finally:
        c.close()


@pytest.fixture
def base_vacia():
    c = _conectar()
    c.cursor().execute(f"DROP DATABASE IF EXISTS `{BASE_NUEVA}`")
    c.cursor().execute(f"CREATE DATABASE `{BASE_NUEVA}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    yield BASE_NUEVA
    c.cursor().execute(f"DROP DATABASE IF EXISTS `{BASE_NUEVA}`")
    c.close()


def _correr(base, codigo):
    entorno = {**os.environ, "DB_NAME": base}
    return subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=120)


def test_instalacion_nueva_queda_igual_que_la_base_de_pruebas(base_vacia):
    r = _correr(base_vacia, "from backend.esquema_maria import asegurar_esquema; print('NUEVA' if asegurar_esquema() else 'EXISTIA')")
    assert r.returncode == 0, r.stderr[-800:]
    assert "NUEVA" in r.stdout
    assert _estructura(base_vacia) == _estructura(os.environ["DB_NAME"]), "el esquema creado difiere del de la base de pruebas"

    # trae las filas de fábrica (categorías, secciones de tanda, configuración…)
    c = _conectar(base_vacia)
    cur = c.cursor()
    for tabla in ("tanda_secciones", "config", "ident_categorias", "paletas", "config_categorias_def"):
        cur.execute(f"SELECT COUNT(*) FROM `{tabla}`")
        assert cur.fetchone()[0] > 0, tabla
    cur.execute("SELECT COUNT(*) FROM audios")
    assert cur.fetchone()[0] == 0
    # Una instalación nueva NO trae géneros, rotaciones ni subgéneros de adorno: solo existen los que el usuario crea.
    # Lo único precargado son las voces oficiales.
    cur.execute("SELECT d.nombre_interno, v.valor FROM config_categorias_valores v JOIN config_categorias_def d ON d.id = v.cat_id")
    por = {}
    for categoria, valor in cur.fetchall():
        por.setdefault(categoria, set()).add(valor)
    assert por.keys() == {"voz"}, por
    assert por["voz"] == {"Hombre", "Mujer", "Dúo", "Grupo", "FT", "Genérico"}
    c.close()


def test_si_la_base_ya_tiene_esquema_no_se_toca(base_vacia):
    r = _correr(base_vacia, "from backend.esquema_maria import asegurar_esquema; asegurar_esquema()")
    assert r.returncode == 0, r.stderr[-800:]
    c = _conectar(base_vacia)
    cur = c.cursor()
    cur.execute("INSERT INTO audios (titulo, artista, archivo_path, tipo_audio) VALUES ('X', 'Y', 'library/x.mp3', 'Musica')")
    c.close()
    r = _correr(base_vacia, "from backend.esquema_maria import asegurar_esquema; print('NUEVA' if asegurar_esquema() else 'EXISTIA')")
    assert r.returncode == 0 and "EXISTIA" in r.stdout, r.stderr[-800:]
    c = _conectar(base_vacia)
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM audios")
    assert cur.fetchone()[0] == 1                              # el dato sigue ahí
    c.close()
    # y crear_esquema se niega a pisar una base con tablas
    r = _correr(base_vacia, "from backend.esquema_maria import crear_esquema; crear_esquema()")
    assert r.returncode != 0 and "ya tiene tablas" in r.stderr
