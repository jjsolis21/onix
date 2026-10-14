"""Panel de problemas del Studio."""

import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def test_detecta_archivo_faltante_y_respaldo_vacio(client):
    from backend.paths import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT INTO audios (titulo, artista, archivo_path, tipo_audio, duracion)"
                 " VALUES ('FANTASMA', 'NADIE', 'library/musica/no_existe.mp3', 'Musica', 200)")
    conn.commit()
    conn.close()
    r = client.get("/api/v1/problemas", params={"forzar": True}).json()
    claves = {p["clave"]: p for p in r["activos"]}
    # Los ejemplos están acotados y otras pruebas dejan audios sin archivo en la misma base: se mira el conteo
    assert int(claves["archivos"]["titulo"].split()[0]) >= 1
    assert claves["archivos"]["ejemplos"]
    assert "respaldo" in claves and "biblioteca" in claves
    assert "version" not in claves                        # motor y servidor al día


def test_eventos_del_motor_quedan_registrados(client):
    from backend.api import api_server
    api_server.engine.bus.emit("aire_muerto", {"motivo": "silencio", "accion": "siguiente",
                                               "msg": "«X» llevaba 10s en silencio"})
    api_server.engine.bus.emit("track_started", {"audio_id": 1})     # no es un problema
    r = client.get("/api/v1/problemas").json()
    assert r["recientes"][0]["nivel"] == "critico" and "AIRE MUERTO" in r["recientes"][0]["msg"]
    assert r["no_vistos"] >= 1
    client.post("/api/v1/problemas/vistos")
    assert client.get("/api/v1/problemas").json()["no_vistos"] == 0
