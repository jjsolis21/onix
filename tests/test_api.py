"""
API completa con TestClient (arranca el lifespan: crea la BD, arranca el
motor en modo mock). Usa la BD y la carpeta de medios temporales de conftest.
"""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def subir_cancion(client, tmp_path, titulo, artista="ARTISTA", seconds=2.0):
    wav = make_wav(tmp_path / f"{titulo}.wav", seconds=seconds)
    with open(wav, "rb") as fh:
        r = client.post(
            "/api/v1/audios",
            data={"titulo": titulo, "artista": artista},
            files={"file": (wav.name, fh, "audio/wav")},
        )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_subida_lee_duracion(client, tmp_path):
    data = subir_cancion(client, tmp_path, "Tema Uno", seconds=3.0)
    audios = client.get("/api/v1/audios", params={"busqueda": "TEMA UNO"}).json()["data"]
    assert audios[0]["id"] == data["id"]
    assert audios[0]["duracion"] == 3
    assert audios[0]["tipo_audio"] == "Musica"


def test_config_motor_tandas(client):
    r = client.put("/api/v1/config/motor", json={"tandas_minutos": "45, 15,15"})
    assert r.status_code == 200
    assert r.json()["tandas_minutos"] == "15,45"
    assert client.get("/api/v1/status").json()["tandas_minutos"] == [15, 45]

    r = client.put("/api/v1/config/motor", json={"tandas_minutos": "15,75"})
    assert r.status_code == 422


def test_pauta_desde_subida_comercial(client, tmp_path):
    wav = make_wav(tmp_path / "cuna.wav", seconds=1.0)
    with open(wav, "rb") as fh:
        r = client.post("/api/v1/upload", data={"tipo": "pauta"},
                        files={"file": ("cuna.wav", fh, "audio/wav")})
    assert r.status_code == 201, r.text
    audio_id = r.json()["audio_id"]

    r = client.post("/api/v1/pautas", json={
        "cliente": "ferreteria", "audio_id": audio_id, "rubro": "hogar",
        "matriz": {"1-10": True}, "menciones_diarias": 3,
    })
    assert r.status_code == 201, r.text
    assert r.json()["cliente"] == "FERRETERIA"


def test_play_pone_al_aire(client, tmp_path):
    data = subir_cancion(client, tmp_path, "Al Aire", seconds=5.0)
    r = client.post("/api/v1/player/play", json={"audio_id": data["id"]})
    assert r.status_code == 200
    for _ in range(40):
        st = client.get("/api/v1/status").json()
        if st["current_track"]:
            break
        time.sleep(0.05)
    assert st["current_track"]["id"] == data["id"]
    client.post("/api/v1/player/stop")


def test_comando_invalido(client):
    assert client.post("/api/v1/player/explotar").status_code == 404
    r = client.post("/api/v1/player/play", json={"audio_id": 999999})
    assert r.status_code == 422


def test_error_de_validacion_devuelve_422_no_500(client):
    r = client.post("/api/v1/pautas", json={
        "cliente": "x", "fecha_inicio": "2026-12-01", "fecha_fin": "2026-01-01",
    })
    assert r.status_code == 422
