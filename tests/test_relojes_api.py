"""API de relojes, grilla y programa."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def subir(client, tmp_path, titulo, tipo="Musica", cat2=None, seconds=3.0):
    wav = make_wav(tmp_path / f"{titulo}.wav", seconds=seconds)
    data = {"titulo": titulo, "artista": f"ART {titulo}", "tipo_audio": tipo}
    if cat2:
        data["cat2"] = cat2
    with open(wav, "rb") as fh:
        r = client.post("/api/v1/audios", data=data, files={"file": (wav.name, fh, "audio/wav")})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_crud_de_reloj(client, tmp_path):
    jid = subir(client, tmp_path, "ID RELOJ", tipo="Efecto")
    subir(client, tmp_path, "EXITO RELOJ", cat2="EXITO")
    r = client.post("/api/v1/relojes", json={
        "nombre": "Mañana", "color": "#ee9b00",
        "filas": [{"tipo_audio": "efecto", "audio_id": jid},
                  {"tipo_audio": "Musica", "filtros": {"cat2": "exito", "basura": "x"}}],
    })
    assert r.status_code == 201, r.text
    reloj = r.json()
    assert reloj["filas"][0]["tipo_audio"] == "Efecto"
    assert reloj["filas"][1]["filtros"] == {"cat2": "EXITO"}          # normaliza y limpia

    assert client.post("/api/v1/relojes", json={"nombre": "Mañana"}).status_code == 409
    assert client.post("/api/v1/relojes", json={"nombre": "X", "filas": [{"tipo_audio": "Cuña"}]}).status_code == 422

    vista = client.get(f"/api/v1/relojes/{reloj['id']}/vista").json()
    assert [f["candidatos"] for f in vista["filas"]] == [1, 1]

    copia = client.post(f"/api/v1/relojes/{reloj['id']}/duplicar").json()
    assert copia["nombre"] == "Mañana (copia)" and len(copia["filas"]) == 2

    r = client.put(f"/api/v1/relojes/{copia['id']}", json={"nombre": "Tarde", "filas": []})
    assert r.status_code == 200 and r.json()["filas"] == []
    assert client.delete(f"/api/v1/relojes/{copia['id']}").status_code == 204


def test_grilla(client):
    rid = client.get("/api/v1/relojes").json()[0]["id"]
    r = client.put("/api/v1/relojes/grilla", json={
        "reloj_id": rid, "celdas": [{"dia": d, "hora": h} for d in range(7) for h in range(6, 10)]})
    assert r.status_code == 200 and len(r.json()) == 28
    r = client.put("/api/v1/relojes/grilla", json={"reloj_id": None, "celdas": [{"dia": 0, "hora": 6}]})
    assert len(r.json()) == 27
    assert client.put("/api/v1/relojes/grilla", json={"reloj_id": rid, "celdas": [{"dia": 7, "hora": 0}]}).status_code == 422


def test_programa(client, tmp_path):
    for i in range(5):
        subir(client, tmp_path, f"PROG {i}", seconds=60)
    client.post("/api/v1/programa/regenerar")
    for _ in range(40):
        prog = client.get("/api/v1/programa").json()
        if prog["items"]:
            break
        time.sleep(0.05)
    assert prog["items"] and prog["items"][0]["hora"]
    uid = prog["items"][0]["uid"]
    client.post(f"/api/v1/programa/{uid}/quitar")
    time.sleep(0.2)
    assert uid not in [i["uid"] for i in client.get("/api/v1/programa").json()["items"]]


def test_config_planificador_y_salida_cartuchera(client):
    r = client.put("/api/v1/config/motor", json={"horas_planificadas": 3, "aleatoriedad": 0})
    assert r.json()["horas_planificadas"] == 3 and r.json()["aleatoriedad"] == 0
    r = client.get("/api/v1/audio/dispositivos").json()
    assert r["salidas"]["cartuchera"] == -2
    assert client.put("/api/v1/audio/salidas", json={"cartuchera": 2}).json()["salidas"]["cartuchera"] == 2
    client.put("/api/v1/audio/salidas", json={"cartuchera": -2})
    client.put("/api/v1/config/motor", json={"horas_planificadas": 2, "aleatoriedad": 3})


def test_eventos_a_hora_fija_crud(client):
    rid = client.post("/api/v1/relojes", json={"nombre": "Para evento", "filas": [{"tipo_audio": "Musica"}]}).json()["id"]
    r = client.post("/api/v1/relojes/eventos", json={"nombre": "Noticias", "reloj_id": rid, "hora": "13:05",
                                                     "dias": [1, 2, 3, 4, 5], "inmediato": True})
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["hora"] == "13:05" and ev["dias"] == [1, 2, 3, 4, 5] and ev["reloj"] == "Para evento"
    assert client.post("/api/v1/relojes/eventos", json={"nombre": "X", "reloj_id": rid, "hora": "25:00"}).status_code == 422
    assert client.post("/api/v1/relojes/eventos", json={"nombre": "X", "reloj_id": rid, "hora": "10:00",
                                                        "dias": []}).status_code == 422
    r = client.put(f"/api/v1/relojes/eventos/{ev['id']}", json={**ev, "hora": "13:10", "fecha_fin": "2026-12-31"})
    assert r.json()["hora"] == "13:10"
    assert any(e["id"] == ev["id"] for e in client.get("/api/v1/relojes/eventos").json())
    assert client.delete(f"/api/v1/relojes/eventos/{ev['id']}").status_code == 204
    assert client.delete(f"/api/v1/relojes/eventos/{ev['id']}").status_code == 404
