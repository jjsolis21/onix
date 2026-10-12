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
    eng._mode = "automatico"      # las pruebas de modo manual lo cambian
    eng.PAUTAS_INTERVAL = 0
    eng._now = Reloj(datetime(2026, 10, 12, 10, 0))
    eng.events = []
    for ev in ("pauta_scheduled", "pauta_missed", "tanda_scheduled",
               "respaldo_activado", "track_started", "playback_error",
               "cue_started", "cue_stopped", "tanda_inmediata", "tanda_excedida"):
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
        " cue_intro, cue_outro, seccion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cliente, audio_id, json.dumps({h: True for h in horas}), rubro,
         cols.get("menciones_diarias"), cols.get("prioridad", 0),
         cols.get("cue_intro"), cols.get("cue_outro"), cols.get("seccion", 3)),
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
    engine._mode = "manual"
    make_wav(media / "respaldo" / "emergencia.wav")
    assert engine._advance() is False


# ── Modo manual: el operador lanza la tanda ──────────────────────────────────

def test_manual_la_tanda_espera_al_operador(engine, media):
    engine._mode = "manual"
    add_pauta(engine, media, "ESPERA")
    cancion = add_audio(engine, media, "CANCION", seconds=10.0)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "manual"})

    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    tanda = [d for e, d in engine.events if e == "tanda_scheduled"][0]
    assert tanda["requiere_operador"] is True
    assert engine.status["tanda_en_espera"] == 1
    assert engine._advance() is False                  # el cambio de pista NO la saca

    assert engine.lanzar_tanda(ahora=True) == 1
    engine._drain_commands()
    assert engine._current_item["origen"] == "pauta"
    assert engine.status["tanda_en_espera"] == 0


def test_manual_tanda_liberada_sale_en_el_proximo_cambio(engine, media):
    engine._mode = "manual"
    add_pauta(engine, media, "PROXIMA")
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    cancion = add_audio(engine, media, "CANCION", seconds=10.0)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "manual"})
    engine.lanzar_tanda(ahora=False)
    engine._drain_commands()
    assert engine._current_item["origen"] == "manual"   # sigue la canción
    assert engine._advance()
    assert engine._current_item["origen"] == "pauta"


def test_automatico_no_espera_al_operador(engine, media):
    add_pauta(engine, media, "SOLA")
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert engine.status["tanda_en_espera"] == 0
    assert engine._advance() and engine._current_item["origen"] == "pauta"


# ── Varias salidas de audio ─────────────────────────────────────────────────

def test_preescucha_sale_por_su_tarjeta(engine, media):
    from backend.audio.audio_engine import CANAL_CUE
    conn = db(engine)
    conn.execute("INSERT OR REPLACE INTO config VALUES ('dispositivo_aire', '1')")
    conn.execute("INSERT OR REPLACE INTO config VALUES ('dispositivo_preescucha', '2')")
    conn.commit()
    conn.close()
    engine.reload_config()

    aire = add_audio(engine, media, "AL AIRE", seconds=10.0)
    pre = add_audio(engine, media, "PREVIA", seconds=10.0)
    assert engine._start({"track": engine._fetch_track(aire), "origen": "manual"})
    assert engine.cue_play(pre, desde=2.0)
    engine._drain_commands()

    assert engine._audio.device_of(engine._active_canal) == 1
    assert engine._audio.device_of(CANAL_CUE) == 2
    st = engine.status
    assert st["current_track"]["id"] == aire           # el aire no se toca
    assert st["preescucha"]["audio_id"] == pre and st["preescucha"]["posicion"] >= 2.0

    engine.cue_stop()
    engine._drain_commands()
    assert engine.status["preescucha"] is None
    assert [e for e, _ in engine.events if e.startswith("cue_")] == ["cue_started", "cue_stopped"]


def test_lista_de_tarjetas_con_roles(engine):
    engine._devices = {"aire": -1, "preescucha": 2}
    devs = {d["index"]: d for d in engine.list_devices()}
    assert devs[1]["roles"] == ["aire"]               # -1 = la predeterminada
    assert devs[2]["roles"] == ["preescucha"]


def test_tarjeta_inexistente_no_rompe_el_aire(engine, media):
    engine._devices["preescucha"] = 9
    aire = add_audio(engine, media, "AL AIRE", seconds=10.0)
    assert engine._start({"track": engine._fetch_track(aire), "origen": "manual"})
    engine.cue_play(aire)
    engine._drain_commands()
    assert engine.status["preescucha"] is None
    assert any(d.get("reason") == "error_preescucha" for e, d in engine.events if e == "playback_error")
    assert engine.status["current_track"]["id"] == aire


# ── Programa de relojes (modo automático estilo SoHo) ────────────────────────

def test_automatico_consume_el_programa_en_orden(engine, media):
    for i in range(6):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"ART {i}")
    engine._actualizar_programa(forzar=True)
    plan = engine.programa()["items"]
    assert len(plan) >= 3 and plan[0]["hora"]
    primero = plan[0]["audio_id"]
    assert engine._advance()
    assert engine._current_track["id"] == primero
    assert engine.programa()["items"][0]["uid"] != plan[0]["uid"]


def test_manual_muestra_el_programa_pero_no_avanza_solo(engine, media):
    engine._mode = "manual"
    for i in range(4):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"ART {i}")
    engine._actualizar_programa(forzar=True)
    assert engine.programa()["items"]
    assert engine._advance() is False                    # fin de pista: no toma del plan
    engine.play_next()                                   # el operador pide "siguiente"
    engine._drain_commands()
    assert engine._current_item["origen"] == "auto"


def test_cola_del_operador_va_antes_del_programa(engine, media):
    for i in range(4):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"ART {i}")
    pedido = add_audio(engine, media, "PEDIDO", seconds=60.0)
    engine._actualizar_programa(forzar=True)
    engine.queue_track(pedido)
    vista = engine.programa()
    assert vista["cola"][0]["id"] == pedido
    assert engine._advance() and engine._current_track["id"] == pedido


def test_quitar_y_mover_items_del_programa(engine, media):
    for i in range(6):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"ART {i}")
    engine._actualizar_programa(forzar=True)
    items = engine.programa()["items"]
    engine.plan_mover(items[2]["uid"], 0)
    engine.plan_quitar(items[1]["uid"])
    engine._drain_commands()
    nuevos = [i["uid"] for i in engine.programa()["items"]]
    assert nuevos[0] == items[2]["uid"] and items[1]["uid"] not in nuevos


def test_marcador_de_tanda_la_lanza_aunque_falten_segundos(engine, media):
    add_pauta(engine, media, "ADELANTADA")
    engine._now.dt = datetime(2026, 10, 12, 10, 14, 30)   # 30 s antes de la tanda :15
    engine._check_pautas()
    assert en_cola(engine) == []
    with engine._programa_lock:
        engine._programa.append({"uid": 999, "tipo": "tanda", "slot": "2026-10-12-10",
                                 "minuto": 15, "duracion": 30, "inicio": "2026-10-12T10:14:30"})
    item = engine._pick_next()
    assert item["origen"] == "pauta" and item["cliente"] == "ADELANTADA"


def test_cartuchera_por_su_propia_salida(engine, media):
    from backend.audio.audio_engine import CANAL_EFX_BASE
    jid = add_audio(engine, media, "JINGLE", tipo="Efecto", seconds=3.0)
    engine._devices.update({"aire": 1, "cartuchera": 2})
    engine.fire_effect(jid)
    engine._drain_commands()
    assert engine._audio.device_of(CANAL_EFX_BASE) == 2
    engine._devices["cartuchera"] = -2                    # "igual que el aire"
    engine.fire_effect(jid)
    engine._drain_commands()
    assert engine._audio.device_of(CANAL_EFX_BASE) == 1


# ── Tandas al estilo Jazler: secciones, opcional, inicio inmediato ──────────

def test_tanda_ordenada_por_secciones(engine, media):
    add_pauta(engine, media, "CIERRE", seccion=4)            # cortina de salida (opcional)
    add_pauta(engine, media, "CLIENTE A", rubro="BANCA")
    add_pauta(engine, media, "APERTURA", seccion=2)          # cortina de entrada (opcional)
    add_pauta(engine, media, "ID ESTACION", seccion=1)
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert en_cola(engine) == ["ID ESTACION", "APERTURA", "CLIENTE A", "CIERRE"]


def test_id_y_cortinas_salen_en_cada_tanda(engine, media):
    add_pauta(engine, media, "ID ESTACION", seccion=1)
    add_pauta(engine, media, "APERTURA", seccion=2)
    add_pauta(engine, media, "CLIENTE A")
    add_pauta(engine, media, "CLIENTE B")
    engine._now.dt = datetime(2026, 10, 12, 10, 45)          # tandas :15 y :45
    engine._check_pautas()
    tandas = [(p["tanda"], p["cliente"]) for p in engine._priority_queue]
    assert tandas.count((15, "ID ESTACION")) == 1 and tandas.count((45, "ID ESTACION")) == 1
    assert tandas.count((15, "APERTURA")) == 1 and tandas.count((45, "APERTURA")) == 1
    # Las comerciales siguen repartidas: una por tanda
    assert sum(1 for t, c in tandas if c.startswith("CLIENTE")) == 2


def test_cortina_opcional_no_sale_sin_comerciales(engine, media):
    add_pauta(engine, media, "ID ESTACION", seccion=1)
    add_pauta(engine, media, "APERTURA", seccion=2)
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert en_cola(engine) == ["ID ESTACION"]


def test_seccion_fija_no_se_repite_en_la_misma_tanda(engine, media):
    add_pauta(engine, media, "ID ESTACION", seccion=1)
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    item = engine._pick_next()
    engine._log_emision(item["track"], item, tipo="pauta")
    engine._check_pautas()
    assert en_cola(engine) == []


def test_tanda_inmediata_corta_la_cancion(engine, media):
    add_pauta(engine, media, "EN PUNTO")
    cancion = add_audio(engine, media, "CANCION", seconds=60.0)
    engine._tandas_minutos, engine._tandas_inmediatas = [0], [0]
    engine._now.dt = datetime(2026, 10, 12, 9, 59, 30)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "auto"})
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "auto"           # todavía no es la hora
    engine._now.dt = datetime(2026, 10, 12, 10, 0, 0)
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "pauta"          # cortó y salió la tanda
    assert any(e == "tanda_inmediata" for e, _ in engine.events)


def test_tanda_inmediata_no_corta_en_manual(engine, media):
    engine._mode = "manual"
    add_pauta(engine, media, "EN PUNTO")
    cancion = add_audio(engine, media, "CANCION", seconds=60.0)
    engine._tandas_minutos, engine._tandas_inmediatas = [0], [0]
    engine._now.dt = datetime(2026, 10, 12, 10, 0, 0)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "manual"})
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "manual"


def test_aviso_de_tanda_demasiado_larga(engine, media):
    for i in range(3):
        add_pauta(engine, media, f"LARGA {i}")
    conn = db(engine)
    conn.execute("UPDATE audios SET duracion = 2 WHERE tipo_audio = 'Cuña'")
    conn.commit()
    conn.close()
    engine._tandas_minutos, engine._tanda_max_seg = [15], 3
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    exc = [d for e, d in engine.events if e == "tanda_excedida"]
    assert exc and exc[0]["duracion"] >= 6 and exc[0]["maximo"] == 3


# ── Studio nuevo: detener al terminar e insertar en el programa ──────────────

def test_detener_al_terminar_retiene_la_automatizacion(engine, media):
    for i in range(3):
        add_audio(engine, media, f"TEMA {i}", seconds=1.0, artista=f"A{i}")
    assert engine._advance()
    engine.detener_al_terminar(True)
    engine._drain_commands()
    assert engine.status["detener_al_terminar"] is True
    import time as _t
    for _ in range(60):                       # deja terminar la pista (1 s en mock)
        engine._tick()
        if engine._current_track is None:
            break
        _t.sleep(0.05)
    assert engine._current_track is None
    st = engine.status
    assert st["retenido"] is True and st["detener_al_terminar"] is False
    engine._idle_retry_at = 0
    engine._tick()                            # en automático NO arranca sola
    assert engine._current_track is None
    engine.play_next()                        # el operador retoma
    engine._drain_commands()
    assert engine._current_track is not None and engine.status["retenido"] is False


def test_insertar_audio_en_el_programa(engine, media):
    for i in range(4):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"A{i}")
    pedido = add_audio(engine, media, "PEDIDO", seconds=60.0)
    engine._actualizar_programa(forzar=True)
    assert engine.plan_insertar(pedido, 1)
    engine._drain_commands()
    items = engine.programa()["items"]
    assert items[1]["audio_id"] == pedido and items[1]["operador"] is True
    st = engine.status
    assert engine.plan_insertar(999999) is False


def test_status_trae_las_marcas_de_la_pista(engine, media):
    aid = add_audio(engine, media, "CON INTRO", seconds=10.0, cue_intro=3.5, cue_mezcla=8.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    ct = engine.status["current_track"]
    assert ct["cue_intro"] == 3.5 and ct["mezcla"] == 8.0 and ct["tipo_audio"] == "Musica"


def test_operador_adelanta_la_cancion(engine, media):
    aid = add_audio(engine, media, "LARGA", seconds=60.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    engine.seek(30)
    engine._drain_commands()
    assert engine._audio.get_position_sec(engine._active_canal) >= 30
    engine.seek(500)                                   # más allá del final: queda 1 s antes
    engine._drain_commands()
    assert engine._audio.get_position_sec(engine._active_canal) <= 60


def test_no_se_adelanta_una_cuna(engine, media):
    add_pauta(engine, media, "CLIENTE")
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert engine._advance() and engine._current_item["origen"] == "pauta"
    with pytest.raises(ValueError):
        engine.seek(1)


def test_vumetros_emiten_nivel_mientras_suena(engine, media):
    vu = []
    engine.bus.subscribe("vu", lambda e, d: vu.append(d))
    engine._emitir_niveles()
    assert vu == []                                   # en silencio no se inunda
    aid = add_audio(engine, media, "CON NIVEL", seconds=10.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    engine._ultimo_vu = 0
    engine._emitir_niveles()
    assert vu and 0 < vu[-1]["l"] <= 1 and 0 < vu[-1]["r"] <= 1
    engine._do_stop("prueba")
    engine._ultimo_vu = 0
    engine._emitir_niveles()                          # un último 0 para bajar las barras
    assert vu[-1] == {"l": 0.0, "r": 0.0}


def test_operador_retrocede_la_cancion(engine, media):
    aid = add_audio(engine, media, "LARGA", seconds=60.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    engine.seek(40)
    engine._drain_commands()
    engine.seek(10)                                    # hacia atrás
    engine._drain_commands()
    assert 10 <= engine._audio.get_position_sec(engine._active_canal) < 12


def test_salto_que_el_archivo_no_respeta_se_avisa(engine, media):
    """BASS puede aceptar la orden y dejar el audio donde estaba: se avisa al operador."""
    errores, saltos = [], []
    engine.bus.subscribe("playback_error", lambda e, d: errores.append(d))
    engine.bus.subscribe("seek", lambda e, d: saltos.append(d))
    aid = add_audio(engine, media, "TERCO", seconds=60.0)
    assert engine._start({"track": engine._fetch_track(aid), "origen": "manual"})
    engine._audio.set_position_sec = lambda canal, seg: True     # "acepta" sin mover
    engine.seek(40)
    engine._drain_commands()
    assert not saltos and errores and "no permite adelantar" in errores[-1]["msg"]


def test_status_informa_la_version_del_motor(engine):
    from backend.version import VERSION
    assert engine.status["version"] == VERSION
