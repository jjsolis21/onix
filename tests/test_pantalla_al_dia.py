"""Los archivos de la pantalla se revalidan en cada carga: nadie se queda con un JavaScript viejo guardado en el navegador."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("ruta", [
    "/admin/js/admin-app.js",
    "/admin/sections/02-biblioteca-musical/biblioteca-vista.js",
    "/admin/sections/02-biblioteca-musical/biblioteca-vista.css",
    "/admin/",
])
def test_los_archivos_de_la_pantalla_se_revalidan(client, ruta):
    r = client.get(ruta)
    assert r.status_code == 200, ruta
    assert r.headers.get("cache-control") == "no-cache", ruta


def test_si_no_cambio_el_servidor_responde_sin_cambios(client):
    primera = client.get("/admin/js/admin-app.js")
    etiqueta = primera.headers.get("etag")
    assert etiqueta, "el servidor debe enviar su marca de versión (ETag)"
    r = client.get("/admin/js/admin-app.js", headers={"If-None-Match": etiqueta})
    assert r.status_code == 304                              # no vuelve a descargar el archivo


def test_la_api_no_se_ve_afectada(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.headers.get("cache-control") != "no-cache"      # solo la pantalla se revalida; la API no
