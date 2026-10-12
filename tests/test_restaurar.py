"""Restaurar la base de datos desde un respaldo (scripts/restaurar_respaldo.py)."""

import sqlite3

import pytest

from restaurar_respaldo import restaurar, verificar


def _base(path, valor):
    from init_db import initialize_database
    initialize_database(str(path))
    conn = sqlite3.connect(path)
    conn.execute("INSERT OR REPLACE INTO config VALUES ('marca', ?)", (valor,))
    conn.commit()
    conn.close()


def _marca(path):
    conn = sqlite3.connect(path)
    v = conn.execute("SELECT valor FROM config WHERE clave = 'marca'").fetchone()[0]
    conn.close()
    return v


def test_restaura_y_guarda_la_base_anterior(tmp_path):
    actual, copia = tmp_path / "radio_core.db", tmp_path / "onix-2026-10-12_0300.db"
    _base(actual, "HOY")
    _base(copia, "AYER")
    (tmp_path / "radio_core.db-wal").write_bytes(b"basura")       # diario viejo: debe borrarse
    anterior = restaurar(copia, actual)
    assert _marca(actual) == "AYER" and _marca(anterior) == "HOY"
    assert not (tmp_path / "radio_core.db-wal").exists()


def test_no_restaura_algo_que_no_es_de_onix(tmp_path):
    actual = tmp_path / "radio_core.db"
    _base(actual, "HOY")
    falsa = tmp_path / "otra.db"
    sqlite3.connect(falsa).execute("CREATE TABLE x (a)").connection.commit()
    basura = tmp_path / "basura.db"
    basura.write_bytes(b"no soy sqlite" * 100)
    for mala in (falsa, basura, tmp_path / "no-existe.db"):
        with pytest.raises(ValueError):
            restaurar(mala, actual)
    assert _marca(actual) == "HOY"


def test_verificar_acepta_un_respaldo_real(client_respaldo):
    verificar(client_respaldo)


@pytest.fixture
def client_respaldo():
    from fastapi.testclient import TestClient
    from backend.api.api_server import app
    from backend.api.respaldos import ajustes
    with TestClient(app) as c:
        nombre = c.post("/api/v1/respaldos").json()["archivo"]
    return ajustes()["carpeta"] / nombre
