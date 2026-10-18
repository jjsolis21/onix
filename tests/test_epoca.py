import sqlite3
import pytest
from backend.api.utils_musica import calcular_epoca
from backend.audio.planner import Planner, FILTRO_COLUMNA
from backend.api.relojes import _limpiar_filtros, CLAVES_FILTRO


def test_calcular_epoca():
    assert calcular_epoca(2024) == "Actual"
    assert calcular_epoca("2020") == "Actual"
    assert calcular_epoca(2015) == "2010s"
    assert calcular_epoca("2010") == "2010s"
    assert calcular_epoca(2005) == "2000s"
    assert calcular_epoca("2000") == "2000s"
    assert calcular_epoca(1994) == "90s"
    assert calcular_epoca("1999") == "90s"
    assert calcular_epoca(1985) == "80s"
    assert calcular_epoca("1980") == "80s"
    assert calcular_epoca(1975) == "70s"
    assert calcular_epoca("1970") == "70s"
    assert calcular_epoca(1965) == "Oldies"
    assert calcular_epoca(1950) == "Oldies"
    assert calcular_epoca(None) is None
    assert calcular_epoca("") is None
    assert calcular_epoca("invalid") is None
    assert calcular_epoca(0) is None


def test_epoca_en_planner_y_relojes(tmp_db):
    assert "epoca" in FILTRO_COLUMNA
    assert "epoca" in CLAVES_FILTRO

    filtros = {"epoca": "90s", "cat1": "Rock", "otro": "ignorado"}
    limpios = _limpiar_filtros(filtros)
    assert limpios["epoca"] == "90s"
    assert limpios["cat1"] == "ROCK"
    assert "otro" not in limpios

    # Epoca: Todas debe ignorarse
    assert "epoca" not in _limpiar_filtros({"epoca": "Todas"})
    assert "epoca" not in _limpiar_filtros({"epoca": "TODAS"})

    # Probar que Planner filtra por epoca en SQL
    from backend.database import get_connection
    db_file = tmp_db
    conn = get_connection()
    conn.execute("INSERT INTO audios (id, titulo, artista, tipo_audio, activo, aprobada, fecha_lanzamiento, epoca, archivo_path) VALUES (1,'Song 90s', 'Artist 1', 'Musica', 1, 1, '1995', '90s', 's1.mp3')")
    conn.execute("INSERT INTO audios (id, titulo, artista, tipo_audio, activo, aprobada, fecha_lanzamiento, epoca, archivo_path) VALUES (2, 'Song 80s', 'Artist 2', 'Musica', 1, 1, '1985', '80s', 's2.mp3')")
    conn.execute("INSERT INTO audios (id, titulo, artista, tipo_audio, activo, aprobada, fecha_lanzamiento, epoca, archivo_path) VALUES (3, 'Song Actual', 'Artist 3', 'Musica', 1, 1, '2023', 'Actual', 's3.mp3')")
    conn.commit()
    conn.close()

    planner = Planner(db_file)
    clauses, params = planner._where_filtros({"epoca": "90s"})
    assert any("a.epoca" in c for c in clauses)
    assert "90s" in params

    from datetime import datetime
    conn = get_connection()
    planner._cargar(conn)
    audio = planner._elegir(conn, {"tipo_audio": "Musica", "filtros": {"epoca": "90s"}}, datetime.now(), [], set(), set())
    assert audio is not None
    assert audio["id"] == 1
    assert audio["titulo"] == "Song 90s"

    audio_80s = planner._elegir(conn, {"tipo_audio": "Musica", "filtros": {"epoca": "80s"}}, datetime.now(), [], set(), set())
    assert audio_80s is not None
    assert audio_80s["id"] == 2
    assert audio_80s["titulo"] == "Song 80s"
    conn.close()


def test_api_crear_y_editar_epoca(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.api_server import app
    from conftest import make_wav

    with TestClient(app) as client:
        wav = make_wav(tmp_path / "tema_epoca.wav", seconds=1.0)
        with open(wav, "rb") as fh:
            r = client.post(
                "/api/v1/audios",
                data={"titulo": "Cancion 90s", "artista": "Banda 90s", "fecha_lanzamiento": "1994"},
                files={"file": (wav.name, fh, "audio/wav")},
            )
        assert r.status_code == 201, r.text
        audio_id = r.json()["data"]["id"]
        assert r.json()["data"]["epoca"] == "90s"

        # Verificar GET /api/v1/audios
        lista = client.get("/api/v1/audios", params={"busqueda": "Cancion 90s"}).json()["data"]
        track = next(x for x in lista if x["id"] == audio_id)
        assert track["epoca"] == "90s"

        # Editar año a 2024 -> debe recalcular epoca a "Actual"
        r_put = client.put(f"/api/v1/audios/{audio_id}", json={"fecha_lanzamiento": "2024"})
        assert r_put.status_code == 200, r_put.text
        assert r_put.json()["data"]["epoca"] == "Actual"

        # Verificar nuevamente en GET
        track_act = client.get("/api/v1/audios", params={"ids": str(audio_id)}).json()["data"][0]
        assert track_act["fecha_lanzamiento"] == "2024"
        assert track_act["epoca"] == "Actual"

        # Verificar en ficha de biblioteca
        ficha = client.get(f"/api/v1/biblioteca/{audio_id}/ficha").json()
        assert ficha["epoca"] == "Actual"

