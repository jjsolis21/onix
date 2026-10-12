"""Medición LUFS con ffmpeg y cálculo de ganancia."""

import pytest

from backend.audio.loudness import find_ffmpeg, ganancia_db, medir
from conftest import make_wav

requiere_ffmpeg = pytest.mark.skipif(not find_ffmpeg(), reason="ffmpeg no instalado")


@requiere_ffmpeg
def test_medir_tono(tmp_path):
    # Tono de 1 kHz mono con pico a -6 dBFS → -9 LUFS
    res = medir(make_wav(tmp_path / "t.wav", seconds=3, amplitude=0.5, freq=1000))
    assert res["lufs"] == pytest.approx(-9.0, abs=0.3)
    assert res["pico_db"] == pytest.approx(-6.0, abs=0.3)


@requiere_ffmpeg
def test_medir_silencio_o_basura(tmp_path):
    assert medir(make_wav(tmp_path / "s.wav", amplitude=0.0)) is None
    roto = tmp_path / "roto.mp3"
    roto.write_bytes(b"esto no es audio")
    assert medir(roto) is None


def test_ganancia():
    assert ganancia_db(-9.0, -6.0, objetivo=-16) == pytest.approx(-7.0)
    # Quieto pero con picos: subir solo hasta que el pico toque -1 dBTP
    assert ganancia_db(-24.0, -3.0, objetivo=-16) == pytest.approx(2.0)
    # Muy quieto y sin picos: tope de +6 dB
    assert ganancia_db(-30.0, -20.0, objetivo=-16) == pytest.approx(6.0)
    assert ganancia_db(None, None, objetivo=-16) == 0.0


def test_motor_aplica_objetivo(tmp_db):
    from backend.audio.audio_engine import AudioEngine
    eng = AudioEngine(db_path=tmp_db)
    eng._load_config()                     # objetivo por defecto -16 LUFS
    track = {"loudness_lufs": -10.0, "pico_db": -0.5}
    assert eng._gain_of(track) == pytest.approx(10 ** (-6 / 20), rel=1e-3)
    # El ajuste manual se suma al objetivo
    assert eng._gain_of({**track, "ganancia_db": 2.0}) == pytest.approx(10 ** (-4 / 20), rel=1e-3)
    eng._norm_activa = False
    assert eng._gain_of(track) == 1.0
