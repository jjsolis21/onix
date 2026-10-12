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


def test_biblioteca_sin_cunas_artistas_y_renombrar_categoria(client, tmp_path):
    cancion = subir_cancion(client, tmp_path, "Tema Categoria", artista="ARTISTA CAT")
    wav = make_wav(tmp_path / "spot.wav", seconds=2.0)
    with open(wav, "rb") as fh:
        r = client.post("/api/v1/audios", data={"titulo": "SPOT", "artista": "ANUNCIANTE", "tipo_audio": "Cuña"},
                        files={"file": ("spot.wav", fh, "audio/wav")})
    assert r.status_code == 201
    musica = client.get("/api/v1/audios", params={"sin_tipo": "Cuña", "limit": 50000}).json()["data"]
    assert all(a["tipo_audio"] != "Cuña" for a in musica) and any(a["id"] == cancion["id"] for a in musica)
    assert any(a["artista"] == "ARTISTA CAT" for a in client.get("/api/v1/artistas", params={"tipo": "Musica"}).json())
    assert not any(a["artista"] == "ANUNCIANTE" for a in client.get("/api/v1/artistas", params={"tipo": "Musica"}).json())
    # Renombrar un valor de categoría actualiza las canciones que lo usan
    v = client.post("/api/v1/config/biblioteca/valores", json={"nombre_interno": "cat1", "valor": "VIEJO"}).json()["data"]
    client.put(f"/api/v1/audios/{cancion['id']}", json={"cat1": "VIEJO"})
    r = client.put(f"/api/v1/config/biblioteca/valores/{v['id']}", json={"valor": "NUEVO", "color": "#123456"})
    assert r.status_code == 200 and r.json()["audios_actualizados"] == 1
    a = next(x for x in client.get("/api/v1/audios", params={"limit": 50000}).json()["data"] if x["id"] == cancion["id"])
    assert a["cat1"] == "NUEVO"
    # Borrar y volver a agregar el mismo valor no choca
    assert client.delete(f"/api/v1/config/biblioteca/valores/{v['id']}").status_code in (200, 204)
    assert client.post("/api/v1/config/biblioteca/valores", json={"nombre_interno": "cat1", "valor": "NUEVO"}).status_code == 201


def test_bandeja_de_entrada_y_edicion_masiva(client, tmp_path):
    a = subir_cancion(client, tmp_path, "Nueva Uno")
    b = subir_cancion(client, tmp_path, "Nueva Dos")
    lista = {x["id"]: x for x in client.get("/api/v1/audios", params={"limit": 50000}).json()["data"]}
    assert lista[a["id"]]["aprobada"] == 0                       # entra a la Bandeja
    # Aprobar sin clasificar: no se aprueba
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"], b["id"]], "cambios": {"aprobada": 1}}).json()
    assert set(r["sin_clasificar"]) == {a["id"], b["id"]}
    # Clasificar a las dos de una vez y aprobar
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"], b["id"]],
                                                     "cambios": {"cat1": "Pop", "cat2": "Recurrente", "energia": 4,
                                                                 "origen": "NACIONAL", "aprobada": 1}})
    assert r.status_code == 200 and r.json()["sin_clasificar"] == []
    lista = {x["id"]: x for x in client.get("/api/v1/audios", params={"limit": 50000}).json()["data"]}
    assert lista[a["id"]]["aprobada"] == 1 and lista[b["id"]]["cat1"] == "Pop" and lista[b["id"]]["energia"] == 4
    assert client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"]], "cambios": {"energia": 9}}).status_code == 422
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [b["id"]], "accion": "desactivar"})
    assert r.json()["actualizados"] == 1
    assert b["id"] not in {x["id"] for x in client.get("/api/v1/audios", params={"limit": 50000}).json()["data"]}
