"""Análisis de canciones: ritmo, energía, marcas y búsqueda de datos (sin internet: las respuestas son de mentira)."""

import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from backend.audio import analisis, metadatos, sugerencias
from backend.audio.loudness import find_ffmpeg
from test_relojes_api import subir

FFMPEG = find_ffmpeg()
sin_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="hace falta ffmpeg")


def pista(ruta, bpm=120, seg=60, entra=8, fade=50):
    """Golpes a `bpm`, parte suave al inicio, fuerte desde `entra` y un final que se apaga desde `fade`."""
    sr = 8000
    beat = 60 / bpm
    datos = bytearray()
    for i in range(sr * seg):
        t = i / sr
        amp = 0.12 if t < entra else 0.5
        if t > fade:
            amp = 0.5 * max(0.0, 1 - (t - fade) / 6)
        golpe = math.exp(-(t % beat) * 25)
        s = amp * (golpe * math.sin(2 * math.pi * 90 * t) + 0.15 * math.sin(2 * math.pi * 440 * t))
        datos += struct.pack("<h", int(max(-1, min(1, s)) * 32767))
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(datos))
    return ruta


def test_energia_segun_el_ritmo():
    assert [analisis.energia_de_bpm(b) for b in (60, 80, 100, 120, 150)] == [1, 2, 3, 4, 5]


@sin_ffmpeg
@pytest.mark.parametrize("bpm", [95, 120, 150])
def test_detecta_el_ritmo_y_las_marcas(tmp_path, bpm):
    a = analisis.analizar(pista(tmp_path / "p.wav", bpm=bpm), FFMPEG)
    assert abs(a["bpm"] - bpm) <= 2
    assert a["energia"] == analisis.energia_de_bpm(a["bpm"])
    assert abs(a["cue_intro"] - 8) <= 2.5                 # entra la parte fuerte
    assert abs(a["cue_mezcla"] - 55) <= 3                 # el final se apaga
    assert a["cue_outro"] and 48 <= a["cue_outro"] <= 53  # y empieza ahí


def test_nombre_de_archivo_y_musicbrainz_con_respuesta_de_mentira():
    assert metadatos.desde_nombre("07. Los Tests - Canción Falsa (Official Video).mp3") == {"artista": "Los Tests", "titulo": "Canción Falsa"}
    visto = []

    def http(url):
        visto.append(url)
        return {"recordings": [
            {"id": "x", "score": 62, "title": "Otra", "artist-credit": [{"name": "Nadie"}]},
            {"id": "y", "score": 100, "title": "Canción Falsa", "first-release-date": "2018-03-02",
             "artist-credit": [{"name": "Los Tests"}],
             "releases": [{"title": "Sencillo", "status": "Official", "release-group": {"primary-type": "Single"}},
                          {"title": "El Disco", "status": "Official", "release-group": {"primary-type": "Album"}}]}]}
    r = metadatos.buscar_musicbrainz("Los Tests", "Canción Falsa", http)
    assert (r["album"], r["anio"], r["confianza"]) == ("El Disco", "2018", "alta")
    assert "musicbrainz.org" in visto[0] and "Canci%C3%B3n+Falsa" in visto[0]
    assert metadatos.buscar_musicbrainz("A", "B", lambda u: {"recordings": [{"score": 40, "title": "x"}]}) is None


def test_acoustid_con_respuesta_de_mentira():
    r = metadatos.buscar_acoustid(200, "AQAA", "clave", lambda u: {"status": "ok", "results": [{"score": 0.97, "recordings": [
        {"title": "Reconocida", "artists": [{"name": "Banda"}], "releasegroups": [{"title": "Maxi", "type": "Single"}, {"title": "Disco", "type": "Album"}]}]}]})
    assert (r["titulo"], r["artista"], r["album"], r["confianza"]) == ("Reconocida", "Banda", "Disco", "alta")


@sin_ffmpeg
def test_sugerir_une_fuentes_sin_internet_y_avisa(tmp_path):
    def sin_red(url):
        raise metadatos.SinConexion("sin red")
    ruta = pista(tmp_path / "Los Tests - Cancion Falsa.wav")
    r = sugerencias.sugerir(ruta, ruta.name, {"titulo": "", "artista": "", "bpm": None, "energia": None}, FFMPEG, "", True, sin_red)
    por = {c["campo"]: c for c in r["campos"]}
    assert por["artista"]["sugerido"] == "Los Tests" and por["artista"]["confianza"] == "baja"
    assert por["bpm"]["sugerido"] in (119, 120, 121) and por["energia"]["sugerido"] == 4
    assert por["cue_intro"]["confianza"] == "baja"                      # el intro siempre se marca como estimación
    assert any("Sin conexión" in a for a in r["avisos"]) and r["en_linea"] is False
    # Lo que ya está igual no se propone
    r2 = sugerencias.sugerir(ruta, ruta.name, {"artista": "Los Tests", "titulo": "Cancion Falsa", "bpm": 120, "energia": 4}, FFMPEG, "", False)
    p2 = {c["campo"]: c for c in r2["campos"]}
    assert not p2["bpm"]["mostrar"] and not p2["energia"]["mostrar"] and not p2["artista"]["mostrar"]


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


@sin_ffmpeg
def test_endpoint_analizar_no_guarda_nada(client, tmp_path, monkeypatch):
    from backend.api import biblioteca
    monkeypatch.setattr(biblioteca, "_http_externo", lambda: (lambda url: {"recordings": [
        {"id": "z", "score": 100, "title": "ENDPOINT", "first-release-date": "2020-01-01", "artist-credit": [{"name": "ART ENDPOINT"}],
         "releases": [{"title": "Disco API", "status": "Official", "release-group": {"primary-type": "Album"}}]}]}))
    aid = subir(client, tmp_path, "ENDPOINT", seconds=3)
    r = client.post(f"/api/v1/biblioteca/{aid}/analizar")
    assert r.status_code == 200, r.text
    d = r.json()
    por = {c["campo"]: c for c in d["campos"]}
    assert por["album"]["sugerido"] == "Disco API" and por["anio"]["sugerido"] == "2020"
    assert d["acoustid"] is False and any("AcoustID" in a for a in d["avisos"])
    assert client.post("/api/v1/biblioteca/999999/analizar").status_code == 404
    # No tocó la ficha
    ficha = client.get(f"/api/v1/biblioteca/{aid}/ficha").json()
    assert not ficha.get("album") and not ficha.get("anio")


def test_clave_acoustid_nunca_se_devuelve(client):
    assert client.get("/api/v1/config/acoustid").json() == {"configurada": False}
    assert client.put("/api/v1/config/acoustid", json={"clave": "ABC123"}).json() == {"configurada": True}
    assert "ABC123" not in client.get("/api/v1/config/acoustid").text
    assert client.put("/api/v1/config/acoustid", json={"clave": ""}).json() == {"configurada": False}
