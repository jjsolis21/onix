"""
Motor de audio en modo mock: se manejan sus métodos directamente (sin
arrancar el hilo) y con un reloj falso para probar tandas a hora fija.
"""

import json
import sqlite3
from datetime import datetime

from backend.database import get_connection
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
    conn = get_connection(engine._db)
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
    # La cuña entra a pleno volumen (sin rampa lenta) y lo que sale es la música
    assert engine._fade_times(a, spot) == (engine.ENTRADA_CORTO, engine.SPOT_FADE_IN)
    assert engine._fade_times(spot, b) == (engine.SPOT_FADE_OUT, engine.SPOT_FADE_OUT)
    # Lo mismo para jingles, sweepers y tips: no arrancan bajos
    for tipo in ("Sweeper", "Tips", "Jingle", "Efecto"):
        corto = {"track": {"tipo_audio": tipo, "fade_in": 3.0}, "origen": "auto"}
        assert engine._fade_times(a, corto)[0] == engine.ENTRADA_CORTO, tipo
        assert engine._fade_times(corto, b) == (engine.SPOT_FADE_OUT, engine.SPOT_FADE_OUT), tipo
    # Entre dos cuñas seguidas el empalme es corto
    assert engine._fade_times(spot, spot) == (engine.ENTRADA_CORTO, engine.SPOT_FADE_OUT)


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
    conn.execute("REPLACE INTO config VALUES ('dispositivo_aire', '1')")
    conn.execute("REPLACE INTO config VALUES ('dispositivo_preescucha', '2')")
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


def test_pasar_a_manual_borra_lo_cargado_por_la_automatizacion(engine, media):
    for i in range(4):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"ART {i}")
    pedido = add_audio(engine, media, "PEDIDO", seconds=60.0)
    engine._actualizar_programa(forzar=True)
    assert engine.programa()["items"]
    engine.queue_track(pedido)                           # lo del operador se queda
    engine.set_mode("manual")
    assert engine.programa()["items"] == []
    assert [q["id"] for q in engine.programa()["cola"]] == [pedido]
    engine._actualizar_programa(forzar=True)             # en manual no se vuelve a planificar
    assert engine.programa()["items"] == []
    assert engine._advance() is True and engine._current_track["id"] == pedido   # la cola sí suena
    assert engine._advance() is False                    # nada más: aire libre, no toma del plan
    engine.set_mode("automatico")                        # al volver, plan nuevo desde ahora
    engine._actualizar_programa(forzar=True)
    assert engine.programa()["items"]


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
    add_pauta(engine, media, "CIERRE", seccion=4)             # Cortina de salida: opcional
    add_pauta(engine, media, "SEPARADOR", seccion=2)          # Separador de entrada: siempre, aunque no haya comerciales
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert en_cola(engine) == ["ID ESTACION", "SEPARADOR"]


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


def test_evento_inmediato_entra_en_punto(engine, media):
    for i in range(6):
        add_audio(engine, media, f"TEMA {i}", seconds=600.0, artista=f"ART {i}", duracion=600)
    esp = add_audio(engine, media, "ESPECIAL", tipo="Programa", seconds=60.0, duracion=60)
    conn = db(engine)
    rid = conn.execute("INSERT INTO relojes (nombre) VALUES ('Especial')").lastrowid
    conn.execute("INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, audio_id) VALUES (?, 0, 'Programa', ?)",
                 (rid, esp))
    conn.execute("INSERT INTO reloj_eventos (nombre, reloj_id, hora, inmediato) VALUES ('En punto', ?, '10:20', 1)",
                 (rid,))
    conn.commit()
    conn.close()
    engine._actualizar_programa(forzar=True)
    assert engine._advance()
    vista = engine.programa()["items"]
    ev = next(i for i in vista if i.get("evento"))
    assert ev["hora"] == "10:20:00"
    engine._now.dt = datetime(2026, 10, 12, 10, 20, 1)
    engine._check_evento_inmediato()
    assert engine._current_track["id"] == esp
    assert "2026-10-12 10:20#1" in engine._eventos_emitidos
    engine._check_evento_inmediato()                       # no vuelve a cortar
    assert engine._current_track["id"] == esp


# ── Aire muerto ──────────────────────────────────────────────────────────────

def _escuchar(engine, evento):
    lista = []
    engine.bus.subscribe(evento, lambda e, d: lista.append(d))
    return lista


def test_aire_muerto_en_automatico_pasa_a_lo_siguiente(engine, media):
    engine._running = True
    alarmas = _escuchar(engine, "aire_muerto")
    a = add_audio(engine, media, "MUDA", seconds=60.0)
    b = add_audio(engine, media, "SIGUIENTE", seconds=60.0)
    assert engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    engine.queue_track(b)
    engine._vigilar_silencio(0.0, 100.0)
    engine._vigilar_silencio(0.0, 105.0)
    assert not alarmas                                   # aún no llega a 10 s
    engine._vigilar_silencio(0.0, 111.0)
    assert alarmas[-1]["accion"] == "siguiente" and alarmas[-1]["titulo"] == "MUDA"
    assert engine._current_track["id"] == b


def test_con_sonido_no_hay_alarma(engine, media):
    engine._running = True
    alarmas = _escuchar(engine, "aire_muerto")
    a = add_audio(engine, media, "SUENA", seconds=60.0)
    assert engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    for t in range(0, 30, 2):
        engine._vigilar_silencio(0.0 if t % 4 else 0.5, float(t))   # silencios cortos
    assert not alarmas


def test_aire_muerto_en_manual_solo_avisa(engine, media):
    engine._running = True
    engine._mode = "manual"
    alarmas = _escuchar(engine, "aire_muerto")
    a = add_audio(engine, media, "MUDA", seconds=60.0)
    assert engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    engine._vigilar_silencio(0.0, 0.0)
    engine._vigilar_silencio(0.0, 11.0)
    engine._vigilar_silencio(0.0, 20.0)                  # no repite la alarma
    assert len(alarmas) == 1 and alarmas[0]["accion"] == "aviso"
    assert engine._current_track["id"] == a


def test_silencio_repetido_deja_de_saltar(engine, media):
    """3 saltos en 10 min: probablemente falla el equipo, no los archivos."""
    engine._running = True
    alarmas = _escuchar(engine, "aire_muerto")
    ids = [add_audio(engine, media, f"MUDA {i}", seconds=60.0) for i in range(5)]
    assert engine._start({"track": engine._fetch_track(ids[0]), "origen": "manual"})
    for i in ids[1:]:
        engine.queue_track(i)
    t = 0.0
    for _ in range(4):
        engine._vigilar_silencio(0.0, t)
        engine._vigilar_silencio(0.0, t + 11)
        engine._finish_crossfade(force=True)            # lo hace el loop del motor
        t += 20
    assert [a["accion"] for a in alarmas] == ["siguiente", "siguiente", "siguiente", "aviso"]


def test_automatico_sin_nada_que_sonar_avisa(engine):
    engine._running = True
    alarmas = _escuchar(engine, "aire_muerto")
    engine._vigilar_silencio(0.0, 0.0)
    engine._vigilar_silencio(0.0, 11.0)
    assert alarmas and alarmas[0]["motivo"] == "sin_audio"
    engine._retenido = True                               # DETENER AL TERMINAR: silencio pedido
    engine._silencio_avisado = None
    engine._vigilar_silencio(0.0, 30.0)
    engine._vigilar_silencio(0.0, 45.0)
    assert len(alarmas) == 1


def test_audio_mudo_queda_fuera_del_programa_un_rato(engine, media):
    engine._running = True
    a = add_audio(engine, media, "MUDA", seconds=60.0)
    for i in range(3):
        add_audio(engine, media, f"OTRA {i}", seconds=60.0, artista=f"A{i}")
    engine._actualizar_programa(forzar=True)
    assert any(i.get("audio_id") == a for i in engine.programa()["items"])
    assert engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    engine._vigilar_silencio(0.0, 0.0)
    engine._vigilar_silencio(0.0, 11.0)
    assert a in engine._failed_ids
    assert not any(i.get("audio_id") == a for i in engine.programa()["items"])


def _config(engine, **kv):
    conn = db(engine)
    for k, v in kv.items():
        conn.execute("INSERT INTO config (clave, valor) VALUES (?, ?)"
                     " ON DUPLICATE KEY UPDATE valor = VALUES(valor)", (k, v))
    conn.commit()
    conn.close()
    engine._load_config()


def test_bloque_al_cierre_arranca_segun_lo_que_dura(engine, media):
    _config(engine, tandas_minutos="0", tandas_cierre="0")
    for c in ("UNO", "DOS"):
        pid = add_pauta(engine, media, c)
        conn = db(engine)
        conn.execute("UPDATE audios SET duracion = 300 WHERE id = (SELECT audio_id FROM pautas WHERE id = ?)", (pid,))
        conn.commit()
        conn.close()
    # 2 cuñas de 5 min = 10 min → el bloque arranca 10:50 para terminar a las 11:00
    engine._now.dt = datetime(2026, 10, 12, 10, 49, 30)
    engine._check_pautas()
    assert en_cola(engine) == []
    engine._now.dt = datetime(2026, 10, 12, 10, 50, 0)
    engine._check_pautas()
    assert sorted(en_cola(engine)) == ["DOS", "UNO"]


def test_spot_con_versiones_rota_en_cada_salida(engine, media):
    pid = add_pauta(engine, media, "ROTA", horas=(LUNES_10, "1-11"))
    b = add_audio(engine, media, "version_b", tipo="Cuña")
    conn = db(engine)
    a = conn.execute("SELECT audio_id FROM pautas WHERE id = ?", (pid,)).fetchone()[0]
    conn.executemany("INSERT INTO pauta_audios (pauta_id, audio_id, orden) VALUES (?, ?, ?)",
                     [(pid, a, 0), (pid, b, 1)])
    conn.commit()
    conn.close()
    salieron = []
    for hora in (10, 11):
        engine._now.dt = datetime(2026, 10, 12, hora, 50)
        engine._check_pautas()
        item = engine._pick_next()
        salieron.append(item["track"]["id"])
        engine._log_emision(item["track"], item, tipo="pauta")
    assert salieron == [a, b]


def test_memo_de_la_cola_llega_al_aire(engine, media):
    aid = add_audio(engine, media, "con_memo")
    assert engine.queue_track(aid, "  Saludar a Valencia  ")
    item = engine._pick_next()
    assert item["origen"] == "cola" and item["nota"] == "Saludar a Valencia"


# ── PAUSA del operador ──
def test_pausa_congela_la_pista_y_no_es_falla(engine, media):
    import time
    add_audio(engine, media, "TEMA P1", seconds=60.0, artista="A1")
    add_audio(engine, media, "TEMA P2", seconds=60.0, artista="A2")
    assert engine._advance()
    pista = engine._current_track["id"]
    time.sleep(0.2)
    engine.pausar(True); engine._drain_commands()
    assert engine.status["pausado"]
    pos = engine._audio.get_position_sec(engine._active_canal)
    assert pos > 0
    time.sleep(0.3)
    engine._tick()
    # sigue la misma pista, sin avanzar, y el vigilante no la ve como caída
    assert engine._current_track["id"] == pista
    assert engine._audio.get_position_sec(engine._active_canal) == pytest.approx(pos)
    assert engine._watchdog_state()["should_be_playing"] is False
    engine._do_recover()
    assert engine._audio.get_position_sec(engine._active_canal) == pytest.approx(pos)
    # reanudar: continúa desde donde quedó
    engine.pausar(False); engine._drain_commands()
    assert not engine.status["pausado"]
    time.sleep(0.2)
    assert engine._audio.get_position_sec(engine._active_canal) > pos + 0.1
    assert engine._watchdog_state()["should_be_playing"] is True


def test_siguiente_en_pausa_sale_de_la_pausa(engine, media):
    add_audio(engine, media, "TEMA S1", seconds=60.0, artista="B1")
    add_audio(engine, media, "TEMA S2", seconds=60.0, artista="B2")
    assert engine._advance()
    primero = engine._current_track["id"]
    engine._do_pausar(True)
    assert engine._advance()
    assert engine._current_track["id"] != primero
    assert not engine._pausado


def test_pausa_sin_pista_no_hace_nada(engine):
    engine._do_pausar(True)
    assert not engine._pausado


# ── Editar la cola del operador (subir, bajar, primero, quitar, insertar en un lugar) ──
def _titulos_cola(engine):
    return [q["titulo"] for q in engine.programa()["cola"]]


def test_cola_se_puede_mover_quitar_e_insertar_en_un_lugar(engine, media):
    ids = {n: add_audio(engine, media, n, seconds=60.0, artista="X") for n in ("A", "B", "C", "D")}
    for n in ("A", "B", "C"):
        engine.queue_track(ids[n])
    uids = {q["titulo"]: q["uid"] for q in engine.programa()["cola"]}
    assert len(set(uids.values())) == 3                       # cada entrada con su uid

    engine.cola_mover(uids["C"], 0); engine._drain_commands()
    assert _titulos_cola(engine) == ["C", "A", "B"]           # PRIMERO
    engine.cola_mover(uids["C"], 1); engine._drain_commands()
    assert _titulos_cola(engine) == ["A", "C", "B"]           # BAJAR
    engine.cola_quitar(uids["A"]); engine._drain_commands()
    assert _titulos_cola(engine) == ["C", "B"]
    engine.queue_track(ids["D"], "", 1); engine._drain_commands()   # insertar tras la primera
    assert _titulos_cola(engine) == ["C", "D", "B"]
    engine.queue_track(ids["A"]); engine._drain_commands()          # sin posición: al final
    assert _titulos_cola(engine)[-1] == "A"
    engine.cola_quitar(999999); engine.cola_mover(999999, 0); engine._drain_commands()   # uid inexistente: nada


def test_audio_del_programa_pasa_a_la_cola_para_sonar_a_continuacion(engine, media):
    for i in range(4):
        add_audio(engine, media, f"TEMA {i}", seconds=60.0, artista=f"ART {i}")
    pedido = add_audio(engine, media, "PEDIDO", seconds=60.0)
    engine._actualizar_programa(forzar=True)
    engine.queue_track(pedido)
    elegido = engine.programa()["items"][2]
    engine.plan_a_cola(elegido["uid"], 0); engine._drain_commands()
    cola = engine.programa()["cola"]
    assert [q["titulo"] for q in cola][:2] == [elegido["titulo"], "PEDIDO"]
    assert elegido["uid"] not in [i["uid"] for i in engine.programa()["items"]]
    assert engine._advance() and engine._current_track["titulo"] == elegido["titulo"]


# ── Voice tracker: la voz entra sobre el final de la canción ────────────────

def _al(engine, seg):
    engine._audio.set_position_sec(engine._active_canal, seg)
    engine._tick()


def test_voice_track_entra_sobre_el_outro_con_la_musica_abajo(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_outro=40.0, cue_mezcla=55.0)
    voz = add_audio(engine, media, "VT 1", tipo="Locucion", seconds=8.0)
    otro = add_audio(engine, media, "OTRO", seconds=60.0)
    engine._mode = "manual"
    assert engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    canal_tema = engine._active_canal
    engine.queue_track(voz); engine.queue_track(otro); engine._drain_commands()

    _al(engine, 39.0)
    assert engine._current_track["id"] == tema               # antes del fin de la voz del cantante, nada
    _al(engine, 40.5)                                        # terminó de cantar y lo que sigue es una voz
    assert engine._current_track["id"] == voz
    st = engine.status
    assert st["voz_sobre"]["audio_id"] == tema and engine._cama["canal"] == canal_tema
    assert engine._audio.is_active(canal_tema)               # la canción sigue sonando debajo
    assert engine._xfade is None

    _al(engine, 8.0 - engine.VOZ_FIN)                        # termina la voz → entra la siguiente
    assert engine._current_track["id"] == otro
    assert engine._cama is None and engine.status["voz_sobre"] is None
    hist = [r[0] for r in db(engine).execute("SELECT tipo FROM historial_emision ORDER BY id")]
    assert hist == ["musica", "voz", "musica"]


def test_sin_voice_track_la_cancion_no_se_corta_en_su_outro(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_outro=40.0, cue_mezcla=55.0)
    otro = add_audio(engine, media, "OTRO", seconds=60.0)
    engine._mode = "manual"
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    engine.queue_track(otro); engine._drain_commands()
    _al(engine, 41.0)
    assert engine._current_track["id"] == tema               # la mezcla normal sigue en 55
    _al(engine, 55.5)
    assert engine._current_track["id"] == otro


def test_voz_sin_nada_despues_en_manual_devuelve_la_cancion(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_outro=40.0, cue_mezcla=55.0)
    voz = add_audio(engine, media, "VT 2", tipo="Locucion", seconds=6.0)
    engine._mode = "manual"
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    engine.queue_track(voz); engine._drain_commands()
    _al(engine, 40.5)
    assert engine._current_track["id"] == voz
    _al(engine, 6.0 - engine.VOZ_FIN)                        # termina la voz y la cola está vacía
    assert engine._current_track["id"] == tema and engine._cama is None
    assert engine._mix_point == float("inf")                 # la canción termina completa


def test_pausa_y_stop_alcanzan_a_la_musica_bajo_la_voz(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_mezcla=50.0)
    voz = add_audio(engine, media, "VT 3", tipo="Locucion", seconds=6.0)
    engine._mode = "manual"
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    engine.queue_track(voz); engine._drain_commands()
    _al(engine, 50.5)                                        # sin outro marcado: entra en la mezcla
    assert engine._current_track["id"] == voz
    cama = engine._cama["canal"]
    engine.pausar(True); engine._drain_commands()
    assert not engine._audio.is_playing(cama)
    engine.pausar(False); engine._drain_commands()
    assert engine._audio.is_playing(cama)
    engine.stop_playback(); engine._drain_commands()
    assert engine._cama is None and not engine._audio.is_active(cama)


def test_voz_sin_musica_antes_suena_sola(engine, media):
    voz = add_audio(engine, media, "VT 4", tipo="Locucion", seconds=6.0)
    assert engine._start({"track": engine._fetch_track(voz), "origen": "manual"})
    assert engine._cama is None and engine._current_track["id"] == voz
    assert abs(engine._mix_point - (6.0 - engine.VOZ_FIN)) < 0.01


# ── Cartuchera como Soho: pulsar otra vez detiene; marcas y volumen del botón ─

def _efx_eventos(engine):
    ev = []
    for n in ("effect_fired", "effect_stopped"):
        engine.bus.subscribe(n, lambda e, d: ev.append((e, d)))
    return ev


def test_pulsar_el_mismo_boton_mientras_suena_lo_detiene(engine, media):
    from backend.audio.audio_engine import CANAL_EFX_BASE
    tema = add_audio(engine, media, "TEMA", seconds=60.0)
    jid = add_audio(engine, media, "CLAPS", tipo="Efecto", seconds=10.0)
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    ev = _efx_eventos(engine)
    engine.fire_effect(jid, 0, {"boton": "p1/1"}); engine._drain_commands()
    assert engine._audio.is_active(CANAL_EFX_BASE) and engine._effect_ducking
    assert ev[-1][0] == "effect_fired" and ev[-1][1]["duracion"] == 10.0

    engine.fire_effect(jid, 0, {"boton": "p1/1"}); engine._drain_commands()      # segunda pulsación
    assert not engine._audio.is_active(CANAL_EFX_BASE) and engine._efx == {}
    assert ev[-1][0] == "effect_stopped" and ev[-1][1]["motivo"] == "operador" and ev[-1][1]["audio_id"] == jid
    engine._restore_ducking()
    assert not engine._effect_ducking                    # la música vuelve a su volumen sin esperar los 10 s

    engine.fire_effect(jid, 0, {"boton": "p1/1"}); engine._drain_commands()      # tercera: suena otra vez
    assert engine._audio.is_active(CANAL_EFX_BASE)


def test_sin_boton_un_segundo_disparo_relanza_como_antes(engine, media):
    from backend.audio.audio_engine import CANAL_EFX_BASE
    jid = add_audio(engine, media, "JINGLE", tipo="Efecto", seconds=5.0)
    engine.fire_effect(jid); engine._drain_commands()
    engine._audio.set_position_sec(CANAL_EFX_BASE, 3.0)
    engine.fire_effect(jid); engine._drain_commands()
    assert engine._audio.is_active(CANAL_EFX_BASE) and engine._audio.get_position_sec(CANAL_EFX_BASE) < 1.0


def test_otro_boton_en_el_mismo_canal_corta_al_anterior(engine, media):
    a = add_audio(engine, media, "UNO", tipo="Efecto", seconds=8.0)
    b = add_audio(engine, media, "DOS", tipo="Efecto", seconds=8.0)
    ev = _efx_eventos(engine)
    engine.fire_effect(a, 0, {"boton": "p1/1"}); engine._drain_commands()
    engine.fire_effect(b, 6, {"boton": "p1/7"}); engine._drain_commands()        # 7 comparte canal con 1
    assert [(e, d.get("motivo")) for e, d in ev] == [("effect_fired", None), ("effect_stopped", "reemplazado"),
                                                    ("effect_fired", None)]
    assert list(engine._efx.values())[0]["audio_id"] == b


def test_el_boton_respeta_marca_in_marca_out_y_volumen(engine, media):
    from backend.audio.audio_engine import CANAL_EFX_BASE
    jid = add_audio(engine, media, "CLAPS", tipo="Efecto", seconds=10.0)
    vols = []
    real = engine._audio.set_volume
    engine._audio.set_volume = lambda canal, v: (vols.append((canal, v)), real(canal, v))[1]
    ev = _efx_eventos(engine)
    engine.fire_effect(jid, 0, {"boton": "p1/1", "mark_in": 2.0, "mark_out": 5.0, "volumen": 50})
    engine._drain_commands()
    assert 2.0 <= engine._audio.get_position_sec(CANAL_EFX_BASE) < 2.5      # arranca en la marca in
    assert ev[-1][1]["duracion"] == 3.0
    v = [x for c, x in vols if c == CANAL_EFX_BASE][-1]
    assert v == pytest.approx(engine._master_volume * engine._gain_of(engine._fetch_track(jid)) * 0.5)

    engine._audio.set_position_sec(CANAL_EFX_BASE, 4.0); engine._vigilar_efectos()
    assert engine._audio.is_active(CANAL_EFX_BASE)                          # aún no llega a la marca out
    engine._audio.set_position_sec(CANAL_EFX_BASE, 5.1); engine._vigilar_efectos()
    assert not engine._audio.is_active(CANAL_EFX_BASE)                      # se corta en la marca out
    assert ev[-1][0] == "effect_stopped" and ev[-1][1]["motivo"] == "fin"


def test_marcas_invalidas_se_ignoran_y_el_fin_natural_avisa(engine, media):
    from backend.audio.audio_engine import CANAL_EFX_BASE
    jid = add_audio(engine, media, "CORTO", tipo="Efecto", seconds=4.0)
    ev = _efx_eventos(engine)
    engine.fire_effect(jid, 0, {"boton": "p1/1", "mark_in": 9.0, "mark_out": 1.0, "volumen": "x"})
    engine._drain_commands()
    assert engine._audio.get_position_sec(CANAL_EFX_BASE) < 0.5 and ev[-1][1]["duracion"] == 4.0
    engine._audio.set_position_sec(CANAL_EFX_BASE, 4.0); engine._vigilar_efectos()   # terminó solo
    assert ev[-1][0] == "effect_stopped" and ev[-1][1]["motivo"] == "fin" and engine._efx == {}


def test_la_musica_bajo_la_voz_queda_al_20_por_ciento_y_es_configurable(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_outro=40.0, cue_mezcla=55.0)
    voz = add_audio(engine, media, "VT", tipo="Locucion", seconds=8.0)
    bajadas = []
    real = engine._audio.slide_volume
    engine._audio.slide_volume = lambda canal, v, ms: (bajadas.append((canal, v)), real(canal, v, ms))[1]

    def al_aire_con_voz():
        bajadas.clear()
        engine._mode = "manual"
        engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
        canal_tema = engine._active_canal
        pleno = engine._target_volume(engine._fetch_track(tema))
        engine.queue_track(voz); engine._drain_commands()
        _al(engine, 40.5)
        assert engine._cama and engine._cama["canal"] == canal_tema
        return [v for c, v in bajadas if c == canal_tema][-1] / pleno

    assert engine._voz_duck == 0.2
    assert al_aire_con_voz() == pytest.approx(0.2)            # por defecto: 20 %
    engine._do_stop("prueba")

    conn = db(engine)
    conn.execute("INSERT INTO config (clave, valor) VALUES ('voz_fondo_pct', '35')"
                 " ON DUPLICATE KEY UPDATE valor = VALUES(valor)")
    conn.commit(); conn.close()
    engine._load_config()
    engine._mode = "manual"
    assert al_aire_con_voz() == pytest.approx(0.35)           # lo que diga Admin › Motor de audio


# ── Tandas por día y hora (grilla, como los «Breaks comerciales» de Jazler) ──

def test_tandas_de_hora_funcion_pura():
    from backend.audio.audio_engine import tandas_de_hora
    base = ([15, 45], [15], [])
    assert tandas_de_hora({}, 1, 10, *base) == ([15, 45], [15], [])
    assert tandas_de_hora({"1-10": {"off": True}}, 1, 10, *base) == ([], [], [])
    assert tandas_de_hora({"1-10": {"minutos": "20,50"}}, 1, 10, *base) == ([20, 50], [], [])
    assert tandas_de_hora({"1-10": {"minutos": "0,15", "inmediatas": "0", "cierre": "15,30"}}, 1, 10, *base) \
        == ([0, 15], [0], [15])
    assert tandas_de_hora({"1-10": {"minutos": "20"}}, 2, 10, *base) == ([15, 45], [15], [])   # otro día


def test_grilla_cambia_los_minutos_de_esa_hora(engine, media):
    add_pauta(engine, media, "CLIENTE A")
    _config(engine, tandas_minutos="15,45", tandas_grilla=json.dumps({"1-10": {"minutos": "20"}}))
    engine._now.dt = datetime(2026, 10, 12, 10, 16)          # lunes 10:16: la de :15 no existe esa hora
    engine._check_pautas(forzar=("x", 0))
    assert en_cola(engine) == []
    engine._now.dt = datetime(2026, 10, 12, 10, 20)
    engine._check_pautas(forzar=("x", 0))
    assert en_cola(engine) == ["CLIENTE A"] and engine._priority_queue[0]["tanda"] == 20


def test_grilla_hora_sin_tandas(engine, media):
    add_pauta(engine, media, "CLIENTE A")
    _config(engine, tandas_minutos="15,45", tandas_grilla=json.dumps({"1-10": {"off": True}}))
    engine._now.dt = datetime(2026, 10, 12, 10, 50)
    engine._check_pautas(forzar=("x", 0))
    assert en_cola(engine) == [] and engine._tandas_minutos == []
    engine._now.dt = datetime(2026, 10, 12, 11, 50)          # la hora siguiente vuelve a lo general
    engine._check_pautas(forzar=("x", 0))
    assert engine._tandas_minutos == [15, 45]


def test_grilla_inicio_inmediato_solo_esa_hora(engine, media):
    add_pauta(engine, media, "EN PUNTO")
    cancion = add_audio(engine, media, "CANCION", seconds=60.0)
    _config(engine, tandas_minutos="0", tandas_grilla=json.dumps({"1-10": {"minutos": "0", "inmediatas": "0"}}))
    engine._mode = "automatico"                              # la config de prueba trae manual
    engine._now.dt = datetime(2026, 10, 12, 9, 59, 30)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "auto"})
    engine._now.dt = datetime(2026, 10, 12, 10, 0, 0)
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "pauta"


# ── Orden a mano de las tandas (Admin › Comerciales › Tandas del día) ────────

def test_aplicar_ajustes_funcion_pura():
    from backend.audio.audio_engine import aplicar_ajustes
    plan = {15: [1, 2], 45: [3, 4]}
    assert aplicar_ajustes(plan, {}) == plan
    assert aplicar_ajustes(plan, {45: [2, 4]}) == {15: [1], 45: [2, 4, 3]}      # 2 pasa a :45, 4 primero
    assert aplicar_ajustes(plan, {30: [1]}) == plan                              # minuto que ya no existe
    assert aplicar_ajustes(plan, {15: [9, 2]}) == {15: [2, 1], 45: [3, 4]}       # una que no es de la hora se ignora


def _ajuste(engine, minuto_ids=None, quitadas=()):
    from backend.audio.audio_engine import leer_ajustes
    conn = db(engine)
    leer_ajustes(conn, "2026-10-12", 10)
    for m, ids in (minuto_ids or {}).items():
        conn.execute("INSERT INTO tanda_ajustes VALUES ('2026-10-12', 10, ?, ?)", (m, json.dumps(ids)))
    for q in quitadas:
        conn.execute("INSERT INTO tanda_quitadas VALUES ('2026-10-12', 10, ?)", (q,))
    conn.commit()
    conn.close()


def _id_de(engine, cliente):
    conn = db(engine)
    pid = conn.execute("SELECT id FROM pautas WHERE cliente = ?", (cliente,)).fetchone()[0]
    conn.close()
    return pid


def test_ajuste_mueve_y_ordena_las_cunas(engine, media):
    for c in ("A", "B", "C"):
        add_pauta(engine, media, c)
    a, b, c = (_id_de(engine, x) for x in "ABC")
    _ajuste(engine, {15: [c, a, b]})                         # todo en :15, en este orden
    engine._tandas_minutos = [15, 45]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas(forzar=("x", 0))
    assert en_cola(engine) == ["C", "A", "B"]
    assert engine._plan[45] == []


def test_ajuste_quita_una_cuna_ese_dia(engine, media):
    add_pauta(engine, media, "A")
    add_pauta(engine, media, "B")
    _ajuste(engine, quitadas=[_id_de(engine, "A")])
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas(forzar=("x", 0))
    engine._check_pautas(forzar=("x", 0))                    # tampoco vuelve como «pauta nueva»
    assert en_cola(engine) == ["B"]


def test_bloque_al_cierre_e_inmediato_corta_para_terminar_en_punto(engine, media):
    # 2 cuñas de 3,5 min = 7 min, al cierre en :00 e inmediata → corta la canción a las 10:53
    _config(engine, tandas_minutos="0", tandas_cierre="0", tandas_inmediatas="0")
    engine._mode = "automatico"                              # la config de prueba trae manual
    for c in ("UNO", "DOS"):
        pid = add_pauta(engine, media, c)
        conn = db(engine)
        conn.execute("UPDATE audios SET duracion = 210 WHERE id = (SELECT audio_id FROM pautas WHERE id = ?)", (pid,))
        conn.commit()
        conn.close()
    cancion = add_audio(engine, media, "CANCION", seconds=60.0)
    engine._now.dt = datetime(2026, 10, 12, 10, 52, 30)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "auto"})
    engine._check_pautas(forzar=("x", 0))                    # el motor mide el bloque
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "auto"           # aún no: faltan 30 s
    engine._now.dt = datetime(2026, 10, 12, 10, 53, 0)
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "pauta"          # cortó: 7 min hasta las 11:00
    assert any(e == "tanda_inmediata" and d["minuto"] == 0 for e, d in engine.events)


def test_corte_inmediato_primero_baja_la_cancion_y_la_cuna_entra_a_la_hora(engine, media):
    """El fade termina justo a la hora y SOLO entonces entra la cuña: nunca se mezclan (la cuña no tapa el fade)."""
    add_pauta(engine, media, "EN PUNTO")
    cancion = add_audio(engine, media, "CANCION", seconds=60.0)
    _config(engine, tandas_minutos="0", tandas_inmediatas="0", fade_corte_seg="1")
    engine._mode = "automatico"
    saliente = []
    engine._now.dt = datetime(2026, 10, 12, 9, 59, 0)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "auto"})
    canal_cancion = engine._active_canal
    original = engine._audio.slide_volume
    engine._audio.slide_volume = lambda canal, vol, ms: (saliente.append(vol) if canal == canal_cancion else None, original(canal, vol, ms))[1]

    engine._now.dt = datetime(2026, 10, 12, 9, 59, 30)
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "auto" and not saliente          # falta mucho: ni baja ni entra

    engine._now.dt = datetime(2026, 10, 12, 9, 59, 59)                          # 1 s antes: empieza a bajar
    engine._check_tanda_inmediata()
    import time as _t
    _t.sleep(1.3)
    assert engine._current_item["origen"] == "auto"                             # la cuña TODAVIA no entró
    assert len(saliente) >= 5 and saliente[-1] == pytest.approx(0.16, abs=0.01)  # termina en 16 %: NO en silencio
    assert saliente == sorted(saliente, reverse=True)                           # solo baja
    assert min(saliente) > 0.1                                                  # nunca se vacía antes de que entre la cuña

    engine._now.dt = datetime(2026, 10, 12, 10, 0, 0)                           # la hora: entra la cuña
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "pauta"
    assert any(e == "tanda_inmediata" for e, _ in engine.events)


def test_corte_inmediato_si_no_hay_tanda_la_cancion_recupera_su_volumen(engine, media):
    cancion = add_audio(engine, media, "CANCION", seconds=60.0)
    _config(engine, tandas_minutos="0", tandas_inmediatas="0", fade_corte_seg="1")
    engine._mode = "automatico"
    volumenes = []
    engine._now.dt = datetime(2026, 10, 12, 9, 59, 0)
    assert engine._start({"track": engine._fetch_track(cancion), "origen": "auto"})
    canal = engine._active_canal
    original = engine._audio.slide_volume
    engine._audio.slide_volume = lambda c, vol, ms: (volumenes.append(vol) if c == canal else None, original(c, vol, ms))[1]
    engine._now.dt = datetime(2026, 10, 12, 9, 59, 59)
    engine._check_tanda_inmediata()
    import time as _t
    _t.sleep(1.3)
    engine._now.dt = datetime(2026, 10, 12, 10, 0, 0)
    engine._check_tanda_inmediata()
    assert engine._current_item["origen"] == "auto"                             # no había cuñas: sigue la canción
    assert volumenes[-1] > 0.5                                                  # y volvió a oírse


def test_curva_del_fade_se_lee_de_la_config():
    from backend.audio.audio_engine import parse_curva_fade
    assert parse_curva_fade("100,70,40,30,16") == [1.0, 0.7, 0.4, 0.3, 0.16]
    assert parse_curva_fade("100; 50 0") == [1.0, 0.5, 0.0]
    assert parse_curva_fade("") == [1.0, 0.7, 0.4, 0.3, 0.16]
    assert parse_curva_fade("abc") == [1.0, 0.7, 0.4, 0.3, 0.16]
    assert parse_curva_fade("50") == [1.0, 0.7, 0.4, 0.3, 0.16]                 # un solo punto no es una curva
    assert parse_curva_fade("150,-5,10") == [1.0, 0.0, 0.1]                     # se recorta a 0–100 %



# ── Voces de mujer: no dos seguidas (regla de las radios venezolanas) ──

def _p(cliente, voz, rubro=""):
    return {"pauta_id": cliente, "cliente": cliente, "voz": voz, "rubro": rubro, "posicion": 0, "orden": None}


def _juntas(lista):
    return any(a["voz"] == "Mujer" and b["voz"] == "Mujer" for a, b in zip(lista, lista[1:]))


def test_es_voz_mujer():
    from backend.audio.audio_engine import es_voz_mujer
    assert es_voz_mujer("Mujer") and es_voz_mujer("MUJER") and es_voz_mujer("Femenina") and es_voz_mujer("f")
    assert not es_voz_mujer("Hombre") and not es_voz_mujer("Dúo") and not es_voz_mujer("") and not es_voz_mujer(None)


def test_mujeres_no_se_pegan_y_la_regla_cede_si_no_hay_como():
    from backend.audio.audio_engine import fijar_regla_voces, ordenar_tanda
    fijar_regla_voces(True)
    try:
        nombres = lambda l: [p["cliente"] for p in l]
        # M M F F -> se reparten (M F M F): no deja las dos mujeres juntas al final
        o = ordenar_tanda([_p("M1", "Hombre"), _p("M2", "Hombre"), _p("F1", "Mujer"), _p("F2", "Mujer")])
        assert not _juntas(o)
        # F F F M M -> F M F M F
        o = ordenar_tanda([_p("F1", "Mujer"), _p("F2", "Mujer"), _p("F3", "Mujer"), _p("M1", "Hombre"), _p("M2", "Hombre")])
        assert not _juntas(o) and len(o) == 5
        # solo mujeres: la regla se rompe y salen todas
        assert sorted(nombres(ordenar_tanda([_p("F1", "Mujer"), _p("F2", "Mujer")]))) == ["F1", "F2"]
        # las sin voz marcada sirven de separador
        assert nombres(ordenar_tanda([_p("F1", "Mujer"), _p("F2", "Mujer"), _p("X", "")]))[1] == "X"
        # apagada: respeta el orden recibido
        fijar_regla_voces(False)
        assert nombres(ordenar_tanda([_p("F1", "Mujer"), _p("F2", "Mujer"), _p("M", "Hombre")])) == ["F1", "F2", "M"]
    finally:
        fijar_regla_voces(True)


def test_la_voz_pesa_mas_que_el_rubro():
    from backend.audio.audio_engine import ordenar_tanda
    o = ordenar_tanda([_p("F1", "Mujer", "BANCA"), _p("F2", "Mujer", "BANCA"), _p("M", "Hombre", "BANCA")])
    assert [p["cliente"] for p in o] == ["F1", "M", "F2"]       # mismo rubro, pero separa a las mujeres


# ── Rubros incompatibles: misma tanda sí, seguidos no ──

def test_rubros_incompatibles_no_suenan_seguidos():
    from backend.audio.audio_engine import fijar_conflictos_rubros, ordenar_tanda, rubros_chocan
    fijar_conflictos_rubros([("Restaurante", "Cafetería")])
    try:
        assert rubros_chocan("RESTAURANTE", "CAFETERIA") and rubros_chocan("CAFETERIA", "RESTAURANTE")
        assert rubros_chocan("BANCA", "BANCA") and not rubros_chocan("BANCA", "CAFETERIA") and not rubros_chocan("", "BANCA")
        r = lambda c, rub: {"pauta_id": c, "cliente": c, "rubro": rub, "voz": "", "posicion": 0, "orden": None}
        o = ordenar_tanda([r("R", "Restaurante"), r("C", "cafeteria "), r("B", "Banca")])
        assert [p["cliente"] for p in o] == ["R", "B", "C"]                 # la de otro rubro las separa
        # solo hay esas dos: se permiten (ninguna se queda sin salir)
        assert len(ordenar_tanda([r("R", "Restaurante"), r("C", "Cafeteria")])) == 2
    finally:
        fijar_conflictos_rubros([])


def test_orden_de_la_tanda_promociones_id_separador_y_comerciales(engine, media):
    """Promociones (0) lo primero, luego Identificación (1), Separador de entrada (2) y por último la publicidad (3)."""
    add_pauta(engine, media, "CLIENTE", seccion=3)
    add_pauta(engine, media, "SEPARADOR", seccion=2)
    add_pauta(engine, media, "ID ESTACION", seccion=1)
    add_pauta(engine, media, "PROMO", seccion=0)
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert en_cola(engine) == ["PROMO", "ID ESTACION", "SEPARADOR", "CLIENTE"]


# ── Tanda «al cierre» sin inicio inmediato: si la canción cruza la hora, la tanda no se pierde ──

def test_tanda_al_cierre_que_espera_la_cancion_sobrevive_al_cambio_de_hora(engine, media):
    add_pauta(engine, media, "CIERRE A")
    add_pauta(engine, media, "CIERRE B")
    conn = db(engine)
    conn.execute("UPDATE audios SET duracion = 30 WHERE tipo_audio = 'Cuña'")
    conn.commit()
    conn.close()
    _config(engine, tandas_minutos="0", tandas_cierre="0", tandas_inmediatas="")
    engine._mode = "automatico"
    engine._now.dt = datetime(2026, 10, 12, 10, 58, 59)      # el bloque (60 s) entra a las 10:59:00 y termina a las 11:00
    engine._check_pautas()
    assert en_cola(engine) == []
    engine._now.dt = datetime(2026, 10, 12, 10, 59, 1)
    engine._check_pautas()
    assert sorted(en_cola(engine)) == ["CIERRE A", "CIERRE B"]   # en cola, esperando a que termine la canción
    # la canción cruza la hora: ya son las 11:00:20 y la cola sigue ahí
    engine._now.dt = datetime(2026, 10, 12, 11, 0, 20)
    engine._check_pautas()
    assert sorted(en_cola(engine)) == ["CIERRE A", "CIERRE B"]
    assert not [d for e, d in engine.events if e == "pauta_missed"]
    assert engine._pick_next()["origen"] == "pauta"             # y sale en cuanto termina la canción
    # pasada la gracia (5 min) ya no tiene sentido: se descarta
    engine._now.dt = datetime(2026, 10, 12, 11, 6, 0)
    engine._check_pautas()
    assert en_cola(engine) == []


def test_una_pauta_normal_de_la_hora_anterior_sigue_descartandose(engine, media):
    add_pauta(engine, media, "NORMAL")
    engine._tandas_minutos = [15]
    engine._now.dt = datetime(2026, 10, 12, 10, 15)
    engine._check_pautas()
    assert en_cola(engine) == ["NORMAL"]
    engine._now.dt = datetime(2026, 10, 12, 11, 1)           # cambió la hora y no salió: se pierde (no se acumula)
    engine._check_pautas()
    assert en_cola(engine) == []
    assert [d for e, d in engine.events if e == "pauta_missed"]
