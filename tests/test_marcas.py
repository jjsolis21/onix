"""Detección automática de inicio y punto de mezcla (backend/audio/marcas.py)."""

import math
import shutil
import sqlite3
import struct
import wave

import pytest

from backend.audio import marcas

FFMPEG = shutil.which("ffmpeg")
sin_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg no instalado")


def cancion(path, silencio_ini=2.0, cuerpo=40.0, fade=8.0, silencio_fin=3.0, rate=8000):
    """Tono con silencio al inicio, fade-out lineal en dB (-0 a -60) y silencio al final."""
    frames = []
    total = silencio_ini + cuerpo + fade + silencio_fin
    for i in range(int(total * rate)):
        t = i / rate
        if t < silencio_ini or t >= silencio_ini + cuerpo + fade:
            amp = 0.0
        elif t < silencio_ini + cuerpo:
            amp = 0.5
        else:
            amp = 0.5 * 10 ** (-60 * (t - silencio_ini - cuerpo) / fade / 20)
        frames.append(struct.pack("<h", int(32767 * amp * math.sin(2 * math.pi * 330 * t))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(frames))
    return path


def test_calcular_final_seco():
    niveles = [-120.0] * 20 + [-10.0] * 1200 + [-120.0] * 40       # 1 s silencio, 60 s, 2 s silencio
    r = marcas.calcular(niveles)
    assert r["cue_inicio"] == pytest.approx(0.95, abs=0.06)
    assert r["cue_mezcla"] == pytest.approx(61.0, abs=0.1)         # justo al terminar, sin la cola


def test_calcular_no_inventa_con_poco_sonido():
    assert marcas.calcular([-120.0] * 100 + [-10.0] * 40) is None   # 2 s de sonido


@sin_ffmpeg
def test_detecta_inicio_y_mezcla_en_un_fade(tmp_path):
    r = marcas.detectar(cancion(tmp_path / "c.wav"), FFMPEG)
    assert r["cue_inicio"] == pytest.approx(2.0, abs=0.15)
    # Fade de 8 s de 0 a -60 dB: -12 dB llega a 1,6 s del fade (≈ 43,6 s); la media de 1 s lo corre un poco
    assert 42.0 < r["cue_mezcla"] < 46.5


@sin_ffmpeg
def test_procesa_la_musica_y_respeta_las_marcas_a_mano(tmp_db, tmp_path):
    a = cancion(tmp_path / "a.wav")
    b, c = shutil.copy(a, tmp_path / "b.wav"), shutil.copy(a, tmp_path / "c.wav")
    conn = sqlite3.connect(tmp_db)
    auto = conn.execute("INSERT INTO audios (titulo, artista, archivo_path, tipo_audio) VALUES"
                        " ('AUTO', 'X', ?, 'Musica')", (str(a),)).lastrowid
    mano = conn.execute("INSERT INTO audios (titulo, artista, archivo_path, tipo_audio, cue_mezcla) VALUES"
                        " ('MANO', 'X', ?, 'Musica', 30)", (str(b),)).lastrowid
    cuna = conn.execute("INSERT INTO audios (titulo, artista, archivo_path, tipo_audio) VALUES"
                        " ('CUÑA', 'X', ?, 'Cuña')", (str(c),)).lastrowid
    conn.commit()
    marcas.procesar_pendientes(str(tmp_db), lambda p: __import__("pathlib").Path(p), FFMPEG)
    filas = {r[0]: r[1:] for r in conn.execute("SELECT id, cue_inicio, cue_mezcla, marcas_auto FROM audios")}
    conn.close()
    assert filas[auto][2] == 1 and filas[auto][0] == pytest.approx(2.0, abs=0.15) and filas[auto][1] > 40
    assert filas[mano] == (None, 30, 0)                         # a mano: no se toca
    assert filas[cuna][2] is None                               # las cuñas suenan completas


def test_mover_la_mezcla_a_mano_la_protege(tmp_path):
    from fastapi.testclient import TestClient
    from backend.api.api_server import app
    from conftest import make_wav
    with TestClient(app) as c:
        wav = make_wav(tmp_path / "x.wav", seconds=3.0)
        with open(wav, "rb") as fh:
            aid = c.post("/api/v1/audios", data={"titulo": "MARCAS", "artista": "Y"},
                         files={"file": ("x.wav", fh, "audio/wav")}).json()["data"]["id"]
        from backend.paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE audios SET cue_mezcla = 2.5, marcas_auto = 1 WHERE id = ?", (aid,))
        conn.commit()
        c.put(f"/api/v1/audios/{aid}", json={"titulo": "MARCAS", "cue_mezcla": 2.5})   # igual: sigue auto
        assert conn.execute("SELECT marcas_auto FROM audios WHERE id = ?", (aid,)).fetchone()[0] == 1
        c.put(f"/api/v1/audios/{aid}", json={"cue_mezcla": 2.0})                       # la movió
        assert conn.execute("SELECT marcas_auto FROM audios WHERE id = ?", (aid,)).fetchone()[0] == 0
        conn.close()
