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


# ── iTunes y Deezer ─────────────────────────────────────────────────────────
ITUNES = {"resultCount": 2, "results": [
    {"trackName": "Otra Canción Distinta", "artistName": "Los Tests", "collectionName": "Otro", "releaseDate": "2000-01-01T08:00:00Z"},
    {"trackName": "Canción Falsa (Remastered 2015)", "artistName": "Los Tests", "collectionName": "El Disco de Verdad",
     "releaseDate": "2015-06-01T07:00:00Z", "primaryGenreName": "Pop",
     "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/abc/100x100bb.jpg"}]}
DEEZER_BUSCA = {"data": [{"id": 77, "title": "Cancion Falsa", "artist": {"name": "Los Tests"},
                          "album": {"title": "El Disco de Verdad", "cover_xl": "https://e-cdns-images.dzcdn.net/images/cover/xyz/1000x1000.jpg"}}]}


def test_itunes_elige_la_cancion_correcta_y_descarta_las_dudosas():
    r = metadatos.buscar_itunes("Los Tests", "Canción Falsa", lambda u: ITUNES)
    assert (r["album"], r["anio"], r["fuente"]) == ("El Disco de Verdad", "2015", "iTunes")
    assert r["caratula"].endswith("600x600bb.jpg")
    assert metadatos.buscar_itunes("Los Tests", "Algo Que No Existe", lambda u: ITUNES) is None
    # otro artista con el mismo título: no se acepta
    assert metadatos.buscar_itunes("Banda Ajena", "Canción Falsa", lambda u: ITUNES) is None
    # un sencillo no se toma como álbum
    solo = {"results": [{"trackName": "Canción Falsa", "artistName": "Los Tests", "collectionName": "Canción Falsa - Single", "releaseDate": "2020-01-01T00:00:00Z"}]}
    assert metadatos.buscar_itunes("Los Tests", "Canción Falsa", lambda u: solo)["album"] == ""


def test_deezer_trae_bpm_solo_si_lo_sabe():
    def http(url):
        return DEEZER_BUSCA if "search" in url else {"id": 77, "bpm": 118.4, "release_date": "2014-02-03"}
    r = metadatos.buscar_deezer("Los Tests", "Canción Falsa", http)
    assert (r["bpm"], r["anio"], r["album"]) == (118, "2014", "El Disco de Verdad")
    cero = metadatos.buscar_deezer("Los Tests", "Canción Falsa", lambda u: DEEZER_BUSCA if "search" in u else {"bpm": 0, "release_date": ""})
    assert cero["bpm"] is None


def test_caratulas_solo_de_itunes_o_deezer():
    assert metadatos.host_permitido("https://is1-ssl.mzstatic.com/a.jpg")
    assert metadatos.host_permitido("https://e-cdns-images.dzcdn.net/b.jpg")
    for malo in ("http://is1-ssl.mzstatic.com/a.jpg", "https://evil.com/mzstatic.com/a.jpg", "https://mzstatic.com.evil.com/a.jpg", "file:///etc/passwd"):
        assert not metadatos.host_permitido(malo), malo
    with pytest.raises(ValueError):
        metadatos.descargar_imagen("https://evil.com/a.jpg")


@sin_ffmpeg
def test_sugerir_con_itunes_y_deezer_y_cruce_de_ritmo(tmp_path):
    ruta = pista(tmp_path / "Los Tests - Cancion Falsa.wav", bpm=120)

    def http(url):
        if "musicbrainz" in url:
            return {"recordings": []}
        if "itunes" in url:
            return ITUNES
        if "api.deezer.com/search" in url:
            return DEEZER_BUSCA
        return {"bpm": 119.6, "release_date": "2014-02-03"}                       # Deezer: ritmo de la canción
    r = sugerencias.sugerir(ruta, ruta.name, {"titulo": "", "artista": "", "tiene_caratula": False}, FFMPEG, "", True, http)
    por = {c["campo"]: c for c in r["campos"]}
    assert por["album"]["sugerido"] == "El Disco de Verdad" and por["album"]["confianza"] == "media"
    assert por["caratula"]["sugerido"].startswith("https://is1-ssl.mzstatic.com") and por["caratula"]["fuente"] == "iTunes"
    assert por["bpm"]["sugerido"] == 120 or por["bpm"]["sugerido"] == 119
    assert por["bpm"]["confianza"] == "alta" and "coinciden" in por["bpm"]["fuente"]          # Deezer y la señal coinciden
    # Apagadas: no se consultan
    visto = []
    sugerencias.sugerir(ruta, ruta.name, {"tiene_caratula": False}, FFMPEG, "", True, lambda u: visto.append(u) or {"recordings": []},
                        {"itunes": False, "deezer": False})
    assert all("itunes" not in u and "deezer" not in u for u in visto)
    # Con carátula propia no se propone otra
    r2 = sugerencias.sugerir(ruta, ruta.name, {"tiene_caratula": True}, FFMPEG, "", True, http)
    assert "caratula" not in {c["campo"] for c in r2["campos"]}
    # Si no coinciden, se avisa de la diferencia
    def http2(url):
        return {"bpm": 90.0, "release_date": "2014-02-03"} if "api.deezer.com/track" in url else http(url)
    r3 = sugerencias.sugerir(ruta, ruta.name, {"tiene_caratula": False}, FFMPEG, "", True, http2)
    assert any("Deezer dice 90" in a for a in r3["avisos"])


def test_config_de_fuentes_y_caratula_por_url(client, tmp_path, monkeypatch):
    from backend.api import biblioteca
    assert client.get("/api/v1/config/analisis").json()["itunes"] is True
    assert client.put("/api/v1/config/analisis", json={"itunes": False}).json() == {"itunes": False, "deezer": True, "acoustid": False}
    client.put("/api/v1/config/analisis", json={"itunes": True})
    aid = subir(client, tmp_path, "CARATULA URL", seconds=3)
    assert client.post(f"/api/v1/audios/{aid}/caratula-url", json={"url": "https://evil.com/x.jpg"}).status_code == 422
    jpg = b"\xff\xd8\xff" + b"0" * 50
    monkeypatch.setattr("backend.audio.metadatos.descargar_imagen", lambda url: (jpg, "jpg"))
    assert client.post(f"/api/v1/audios/{aid}/caratula-url", json={"url": "https://is1-ssl.mzstatic.com/a/600x600bb.jpg"}).status_code == 200
    assert client.get(f"/api/v1/audios/{aid}/caratula").content == jpg
