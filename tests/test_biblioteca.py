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


def test_papelera_restaurar_y_borrar_definitivo(client, tmp_path):
    from backend.paths import resolve_media
    a = subir(client, tmp_path, "Para Borrar", "Artista Papelera")
    ruta = resolve_media(a["archivo_path"])
    assert ruta.is_file()
    client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"]], "accion": "desactivar"})
    assert any(x["id"] == a["id"] for x in client.get("/api/v1/biblioteca/papelera").json())
    client.post("/api/v1/biblioteca/restaurar", json={"ids": [a["id"]]})
    assert not any(x["id"] == a["id"] for x in client.get("/api/v1/biblioteca/papelera").json())
    # Borrar definitivo solo funciona desde la papelera
    assert client.post("/api/v1/biblioteca/borrar", json={"ids": [a["id"]]}).json()["borrados"] == 0
    client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"]], "accion": "desactivar"})
    r = client.post("/api/v1/biblioteca/borrar", json={"ids": [a["id"]]}).json()
    assert r["borrados"] == 1 and r["archivos_borrados"] == 1 and not ruta.exists()


def test_calidad_detecta_duplicados_y_sin_clasificar(client, tmp_path):
    a = subir(client, tmp_path, "Doble Uno", "Artista Doble")
    b = subir(client, tmp_path, "Doble Uno (Remix)", "ARTISTA DOBLE")
    c = client.get("/api/v1/biblioteca/calidad").json()
    assert {a["id"], b["id"]} <= set(c["duplicados"]) and a["id"] in c["sin_clasificar"]


def test_restricciones_por_lote(client, tmp_path):
    a = subir(client, tmp_path, "Con Reglas", "Artista Reglas")
    r = client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"]], "cambios": {
        "restr_desde": 20, "restr_hasta": 6, "restr_dias": [5, 6], "max_por_dia": 2}})
    assert r.status_code == 200
    f = client.get(f"/api/v1/biblioteca/{a['id']}/ficha").json()
    assert (f["restr_desde"], f["restr_hasta"], f["restr_dias"], f["max_por_dia"]) == (20, 6, "5,6", 2)
    assert a["id"] in client.get("/api/v1/biblioteca/calidad").json()["restringidas"]
    # Los 7 días = sin restricción de días
    client.post("/api/v1/biblioteca/lote", json={"ids": [a["id"]], "cambios": {"restr_dias": list(range(7))}})
    assert client.get(f"/api/v1/biblioteca/{a['id']}/ficha").json()["restr_dias"] is None


def test_caratula_y_difusion(client, tmp_path):
    from mutagen.id3 import ID3, APIC
    a = subir(client, tmp_path, "Sin Portada", "Artista Portada")
    assert client.get(f"/api/v1/audios/{a['id']}/caratula").status_code == 404
    # MP3 con carátula embebida
    import shutil, subprocess
    if shutil.which("ffmpeg"):
        mp3 = tmp_path / "con_portada.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=2", str(mp3)], check=True)
        t = ID3(); t.add(APIC(encoding=3, mime="image/png", type=3, desc="", data=b"\x89PNG\r\n\x1a\nFAKE")); t.save(str(mp3))
        with open(mp3, "rb") as fh:
            b = client.post("/api/v1/audios", data={"titulo": "Con Portada", "artista": "X"},
                            files={"file": ("con_portada.mp3", fh, "audio/mpeg")}).json()["data"]
        r = client.get(f"/api/v1/audios/{b['id']}/caratula")
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    f = client.get(f"/api/v1/biblioteca/{a['id']}/ficha").json()
    assert len(f["historial"]["difusion"]) == 35 and len(f["historial"]["difusion"][0]["horas"]) == 24


def test_editor_guarda_outro_prioridad_y_caratula(client, tmp_path):
    a = subir(client, tmp_path, "Editor Uno", "Artista Editor")
    r = client.put(f"/api/v1/audios/{a['id']}", json={"cue_outro": 1.5, "prioridad": 1, "repeticion_horas": 6,
                                                       "ganancia_db": -2, "fade_in": 0.5})
    assert r.status_code == 200, r.text
    f = client.get(f"/api/v1/biblioteca/{a['id']}/ficha").json()
    assert (f["cue_outro"], f["prioridad"], f["repeticion_horas"], f["fade_in"]) == (1.5, 1, 6, 0.5)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    assert client.post(f"/api/v1/audios/{a['id']}/caratula", files={"file": ("x.png", png, "image/png")}).status_code == 200
    assert client.get(f"/api/v1/audios/{a['id']}/caratula").content == png
    assert client.post(f"/api/v1/audios/{a['id']}/caratula", files={"file": ("x.txt", b"hola", "text/plain")}).status_code == 422
    client.delete(f"/api/v1/audios/{a['id']}/caratula")
    assert client.get(f"/api/v1/audios/{a['id']}/caratula").status_code == 404
    ac = client.post(f"/api/v1/biblioteca/{a['id']}/autocue")
    assert ac.status_code in (200, 422, 503)


def _aprobada(audio_id):
    import sqlite3
    from backend.paths import DB_PATH
    c = sqlite3.connect(DB_PATH)
    try:
        return c.execute("SELECT aprobada FROM audios WHERE id = ?", (audio_id,)).fetchone()[0]
    finally:
        c.close()


def test_la_musica_nueva_se_aprueba_sola_solo_si_ya_trae_genero_y_rotacion(client, tmp_path):
    schema = client.get("/api/v1/config/biblioteca/schema").json()["categorias"]
    valores = {c["nombre_interno"]: c["valores"][0]["valor"] for c in schema if c["valores"]}
    assert "cat1" in valores and "cat2" in valores, "la base de pruebas debe traer géneros y rotaciones"

    def subir(titulo, **extra):
        wav = make_wav(tmp_path / f"{titulo}.wav", seconds=2.0)
        with open(wav, "rb") as fh:
            r = client.post("/api/v1/audios", data={"titulo": titulo, "artista": "PRUEBA APROBAR", **extra},
                            files={"file": (wav.name, fh, "audio/wav")})
        assert r.status_code == 201, r.text
        return r.json()["data"]["id"]

    # clasificada (género y rotación): entra aprobada
    assert _aprobada(subir("Con Todo", cat1=valores["cat1"], cat2=valores["cat2"])) == 1
    # sin clasificar, o a medias: a la Bandeja
    assert _aprobada(subir("Sin Nada")) == 0
    assert _aprobada(subir("Solo Genero", cat1=valores["cat1"])) == 0
    assert _aprobada(subir("Solo Rotacion", cat2=valores["cat2"])) == 0
    # lo que no es música no pasa por la Bandeja
    assert _aprobada(subir("Un Efecto", tipo_audio="Efecto")) == 1
