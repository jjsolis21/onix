"""Biblioteca fase B: ficha, etiquetas y duplicados."""

import io

import pytest
from fastapi.testclient import TestClient

from backend.api.biblioteca import leer_etiquetas, normalizar
from conftest import make_wav


@pytest.fixture(scope="module")
def client():
    from backend.api.api_server import app
    with TestClient(app) as c:
        yield c


def subir(client, tmp_path, titulo, artista):
    wav = make_wav(tmp_path / f"{titulo}.wav", seconds=2.0)
    with open(wav, "rb") as fh:
        r = client.post("/api/v1/audios", data={"titulo": titulo, "artista": artista},
                        files={"file": (wav.name, fh, "audio/wav")})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_normalizar_para_duplicados():
    assert normalizar("Provenza (Remix)") == normalizar("PROVENZA") == "provenza"
    assert normalizar("Canción Única feat. Otro") == "cancion unica"


def test_etiquetas_desde_el_nombre_si_no_hay_id3():
    r = leer_etiquetas(b"no es audio", "07 - Karol G - Provenza.mp3")
    assert (r["artista"], r["titulo"], r["origen_datos"]) == ("Karol G", "Provenza", "nombre")


def test_etiquetas_id3_reales(tmp_path):
    mutagen = pytest.importorskip("mutagen")
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC
    p = tmp_path / "x.mp3"
    p.write_bytes(b"")
    t = ID3()
    t.add(TIT2(encoding=3, text="Ojitos Lindos")); t.add(TPE1(encoding=3, text="Bad Bunny"))
    t.add(TALB(encoding=3, text="Un Verano Sin Ti")); t.add(TDRC(encoding=3, text="2022"))
    t.save(str(p))
    # ID3 + unos frames MPEG mínimos para que mutagen lo reconozca como MP3
    datos = p.read_bytes() + (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 20
    r = leer_etiquetas(datos, "cualquiera.mp3")
    assert (r["titulo"], r["artista"], r["album"], r["anio"]) == ("Ojitos Lindos", "Bad Bunny", "Un Verano Sin Ti", "2022")


def test_endpoint_etiquetas_avisa_duplicado(client, tmp_path):
    subir(client, tmp_path, "Tema Repetido", "Artista Dup")
    r = client.post("/api/v1/biblioteca/etiquetas", data={"nombre": "Artista Dup - Tema Repetido (Radio Edit).mp3"},
                    files={"file": ("a.mp3", io.BytesIO(b"x" * 100), "audio/mpeg")})
    assert r.status_code == 200
    assert r.json()["duplicados"] and r.json()["duplicados"][0]["titulo"] == "TEMA REPETIDO"


def test_ficha_con_relojes_e_historial(client, tmp_path):
    a = subir(client, tmp_path, "Tema Ficha", "Artista Ficha")
    client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"]], "cambios": {"cat1": "Pop", "cat2": "Recurrente"}})
    client.post("/api/v1/relojes", json={"nombre": "Solo Pop", "filas": [{"tipo_audio": "Musica", "filtros": {"cat1": "POP"}}]})
    client.post("/api/v1/relojes", json={"nombre": "Solo Rock", "filas": [{"tipo_audio": "Musica", "filtros": {"cat1": "ROCK"}}]})
    f = client.get(f"/api/v1/biblioteca/{a['id']}/ficha").json()
    assert f["titulo"] == "TEMA FICHA" and f["historial"]["total"] == 0 and len(f["historial"]["por_hora_30d"]) == 24
    nombres = [r["nombre"] for r in f["relojes"]]
    assert "Solo Pop" in nombres and "Solo Rock" not in nombres
    assert client.get("/api/v1/biblioteca/999999/ficha").status_code == 404
