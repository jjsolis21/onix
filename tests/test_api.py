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


@pytest.mark.skipif(not __import__("backend.audio.loudness", fromlist=["x"]).find_ffmpeg(),
                    reason="ffmpeg no instalado")
def test_normalizador_mide_lo_subido(client, tmp_path):
    data = subir_cancion(client, tmp_path, "Para Medir", seconds=2.0)
    for _ in range(100):
        audio = client.get("/api/v1/audios", params={"busqueda": "PARA MEDIR"}).json()["data"][0]
        if audio["loudness_estado"]:
            break
        time.sleep(0.05)
    assert audio["id"] == data["id"]
    assert audio["loudness_estado"] == "ok"
    assert audio["loudness_lufs"] == pytest.approx(-9.0, abs=1.0)   # tono 440 Hz, pico -6 dBFS
    estado = client.get("/api/v1/normalizacion").json()
    assert estado["ffmpeg"] and estado["config"]["normalizacion_lufs"] == -16


def test_config_normalizacion(client):
    r = client.put("/api/v1/config/motor", json={"normalizacion_activa": False, "normalizacion_lufs": -18})
    assert r.status_code == 200
    assert r.json()["normalizacion_activa"] is False and r.json()["normalizacion_lufs"] == -18
    client.put("/api/v1/config/motor", json={"normalizacion_activa": True, "normalizacion_lufs": -16})


def test_secciones_de_tanda(client, tmp_path):
    secs = client.get("/api/v1/tandas/secciones").json()
    assert [s["nombre"] for s in secs][:3] == ["Identificación", "Cortina de entrada", "Comerciales"]
    nuevas = [{"numero": 1, "nombre": "ID", "opcional": False, "comercial": False},
              {"numero": 3, "nombre": "Comerciales", "opcional": False, "comercial": True},
              {"numero": 9, "nombre": "Cierre", "opcional": True, "comercial": False}]
    assert len(client.put("/api/v1/tandas/secciones", json=nuevas).json()) == 3
    sin_comercial = [{"numero": 1, "nombre": "ID"}]
    assert client.put("/api/v1/tandas/secciones", json=sin_comercial).status_code == 422
    # La pauta guarda su sección
    wav = make_wav(tmp_path / "id.wav", seconds=1.0)
    with open(wav, "rb") as fh:
        aid = client.post("/api/v1/upload", data={"tipo": "pauta"},
                          files={"file": ("id.wav", fh, "audio/wav")}).json()["audio_id"]
    r = client.post("/api/v1/pautas", json={"cliente": "emisora", "audio_id": aid, "seccion": 1,
                                             "matriz": {"1-10": True}})
    assert r.status_code == 201 and r.json()["seccion"] == 1


def test_config_tandas_inmediatas(client):
    r = client.put("/api/v1/config/motor", json={"tandas_inmediatas": "0, 30", "tanda_max_seg": 180})
    assert r.json()["tandas_inmediatas"] == "0,30" and r.json()["tanda_max_seg"] == 180
    r = client.put("/api/v1/config/motor", json={"tandas_inmediatas": ""})
    assert r.json()["tandas_inmediatas"] == ""
    assert client.put("/api/v1/config/motor", json={"tandas_inmediatas": "61"}).status_code == 422


def test_status_trae_version_de_motor_y_servidor(client):
    st = client.get("/api/v1/status").json()
    assert st["version"] == st["version_api"]


def test_raiz_sirve_el_studio_y_studio_redirige(client):
    r = client.get("/")
    assert r.status_code == 200 and "ONIX FM — Studio" in r.text and "/static/studio/studio.js" in r.text
    r = client.get("/studio", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/"
    assert client.get("/static/studio/studio.js").status_code == 200
