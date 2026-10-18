"""Studio muestra las cuñas de cada tanda: el endpoint devuelve la lista real, en el orden en que saldrán."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_slot_invalido_o_minuto_fuera_de_rango(client):
    assert client.get("/api/v1/studio/tanda", params={"slot": "mañana", "minuto": 15}).status_code == 422
    assert client.get("/api/v1/studio/tanda", params={"slot": "2026-10-18-10", "minuto": 75}).status_code == 422


def test_tanda_sin_pautas_devuelve_lista_vacia(client):
    r = client.get("/api/v1/studio/tanda", params={"slot": "2026-10-18-10", "minuto": 15})
    assert r.status_code == 200
    d = r.json()
    assert d["minuto"] == 15 and d["cunas"] == [] and d["duracion"] == 0
