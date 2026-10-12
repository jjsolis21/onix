"""Respaldo automático de la base de datos."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_respaldar_ahora_crea_una_copia_valida(client):
    r = client.post("/api/v1/respaldos")
    assert r.status_code == 201, r.text
    info = client.get("/api/v1/respaldos").json()
    copia = Path(info["carpeta"]) / r.json()["archivo"]
    assert info["copias"][0]["archivo"] == copia.name
    conn = sqlite3.connect(copia)
    assert conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] > 0      # trae los datos
    conn.close()
    d = client.get(f"/api/v1/respaldos/{copia.name}")
    assert d.status_code == 200 and d.content[:15] == b"SQLite format 3"


def test_conserva_solo_las_mas_nuevas(client):
    client.put("/api/v1/respaldos/config", json={"conservar": 2})
    for _ in range(4):
        assert client.post("/api/v1/respaldos").status_code == 201
    assert len(client.get("/api/v1/respaldos").json()["copias"]) == 2
    client.put("/api/v1/respaldos/config", json={"conservar": 14})


def test_carpeta_propia_y_validaciones(client, tmp_path):
    destino = tmp_path / "usb" / "onix"
    r = client.put("/api/v1/respaldos/config", json={"carpeta": str(destino)})
    assert r.status_code == 200 and Path(r.json()["carpeta"]) == destino and r.json()["personalizada"]
    assert client.post("/api/v1/respaldos").status_code == 201
    assert any(destino.glob("onix-*.db"))
    assert client.put("/api/v1/respaldos/config", json={"carpeta": "relativa/no"}).status_code == 422
    client.put("/api/v1/respaldos/config", json={"carpeta": ""})              # vuelve a la de ONIX


def test_nombres_raros_no_se_descargan(client):
    assert client.get("/api/v1/respaldos/..%2Fapi.db").status_code == 404
    assert client.get("/api/v1/respaldos/api.db").status_code == 404


def test_respaldo_automatico_solo_si_el_ultimo_es_viejo(client):
    from backend.api import respaldos
    antes = len(respaldos.listar())
    respaldos.revisar_y_respaldar()                       # el último es de hace segundos
    assert len(respaldos.listar()) == antes
