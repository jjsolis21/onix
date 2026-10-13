"""La cola del operador por la API: elegir un lugar, subir, bajar, pasar del programa a la cola y quitar."""

import time

import pytest
from fastapi.testclient import TestClient

from test_relojes_api import subir


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def cola(client):
    return client.get("/api/v1/programa").json()["cola"]


def esperar(client, condicion, intentos=60):
    for _ in range(intentos):
        c = cola(client)
        if condicion(c):
            return c
        time.sleep(0.05)
    return cola(client)


def test_cola_insertar_mover_y_quitar(client, tmp_path):
    client.post("/api/v1/player/clear_queue")
    a, b, c, d = (subir(client, tmp_path, f"COLA API {n}", seconds=30) for n in "ABCD")
    for x in (a, b, c):
        assert client.post("/api/v1/player/queue", json={"audio_id": x}).status_code == 200
    assert [q["id"] for q in esperar(client, lambda q: len(q) == 3)] == [a, b, c]

    # cola_insertar respeta la posición
    assert client.post("/api/v1/player/cola_insertar", json={"audio_id": d, "posicion": 1}).status_code == 200
    q = esperar(client, lambda q: len(q) == 4)
    assert [x["id"] for x in q] == [a, d, b, c]

    uid_c = next(x["uid"] for x in q if x["id"] == c)
    client.post("/api/v1/player/cola_mover", json={"uid": uid_c, "posicion": 0})
    assert [x["id"] for x in esperar(client, lambda q: q[0]["id"] == c)] == [c, a, d, b]
    client.post("/api/v1/player/cola_quitar", json={"uid": uid_c})
    assert [x["id"] for x in esperar(client, lambda q: len(q) == 3)] == [a, d, b]
    assert client.post("/api/v1/player/cola_mover", json={"posicion": 0}).status_code == 422     # falta uid
    client.post("/api/v1/player/clear_queue")
