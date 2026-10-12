"""
Motor de audio en modo mock: se manejan sus métodos directamente (sin
arrancar el hilo) y con un reloj falso para probar tandas a hora fija.
"""

import json
import sqlite3
from datetime import datetime

import pytest

from backend.audio.audio_engine import AudioEngine
from conftest import make_wav

# 2026-10-12 es lunes → clave de matriz "1-10" para las 10:xx
LUNES_10 = "1-10"


class Reloj:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def media(tmp_path):
    return tmp_path / "media"


@pytest.fixture
def engine(tmp_db, media):
    eng = AudioEngine(db_path=tmp_db, respaldo_dir=media / "respaldo")
    eng._load_config()
    eng.PAUTAS_INTERVAL = 0
    eng._now = Reloj(datetime(2026, 10, 12, 10, 0))
    eng.events = []
    for ev in ("pauta_scheduled", "pauta_missed", "tanda_scheduled",
               "respaldo_activado", "track_started", "playback_error"):
        eng.bus.subscribe(ev, lambda e, d: eng.events.append((e, d)))
    return eng


def db(engine):
    conn = sqlite3.connect(engine._db)
    conn.row_factory = sqlite3.Row
    return conn


def add_audio(engine, media, titulo, tipo="Musica", seconds=2.0, **cols):
    path = make_wav(media / f"{titulo}.wav", seconds=seconds)
    conn = db(engine)
    campos = {"titulo": titulo, "artista": cols.pop("artista", titulo),
              "archivo_path": str(path), "tipo_audio": tipo, **cols}
    cur = conn.execute(
        f"INSERT INTO audios ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
        list(campos.values()),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def add_pauta(engine, media, cliente, rubro="", horas=(LUNES_10,), **cols):
    audio_id = add_audio(engine, media, f"spot_{cliente}", tipo="Cuña")
    conn = db(engine)
    cur = conn.execute(
        "INSERT INTO pautas (cliente, audio_id, matriz, rubro, menciones_diarias, prioridad,"
        " cue_intro, cue_outro) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cliente, audio_id, json.dumps({h: True for h in horas}), rubro,
         cols.get("menciones_diarias"), cols.get("prioridad", 0),
         cols.get("cue_intro"), cols.get("cue_outro")),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def en_cola(engine):
    return [p["cliente"] for p in engine._priority_queue]


# ── Tandas ──────────────────────────────────────────────────────────────────

def test_las_pautas_esperan_al_minuto_de_su_tanda(engine, media):
    add_pauta(engine, media, "BANCO A", rubro="BANCO")
    add_pauta(engine, media, "BANCO B", rubro="BANCO")
    add_pauta(engine, media, "FARMACIA", rubro="SALUD")

    engine._now.dt = datetime(2026, 10, 12, 10, 5)
    engine._check_pautas()
    assert en_cola(engine) == []

    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    primera = en_cola(engine)
    assert len(primera) == 2 and sum(c.startswith("BANCO") for c in primera) == 1
    assert [e for e, _ in engine.events].count("tanda_scheduled") == 1

    engine._now.dt = datetime(2026, 10, 12, 10, 45)
    engine._check_pautas()
    assert len(en_cola(engine)) == 3          # la segunda tanda se suma, sin duplicar


def test_pauta_emitida_no_se_repite_en_la_hora(engine, media):
    add_pauta(engine, media, "TIENDA")
    engine._now.dt = datetime(2026, 10, 12, 10, 50)
    engine._check_pautas()
    item = engine._pick_next()
    assert item["origen"] == "pauta" and item["tanda"] == 15
    engine._log_emision(item["track"], item, tipo="pauta")

    engine._check_pautas()
    assert en_cola(engine) == []


def test_menciones_diarias_tope(engine, media):
    add_pauta(engine, media, "TOPE", horas=("1-9", LUNES_10), menciones_diarias=1)
    engine._now.dt = datetime(2026, 10, 12, 9, 50)
    engine._check_pautas()
    item = engine._pick_next()
    engine._log_emision(item["track"], item, tipo="pauta")

    engine._now.dt = datetime(2026, 10, 12, 10, 50)
    engine._check_pautas()
    assert en_cola(engine) == []


def test_pauta_creada_a_mitad_de_hora_va_a_la_proxima_tanda(engine, media):
    add_pauta(engine, media, "PRIMERA")
    engine._now.dt = datetime(2026, 10, 12, 10, 20)
    engine._check_pautas()                     # PRIMERA → tanda :15 (ya vencida)
    assert en_cola(engine) == ["PRIMERA"]

    add_pauta(engine, media, "NUEVA")
    engine._check_pautas()
    assert en_cola(engine) == ["PRIMERA"]      # espera a la tanda :45
    assert engine._plan[45] == [2]

    engine._now.dt = datetime(2026, 10, 12, 10, 45)
    engine._check_pautas()
    assert en_cola(engine) == ["PRIMERA", "NUEVA"]


def test_pauta_que_no_sale_en_su_hora_se_descarta(engine, media):
    add_pauta(engine, media, "TARDE")
    engine._now.dt = datetime(2026, 10, 12, 10, 46)
    engine._check_pautas()
    assert en_cola(engine) == ["TARDE"]

    engine._now.dt = datetime(2026, 10, 12, 11, 0)
    engine._check_pautas()
    assert en_cola(engine) == []
    assert any(e == "pauta_missed" for e, _ in engine.events)


def test_tandas_configurables(engine, media):
    conn = db(engine)
    conn.execute("UPDATE config SET valor = '0' WHERE clave = 'tandas_minutos'")
    conn.commit()
    conn.close()
    engine.reload_config()
    add_pauta(engine, media, "EN PUNTO")
    engine._now.dt = datetime(2026, 10, 12, 10, 0)
    engine._check_pautas()
    assert en_cola(engine) == ["EN PUNTO"]


def test_pauta_respeta_sus_cues(engine, media):
    add_pauta(engine, media, "RECORTADA", cue_intro=0.5, cue_outro=1.5)
    engine._now.dt = datetime(2026, 10, 12, 10, 50)
    engine._check_pautas()
    assert engine._advance()
    assert engine._current_item["origen"] == "pauta"
    assert engine._hard_end == pytest.approx(1.5)
    assert engine._mix_point == pytest.approx(1.5 - engine.SPOT_FADE_OUT)
    assert engine._audio.get_position_sec(engine._active_canal) >= 0.5


# ── Fades y punto de mezcla ─────────────────────────────────────────────────

def test_fades_por_pista(engine):
    engine._crossfade_secs = 4.0
    a = {"track": {"fade_out": 2.0}, "origen": "auto"}
    b = {"track": {"fade_in": 1.0}, "origen": "auto"}
    sin = {"track": {}, "origen": "auto"}
    assert engine._fade_times(a, b) == (1.0, 2.0)
    assert engine._fade_times(sin, sin) == (4.0, 4.0)
    spot = {"track": {"tipo_audio": "Cuña", "fade_in": 3.0}, "origen": "pauta"}
    assert engine._fade_times(a, spot) == (engine.SPOT_FADE_IN, engine.SPOT_FADE_IN)
    assert engine._fade_times(spot, b) == (engine.SPOT_FADE_OUT, engine.SPOT_FADE_OUT)


def test_punto_de_mezcla_usa_fade_out(engine, media):
    aid = add_audio(engine, media, "CON FADE", seconds=10.0, fade_out=3.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    assert engine._mix_point == pytest.approx(engine._duration - 3.0, abs=0.05)


def test_cue_mezcla_tiene_prioridad(engine, media):
    aid = add_audio(engine, media, "CON CUE", seconds=10.0, cue_mezcla=7.0, fade_out=3.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    assert engine._mix_point == pytest.approx(7.0)


# ── Normalización ───────────────────────────────────────────────────────────

def test_ganancia_por_audio(engine):
    assert engine._gain_of({"ganancia_db": -6.0}) == pytest.approx(0.501, abs=0.001)
    assert engine._gain_of({"ganancia_db": None}) == 1.0
    assert engine._gain_of({"ganancia_db": 20}) == pytest.approx(10 ** (6 / 20))   # tope +6 dB
    assert engine._target_volume({"ganancia_db": -6.0}) == pytest.approx(0.501, abs=0.001)


# ── Lista de respaldo ───────────────────────────────────────────────────────

def test_respaldo_cuando_no_hay_musica(engine, media):
    engine._mode = "automatico"
    assert engine._advance() is False          # sin música ni respaldo: silencio

    make_wav(media / "respaldo" / "emergencia.wav", seconds=3.0)
    assert engine._advance() is True
    assert engine._current_item["origen"] == "respaldo"
    assert any(e == "respaldo_activado" for e, _ in engine.events)

    conn = db(engine)
    fila = conn.execute("SELECT * FROM historial_emision ORDER BY id DESC").fetchone()
    conn.close()
    assert fila["origen"] == "respaldo" and fila["audio_id"] is None


def test_rotacion_tiene_prioridad_sobre_respaldo(engine, media):
    engine._mode = "automatico"
    make_wav(media / "respaldo" / "emergencia.wav")
    add_audio(engine, media, "CANCION")
    assert engine._advance()
    assert engine._current_item["origen"] == "auto"


def test_respaldo_en_manual_no_suena(engine, media):
    make_wav(media / "respaldo" / "emergencia.wav")
    assert engine._advance() is False
