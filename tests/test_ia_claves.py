"""Claves de IA: se guardan sin devolverse, se prueban y se usan en orden con respaldo."""

import pytest
from fastapi.testclient import TestClient

from backend.api.auth import permiso
from backend.ia import proveedores


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def limpio(client):
    for k in client.get("/api/v1/ia/claves").json():
        client.delete(f"/api/v1/ia/claves/{k['id']}")
    proveedores._fallos.clear()
    yield
    proveedores._fallos.clear()


def _alta(client, nombre, proveedor="gemini", clave="CLAVE-SECRETA-1234", **extra):
    r = client.post("/api/v1/ia/claves", json={"nombre": nombre, "proveedor": proveedor, "clave": clave, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_la_clave_nunca_se_devuelve(client):
    k = _alta(client, "Gemini 1")
    assert "clave" not in k and k["clave_fin"].endswith("1234")
    assert "CLAVE-SECRETA" not in client.get("/api/v1/ia/claves").text
    # editar sin clave la conserva
    assert client.put(f"/api/v1/ia/claves/{k['id']}", json={"nombre": "Gemini uno", "proveedor": "gemini", "clave": ""}).status_code == 200
    assert client.get("/api/v1/ia/claves").json()[0]["nombre"] == "Gemini uno"
    assert client.post("/api/v1/ia/claves", json={"nombre": "x", "proveedor": "nada", "clave": "k"}).status_code == 422
    assert client.post("/api/v1/ia/claves", json={"nombre": "x", "proveedor": "gemini", "clave": " "}).status_code == 422


def test_orden_y_mover(client):
    a, b = _alta(client, "A"), _alta(client, "B")
    client.post(f"/api/v1/ia/claves/{b['id']}/mover", json={"dir": -1})
    assert [k["nombre"] for k in client.get("/api/v1/ia/claves").json()] == ["B", "A"]


def test_respaldo_pasa_a_la_siguiente_si_falla(client, monkeypatch):
    _alta(client, "Primera", clave="PRIMERA-AAAA")
    _alta(client, "Segunda", proveedor="groq", clave="SEGUNDA-BBBB")
    llamadas = []

    def falso(url, cab, cuerpo, tiempo=20):
        llamadas.append(url)
        if "generativelanguage" in url:
            assert cab["x-goog-api-key"] == "PRIMERA-AAAA"
            raise RuntimeError("HTTP 429 cuota PRIMERA-AAAA agotada")
        assert cab["Authorization"] == "Bearer SEGUNDA-BBBB"
        return {"choices": [{"message": {"content": " Hola, soy el respaldo "}}]}
    monkeypatch.setattr(proveedores, "http_post", falso)

    r = proveedores.generar_texto("saluda", "eres locutor")
    assert r["texto"] == "Hola, soy el respaldo" and r["proveedor"] == "groq"
    assert "PRIMERA-AAAA" not in r["intentos"][0]["error"]            # la clave se tapa en los errores
    lista = client.get("/api/v1/ia/claves").json()
    assert lista[0]["ultimo_error"] and lista[1]["ultimo_ok"]
    # la fallida se salta un rato: el segundo pedido va directo a la buena
    llamadas.clear()
    proveedores.generar_texto("otra vez")
    assert len(llamadas) == 1 and "groq" in llamadas[0]


def test_si_todas_fallan_lanza_error_para_usar_plantilla(client, monkeypatch):
    _alta(client, "Sola")
    monkeypatch.setattr(proveedores, "http_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin red")))
    with pytest.raises(proveedores.IAError):
        proveedores.generar_texto("hola")
    proveedores._fallos.clear()
    for k in client.get("/api/v1/ia/claves").json():
        client.delete(f"/api/v1/ia/claves/{k['id']}")
    with pytest.raises(proveedores.IAError, match="No hay claves"):
        proveedores.generar_texto("hola")


def test_probar_y_formato_gemini(client, monkeypatch):
    k = _alta(client, "Probar", modelo="gemini-x")

    def falso(url, cab, cuerpo, tiempo=20):
        assert url.endswith("/models/gemini-x:generateContent")
        assert cuerpo["contents"][0]["parts"][0]["text"]
        return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
    monkeypatch.setattr(proveedores, "http_post", falso)
    r = client.post(f"/api/v1/ia/claves/{k['id']}/probar").json()
    assert r["ok"] is True
    monkeypatch.setattr(proveedores, "http_post", lambda *a, **kw: {"candidates": []})
    r = client.post(f"/api/v1/ia/claves/{k['id']}/probar").json()
    assert r["ok"] is False and "vacío" in r["error"]


def test_solo_admin_ve_las_claves():
    for rol in ("programacion", "operador"):
        assert not permiso(rol, "GET", "/api/v1/ia/claves")
        assert not permiso(rol, "POST", "/api/v1/ia/claves/1/probar")
    assert permiso("admin", "GET", "/api/v1/ia/claves")
