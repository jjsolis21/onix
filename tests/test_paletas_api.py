"""Paletas de la cartuchera: crear, renombrar, achicar y borrar."""

import pytest
from fastapi.testclient import TestClient

from backend.api.auth import permiso
from test_relojes_api import subir


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_las_cinco_de_siempre_se_conservan(client):
    d = client.get("/api/v1/cartuchera").json()
    claves = [p["clave"] for p in d["paletas"]]
    assert claves[:5] == ["jingles", "efectos", "voces", "publicidad", "especiales"]
    assert d["bancos"]["jingles"] % 9 == 0 and d["nombres"]["voces"] == "Voces"


def test_crear_renombrar_cargar_y_borrar_paleta(client, tmp_path):
    r = client.post("/api/v1/paletas", json={"nombre": "Tips genérico"})
    assert r.status_code == 201, r.text
    assert r.json()["slots"] == 9                                   # nace con una página
    clave = r.json()["clave"]
    # nombre repetido (sin importar mayúsculas) se rechaza
    assert client.post("/api/v1/paletas", json={"nombre": "TIPS genérico"}).status_code == 409

    aid = subir(client, tmp_path, "TIP PALETA", tipo="Tips")
    assert client.put(f"/api/v1/cartuchera/{clave}/5", json={"audio_id": aid}).status_code == 200
    assert client.put(f"/api/v1/cartuchera/{clave}/10", json={"audio_id": aid}).status_code == 404   # solo 9 botones
    # agregar la segunda página: ahora existe el botón 10
    assert client.put(f"/api/v1/paletas/{clave}", json={"slots": 18}).status_code == 200
    assert client.put(f"/api/v1/cartuchera/{clave}/10", json={"audio_id": aid}).status_code == 200
    d = client.get("/api/v1/cartuchera").json()
    assert [s["slot"] for s in d["slots"][clave]] == [5, 10] and d["bancos"][clave] == 18

    # Quitar la página borra los botones que quedan fuera
    assert client.put(f"/api/v1/paletas/{clave}", json={"nombre": "Tips", "slots": 9}).status_code == 200
    d = client.get("/api/v1/cartuchera").json()
    assert d["nombres"][clave] == "Tips" and [s["slot"] for s in d["slots"][clave]] == [5]

    assert client.delete(f"/api/v1/paletas/{clave}").status_code == 200
    assert clave not in client.get("/api/v1/cartuchera").json()["bancos"]


def test_el_tope_es_63_botones_de_9_en_9(client):
    clave = client.post("/api/v1/paletas", json={"nombre": "Tope de prueba", "slots": 63}).json()["clave"]
    assert client.get("/api/v1/cartuchera").json()["bancos"][clave] == 63
    assert client.put(f"/api/v1/paletas/{clave}", json={"slots": 72}).status_code == 422     # más de 7 páginas
    assert client.put(f"/api/v1/paletas/{clave}", json={"slots": 15}).status_code == 422     # no es de 9 en 9
    assert client.put(f"/api/v1/paletas/{clave}", json={"slots": 3}).status_code == 422
    assert client.post("/api/v1/paletas", json={"nombre": "Otra", "slots": 64}).status_code == 422
    client.delete(f"/api/v1/paletas/{clave}")


def test_permisos_el_operador_carga_botones_pero_no_crea_paletas():
    assert permiso("operador", "PUT", "/api/v1/cartuchera/jingles/3")
    assert permiso("operador", "DELETE", "/api/v1/cartuchera/jingles/3")
    assert not permiso("operador", "POST", "/api/v1/paletas")
    assert permiso("programacion", "POST", "/api/v1/paletas")
