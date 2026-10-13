"""Identificadores: jingles, sweepers y tips con categorías propias."""

import pytest
from fastapi.testclient import TestClient

from test_relojes_api import subir


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_categorias_de_fabrica(client):
    c = client.get("/api/v1/ident/categorias").json()
    assert set(c) >= {"Efecto", "Sweeper", "Tips"}
    assert "Stinger" in [x["nombre"] for x in c["Efecto"]]
    assert [x["orden"] for x in c["Efecto"]] == sorted(x["orden"] for x in c["Efecto"])


def test_agregar_asignar_filtrar_renombrar_y_borrar(client, tmp_path):
    cat = client.post("/api/v1/ident/categorias", json={"tipo_audio": "Efecto", "nombre": "Navidad", "comentario": "diciembre"})
    assert cat.status_code == 201, cat.text
    cid = cat.json()["id"]
    assert client.post("/api/v1/ident/categorias", json={"tipo_audio": "Efecto", "nombre": "NAVIDAD"}).status_code == 409

    a = subir(client, tmp_path, "JINGLE NAVIDEÑO", tipo="Efecto")
    b = subir(client, tmp_path, "JINGLE SUELTO", tipo="Efecto")
    assert client.put("/api/v1/ident/audios", json={"ids": [a], "categoria": "navidad"}).json()["categoria"] == "Navidad"
    assert client.put("/api/v1/ident/audios", json={"ids": [a], "categoria": "Inventada"}).status_code == 422
    assert client.put("/api/v1/ident/audios", json={"ids": [a], "categoria": "Entrada de programa"}).status_code == 422   # es de Sweeper
    client.put("/api/v1/ident/audios", json={"ids": [a], "categoria": "Navidad"})

    lista = client.get("/api/v1/ident/audios", params={"tipo": "Efecto", "categoria": "Navidad"}).json()
    assert [x["id"] for x in lista["data"]] == [a] and lista["total"] == 1
    sin = client.get("/api/v1/ident/audios", params={"tipo": "Efecto", "categoria": "__sin", "q": "jingle sueltO"}).json()
    assert [x["id"] for x in sin["data"]] == [b]

    # Un reloj que filtra por la categoría sigue al renombrarla
    rid = client.post("/api/v1/relojes", json={"nombre": "RELOJ IDENT", "filas": [
        {"tipo_audio": "Efecto", "filtros": {"ident": "navidad"}}]}).json()["id"]
    filtros = lambda: next(r for r in client.get("/api/v1/relojes").json() if r["id"] == rid)["filas"][0]["filtros"]
    assert filtros() == {"ident": "NAVIDAD"}
    assert client.put(f"/api/v1/ident/categorias/{cid}", json={"nombre": "Fiestas"}).status_code == 200
    assert [x["id"] for x in client.get("/api/v1/ident/audios", params={"tipo": "Efecto", "categoria": "Fiestas"}).json()["data"]] == [a]
    assert filtros() == {"ident": "FIESTAS"}
    client.delete(f"/api/v1/relojes/{rid}")

    # Con audios no se borra a menos que se pida quitarla de ellos
    assert client.delete(f"/api/v1/ident/categorias/{cid}").status_code == 409
    r = client.delete(f"/api/v1/ident/categorias/{cid}", params={"quitar": "true"})
    assert r.status_code == 200 and r.json()["quitada_de"] == 1
    assert client.get("/api/v1/ident/audios", params={"tipo": "Efecto", "categoria": "__sin", "q": "navideno"}).json()["total"] == 1


def test_mover_categoria(client):
    efectos = client.get("/api/v1/ident/categorias").json()["Efecto"]
    primero, segundo = efectos[0]["id"], efectos[1]["id"]
    client.post(f"/api/v1/ident/categorias/{primero}/mover", json={"dir": 1})
    nuevo = [x["id"] for x in client.get("/api/v1/ident/categorias").json()["Efecto"]]
    assert nuevo.index(primero) == 1 and nuevo.index(segundo) == 0
    client.post(f"/api/v1/ident/categorias/{primero}/mover", json={"dir": -1})     # deja el orden como estaba
