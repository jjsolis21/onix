"""Las locuciones (voces del Locutor IA y voice tracks) van en su propia sección, no en la Biblioteca Musical.

Estas pruebas ESCRIBEN en la base (insertan y borran locuciones): se omiten solas si la base es la real (`onix_db`).
Para correrlas: DB_NAME=onix_test (una base de pruebas con el esquema de ONIX).
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.database import DB_NAME, get_connection
from backend.paths import MEDIA_ROOT

pytestmark = pytest.mark.skipif(DB_NAME == "onix_db", reason="escribe en la base: no se prueba contra la base real (usa DB_NAME=onix_test)")

PREFIJO = "TEST-LOC"


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _limpiar():
    yield
    c = get_connection()
    ids = [r[0] for r in c.execute("SELECT id FROM audios WHERE titulo LIKE ?", (PREFIJO + "%",)).fetchall()]
    for i in ids:
        c.execute("UPDATE locutor_intervenciones SET audio_id = NULL WHERE audio_id = ?", (i,))
        c.execute("DELETE FROM locutor_intervenciones WHERE texto LIKE ?", (PREFIJO + "%",))
        c.execute("DELETE FROM audios WHERE id = ?", (i,))
    c.commit()
    c.close()


def _ahora(dias=0):
    return (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")


def _locucion(c, titulo, ruta, dias=0):
    f = MEDIA_ROOT / ruta
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio, created_at)"
                     " VALUES (?, 'LOCUTOR IA', 6, ?, 'Locucion', ?)", (f"{PREFIJO} {titulo}", ruta, _ahora(dias))).lastrowid


def test_las_locuciones_no_salen_en_las_listas_de_audios_pero_si_en_su_seccion(client):
    c = get_connection()
    ia = _locucion(c, "ia", "VOCES/test/vt_test_ia.wav")
    vt = _locucion(c, "voicetrack", "VOCES/test/vt_test_op.wav")
    c.execute("INSERT INTO locutor_intervenciones (tipo, estado, persona, voz, texto, audio_id, proveedor_voz) VALUES"
              " ('hora', 'al_aire', 'LOCUTOR IA', 'fish:abc', ?, ?, 'fish')", (f"{PREFIJO} Son las diez de la mañana", ia))
    c.commit(); c.close()

    # No están en la lista general (Biblioteca Musical, Studio, playlists…) ni en los conteos
    ids = [x["id"] for x in client.get("/api/v1/audios", params={"sin_tipo": "Cuña", "limit": 50000}).json()["data"]]
    assert ia not in ids and vt not in ids
    assert "Locucion" not in client.get("/api/v1/biblioteca/conteos").json()
    # …pero se pueden pedir por tipo o por id
    assert ia in [x["id"] for x in client.get("/api/v1/audios", params={"tipo": "Locucion", "limit": 50000}).json()["data"]]
    assert ia in [x["id"] for x in client.get("/api/v1/audios", params={"ids": str(ia)}).json()["data"]]

    # Su sección: con origen y texto
    r = client.get("/api/v1/locuciones", params={"q": PREFIJO.lower(), "limit": 500}).json()
    por_id = {x["id"]: x for x in r["data"]}
    assert por_id[ia]["origen"] == "ia" and "Son las diez" in por_id[ia]["texto"] and por_id[ia]["interv_tipo"] == "hora"
    assert por_id[vt]["origen"] == "voicetrack"
    assert r["resumen"]["ia"] >= 1 and r["resumen"]["voicetrack"] >= 1
    solo_ia = [x["id"] for x in client.get("/api/v1/locuciones", params={"origen": "ia", "q": PREFIJO.lower(), "limit": 500}).json()["data"]]
    assert ia in solo_ia and vt not in solo_ia


def test_borrar_una_locucion_y_limpiar_las_viejas(client):
    c = get_connection()
    vieja = _locucion(c, "vieja", "VOCES/test/vieja.wav", dias=20)
    nueva = _locucion(c, "nueva", "VOCES/test/nueva.wav")
    lista = _locucion(c, "lista", "VOCES/test/lista.wav", dias=20)
    c.execute("INSERT INTO locutor_intervenciones (tipo, estado, persona, texto, audio_id) VALUES ('hora', 'lista', 'LOCUTOR IA', ?, ?)",
              (f"{PREFIJO} espera", lista))
    c.commit(); c.close()
    # una lista para salir al aire no se borra
    assert client.delete(f"/api/v1/locuciones/{lista}").status_code == 409
    # limpiar: borra la vieja (y su archivo), deja la nueva y la que espera salir
    r = client.post("/api/v1/locuciones/limpiar", json={"dias": 7}).json()
    assert r["borradas"] >= 1 and r["saltadas"] >= 1
    assert not (MEDIA_ROOT / "VOCES/test/vieja.wav").exists() and (MEDIA_ROOT / "VOCES/test/nueva.wav").exists()
    ids = [x["id"] for x in client.get("/api/v1/locuciones", params={"q": PREFIJO.lower(), "limit": 500}).json()["data"]]
    assert vieja not in ids and nueva in ids and lista in ids
    # borrar una a mano
    assert client.delete(f"/api/v1/locuciones/{nueva}").status_code == 200
    assert not (MEDIA_ROOT / "VOCES/test/nueva.wav").exists()
    assert client.delete(f"/api/v1/locuciones/{nueva}").status_code == 404
    assert client.post("/api/v1/locuciones/limpiar", json={"dias": 0}).status_code == 422
