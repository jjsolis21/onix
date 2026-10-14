"""«Publicar metadata» por audio (como Soho): qué texto se muestra al aire."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.audio.publicar import modo_efectivo, texto_publicado
from backend.paths import DB_PATH


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def _db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _audio(c, titulo, artista, tipo, **cols):
    campos = {"titulo": titulo, "artista": artista, "tipo_audio": tipo, "duracion": 30,
              "archivo_path": f"library/{titulo}.mp3", **cols}
    return c.execute(f"INSERT INTO audios ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                     list(campos.values())).lastrowid


def test_modo_por_defecto_segun_el_tipo():
    assert modo_efectivo("Musica", "") == "actualizar"
    assert modo_efectivo("Cuña", "") == "no_actualizar"
    assert modo_efectivo("Efecto", None) == "no_actualizar"
    assert modo_efectivo("Cuña", "actualizar") == "actualizar"
    assert modo_efectivo("Musica", "no_actualizar") == "no_actualizar"


def test_auto_usa_los_datos_del_audio_y_lo_escrito_manda():
    base = {"titulo": "VERANO", "artista": "PEPSI", "tipo_audio": "Cuña", "meta_modo": "actualizar"}
    t = texto_publicado({**base, "meta_titulo": "AUTO", "meta_artista": ""})
    assert (t["titulo"], t["artista"], t["texto"], t["actualizar"]) == ("VERANO", "PEPSI", "PEPSI - VERANO", True)
    t = texto_publicado({**base, "meta_titulo": "Refresca tu verano", "meta_artista": "Pepsi Venezuela", "meta_extra1": "pepsi.com"})
    assert t["texto"] == "Pepsi Venezuela - Refresca tu verano" and t["extra1"] == "pepsi.com"


def test_guardar_en_el_audio_y_la_ficha_lo_devuelve(client):
    c = _db()
    aid = _audio(c, "META CUNA", "ANUNCIANTE", "Cuña")
    c.commit(); c.close()
    r = client.put(f"/api/v1/audios/{aid}", json={"meta_modo": "actualizar", "meta_titulo": "Mi eslogan",
                                                  "meta_artista": "", "meta_extra1": "www.cliente.com"})
    assert r.status_code == 200, r.text
    f = client.get(f"/api/v1/biblioteca/{aid}/ficha").json()
    assert (f["meta_modo"], f["meta_titulo"], f["meta_extra1"]) == ("actualizar", "Mi eslogan", "www.cliente.com")
    assert client.put(f"/api/v1/audios/{aid}", json={"meta_modo": "raro"}).status_code == 422
    # volver a AUTO: vacío
    client.put(f"/api/v1/audios/{aid}", json={"meta_titulo": ""})
    assert client.get(f"/api/v1/biblioteca/{aid}/ficha").json()["meta_titulo"] == ""


def test_nowplaying_mantiene_el_texto_de_la_ultima_que_actualizo(client):
    c = _db()
    cancion = _audio(c, "CANCION PUB", "ARTISTA PUB", "Musica")
    cuna = _audio(c, "CUNA PUB", "CLIENTE PUB", "Cuña")                    # por defecto: no actualiza
    c.execute("INSERT INTO historial_emision (audio_id, titulo, artista, tipo) VALUES (?, 'CANCION PUB', 'ARTISTA PUB', 'musica')", (cancion,))
    c.execute("INSERT INTO historial_emision (audio_id, titulo, artista, tipo) VALUES (?, 'CUNA PUB', 'CLIENTE PUB', 'pauta')", (cuna,))
    c.commit(); c.close()
    r = client.get("/api/v1/nowplaying").json()
    assert r["publicado"]["texto"] == "ARTISTA PUB - CANCION PUB"           # la cuña no pisó lo publicado
    assert r["actualiza"] is False
