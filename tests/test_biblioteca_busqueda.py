"""Búsqueda y conteos de la biblioteca para el Studio (decenas de miles de archivos)."""

import pytest
from fastapi.testclient import TestClient

from test_relojes_api import subir


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_busqueda_sin_tildes_ni_mayusculas_y_por_palabras(client, tmp_path):
    aid = subir(client, tmp_path, "Canción Ñandú Búsqueda")
    for texto in ("cancion nandu", "BUSQUEDA", "art canción", "ñandú"):
        ids = [a["id"] for a in client.get("/api/v1/audios", params={"q": texto, "limit": 50}).json()["data"]]
        assert aid in ids, texto
    r = client.get("/api/v1/audios", params={"q": "cancion zzzinexistente"}).json()
    assert r["total"] == 0 and r["data"] == []


def test_varios_tipos_a_la_vez_y_paginacion(client, tmp_path):
    ids = [subir(client, tmp_path, f"IMG {t} PAG", tipo=t) for t in ("Efecto", "Sweeper", "Tips")]
    r = client.get("/api/v1/audios", params={"tipo": "Efecto,Sweeper,Tips", "q": "IMG PAG", "limit": 2}).json()
    assert r["total"] == 3 and len(r["data"]) == 2
    r2 = client.get("/api/v1/audios", params={"tipo": "Efecto,Sweeper,Tips", "q": "IMG PAG", "limit": 2, "offset": 2}).json()
    assert len(r2["data"]) == 1
    assert {a["id"] for a in r["data"] + r2["data"]} == set(ids)
    assert client.get("/api/v1/audios", params={"tipo": "Efecto,Inventado"}).status_code == 422


def test_conteos_y_filtros(client, tmp_path):
    subir(client, tmp_path, "CONTEO MUS", cat2="TIPO-X")
    c = client.get("/api/v1/biblioteca/conteos").json()
    assert c["Musica"] >= 1 and sum(c.values()) >= 1
    f = client.get("/api/v1/biblioteca/filtros", params={"tipo": "Musica"}).json()
    assert "TIPO-X" in f["cat2"] and set(f) == {"cat1", "cat2", "voz"}


def test_busq_se_mantiene_sola_al_editar_y_aguanta_comodines(client, tmp_path):
    import sqlite3
    from backend.paths import DB_PATH
    aid = subir(client, tmp_path, "Nombre Original")
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE audios SET titulo = 'Corazón 100% Ñoño', artista = 'Otro' WHERE id = ?", (aid,))
    con.commit(); con.close()
    def hallar(texto):
        return aid in [a["id"] for a in client.get("/api/v1/audios", params={"q": texto, "limit": 50}).json()["data"]]
    assert hallar("corazon nono") and hallar("100%")
    assert not hallar("nombre original")             # ya no se llama así
    assert not hallar("cora_on")                     # _ y % no son comodines del usuario


def test_pedir_audios_por_id(client, tmp_path):
    a = subir(client, tmp_path, "POR ID UNO")
    b = subir(client, tmp_path, "POR ID DOS")
    r = client.get("/api/v1/audios", params={"ids": f"{a},{b},999999,xx"}).json()
    assert sorted(x["id"] for x in r["data"]) == sorted([a, b])
    assert client.get("/api/v1/audios", params={"ids": "xx"}).json()["data"] == []
